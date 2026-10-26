"""ppx auth: the doctor, sign-in and sign-out per backend, and the Tier B app registration spec."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path

from ppx import apis, auth, exports, proc
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, SetupNeeded, UsageError

AUDIENCES = ("pbi", "fabric", "graph", "powerplatform", "dataverse", "sharepoint")
GROUPS = {"pbi": "Power BI verbs", "fabric": "Fabric item-definition verbs (Power BI edits)",
          "graph": "Graph verbs (SharePoint files)", "powerplatform": "Power Platform API verbs",
          "dataverse": "Dataverse and Power Apps verbs", "sharepoint": "SharePoint verbs",
          "pac": "pac verbs (solutions, canvas source)"}
SOURCE_OF = {backend: source for source, backend in auth.BACKEND.items()}
CHECK_TIMEOUT = 10  # per prerequisite; all run at once, so the whole check stays near this
PROBE_TIMEOUT = 12  # per audience token probe; all run at once, so the doctor answers within about 13 s
INDEXES = (("pypi.org", "https://pypi.org/simple/"), ("registry.npmjs.org", "https://registry.npmjs.org/"))
# One Windows PowerShell spawn answers three lines: its version, the maker module, and Power BI
# Desktop (the installer's exe, else the Store package; Get-AppxPackage needs 5.1, not pwsh 7).
_MAKER_PS = ("$PSVersionTable.PSVersion.ToString(); "
             "[bool](Get-Module -ListAvailable -Name Microsoft.PowerApps.PowerShell); "
             "$d = Join-Path $env:ProgramFiles 'Microsoft Power BI Desktop/bin/PBIDesktop.exe'; "
             "if (Test-Path $d) { (Get-Item $d).VersionInfo.ProductVersion } "
             "else { $p = Get-AppxPackage -Name Microsoft.MicrosoftPowerBIDesktop | Select-Object -First 1; "
             "if ($p) { $p.Version.ToString() + ' (Store)' } else { '-' } }")
_DESKTOP_INSTALL = "winget install -e --id Microsoft.PowerBI"
_CHECKS = (
    ("uv", ["uv", "--version"], 'powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'),
    ("git", ["git", "--version"], "winget install -e --id Git.Git"),
    ("az", ["az", "version", "--output", "json"], auth.INSTALL["az"]),
    ("pac", ["pac"], auth.INSTALL["pac"]),
    ("m365", ["m365", "version", "--output", "text"], auth.INSTALL["m365"]),
    ("node", ["node", "--version"], "winget install -e --id OpenJS.NodeJS.LTS"),
    ("dotnet", ["dotnet", "--version"], "winget install -e --id Microsoft.DotNet.SDK.8"),
    ("claude", ["claude", "--version"], 'powershell -c "irm https://claude.ai/install.ps1 | iex"'),
    # --system skips the harness's own venv, so the answer matches what the hooks' `uv run --no-project` finds.
    ("python>=3.12 (uv)", ["uv", "python", "find", "--system", "--show-version", ">=3.12"], "uv python install 3.12"),
    ("powershell 5.1", ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _MAKER_PS],
     auth.INSTALL["powershell.exe"]),
)
_MAKER_INSTALL = ('powershell.exe -NoProfile -Command "Install-Module Microsoft.PowerApps.PowerShell '
                  '-Scope CurrentUser"')
_VERSION = re.compile(r"\d+(?:\.\d+)+[\w.+-]*")
# The login line is pasted into the user's own shell, so these values may not carry shell syntax.
_ID = re.compile(r"[A-Za-z0-9.-]+")
_LOGIN_VALUES = (("--environment", "environment", re.compile(r"(https://)?[\w.-]+/?"),
                  "an https environment URL, id or unique name"),
                 ("--tenant", "tenant", _ID, "a tenant id or domain"),
                 ("--app-id", "app_id", _ID, "an application (client) id"))

APP_NAME = "ppx (power-platform-forge)"
# Well-known resource app ids are public; the scope ids are looked up at run time, never hard-coded.
TIER_B_SCOPES = (
    ("00000003-0000-0000-c000-000000000000", "Microsoft Graph", ("User.Read", "Sites.ReadWrite.All", "Files.ReadWrite.All")),
    ("00000003-0000-0ff1-ce00-000000000000", "SharePoint", ("AllSites.Write",)),
    ("00000009-0000-0000-c000-000000000000", "Power BI Service",
     ("Dataset.ReadWrite.All", "Report.ReadWrite.All", "Workspace.Read.All", "Item.ReadWrite.All")),
    ("00000007-0000-0000-c000-000000000000", "Dynamics CRM", ("user_impersonation",)),
)
PORTAL_STEPS = (
    "Entra admin center (https://entra.microsoft.com) > App registrations > New registration: "
    f"name {APP_NAME!r}, accounts in this organizational directory only",
    "Authentication > Add a platform > Mobile and desktop applications: redirect URI http://localhost; "
    "set Allow public client flows to Yes",
    "API permissions > Add a permission > Delegated: "
    + "; ".join(f"{name} {', '.join(scopes)}" for _, name, scopes in TIER_B_SCOPES),
    "Overview: copy the Application (client) ID and the Directory (tenant) ID",
    "in your own terminal: ppx auth login --backend m365 --app-id <appId> --tenant <tenantId>, "
    "then accept the consent prompt",
)


def _reachable(url: str, timeout: float) -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True  # the server answered; only the method or path was refused
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _check(name: str, argv: list[str], install: str, timeout: float) -> list[dict]:
    try:
        p = proc.run(argv, timeout=timeout, install_hint=install)
    except SetupNeeded:
        return [{"name": name, "found": False, "install": install}]
    except PpxError:  # timed out: the tool exists but did not answer in time
        return [{"name": name, "found": True, "version": "no answer in time"}]
    text = p.stdout.strip()
    if name == "python>=3.12 (uv)":
        if p.code != 0 or not text:
            return [{"name": name, "found": False, "install": install}]
        return [{"name": name, "found": True, "version": text.splitlines()[0][:40]}]
    if name == "powershell 5.1":
        lines = text.splitlines()
        maker = len(lines) > 1 and lines[1].strip().lower() == "true"
        module = {"name": "Microsoft.PowerApps.PowerShell", "found": maker}
        desktop_version = lines[2].strip() if len(lines) > 2 else "-"
        desktop = ({"name": "Power BI Desktop", "found": True, "version": desktop_version[:40]}
                   if desktop_version not in ("", "-") else
                   {"name": "Power BI Desktop", "found": False, "install": _DESKTOP_INSTALL})
        return [{"name": name, "found": True, "version": lines[0].strip() if lines else "unknown"},
                module if maker else {**module, "install": _MAKER_INSTALL}, desktop]
    if name == "az":
        try:
            text = json.loads(text).get("azure-cli") or text
        except (ValueError, AttributeError):
            pass
    found = re.search(r"Version:\s*(\S+)", text) if name == "pac" else _VERSION.search(text)
    version = (found.group(1) if name == "pac" else found.group(0)) if found else "unknown"
    return [{"name": name, "found": True, "version": version[:40]}]


def _parallel(jobs: dict, timeout: float) -> dict:
    """Run each job on its own thread; return {name: result or exception} for the jobs done within `timeout`.

    Daemon threads, not an executor: interpreter exit joins executor workers, so one hung tool
    would hold the process open long after the envelope is printed.
    """
    done: dict = {}

    def run(name, fn):
        try:
            done[name] = fn()
        except Exception as exc:
            done[name] = exc

    threads = [threading.Thread(target=run, args=job, daemon=True) for job in jobs.items()]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    return dict(done)  # a snapshot: a job that finishes late must not change the answer


def prerequisites(timeout: float) -> list[dict]:
    """Which tools exist and their versions, plus package-index reachability, checked in parallel."""
    jobs = {name: partial(_check, name, argv, install, timeout) for name, argv, install in _CHECKS}
    jobs.update({host: partial(_reachable, url, min(5, timeout)) for host, url in INDEXES})
    done = _parallel(jobs, timeout + 0.5)
    out = [{"name": "python", "found": True, "version": sys.version.split()[0]}]
    for name, argv, _ in _CHECKS:
        found = done.get(name)
        out.extend(found if isinstance(found, list)
                   else [{"name": name, "found": proc.which(argv[0]) is not None, "version": "no answer in time"}])
    for host, _ in INDEXES:
        out.append({"name": host, "reachable": done.get(host) is True})
    return out


def _probe(resource: str, data_dir, timeout: float, exclude: tuple = ()) -> dict:
    start = time.monotonic()
    try:
        tok = auth.token(resource, "read", exclude=exclude, data_dir=data_dir, timeout=timeout)
        out = {"read": {"status": "ok", "source": tok.source}, "scp": auth.decode_claims(tok.value).get("scp")}
    except PpxError as exc:
        code = exc.data.get("code") or "error"
        source = exc.data.get("source")
        out = {"read": {"status": {"missing": "missing", "timeout": "timeout"}.get(code, "blocked"), "source": source},
               "_why": _why(code, source, timeout), "_next": exc.next_actions}
        if code.isdigit():
            out["aadsts"] = code
        if exc.next_actions:
            first = exc.next_actions[0]
            out["fix"] = first.command or first.url or first.action
        if code == "error":
            out["error"] = exc.message[:160]
    out["elapsed_ms"] = round((time.monotonic() - start) * 1000)
    return out


def _outcome(found, timeout: float) -> dict:
    """A probe's row from what _parallel returned: the row itself, the exception it raised, or nothing in time."""
    if found is None:
        return {"read": {"status": "timeout"}, "_why": _why("timeout", None, timeout),
                "elapsed_ms": round(timeout * 1000)}
    if isinstance(found, BaseException):
        return {"read": {"status": "blocked"}, "_why": _why("error", None, timeout),
                "error": auth.clean(str(found))[:160]}
    return found


