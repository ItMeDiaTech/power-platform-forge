"""ppx probe: what this user can reach on the tenant, per area, check and token source.

Run on the work laptop. Verbatim results (response bodies, tenant identifiers) stay in
<data>/probe/<date>/raw.json on this computer; <data>/routing.json records, per API, the token
sources that worked so ppx.auth tries them first; <data>/probe/<date>/report.md is the scrubbed
copy that may leave the laptop. Each check has its own timeout and one failing check never stops
the others. The probe never refreshes a model.

Split by job: probe_writes (write probes), probe_scrub (report scrubbing) and probe_records (shared
constants and check-record helpers).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.parse
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from ppx import __version__, apis, auth, cmd_auth, http, maker_session, pac, proc
from ppx.errors import PpxError, UsageError
from ppx.probe_records import (KINDS, ORDER, SLOW, _cell, _cli_failure, _dax_summary,
                               _definition_summary, _detail, _digest, _failure, _fields, _from_error, _json,
                               _key_paths, _md, _ok, _pick, _run, _settle, _tool, _yaml_hits)
from ppx.probe_scrub import Scrubber
from ppx.probe_writes import _WriteProbes, _plan_writes
from ppx.redact import scrub
from ppx.verbs_core import AREAS

# AREAS lists tokens before toolchain, so the pbi/az mint is the first az start of the run (the cold timing).

DEVICE_FIELDS = ("AzureAdJoined", "WorkplaceJoined", "DeviceAuthStatus", "AzureAdPrt")
DAX = 'EVALUATE ROW("n", 1)'
_PS = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
_PS_CMDLETS = ("$o = @{commands = @(Get-Command -Module Microsoft.PowerApps.PowerShell | ForEach-Object Name); "
               "help = @{}}; foreach ($c in 'Set-PowerAppRoleAssignment', 'Restore-PowerAppVersion') "
               "{ $o.help[$c] = @((Get-Help $c).parameters.parameter | ForEach-Object name) }; "
               "$o | ConvertTo-Json -Depth 4 -Compress")
_KV = re.compile(r"^\s*([A-Za-z][\w ]*?)\s*:\s?(.*?)\s*$", re.M)
_COLUMN = r"[A-Za-z][\w()./-]*(?: [\w()./-]+)*"
_HEADER = re.compile(rf"^\s*{_COLUMN}(?: {{2,}}{_COLUMN})+\s*$")


def run(ctx, args) -> dict:
    wanted = {a for a in re.split(r"[,\s]+", args.area or "") if a}
    unknown = sorted(wanted - {*AREAS, "all"})
    if unknown:
        raise UsageError(f"unknown --area {', '.join(unknown)}; choose from {', '.join(AREAS)} or all, comma-separated")
    areas = list(AREAS) if not wanted or "all" in wanted else [a for a in AREAS if a in wanted]
    site = args.site or (ctx.profile.site if ctx.profile else None)
    try:
        if site:
            apis.sharepoint(site)
    except (PpxError, ValueError) as exc:  # urlsplit raises ValueError on a URL such as https://[x
        raise UsageError(f"{site!r} is not a SharePoint Online site URL: pass --site "
                         "https://<tenant>.sharepoint.com/sites/<site> or fix the profile's site") from exc
    data_dir = ctx.need_data_dir()
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    folder = data_dir / "probe" / day
    # Timestamped, so a second run on the same day never overwrites the copy taken before a write-back.
    backups = {kind: folder / f"{kind}-definition-{now:%H%M%S}.json" for kind in KINDS}
    if args.write:
        missing = [flag for flag, given in (("--workspace", args.workspace), ("--model or --report",
                                                                              args.model or args.report)) if not given]
        if missing:
            raise UsageError(f"--write needs scratch items you own as targets: missing {' and '.join(missing)}")
        _plan_writes(ctx, args, {kind: path.relative_to(data_dir).as_posix() for kind, path in backups.items()})
    if ctx.dry_run:
        return {"areas": areas, "write": bool(args.write)}
    ctx.changes.require_yes(ctx.yes)

    probe = _Probe(ctx, args, backups, site)
    for area in areas:
        probe.area(area, getattr(probe, f"area_{area}"))
    if args.write:
        probe.area("write", probe.writes)
    routing = probe.routing()
    raw = {"date": day, "ppx": __version__, "areas": areas, "write": bool(args.write), "results": probe.results}
    scrubber = Scrubber()
    # Tool names are not tenant data, and learning them as names would blank out the toolchain table.
    scrubber.learn({area: checks for area, checks in probe.results.items() if area != "toolchain"})
    scrubber.learn_args(args, probe.site)
    report = probe.report(day, areas, routing, scrubber.text)
    raw_path, report_path = folder / "raw.json", folder / "report.md"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps(scrub(raw), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        report_path.write_text(report, encoding="utf-8")
        _save_routing(data_dir, routing)
    except OSError as exc:
        raise PpxError(f"could not write the probe results: {exc}") from exc
    ctx.next(f"read {report_path.name} (scrubbed), then copy it to the dev PC; raw.json and routing.json stay here")
    return {"matrix": {area: {name: _cell(rec) for name, rec in checks.items()}
                       for area, checks in probe.results.items()},
            "report": str(report_path), "raw": str(raw_path), "routing": str(data_dir / auth.ROUTING_FILE)}


class _Tokens:
    """Tokens minted once per resource and source for this run, through auth's single-source attempt.

    auth.token honours routing.json and the memo, so through it the probe could neither re-test a
    source an earlier probe dropped nor time a cold az start.
    """

    def __init__(self, timeout: float):
        self.timeout, self.got = timeout, {}

    def get(self, resource: str, source: str, fresh: bool = False):
        got = self.got.get((resource, source))
        if fresh or got is None or (isinstance(got, auth.Token) and got.expires_on - 60 < time.time()):
            got = self.got[(resource, source)] = auth._attempt(source, resource, self.timeout)
        return got

    def provider(self, resource: str, source: str):
        """A token provider for ppx.http that only ever hands out `source`'s token."""
        def provide(audience, op="read", exclude=()):
            tok = self.get(resource, source)
            if source in exclude or not isinstance(tok, auth.Token):
                raise PpxError(f"no {source} token left to try")
            return tok
        return provide


