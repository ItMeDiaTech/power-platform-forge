"""dlp check: which data loss prevention (DLP) policies mention the given connectors.

Classic DLP policies are readable only through `pac admin dlp-policy list` and `show --policy-name
<GUID>` (Learn, admin command group, ms.date 2026-02-25), which need a Power Platform administrator
role for tenant policies, and the Power Platform API has no classic DLP endpoint (permission
reference, ms.date 2025-09-26). pac documents neither command's output nor a --json switch, so ppx
does not parse it into groups: it takes the policy GUIDs from the list, saves each policy's text to
one file, and reports which policies mention each connector. Without the admin role the verb exits 2
with the request for IT, which is the usual answer for a maker.
"""

from __future__ import annotations

import re

from ppx import exports, pac
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, UsageError

PROVIDER = "/providers/microsoft.powerapps/apis/"
DEFAULT_POLICIES = 20  # pac makes one call per policy; --top raises it
SHOWN_IDS = 5  # policy ids per connector; every id is in saved_to
SHOWN_POLICIES = 10
_GUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_DENIED = re.compile(r"\b403\b|forbidden|not authori[sz]ed|access (?:is )?denied|permission|"
                     r"insufficient privileges|administrator role|admin role", re.I)
# 401 and an expired token mean pac's sign-in, not a missing role; IT cannot fix that
_SIGNED_OUT = re.compile(r"\b401\b|unauthori[sz]ed|expired|pac auth create|no profiles were found|not logged in", re.I)
_SIGN_IN = NextAction("sign pac in again in your own terminal, then retry", command="ppx auth login --backend pac")
ADMIN_REQUEST = ("I build Power Apps and flows in {env}. Could you tell me which group (Business, Non-business or "
                 "Blocked) the data loss prevention (DLP) policies that cover that environment put these connectors "
                 "in: {connectors}? I need to know whether they can be used together in one app or flow.")
_SEND_REQUEST = NextAction("send IT the request in data.admin_request (docs/ADMIN-REQUESTS.md#power-platform)")


def connector_ids(text: str) -> list[str]:
    """shared_x names from a comma-separated list of names, ids or /providers/... paths."""
    out = []
    for part in text.split(","):
        name = part.strip().lower()
        if name.startswith(PROVIDER):
            name = name[len(PROVIDER):]
        if name and name not in out:
            out.append(name)
    if not out:
        raise UsageError("name at least one connector, e.g. shared_sharepointonline")
    return out


def _request(ctx, names: list[str]) -> str:
    env = ctx.profile.url if ctx.profile and ctx.profile.url else "my environment"
    return ADMIN_REQUEST.format(env=env, connectors=", ".join(names))


def _refusal(ctx, names: list[str], text: str) -> NeedsUser | None:
    """The exit-2 error for a missing admin role or a lapsed pac sign-in, else None."""
    if _DENIED.search(text):
        return NeedsUser(f"reading DLP policies needs a Power Platform administrator role ({text})",
                         next_actions=[_SEND_REQUEST], data={"admin_request": _request(ctx, names)})
    if _SIGNED_OUT.search(text):
        return NeedsUser(f"pac is not signed in or its sign-in expired ({text})",
                         next_actions=[_SIGN_IN, NextAction("check every sign-in", command="ppx auth doctor")])
    return None


def _pac(ctx, names: list[str], args: list[str]) -> str:
    try:
        return pac.run(args, timeout=ctx.timeout).stdout
    except PpxError as exc:
        refusal = _refusal(ctx, names, exc.message)
        if refusal:
            raise refusal from None
        raise


def _mentions(ids: list[str], read: int) -> dict:
    if ids and len(ids) == read:  # policy texts usually name every classified connector
        return {"mentioned_in_count": len(ids), "in_all_read": True}
    return {"mentioned_in_count": len(ids), "mentioned_in": ids[:SHOWN_IDS]}


def check(ctx, args) -> dict:
    names = connector_ids(args.connectors)
    listing = _pac(ctx, names, ["admin", "dlp-policy", "list"])
    listed = list(dict.fromkeys(g.lower() for g in _GUID.findall(listing)))
    if not listed:
        refusal = _refusal(ctx, names, listing.strip()[:200])  # pac may print the refusal and still exit 0
        if refusal:
            raise refusal
        ctx.warn("pac admin dlp-policy list showed no policy ids: either the tenant has no DLP policy, pac's "
                 "output changed or this account cannot see them; this does not mean the connectors are uncovered")
        ctx.next("ask IT which DLP groups cover these connectors: send the request in data.admin_request")
        return {"connectors": [{"connector": n, **_mentions([], 0)} for n in names], "policies_listed": 0,
                "admin_request": _request(ctx, names)}
    limit = ctx.top if ctx.top is not None else DEFAULT_POLICIES
    texts, read, mentions, unreadable = [], [], {n: [] for n in names}, []
    for policy in listed[:limit]:
        try:
            text = _pac(ctx, names, ["admin", "dlp-policy", "show", "--policy-name", policy])
        except NeedsUser:
            raise
        except PpxError:  # the list's undocumented text may hold GUIDs that are not policy names
            unreadable.append(policy)
            continue
        texts.append(f"===== policy {policy}\n{text.strip()}\n")
        read.append(policy)
        low = text.lower()
        for n in names:
            if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", low):  # whole ids: office365 is not office365users
                mentions[n].append(policy)
    if len(listed) > limit:
        ctx.warn(f"read {limit} of {len(listed)} policies; pass --top {len(listed)} to read them all")
    if unreadable:
        ctx.warn(f"pac could not show {len(unreadable)} of the ids it listed: {', '.join(unreadable[:5])}")
    if not texts:
        raise PpxError("pac admin dlp-policy show failed for every id the list printed",
                       next_actions=[NextAction("check pac's sign-in", command="ppx auth doctor"),
                                     NextAction("or send IT the request in data.admin_request")],
                       data={"policies_listed": len(listed), "admin_request": _request(ctx, names)})
    saved = exports.save(ctx.need_data_dir(), "dlp-policies", "\n".join(texts), ".txt")
    ctx.warn("pac documents no output format for DLP policies, so ppx does not name each connector's group: read "
             "the group around each connector in saved_to, or send IT data.admin_request; a policy that does not "
             "mention a connector may still cover it through its default group")
    return {"connectors": [{"connector": n, **_mentions(mentions[n], len(read))} for n in names],
            "policies_listed": len(listed), "policies_read": len(read), "policies": read[:SHOWN_POLICIES],
            "saved_to": str(saved),
            "admin_request": _request(ctx, names)}
