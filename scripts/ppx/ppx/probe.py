"""ppx probe: what this user can reach on the tenant, per area, check and token source.

Run on the work laptop. Verbatim results (response bodies, tenant identifiers) stay in
<data>/probe/<date>/raw.json on this computer; <data>/routing.json records, per API, the token
sources that worked so ppx.auth tries them first; <data>/probe/<date>/report.md is the scrubbed
copy that may leave the laptop. Each check has its own timeout and one failing check never stops
the others. The probe never refreshes a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import urllib.parse
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from ppx import __version__, apis, auth, cmd_auth, http, pac, proc, resolve
from ppx.errors import PpxError, SetupNeeded, UsageError
from ppx.redact import scrub
from ppx.verbs_core import AREAS

# AREAS lists tokens before toolchain, so the pbi/az mint is the first az start of the run (the cold timing).
ORDER = ("az", "tier_b")  # read and write fallback order; pac mints Power Platform API tokens only
SLOW = 3  # definitions, canvas downloads and PowerShell cold starts get this many --timeout spans
CELL_MAX = 32  # matrix cells stay short so the envelope fits in 4 KB
DEVICE_FIELDS = ("AzureAdJoined", "WorkplaceJoined", "DeviceAuthStatus", "AzureAdPrt")
DAX = 'EVALUATE ROW("n", 1)'
KINDS = {"report": ("reports", "PBIR"), "model": ("semanticModels", "TMDL")}
_PS = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
# 'Stop' turns a failed sign-in into a non-zero exit instead of an error record followed by success.
_PS_APP = "$ErrorActionPreference = 'Stop'; Get-PowerApp | Select-Object -First 1 | ConvertTo-Json -Depth 4"
_PS_AGAIN = "$ErrorActionPreference = 'Stop'; $null = Get-PowerApp | Select-Object -First 1; 'signed-in'"
_PS_CMDLETS = ("$o = @{commands = @(Get-Command -Module Microsoft.PowerApps.PowerShell | ForEach-Object Name); "
               "help = @{}}; foreach ($c in 'Set-PowerAppRoleAssignment', 'Restore-PowerAppVersion') "
               "{ $o.help[$c] = @((Get-Help $c).parameters.parameter | ForEach-Object name) }; "
               "$o | ConvertTo-Json -Depth 4 -Compress")
_KV = re.compile(r"^\s*([A-Za-z][\w ]*?)\s*:\s?(.*?)\s*$", re.M)
_COLUMN = r"[A-Za-z][\w()./-]*(?: [\w()./-]+)*"
_HEADER = re.compile(rf"^\s*{_COLUMN}(?: {{2,}}{_COLUMN})+\s*$")
_YAML_KEY = re.compile(r"""^[\s-]*('[^']*'|"[^"]*"|[A-Za-z_][\w .()-]*?)\s*:(?:\s|$)""")
_BLOCK = re.compile(r"\s*[|>][-+0-9]*\s*(?:#.*)?$")  # a `key: |-` value: the deeper lines are text, not keys
_AADSTS = re.compile(r"AADSTS(\d+)")
_BANNER = re.compile(r"^[ \t]*Connected to\.*[ \t]*(\S.*?)[ \t\r]*$", re.M)  # pac names the environment it is signed in to
_SIGN_IN = re.compile(r"log ?in|sign ?in|auth create|no profiles", re.I)
_GUID = auth._GUID


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


def _plan_writes(ctx, args, backups: dict[str, str]) -> None:
    where = f"workspace {args.workspace}"
    if args.model:
        ctx.changes.add("create a scratch report bound to the model, then delete it", f"{where}, model {args.model}",
                        undo="the probe deletes the report it creates")
    if args.report:
        ctx.changes.add("write the report's own definition back unchanged (updateDefinition)",
                        f"{where}, report {args.report}",
                        backup=f"<data>/{backups['report']} holds the definition pulled just before")
    if args.model:
        ctx.changes.add("write the model's own definition back unchanged (updateDefinition, allowPurgeData false)",
                        f"{where}, model {args.model}",
                        backup=f"<data>/{backups['model']} holds the definition pulled just before")
        # Not reversible from here: only the previous owner can take it back, and its refresh
        # credentials stay with them, so a wrongly named shared model can stop refreshing.
        ctx.changes.add("take over the model (you become its owner)", f"{where}, model {args.model}",
                        reversible=False, undo="the previous owner can take it over again")


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