def _report(ctx, group: str, row: dict) -> None:
    """Move a row's private reason and fixes into the envelope's warnings and next_actions, once each."""
    why, fixes = row.pop("_why", None), row.pop("_next", [])
    if why:
        ctx.warn(f"{group} unavailable: {why}")
    seen = {a.command or a.url or a.action for a in ctx.next_actions}
    for fix in fixes:
        if (fix.command or fix.url or fix.action) not in seen:
            seen.add(fix.command or fix.url or fix.action)
            ctx.next_actions.append(fix)


def _why(code: str, source: str | None, timeout: float) -> str:
    if code == "missing":
        return f"{auth.BACKEND.get(source, 'token tool')} not installed"
    if code == "timeout":
        return f"no token within {timeout:g} s"
    if code == "not-signed-in":
        return "no Tier B sign-in" if source == "tier_b" else f"not signed in to {source}"
    if code.isdigit():
        return f"AADSTS{code}" + (f" ({auth.MEANING[code]})" if code in auth.MEANING else "")
    return "token request failed"


def _write_status(recorded: dict | None, name: str):
    entry = (recorded or {}).get(name)
    if not entry or "write" not in entry:
        return "unknown (run ppx probe --write)"
    return {"status": "ok", "source": entry["write"][0]} if entry["write"] else {"status": "blocked"}


