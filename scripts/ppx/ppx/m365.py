"""CLI for Microsoft 365 (`m365`) calls: JSON out, log lines stripped, sign-in and consent errors that name the fix.

m365 runs signed in with the user's own app (Tier B, `ppx auth login --backend m365`). Output contract
(https://pnp.github.io/cli-microsoft365/user-guide/cli-output-mode/): JSON by default, a collection is
always an array, a single value prints as-is. Errors (src/cli/cli.ts closeWithError, read 2026-10-26)
exit non-zero with `Error: <message>` on stderr, or `{"error": "<message>"}` when the user turned
printErrorsAsPlainText off, and on stdout when errorOutput is set to stdout.
"""

from __future__ import annotations

import json
import re

from ppx import auth, datadir, proc
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError
from ppx.redact import scrub_text

LOGIN = "ppx auth login --backend m365"
# The Ask-IT text of docs/ADMIN-REQUESTS.md#consent, returned so the user can file it as is.
CONSENT_REQUEST = ("Please grant admin consent for the delegated permissions on my app registration "
                   "{app}: Microsoft Graph User.Read, Sites.ReadWrite.All and "
                   "Files.ReadWrite.All, and SharePoint AllSites.Write. They act only as me, on what I can "
                   "already reach.")
_CONSENT = ("65001", "90094")  # consent missing; admin approval required
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_AADSTS = re.compile(r"AADSTS(\d+)")
_RESULT_START = re.compile(r"^[\[{]", re.M)


def run(args: list[str], *, timeout: float = 120):
    """Run `m365 <args> --output json` and return the parsed result (dict or list; None when it printed nothing)."""
    p = proc.run(["m365", *args, "--output", "json"], timeout=timeout, install_hint=auth.INSTALL["m365"])
    what = " ".join(args[:3])
    if p.code != 0:
        raise _failure(what, p)
    return _parse(what, p.stdout)


def consent_request() -> str:
    """CONSENT_REQUEST naming the client id `ppx auth login --app-id` saved; placeholders when none was saved."""
    try:
        app_id = auth.settings(datadir.resolve()).get("m365", {}).get("app_id")
    except datadir.DataDirMissing:
        app_id = None
    return CONSENT_REQUEST.format(app=f"with client id {app_id}" if app_id else "<app name> (client id <client id>)")


def _parse(what: str, stdout: str):
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for m in _RESULT_START.finditer(text):  # skip log or update-notice lines printed before the result
        try:
            return decoder.raw_decode(text, m.start())[0]
        except ValueError:
            continue
    raise PpxError(f"m365 {what} printed no JSON", data={"first_line": scrub_text(text.splitlines()[0])[:120]})


def _message(p: proc.Proc) -> str:
    text = _ANSI.sub("", p.stderr.strip() or p.stdout.strip())
    for line in text.splitlines():
        if line.startswith("{"):
            try:
                err = json.loads(line).get("error")
            except (ValueError, AttributeError):
                continue
            if err:
                return scrub_text(str(err))[:500]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("Run 'm365")]
    return scrub_text(" ".join(lines).removeprefix("Error: "))[:500] or f"exit code {p.code}"


def _failure(what: str, p: proc.Proc) -> PpxError:
    msg = _message(p)
    low = msg.lower()
    if "log in to microsoft 365 first" in low:
        return NeedsUser("CLI for Microsoft 365 is not signed in",
                         next_actions=[NextAction("sign in with your own app in your own terminal, then retry",
                                                  command=LOGIN)],
                         data={"code": "not-signed-in"})
    found = _AADSTS.search(msg)
    code = found.group(1) if found else None
    if code in _CONSENT:
        return NeedsUser(f"m365 {what}: AADSTS{code} consent is missing for your app",
                         next_actions=[NextAction("sign in again in your own terminal and accept the consent prompt",
                                                  command=LOGIN),
                                       NextAction("if the prompt says an administrator must approve, send IT the "
                                                  "admin_request text (docs/ADMIN-REQUESTS.md#consent)")],
                         data={"code": code, "admin_request": consent_request()})
    if code in auth.MEANING:
        return NeedsUser(f"m365 {what}: AADSTS{code} {auth.MEANING[code]}",
                         next_actions=[NextAction("see what blocks SharePoint and the fix",
                                                  command="ppx auth doctor --audience sharepoint")],
                         data={"code": code})
    if code:
        return PpxError(f"m365 {what}: {msg}", data={"code": code},
                        next_actions=[NextAction(f"look up AADSTS{code}", url=auth.LOOKUP_URL.format(code))])
    if "list view threshold" in low:
        return PpxError(f"m365 {what}: {msg}",
                        next_actions=[NextAction("filter on an indexed column (`ppx sp fields ls` marks them), "
                                                 "or read every row with `ppx sp export`")])
    return PpxError(f"m365 {what} failed: {msg}", data={"exit_code": p.code})