class _Probe:
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
        self.check("pa", "get-powerapp", partial(self._maker, _PS_APP), spans=SLOW)
        # A fresh process shows whether the maker sign-in of the first one survives.
        self.check("pa", "get-powerapp-again", partial(self._maker, _PS_AGAIN), spans=SLOW)
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

    def _maker(self, script: str) -> dict:
        try:
            p = proc.run([*_PS, script], timeout=self.timeout * SLOW, install_hint=auth.INSTALL["powershell.exe"])
        except proc.ProcTimeout:
            return {"status": "blocked", "code": "prompt", "summary": {"sign_in": "prompt"},
                    "message": f"no answer within {self.timeout * SLOW:g} s; a sign-in window was probably waiting"}
        if p.code != 0:
            return {**_cli_failure(p), "summary": {"sign_in": "error"}}
        if script == _PS_AGAIN:
            return _ok({"sign_in": "silent"}, {"stdout": p.stdout})
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

    # -- write probes ---------------------------------------------------------------------------

    def writes(self) -> None:
        targets = self.check("write", "targets", self._targets)
        if not targets:
            for name in ("create-report", "updateDefinition-report", "updateDefinition-model", "takeover"):
                self.skip("write", name, "scratch targets not resolved")
            return
        ws, model, report = targets["workspace"], targets.get("model"), targets.get("report")
        scratch = f"ppx-probe-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        jobs = []
        if model:
            # POST, its operation (SLOW spans) and the DELETE all fit in the check's budget.
            jobs.append(("create-report", partial(self._create_report, ws, model, scratch), SLOW + 2))
        jobs += [(f"updateDefinition-{kind}", partial(self._update_definition, kind, ws, item), 2 * SLOW)
                 for kind, item in (("report", report), ("model", model)) if item]
        if model:
            jobs.append(("takeover", partial(self._takeover, ws, model), 1))
        for name, fn, spans in jobs:
            got = self.check("write", name, fn, spans)
            if got:
                key, attempts = got
                self.evidence.setdefault(key, []).extend((a["source"], a["status"]) for a in attempts)
        left = self.results["write"].get("create-report", {})
        if left.get("code") in ("not-deleted", "maybe-created"):
            self.ctx.warn(left["message"])
        elif left.get("status") == "timeout":
            self.ctx.warn(f"creating scratch report {scratch} ran out of time and it may still appear in workspace "
                          f"{ws}; delete it there")

    def _targets(self) -> dict:
        a = self.args
        ws = self.ids.get("workspace") or self._lookup("workspace", a.workspace, "groups")
        out = {"workspace": ws["id"]}
        for kind, wanted, path in (("model", a.model, "datasets"), ("report", a.report, "reports")):
            if wanted:
                out[kind] = (self.ids.get(kind) or self._lookup(kind, wanted, f"groups/{ws['id']}/{path}"))["id"]
        return _ok({"resolved": sorted(out)}, out, value=out)

    def _lookup(self, kind: str, wanted: str, path: str) -> dict:
        if _GUID.fullmatch(wanted.strip()):
            return {"id": wanted.strip()}
        api = apis.pbi()
        attempts = self.via(api.resource, ORDER, lambda prov: http.paged(
            api.url(path), audience=api.resource, timeout=self.timeout, token_provider=prov)[0])
        return resolve.pick(kind, wanted, next((a["value"] for a in attempts if a["status"] == "ok"), []))

    def _write(self, key: str, fn, summarise=lambda value: {}) -> dict:
        attempts = self.via(apis.by_name(key).resource, ORDER, fn, write=True)
        out = _settle(attempts, summarise)
        out["value"] = (key, attempts)
        return out

    def _create_report(self, ws: str, model: str, name: str) -> dict:
        api = apis.fabric()
        body = {"displayName": name,
                "creationPayload": {"semanticModelReference": {"referenceType": "ById", "itemId": model,
                                                               "workspaceId": ws}}}

        def create(prov):
            resp = http.request("POST", api.url(f"workspaces/{ws}/reports"), audience=api.resource, op="write",
                                json_body=body, timeout=self.timeout, token_provider=prov)
            try:
                created = http.wait_operation(resp, audience=api.resource, timeout=self.timeout * SLOW,
                                              token_provider=prov, fetch_result=True) or {}
                if not created.get("id"):
                    raise PpxError("create report answered without the new report's id")
            except PpxError as exc:
                # Accepted but not confirmed: Fabric may still create it, and no other source may try again.
                raise PpxError(f"scratch report {name} may exist in workspace {ws} ({exc.message}); delete it there",
                               data={"code": "maybe-created"}) from exc
            try:  # inside, so a failed delete never makes the next source create a second report
                http.request("DELETE", api.url(f"workspaces/{ws}/reports/{created['id']}"), audience=api.resource,
                             op="write", timeout=self.timeout, token_provider=prov)
            except PpxError as exc:
                return {"created": created, "lro": resp.status == 202, "deleted": False, "error": exc.message}
            return {"created": created, "lro": resp.status == 202, "deleted": True}

        out = self._write("fabric", create, lambda v: {"lro": v["lro"], "deleted": v["deleted"]})
        done = next((a["value"] for a in out["value"][1] if a["status"] == "ok"), None)
        if done and not done["deleted"]:
            out.update(status="error", code="not-deleted",
                       message=f"the probe created scratch report {name} ({done['created']['id']}) but could not "
                               f"delete it ({done['error']}); delete it in workspace {ws}")
        return out

    def _update_definition(self, kind: str, ws: str, item: str) -> dict:
        api = apis.fabric()
        path = f"workspaces/{ws}/{KINDS[kind][0]}/{item}"
        # A report goes back in the format it is stored in (PBIR or PBIR-Legacy): writing the PBIR pull of a
        # PBIR-Legacy report would convert it. A model's TMDL is only a serialisation, so that pull is reused.
        fmt = KINDS[kind][1] if kind == "model" else None
        definition = self.definitions.get(kind)
        if not definition:
            pulled = self.via(api.resource, ORDER, partial(self._pull, path, fmt))
            definition = next((a["value"]["definition"] for a in pulled if a["status"] == "ok"), None)
        if not (definition or {}).get("parts"):
            return {"status": "error", "code": "no-definition", "message": f"could not pull the {kind} definition "
                                                                           "to write back", "raw": {}}
        body = {"definition": definition}
        if kind == "model":
            body["options"] = {"allowPurgeData": False}
        backup = self.backups[kind]
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(json.dumps({"definition": definition}, indent=1), encoding="utf-8")
        except OSError as exc:
            return {"status": "error", "code": "no-backup", "message": f"not written back: could not save the {kind} "
                                                                       f"definition first ({exc.strerror or exc})",
                    "raw": {}}

        def update(prov):
            resp = http.request("POST", api.url(f"{path}/updateDefinition"), audience=api.resource, op="write",
                                params={"updateMetadata": "false"}, json_body=body, timeout=self.timeout,
                                token_provider=prov)
            http.wait_operation(resp, audience=api.resource, timeout=self.timeout * SLOW, token_provider=prov)
            return {"lro": resp.status == 202}
        return self._write("fabric", update, lambda v: v)

    def _takeover(self, ws: str, model: str) -> dict:
        api = apis.pbi()

        def take(prov):
            http.request("POST", api.url(f"groups/{ws}/datasets/{model}/Default.TakeOver"), audience=api.resource,
                         op="write", body=b"", timeout=self.timeout, token_provider=prov)
            return {}
        return self._write("pbi", take)

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