def doctor(ctx, args) -> dict:
    only = args.audience
    if only and only not in AUDIENCES:
        raise UsageError(f"--audience must be one of {', '.join(AUDIENCES)}, not {only!r}")
    names = [only] if only else [n for n in AUDIENCES
                                 if n not in ("dataverse", "sharepoint")
                                 or (ctx.profile and (ctx.profile.url if n == "dataverse" else ctx.profile.site))]
    probe_timeout, check_timeout = min(ctx.timeout, PROBE_TIMEOUT), min(ctx.timeout, CHECK_TIMEOUT)
    recorded = auth.routing(ctx.data_dir)
    working = [n for n, e in (recorded or {}).items() if e.get("read")]
    jobs, unusable = {"prerequisites": partial(prerequisites, check_timeout)}, {}
    for n in names:
        try:
            jobs[n] = partial(_probe, apis.by_name(n, ctx.profile).resource, ctx.data_dir, probe_timeout)
        except PpxError as exc:  # one unusable profile field must not hide every other result
            message = auth.clean(exc.message)[:160]
            unusable[n] = {"read": {"status": "blocked"}, "error": message, "_why": message, "_next": exc.next_actions}
    if not only and not working:
        # Before a probe "pac works" decides ok; az usually mints the Power Platform token, so ask pac alone.
        jobs["pac"] = partial(_probe, apis.POWERPLATFORM_RESOURCE, ctx.data_dir, probe_timeout, ("az", "tier_b"))
    done = _parallel(jobs, max(probe_timeout, check_timeout) + 1)

    audiences, results = [], {}
    for name in names:
        res = unusable.get(name) or _outcome(done.get(name), probe_timeout)
        results[name] = res
        _report(ctx, GROUPS[name], res)
        res["read"] = {k: v for k, v in res["read"].items() if v is not None}
        audiences.append({"audience": name, **{k: v for k, v in res.items() if v is not None},
                          "write": _write_status(recorded, name)})
    ok_names = {n for n, r in results.items() if r["read"]["status"] == "ok"}
    if working:
        ok = all(n in ok_names for n in working if n in results)
    elif only:
        ok = only in ok_names
    else:
        pac_row = _outcome(done.get("pac"), probe_timeout)
        _report(ctx, GROUPS["pac"], pac_row)
        ok = "pbi" in ok_names or pac_row["read"]["status"] == "ok"
    if ctx.profile is None:
        ctx.next("save a profile with your environment, workspace and site",
                 command="ppx env add work --url https://<org>.crm.dynamics.com --workspace <workspace> "
                         "--site https://<tenant>.sharepoint.com/sites/<site> --use")
    pre = done.get("prerequisites")
    if not isinstance(pre, list):
        pre = [{"name": "prerequisites", "found": False, "version": "no answer in time"}]
    data = {"audiences": audiences, "prerequisites": pre}
    if not ok:
        raise NeedsUser("the APIs ppx needs cannot get a token yet; the user should run the next_actions "
                        "in their own terminal", data=data)
    return data


