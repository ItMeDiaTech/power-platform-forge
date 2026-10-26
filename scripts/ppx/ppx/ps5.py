"""Windows PowerShell 5.1 for the Power Apps maker module (Microsoft.PowerApps.PowerShell needs 5.x).

The module keeps its sign-in only inside the PowerShell process that ran Add-PowerAppsAccount (module
1.0.49 source: Get-JwtToken acquires interactively when the in-process session is empty; there is no
token cache file). ppx starts a fresh hidden process per call, where the module would open a sign-in
window and wait, which verbs must never do. So until a long-lived session helper hosts these calls
(SESSION_KEPT), every maker call exits 2 with the exact lines for the user's own PowerShell window.

Arguments travel as JSON in an environment variable and the script reads them as `$in`, so an app
name or a UPN is always data and can never become code.
"""

from __future__ import annotations

import json
import re
from http import HTTPStatus

from ppx import auth, cmd_auth, proc
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, SetupNeeded
from ppx.redact import scrub_text

EXE = "powershell.exe"
INPUT_VAR = "PPX_PS_INPUT"
LOGIN = "ppx auth login --backend maker"
MAKER_URL = "https://make.powerapps.com"
SESSION_KEPT = False  # True only once a long-lived PowerShell process holds the maker sign-in
# Windows PowerShell 5.1 writes redirected output in the OEM code page; ppx.proc decodes UTF-8.
_PREFIX = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
           "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'; "
           "$in = $env:PPX_PS_INPUT | ConvertFrom-Json; try { $out = & { ")
_SUFFIX = (" }; if ($null -ne $out) { ConvertTo-Json -InputObject $out -Depth 6 -Compress } } "
           "catch { ConvertTo-Json -InputObject @{error = [string]$_.Exception.Message; "
           "category = [string]$_.CategoryInfo.Category} -Compress; exit 1 }")
# No "unauthorized": a cmdlet refused for lack of permission must not read as a missing sign-in.
_NO_SESSION = re.compile(r"Add-PowerAppsAccount|\bsign.?in\b|\blog.?in\b|authenticat|AADSTS|access token", re.I)
_NO_MODULE = re.compile(r"is not recognized as the name of a cmdlet|CommandNotFound", re.I)


def sign_in_needed(message: str) -> NeedsUser:
    return NeedsUser(message, next_actions=[
        NextAction("sign in to the Power Apps maker module in your own terminal, then retry", command=LOGIN),
        NextAction("if that sign-in is blocked, do this step in the maker portal instead", url=MAKER_URL)])


def ps_quote(value) -> str:
    """A PowerShell single-quoted literal: nothing inside it is expanded."""
    return "'" + str(value).replace("'", "''") + "'"


def attended(what: str, lines: list[str]) -> NeedsUser:
    """The maker step as lines for the user's own Windows PowerShell window (see the module docstring)."""
    return NeedsUser(f"{what} needs the Power Apps maker module signed in within one PowerShell window, which ppx "
                     "cannot hold yet; run these lines in Windows PowerShell 5.1 (not pwsh), or use the maker portal",
                     next_actions=[NextAction("run in Windows PowerShell 5.1", command="; ".join(["Add-PowerAppsAccount",
                                                                                                  *lines])),
                                   NextAction("or do it in the maker portal", url=MAKER_URL)])


def cmdlet_line(cmdlet: str, params: dict) -> str:
    return " ".join([cmdlet, *(f"-{k} {ps_quote(v)}" for k, v in params.items() if v is not None)])