class Scrubber:
    """Stable placeholders for tenant identifiers: <org>, <site-1>, <env-1>, <guid-1>, <user-1>, <name-1>."""

    _EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
    _HOST = re.compile(r"\b(?:[\w-]+\.)+(?:sharepoint\.com|dynamics\.com|onmicrosoft\.com)\b", re.I)
    _MARK = re.compile(r"(<(?:org|site-\d+|env-\d+|guid-\d+|user-\d+|name-\d+)>)")
    _NAME_KEYS = ("title", "configuredby", "createdby", "modifiedby", "owner")
    MIN_NAME = 3  # shorter values identify nothing and would mangle field names such as "id"

    def __init__(self):
        self.seen: dict[tuple[str, str], str] = {}
        self.names: set[str] = set()
        self.orgs: set[str] = set()

    def learn(self, obj, key: str = "") -> None:
        """Collect display names and tenant labels from raw results, to mask them wherever they reappear."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                self.learn(v, str(k))
        elif isinstance(obj, list):
            for v in obj:
                self.learn(v, key)
        elif isinstance(obj, str):
            low = key.lower()
            if low == "tenantname":
                self.orgs.add(obj.strip())
            elif low.endswith("name") or low in self._NAME_KEYS:
                self.names.add(obj.strip())
            self.names.update(_BANNER.findall(obj))
            for host in self._HOST.findall(obj):
                self.orgs.add(re.sub(r"-(my|admin)$", "", host.split(".")[0], flags=re.I))

    def learn_args(self, args, site: str | None) -> None:
        self.names.update(v.strip() for v in (args.workspace, args.model, args.report, args.app, args.list) if v)
        if site:
            self.learn(site)
            segments = urllib.parse.unquote(urllib.parse.urlsplit(site).path).split("/")
            self.names.update(s for s in segments if s and s.lower() not in ("sites", "teams"))

    def _tag(self, kind: str, value: str) -> str:
        key = (kind, value.lower())
        if key not in self.seen:
            self.seen[key] = "<org>" if kind == "org" else \
                f"<{kind}-{sum(k == kind for k, _ in self.seen) + 1}>"
        return self.seen[key]

    def _host(self, host: str) -> str:
        low = host.lower()
        if low.endswith(".onmicrosoft.com"):
            return "<org>"
        return self._tag("site" if low.endswith(".sharepoint.com") else "env", low)

    def text(self, s: str) -> str:
        s = self._EMAIL.sub(lambda m: self._tag("user", m.group()), s)
        s = self._HOST.sub(lambda m: self._host(m.group()), s)
        s = _GUID.sub(lambda m: self._tag("guid", m.group()), s)
        orgs = {o.lower() for o in self.orgs}
        words = sorted({w for w in self.names | self.orgs if len(w) >= self.MIN_NAME}, key=len, reverse=True)
        if not words:
            return s
        pattern = re.compile("|".join(rf"(?<!\w){re.escape(w)}(?!\w)" for w in words), re.I)
        tag = lambda m: self._tag("org" if m.group().lower() in orgs else "name", m.group())  # noqa: E731
        # Placeholders already written are left alone, so a name such as "site" cannot eat "<site-1>".
        return "".join(part if i % 2 else pattern.sub(tag, part) for i, part in enumerate(self._MARK.split(s)))


# -- helpers ------------------------------------------------------------------------------------


def _ok(summary: dict, raw=None, value=None) -> dict:
    return {"status": "ok", "summary": summary, "raw": raw, "value": value}


def _run(fn, timeout: float) -> dict:
    start = time.monotonic()
    done = cmd_auth._parallel({"check": fn}, timeout).get("check")
    if done is None:
        out = {"status": "timeout", "code": "timeout", "message": f"no answer within {timeout:g} s"}
    elif isinstance(done, BaseException):
        out = _from_error(done)
    else:
        out = done
    out["elapsed_ms"] = round((time.monotonic() - start) * 1000)
    return out


def _from_error(exc: BaseException) -> dict:
    if isinstance(exc, http.HttpError):
        refused = exc.status in (401, 403) or "read-only" in exc.message.lower()
        return {"status": "blocked" if refused else "error", "code": f"{exc.status} {exc.code or ''}".strip(),
                "message": exc.message}
    if isinstance(exc, proc.ProcTimeout):
        return {"status": "timeout", "code": "timeout", "message": exc.message}
    if isinstance(exc, SetupNeeded):
        return {"status": "missing", "code": "missing", "message": exc.message}
    if isinstance(exc, PpxError):
        return {"status": "error", "code": exc.data.get("code") or ("error" if type(exc) is PpxError
                                                                    else type(exc).__name__),
                "message": exc.message}
    return {"status": "error", "code": type(exc).__name__, "message": auth.clean(str(exc))}


def _failure(f) -> dict:
    """An auth._Failure (one source could not mint a token) as an outcome."""
    return {"status": {"missing": "missing", "timeout": "timeout", "error": "error"}.get(f.code, "blocked"),
            "code": f"AADSTS{f.code}" if f.code.isdigit() else f.code, "message": f.detail, "token": True}


def _cli_failure(p: proc.Proc) -> dict:
    # pac's "Connected as/to..." banner names the user and environment and says nothing about the failure.
    text = "\n".join(line for line in f"{p.stderr}\n{p.stdout}".splitlines()
                     if not line.lstrip().startswith("Connected "))
    found = _AADSTS.search(text)
    code = f"AADSTS{found.group(1)}" if found else ("not-signed-in" if _SIGN_IN.search(text) else f"exit {p.code}")
    return {"status": "error" if code.startswith("exit") else "blocked", "code": code, "message": auth.clean(text),
            "raw": {"exit": p.code, "stdout": p.stdout, "stderr": p.stderr}}


def _settle(attempts: list[dict], summarise) -> dict:
    """One check's outcome from its per-source attempts: ok when any source worked."""
    via = "; ".join(f"{a['source']} {_cell(a)}" for a in attempts)
    ok = [a for a in attempts if a["status"] == "ok"]
    if ok:
        return {"status": "ok", "summary": {**summarise(ok[0]["value"]), "via": via},
                "raw": {"attempts": attempts}, "value": ok[0]["value"]}
    worst = next((a for a in attempts if not a.get("token")), attempts[0])  # an API refusal says more
    return {"status": worst["status"], "code": worst.get("code"), "message": worst.get("message"),
            "summary": {"via": via}, "raw": {"attempts": attempts}}