def _login_line(args) -> str:
    parts = ["ppx", "auth", "login", "--backend", args.backend]
    for flag, dest, pattern, what in _LOGIN_VALUES:
        value = getattr(args, dest)
        if value and not pattern.fullmatch(value):
            raise UsageError(f"{flag} must be {what}, not {value[:60]!r}")
        if value:
            parts += [flag, value]
    return " ".join(parts)


def login(ctx, args) -> dict:
    backend = args.backend
    line = _login_line(args)
    if not ctx.interactive:
        raise NeedsUser(f"signing in to {backend} opens a browser; the user must run the command in their own "
                        "terminal, then retry", next_actions=[NextAction("run this in your own terminal",
                                                                         command=line)])
    saved = auth.settings(ctx.data_dir).get("m365", {}) if backend == "m365" else {}
    tenant = args.tenant or saved.get("tenant")
    app_id = args.app_id or saved.get("app_id")
    environment = args.environment or (ctx.profile.url if ctx.profile else None)
    argv = {
        "az": ["az", "login", "--allow-no-subscriptions"] + (["--tenant", tenant] if tenant else []),
        "pac": ["pac", "auth", "create"] + (["--environment", environment] if environment else []),
        "m365": ["m365", "login", "--authType", "browser"] + (["--appId", app_id] if app_id else [])
                + (["--tenant", tenant] if tenant else []),
        "maker": ["powershell.exe", "-NoProfile", "-Command", "Add-PowerAppsAccount"],
        "fab": ["fab", "auth", "login"],
    }[backend]
    ctx.changes.add(f"sign in to {backend}", "this computer", undo=f"ppx auth logout --backend {backend}")
    if ctx.dry_run:
        return {"backend": backend, "command": " ".join(argv)}
    code = proc.run_interactive(argv, install_hint=auth.INSTALL[argv[0]])
    if code != 0:
        fix = (NextAction("register your own app first", command="ppx auth app-spec")
               if backend == "m365" and not app_id else NextAction("try again", command=line))
        raise PpxError(f"{backend} sign-in exited with code {code}", next_actions=[fix])
    if backend == "m365" and ctx.data_dir and (args.app_id or args.tenant):
        try:
            auth.save_settings(ctx.data_dir, "m365", {"app_id": app_id, "tenant": tenant})
        except OSError as exc:
            ctx.warn(f"signed in, but the app id was not saved ({exc}); pass --app-id next time")
    if backend in SOURCE_OF:
        auth.clear(SOURCE_OF[backend], ctx.data_dir)
    return {"backend": backend, "signed_in": True}