class _Probe(_WriteProbes):
    def __init__(self, ctx, args, backups: dict[str, Path], site: str | None):
        self.ctx, self.args, self.backups, self.site = ctx, args, backups, site
        self.timeout = float(ctx.timeout)
        self.tokens = _Tokens(self.timeout)
        self.env_url = ctx.profile.url if ctx.profile else None
        self.results: dict[str, dict[str, dict]] = {}
        self.ids: dict[str, dict | None] = {}
        self.definitions: dict[str, dict] = {}
        self.evidence: dict[str, list[tuple[str, str]]] = {}  # audience key -> (source, status) of each write
        self.refused: dict[str, set[str]] = {}  # audience key -> read sources an API refused, see via()

    # -- running checks -------------------------------------------------------------------------

    def area(self, name: str, fn) -> None:
        try:
            fn()
        except Exception as exc:  # a bug or surprise in one area must not cost the others
            self.put(name, "area", {**_from_error(exc), "elapsed_ms": 0})

    def check(self, area: str, name: str, fn, spans: int = 1):
        return self.put(area, name, _run(fn, self.timeout * spans + 1))

    def put(self, area: str, name: str, rec: dict):
        value = rec.pop("value", None)
        self.results.setdefault(area, {})[name] = rec
        return value

    def skip(self, area: str, name: str, why: str) -> None:
        self.put(area, name, {"status": "skipped", "summary": {"why": why}, "elapsed_ms": 0})

    def via(self, resource: str, sources, fn, every: bool = False, write: bool = False) -> list[dict]:
        """Run fn(token_provider) per source; a missing token or a 401/403/read-only refusal moves to the next.

        `every` tries every source whatever the others answered. A read source the API refused while
        another source got through is remembered, so routing() leaves it out.
        """
        attempts = []
        for source in sources:
            got = self.tokens.get(resource, source)
            if not isinstance(got, auth.Token):
                attempts.append({"source": source, **_failure(got)})
                continue
            try:
                value = fn(self.tokens.provider(resource, source))
            except PpxError as exc:
                attempts.append({"source": source, **_from_error(exc)})
                if every or attempts[-1]["status"] == "blocked":
                    continue
                break
            attempts.append({"source": source, "status": "ok", "value": value})
            if not every:
                break
        if not write and any(a["status"] == "ok" for a in attempts):
            self.refused.setdefault(auth.audience_key(resource), set()).update(
                a["source"] for a in attempts if a["status"] == "blocked")
        return attempts

    def _list(self, api: apis.Api, path: str, params: dict | None = None) -> dict:
        return _settle(self.via(api.resource, ORDER, lambda prov: http.paged(
            api.url(path), audience=api.resource, params=params, timeout=self.timeout, token_provider=prov)[0]),
            _fields)

    def _get(self, api: apis.Api, path: str, params: dict | None, sources, every: bool = False) -> dict:
        return _settle(self.via(api.resource, sources, lambda prov: http.call(
            "GET", api.url(path), audience=api.resource, params=params, timeout=self.timeout,
            token_provider=prov), every), _fields)

    def _mint(self, resource: str, source: str, fresh: bool = False) -> dict:
        got = self.tokens.get(resource, source, fresh)
        if not isinstance(got, auth.Token):
            return _failure(got)
        claims = auth.decode_claims(got.value)
        return _ok({"scp": claims.get("scp")}, {"claims": claims})

    # -- areas ----------------------------------------------------------------------------------

    def area_device(self) -> None:
        if not proc.IS_WINDOWS:
            return self.skip("device", "dsregcmd", "not Windows")

        def dsregcmd():
            p = proc.run(["dsregcmd", "/status"], timeout=self.timeout, install_hint="dsregcmd ships with Windows")
            if p.code != 0:
                return _cli_failure(p)
            fields = dict(_KV.findall(p.stdout))
            return _ok({k: fields.get(k, "absent") for k in DEVICE_FIELDS}, {"fields": fields, "stdout": p.stdout})
        self.check("device", "dsregcmd", dsregcmd)

    def area_toolchain(self) -> None:
        def prerequisites():
            rows = cmd_auth.prerequisites(min(self.timeout, cmd_auth.CHECK_TIMEOUT))
            missing = [r["name"] for r in rows if not r.get("found", r.get("reachable"))]
            out = _ok({"tools": [_tool(r) for r in rows]}, rows)
            if missing:
                out.update(status="missing", code=",".join(missing))
            return out
        self.check("toolchain", "prerequisites", prerequisites)

    def area_tokens(self) -> None:
        audiences = [(key, apis.by_name(key).resource) for key in ("pbi", "fabric", "graph", "powerplatform")]
        if self.env_url:
            audiences.append(("dataverse", apis.dataverse(self.env_url).resource))
        if self.site:
            audiences.append(("sharepoint", apis.sharepoint(self.site).resource))
        for key, resource in audiences:
            for source in ORDER + (("pac",) if key == "powerplatform" else ()):
                self.check("tokens", f"{key}/{source}", partial(self._mint, resource, source))
        # pbi/az above is the cold az start; a second spawn shows what a warm one costs.
        self.check("tokens", "az-warm", partial(self._mint, apis.PBI_RESOURCE, "az", True))

    def area_pbi(self) -> None:
        pbi, fab, a = apis.pbi(), apis.fabric(), self.args
        ws = self.ids["workspace"] = _pick("workspace", a.workspace, self.check("pbi", "groups",
                                                                                  partial(self._list, pbi, "groups")))
        self.check("pbi", "fabric-workspaces", partial(self._list, fab, "workspaces"))
        later = ("reports", "datasets", "dashboards", "dataflows", "refreshes", "fabric-items")
        if not ws:
            for name in later:
                self.skip("pbi", name, f"workspace {a.workspace!r} not found" if a.workspace else "no workspace")
            return
        wid = ws["id"]
        lists = {kind: self.check("pbi", kind, partial(self._list, pbi, f"groups/{wid}/{kind}"))
                 for kind in ("reports", "datasets", "dashboards", "dataflows")}
        if lists["datasets"]:
            self.check("pbi", "refreshes", partial(self._list, pbi, f"groups/{wid}/datasets/"
                                                   f"{lists['datasets'][0]['id']}/refreshes", {"$top": 1}))
        else:
            self.skip("pbi", "refreshes", "no model in the workspace")
        self.check("pbi", "fabric-items", partial(self._list, fab, f"workspaces/{wid}/items"))
        model = self.ids["model"] = _pick("model", a.model, lists["datasets"]) if a.model else None
        report = self.ids["report"] = _pick("report", a.report, lists["reports"]) if a.report else None
        if model:
            self.check("pbi", "executeQueries", partial(self._dax, wid, model["id"]))
        else:
            self.skip("pbi", "executeQueries", f"model {a.model!r} not found" if a.model else "needs --model")
        for kind, item, wanted in (("report", report, a.report), ("model", model, a.model)):
            if item:
                path = f"workspaces/{wid}/{KINDS[kind][0]}/{item['id']}"
                pulled = self.check("pbi", f"getDefinition-{kind}", partial(self._get_definition, path,
                                                                             KINDS[kind][1]), spans=2 * SLOW)
                if pulled and kind == "model":  # a report's write-back pulls its stored format instead
                    self.definitions[kind] = pulled["definition"]
            else:
                self.skip("pbi", f"getDefinition-{kind}", f"{kind} {wanted!r} not found" if wanted
                          else f"needs --{kind}")

    def _dax(self, wid: str, mid: str) -> dict:
        api = apis.pbi()
        body = {"queries": [{"query": DAX}], "serializerSettings": {"includeNulls": True}}
        return _settle(self.via(api.resource, ORDER, lambda prov: http.call(
            "POST", api.url(f"groups/{wid}/datasets/{mid}/executeQueries"), audience=api.resource, json_body=body,
            timeout=self.timeout, token_provider=prov)), _dax_summary)

    def _pull(self, path: str, fmt: str | None, prov) -> dict:
        """getDefinition in `fmt`; None returns the format the item is stored in."""
        api = apis.fabric()
        resp = http.request("POST", api.url(f"{path}/getDefinition"), audience=api.resource,
                            params={"format": fmt} if fmt else None, body=b"", timeout=self.timeout,
                            token_provider=prov)
        body = http.wait_operation(resp, audience=api.resource, timeout=self.timeout * SLOW, token_provider=prov,
                                   fetch_result=True)
        return {"lro": resp.status == 202, "definition": (body or {}).get("definition") or {}}

    def _get_definition(self, path: str, fmt: str) -> dict:
        """Pull with every source (getDefinition is the Pro-workspace question), then compare two pulls."""
        attempts = self.via(apis.FABRIC_RESOURCE, ORDER, partial(self._pull, path, fmt), every=True)
        pulls = [a["value"] for a in attempts if a["status"] == "ok"]
        if len(pulls) == 1:
            source = next(a["source"] for a in attempts if a["status"] == "ok")
            again = self.via(apis.FABRIC_RESOURCE, (source,), partial(self._pull, path, fmt))
            attempts += [{**a, "second_pull": True} for a in again]
            pulls += [a["value"] for a in again if a["status"] == "ok"]
        out = _settle(attempts, _definition_summary)
        if len(pulls) >= 2:
            out["summary"]["same_on_second_pull"] = _digest(pulls[0]["definition"]) == _digest(pulls[1]["definition"])
        return out

    def area_sp(self) -> None:
        if self.site:
            self.check("sp", "m365-web-get", self._web_get)
            self.check("sp", "sharepoint-token", lambda: self._mint(apis.sharepoint(self.site).resource, "tier_b"))
        else:
            for name in ("m365-web-get", "sharepoint-token"):
                self.skip("sp", name, "no site: pass --site or set one on the profile")
        self.check("sp", "graph-sites-root", partial(self._get, apis.graph(), "sites/root", {"$select": "id"},
                                                     ORDER, True))

    def _web_get(self) -> dict:
        p = proc.run(["m365", "spo", "web", "get", "--url", self.site, "--output", "json"], timeout=self.timeout,
                     install_hint=auth.INSTALL["m365"])
        if p.code != 0:
            return _cli_failure(p)
        body = _json(p.stdout)
        return _ok(_fields(body), body)

    def area_pa(self) -> None:
        self.check("pa", "pac-canvas-list", self._canvas_list)
        if self.args.app:
            self.check("pa", "pac-canvas-download", self._canvas_download, spans=SLOW)
        else:
            self.skip("pa", "pac-canvas-download", "needs --app")
        # Get-PowerApp signs in, which a fresh hidden PowerShell would do with a prompt: it runs only in the
        # maker session window (PLATFORM-FACTS 20). Listing the cmdlets only loads the module, so it may run here.
        if maker_session.status(self.ctx.data_dir)["running"]:
            self.check("pa", "get-powerapp", self._maker_apps, spans=SLOW)
        else:
            self.skip("pa", "get-powerapp", "no maker session: run `ppx auth login --backend maker` first to include it")
        self.check("pa", "maker-cmdlets", self._cmdlets, spans=SLOW)

    def _canvas_list(self) -> dict:
        p = proc.run(["pac", "canvas", "list"], timeout=self.timeout, install_hint=pac.INSTALL)
        if p.code != 0:
            return _cli_failure(p)
        lines = p.stdout.splitlines()
        at = next((i for i, line in enumerate(lines) if _HEADER.match(line)), None)
        if at is None:
            return {"status": "error", "code": "no-header", "message": "pac canvas list printed no table header",
                    "raw": {"stdout": p.stdout}}
        rows = [line for line in lines[at + 1:] if set(line.strip()) - set("-= ")]
        return _ok({"header": re.split(r" {2,}", lines[at].strip()), "rows": len(rows)}, {"stdout": p.stdout})

    def _canvas_download(self) -> dict:
        with tempfile.TemporaryDirectory(prefix="ppx-probe-", ignore_cleanup_errors=True) as tmp:
            src = Path(tmp) / "src"
            p = proc.run(["pac", "canvas", "download", "--name", self.args.app, "--file-name",
                          str(Path(tmp) / "app.msapp"), "--extract-to-directory", str(src), "--overwrite"],
                         timeout=self.timeout * SLOW, install_hint=pac.INSTALL)
            if p.code != 0:
                return _cli_failure(p)
            files = sorted(src.rglob("*.pa.yaml"))
            needles = self._needles()
            hits = [(f.name, key, what) for f in files
                    for key, what in _yaml_hits(f.read_text(encoding="utf-8", errors="replace"), needles)]
        # Earlier key segments are authored names (data sources, screens, controls): only the trailing
        # schema keys go in the summary, the full paths stay in raw.
        shown = sorted({(name, "/".join(key.split("/")[-2:]), what) for name, key, what in hits})
        return _ok({"pa_yaml_files": [f.name for f in files],
                    "hits": [{"file": name, "key": key, "value": what} for name, key, what in shown]},
                   {"stdout": p.stdout, "files": [str(f.relative_to(src)) for f in files],
                    "hits": [{"file": name, "key": key, "value": what} for name, key, what in hits]})

    def _needles(self) -> list[tuple[str, str]]:
        found = []
        if self.site:
            parts = urllib.parse.urlsplit(self.site)
            found += [(self.site.rstrip("/").lower(), "site URL"), ((parts.hostname or "").lower(), "site host"),
                      (parts.path.rstrip("/").lower(), "site path")]
        if self.args.list:
            found.append((self.args.list.lower(), "list"))
        return [(needle, what) for needle, what in found if needle]

    def _maker_apps(self) -> dict:
        p = maker_session.call("Get-PowerApp | Select-Object -First 1", {}, self.timeout * SLOW, self.ctx.data_dir)
        if p is None:
            return {"status": "blocked", "code": "no-session", "message": "the maker session window closed",
                    "summary": {"sign_in": "no session"}}
        if p.code != 0:
            return {**_cli_failure(p), "summary": {"sign_in": "error"}}
        if not p.stdout.strip():
            return _ok({"apps": 0}, {"stdout": p.stdout})
        body = _json(p.stdout)
        return _ok({"fields": _key_paths(body, 3)}, body)

    def _cmdlets(self) -> dict:
        p = proc.run([*_PS, _PS_CMDLETS], timeout=self.timeout * SLOW, install_hint=auth.INSTALL["powershell.exe"])
        if p.code != 0:
            return _cli_failure(p)
        body = _json(p.stdout)
        commands = body.get("commands") or []
        return _ok({"count": len(commands), "commands": commands, "parameters": body.get("help")}, body)

    def area_flows(self) -> None:
        self.check("flows", "pac-solution-list", partial(self._pac_json, ["solution", "list"]))
        self.check("flows", "pac-list-cloud-flows", partial(self._pac_json, ["power-automate", "list-cloud-flows"]))
        if not self.env_url:
            for name in ("dataverse-whoami", "dataverse-workflows"):
                self.skip("flows", name, "no environment URL on the profile")
            return
        dv = apis.dataverse(self.env_url)
        self.check("flows", "dataverse-whoami", partial(self._get, dv, "WhoAmI", None, ("az",)))
        self.check("flows", "dataverse-workflows", partial(self._get, dv, "workflows",
                                                           {"$top": 1, "$select": "name,category"}, ("az",)))

    def _pac_json(self, args: list[str]) -> dict:
        p = proc.run(["pac", *args, "--json"], timeout=self.timeout, install_hint=pac.INSTALL)
        if p.code != 0:
            return _cli_failure(p)
        body = _json(p.stdout)
        return _ok(_fields(body), body)

    # -- results --------------------------------------------------------------------------------

    def routing(self) -> dict:
        """Per audience, the sources that worked: read from the token checks less the sources an API refused
        where another got through (a refusal of every source is the user's rights, not the source's), write
        from the write probes."""
        found: dict[str, dict] = {}
        for name, rec in self.results.get("tokens", {}).items():
            if "/" in name:
                key, source = name.split("/", 1)
                worked = rec["status"] == "ok" and source not in self.refused.get(key, ())
                found.setdefault(key, {"read": []})["read"] += [source] if worked else []
        for key, seen in self.evidence.items():
            worked = [s for s in ORDER if (s, "ok") in seen]
            # Only refusals count against a source; a 500 or a timeout says nothing about its rights.
            if worked or all(status == "blocked" for _, status in seen):
                found.setdefault(key, {})["write"] = worked
        return found

    def report(self, day: str, areas: list[str], routing: dict, clean) -> str:
        """The Markdown report; `clean` scrubs the cells that carry tenant data (check names never do)."""
        lines = ["# ppx probe report", "",
                 f"Date (UTC): {day}. ppx {__version__}. Areas: {', '.join(areas)}. "
                 f"Write probes: {'yes' if self.args.write else 'no'}.", "",
                 "Tenant identifiers are replaced with placeholders; response bodies are reduced to status, "
                 "error code and message shape.", ""]
        for area, checks in self.results.items():
            lines += [f"## {area}", "", "| check | status | code | ms | detail |", "|---|---|---|---|---|"]
            lines += ["| " + " | ".join(_md(x) for x in (name, rec["status"], clean(rec.get("code") or ""),
                                                         rec.get("elapsed_ms", ""), clean(_detail(rec)))) + " |"
                      for name, rec in checks.items()]
            lines.append("")
        lines += ["## routing.json from this run", "", "```json", json.dumps(routing, indent=2), "```", ""]
        return "\n".join(lines)


def _save_routing(data_dir: Path, found: dict) -> None:
    """Merge this run's evidence into routing.json; audiences and operations not probed keep their entries."""
    path = Path(data_dir) / auth.ROUTING_FILE
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    for key, ops in found.items():
        current[key] = {**(current.get(key) if isinstance(current.get(key), dict) else {}), **ops}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
    os.replace(tmp, path)