def _cell(rec: dict) -> str:
    status, code = rec["status"], rec.get("code")
    return (status if status in ("ok", "skipped") or not code or code == status else f"{status}:{code}")[:CELL_MAX]


def _pick(kind: str, wanted: str | None, items: list | None) -> dict | None:
    """The item `wanted` names (an id or a name), the first item when nothing was asked for, else None."""
    if not wanted:
        return items[0] if items else None
    if _GUID.fullmatch(wanted.strip()):
        return {"id": wanted.strip()}
    try:
        return resolve.pick(kind, wanted, items or [])
    except PpxError:
        return None


def _fields(body) -> dict:
    if isinstance(body, dict) and isinstance(body.get("value"), list):
        body = body["value"]
    if isinstance(body, list):
        first = body[0] if body and isinstance(body[0], dict) else {}
        out = {"count": len(body), "fields": sorted(first)}
        types = sorted({str(i["type"]) for i in body if isinstance(i, dict) and "type" in i})
        return {**out, "types": types} if types else out
    return {"fields": sorted(body)} if isinstance(body, dict) else {"type": type(body).__name__}


def _tool(row: dict) -> str:
    if "reachable" in row:
        return f"{row['name']} {'reachable' if row['reachable'] else 'unreachable'}"
    return f"{row['name']} {row.get('version', '')}".strip() if row.get("found") else f"{row['name']} missing"