def run(script: str, timeout: float, data: dict | None = None, attended_lines: list[str] | None = None):
    """Run `script` in Windows PowerShell 5.1 and return its output parsed from JSON (None when it printed nothing).

    Without a kept session it runs nothing and raises `attended` with `attended_lines` (default: the script).
    """
    if not SESSION_KEPT:
        raise attended("this step", attended_lines or [script])
    if '"' in script:  # Windows re-parses the command line; single quotes need no escaping there
        raise ValueError("ps5 scripts use single quotes only")
    argv = [EXE, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _PREFIX + script + _SUFFIX]
    try:
        p = proc.run(argv, timeout=timeout, env={INPUT_VAR: json.dumps(data or {})}, install_hint=auth.INSTALL[EXE])
    except proc.ProcTimeout as exc:
        raise sign_in_needed(f"Windows PowerShell gave no answer within {timeout:g} s; the maker module was "
                             "probably waiting for a sign-in window") from exc
    body = _last_json(p.stdout)
    if p.code == 0:
        return body
    detail = scrub_text(str(body.get("error") if isinstance(body, dict) else (p.stderr or p.stdout)).strip())[:400]
    if _NO_MODULE.search(detail):
        raise SetupNeeded(f"the Power Apps maker module is not installed for Windows PowerShell 5.1: {detail}",
                          next_actions=[NextAction("install it", command=cmd_auth.MAKER_INSTALL)])
    if _NO_SESSION.search(detail):
        raise sign_in_needed(f"the Power Apps maker module is not signed in: {detail}")
    raise PpxError(f"Windows PowerShell failed: {detail or f'exit {p.code}'}",
                   data={"category": body.get("category") if isinstance(body, dict) else None})


def _last_json(stdout: str):
    """The JSON line the wrapper printed; anything a cmdlet wrote to the host before it is ignored."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line and line[0] in '[{"':
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def invoke(cmdlet: str) -> str:
    """A script that splats $in.params onto `cmdlet` after checking that each parameter name exists.

    The maker module has no cmdlet pages on Learn, so a name this module version lacks fails naming
    the cmdlet and Get-Help instead of reaching the service.
    """
    if not re.fullmatch(r"[A-Za-z]+-[A-Za-z]+", cmdlet):
        raise ValueError(f"not a cmdlet name: {cmdlet!r}")
    return ("$c = Get-Command " + cmdlet + "; $p = @{}; "
            "foreach ($k in $in.params.PSObject.Properties) { $n = $k.Name; "
            "if (-not $c.Parameters.ContainsKey($n)) { throw ('" + cmdlet + " has no parameter ' + $n + "
            "'; check Get-Help " + cmdlet + " -Full') }; $p[$n] = $k.Value }; " + cmdlet + " @p")


def maker(cmdlet: str, params: dict, timeout: float):
    """Run one maker-module cmdlet with checked parameters and return its output."""
    params = {k: v for k, v in params.items() if v is not None}
    return run(invoke(cmdlet), timeout, {"params": params}, attended_lines=[cmdlet_line(cmdlet, params)])


_STATUS = {s.name.replace("_", "").lower(): s.value for s in HTTPStatus}


def check_response(cmdlet: str, result) -> None:
    """Raise when a maker cmdlet's CreateHttpResponse object ({Code, Description, Internal}) reports a failure.

    The module's InvokeApi runs without -ThrowOnFailure for these cmdlets, so a refused call still
    exits 0 and only this object says so. Code is null or 2xx on success; an unreadable code fails closed.
    """
    if not isinstance(result, dict) or result.get("Code") is None:
        return
    code = result["Code"]
    status = int(code) if str(code).isdigit() else _STATUS.get(str(code).replace(" ", "").lower())
    if status is not None and 200 <= status < 300:
        return
    inner = result.get("Internal") if isinstance(result.get("Internal"), dict) else {}
    err = inner.get("error") or inner.get("Error")
    reason = (err.get("message") or err.get("Message")) if isinstance(err, dict) else err
    reason = reason or inner.get("message") or inner.get("Message")
    detail = scrub_text(" ".join(str(x) for x in (code, result.get("Description"), reason) if x))[:400]
    if status == 401:
        raise sign_in_needed(f"{cmdlet} was refused as not signed in: {detail}")
    raise PpxError(f"{cmdlet} failed: {detail}", data={"status": status or str(code)})
