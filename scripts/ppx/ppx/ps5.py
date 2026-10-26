"""Windows PowerShell 5.1 for the Power Apps maker module (Microsoft.PowerApps.PowerShell needs 5.x).

The module keeps its sign-in only inside the PowerShell process that ran Add-PowerAppsAccount (module
1.0.49 source: Get-JwtToken acquires interactively when the in-process session is empty; there is no
token cache file). So maker scripts run in the session window that `ppx auth login --backend maker`
opens and keeps signed in (ppx.maker_session); without that window they exit 2 with the exact lines
for the user's own PowerShell window. Nothing here ever starts a hidden PowerShell.

Arguments travel as JSON data and the script reads them as `$in`, so an app name or a UPN is always
data and can never become code.
"""

from __future__ import annotations

import json
import re
from http import HTTPStatus

from ppx import cmd_auth, maker_session
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, SetupNeeded
from ppx.redact import scrub_text

LOGIN = "ppx auth login --backend maker"
MAKER_URL = "https://make.powerapps.com"
ps_quote = maker_session.ps_quote
# No "unauthorized": a cmdlet refused for lack of permission must not read as a missing sign-in.
_NO_SESSION = re.compile(r"Add-PowerAppsAccount|\bsign.?in\b|\blog.?in\b|authenticat|AADSTS|access token", re.I)
_NO_MODULE = re.compile(r"is not recognized as the name of a cmdlet|CommandNotFound", re.I)


def sign_in_needed(message: str) -> NeedsUser:
    return NeedsUser(message, next_actions=[
        NextAction("sign in again in the ppx maker session window, or restart it in your own terminal", command=LOGIN),
        NextAction("if that sign-in is blocked, do this step in the maker portal instead", url=MAKER_URL)])


def attended(what: str, lines: list[str]) -> NeedsUser:
    """No session window: how to start one, or the step as lines for the user's own PowerShell window."""
    return NeedsUser(f"{what} needs the Power Apps maker session: start it once in your own terminal (a PowerShell "
                     "window opens, you sign in, and it keeps the sign-in for the day), or run the lines yourself",
                     next_actions=[NextAction("start the maker session (once a day)", command=LOGIN),
                                   NextAction("or run these in Windows PowerShell 5.1 yourself",
                                              command="; ".join(["Add-PowerAppsAccount", *lines])),
                                   NextAction("or do it in the maker portal", url=MAKER_URL)])


def has_session() -> bool:
    """Whether a maker session window answers right now."""
    return maker_session.status()["running"]


def cmdlet_line(cmdlet: str, params: dict) -> str:
    return " ".join([cmdlet, *(f"-{k} {ps_quote(v)}" for k, v in params.items() if v is not None)])


def run(script: str, timeout: float, data: dict | None = None, attended_lines: list[str] | None = None):
    """Run `script` in the maker session window and return its output parsed from JSON (None when it gave none).

    Without a session window it runs nothing and raises `attended` with `attended_lines` (default: the script).
    """
    if '"' in script:  # the scripts are static text; values only ever travel as data
        raise ValueError("ps5 scripts use single quotes only")
    p = maker_session.call(script, data or {}, timeout)
    if p is None:
        raise attended("this step", attended_lines or [script])
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