def logout(ctx, args) -> dict:
    backend = args.backend
    argv = {
        "az": ["az", "logout"],
        "pac": ["pac", "auth", "clear"],
        "m365": ["m365", "logout"],
        "maker": ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "Remove-PowerAppsAccount"],
        "fab": ["fab", "auth", "logout"],
    }[backend]
    ctx.changes.add(f"sign out of {backend}", "this computer", undo=f"ppx auth login --backend {backend}")
    if ctx.dry_run:
        return {"backend": backend, "command": " ".join(argv)}
    if backend in SOURCE_OF:  # first, so a missing or hung tool cannot keep the memoised tokens alive
        auth.clear(SOURCE_OF[backend], ctx.data_dir)
    p = proc.run(argv, timeout=ctx.timeout, install_hint=auth.INSTALL[argv[0]])
    if p.code != 0:
        raise PpxError(f"{backend} sign-out exited with code {p.code}: {auth.clean(p.stderr or p.stdout)}")
    return {"backend": backend, "signed_out": True}


def _az(argv: list[str], timeout: float) -> str:
    p = proc.run(["az", *argv], timeout=timeout, install_hint=auth.INSTALL["az"])
    if p.code != 0:
        raise PpxError(auth.clean(p.stderr or p.stdout) or f"az exited with code {p.code}")
    return p.stdout


def _resource_access(app_id: str, scopes: tuple[str, ...], timeout: float) -> tuple[dict, list[str]]:
    listed = json.loads(_az(["ad", "sp", "show", "--id", app_id, "--query", "oauth2PermissionScopes",
                             "--output", "json"], timeout) or "[]")
    ids = {s.get("value"): s.get("id") for s in (listed if isinstance(listed, list) else []) if isinstance(s, dict)}
    return ({"resourceAppId": app_id, "resourceAccess": [{"id": ids[s], "type": "Scope"} for s in scopes if ids.get(s)]},
            [s for s in scopes if not ids.get(s)])


def _failed(found) -> str:
    return auth.clean(str(found))[:160] if isinstance(found, BaseException) else "no answer in time"


def app_spec(ctx, args) -> dict:
    timeout = min(ctx.timeout, PROBE_TIMEOUT)
    jobs = {"tenant": partial(_az, ["account", "show", "--query", "tenantId", "--output", "tsv"], timeout)}
    jobs.update({app: partial(_resource_access, app, scopes, timeout) for app, _, scopes in TIER_B_SCOPES})
    done = _parallel(jobs, timeout + 1)
    if not any(isinstance(done.get(app), tuple) for app, _, _ in TIER_B_SCOPES):
        ctx.warn(f"az could not look up the permission ids ({_failed(done.get(TIER_B_SCOPES[0][0]))}); "
                 "use the portal steps")
        ctx.next("then sign in with the new app in your own terminal",
                 command="ppx auth login --backend m365 --app-id <appId> --tenant <tenantId>")
        return {"method": "portal", "steps": list(PORTAL_STEPS)}
    tenant = done.get("tenant")
    tenant = tenant.strip() if isinstance(tenant, str) and tenant.strip() else "<tenantId>"
    accesses = []
    for app, name, scopes in TIER_B_SCOPES:
        found = done.get(app)
        if not isinstance(found, tuple):  # e.g. no Dataverse in this tenant: keep the other APIs
            ctx.warn(f"az could not look up {name} ({_failed(found)}); add {', '.join(scopes)} in the portal "
                     "if you need it")
            continue
        access, missing = found
        if access["resourceAccess"]:
            accesses.append(access)
        if missing:
            ctx.warn(f"{name} has no delegated scope named {', '.join(missing)}; add it in the portal if you need it")
    content = json.dumps(accesses, indent=2)
    try:
        if ctx.data_dir:
            path = exports.save(ctx.data_dir, "tier-b-required-resource-accesses", content)
        else:
            fd, name = tempfile.mkstemp(prefix="ppx-tier-b-", suffix=".json")
            with open(fd, "w", encoding="utf-8") as f:
                f.write(content)
            path = Path(name)
    except OSError as exc:
        raise PpxError(f"could not write the app spec file: {exc}") from exc
    command = (f'az ad app create --display-name "{APP_NAME}" --public-client-redirect-uris http://localhost '
               f'--is-fallback-public-client true --required-resource-accesses "@{path.as_posix()}"')
    login_line = f"ppx auth login --backend m365 --app-id <appId> --tenant {tenant}"
    ctx.next("create the app registration in your own terminal and note the appId it prints", command=command)
    ctx.next("sign in with it and accept the consent prompt", command=login_line)
    return {"method": "az", "file": str(path), "command": command,
            "steps": ["run the command; note appId in its output", f"run: {login_line}",
                      "accept the consent prompt in the browser"]}