def _dax_summary(body) -> dict:
    result = ((body or {}).get("results") or [{}])[0]
    table = (result.get("tables") or [{}])[0]
    error = (body or {}).get("error") or result.get("error") or table.get("error")
    return {"rows": len(table.get("rows") or []), "error": error.get("code") if isinstance(error, dict) else None}


def _definition_summary(pulled: dict) -> dict:
    definition = pulled["definition"]
    paths = [str(p.get("path", "")) for p in definition.get("parts") or []]
    return {"format": definition.get("format") or _format_of(paths), "parts": len(paths), "lro": pulled["lro"],
            "roots": sorted({p.split("/")[0] + ("/" if "/" in p else "") for p in paths})}


def _format_of(paths: list[str]) -> str:
    if any(p.endswith(".tmdl") for p in paths):
        return "TMDL"
    if "model.bim" in paths:
        return "TMSL"
    if "report.json" in paths:
        return "PBIR-Legacy"
    return "PBIR" if any(p.startswith("definition/") for p in paths) else "unknown"


def _digest(definition: dict) -> str:
    h = hashlib.sha256()
    for part in sorted(definition.get("parts") or [], key=lambda p: str(p.get("path"))):
        h.update(f"{part.get('path')}\0{part.get('payload')}\0".encode())
    return h.hexdigest()


def _json(text: str):
    """The first JSON value in a CLI's output, skipping banner lines before it."""
    decoder = json.JSONDecoder()
    for m in re.finditer(r"[\[{]", text):
        try:
            return decoder.raw_decode(text, m.start())[0]
        except ValueError:
            continue
    raise PpxError("printed no JSON", data={"code": "no-json"})


def _key_paths(obj, depth: int, prefix: str = "") -> list[str]:
    if depth == 0 or not isinstance(obj, dict):
        return []
    out = []
    for k, v in obj.items():
        out += [prefix + k] + _key_paths(v, depth - 1, f"{prefix}{k}.")
    return out


def _yaml_hits(text: str, needles: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(key path, what) for each line of a .pa.yaml file that holds one of the needles."""
    # shortcut: key paths come from indentation, not a YAML parser (stdlib has none); enough to say where
    # a value sits, upgrade if real apps put hits under the wrong key.
    stack: list[tuple[int, str]] = []
    hits = set()
    block = None  # indent of the key whose block scalar (a multi-line formula) is being read
    for line in text.splitlines():
        if block is not None and (not line.strip() or len(line) - len(line.lstrip(" ")) > block):
            m = None
        else:
            block, m = None, _YAML_KEY.match(line)
        if m:
            indent = len(line) - len(line.lstrip(" -"))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            stack.append((indent, m.group(1).strip("'\"")))
            if _BLOCK.match(line[m.end():]):
                block = indent
        low = line.lower()
        hits.update(("/".join(k for _, k in stack), what) for needle, what in needles if needle in low)
    return sorted(hits)


def _shape(message: str) -> str:
    return re.sub(r"'[^']*'|\"[^\"]*\"", "'<q>'", auth.clean(str(message)))[:200]


def _detail(rec: dict) -> str:
    bits = []
    for k, v in (rec.get("summary") or {}).items():
        if v in (None, "", [], {}):
            continue
        if isinstance(v, (list, tuple)):
            v = ", ".join(json.dumps(x, separators=(",", ":")) if isinstance(x, dict) else str(x) for x in v)
        elif isinstance(v, dict):
            v = json.dumps(v, separators=(",", ":"))
        bits.append(f"{k}: {v}")
    if rec.get("message") and rec["status"] != "ok":
        bits.append(f"message: {_shape(rec['message'])}")
    return "; ".join(bits)


def _md(value) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


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
