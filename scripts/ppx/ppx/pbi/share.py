"""pbi share: give a person a role in a Power BI workspace.

Facts (Learn, read 2026-10-26): Groups - Add Group User (POST /groups/{groupId}/users; identifier,
principalType and groupUserAccessRight required, emailAddress optional; roles Viewer, Contributor, Member,
Admin; at most 1,000 users or groups per workspace; new permissions can take a while to apply); Groups - Get
Group Users (GET /groups/{groupId}/users, {value: [{emailAddress, identifier, principalType,
groupUserAccessRight}]}, $top/$skip).
"""

from __future__ import annotations

import re
from urllib.parse import quote

from ppx import apis, http
from ppx.errors import NeedsUser, UsageError
from ppx.pbi import common

ROLES = ("Viewer", "Contributor", "Member", "Admin")
_UPN = re.compile(r"[\w.+'-]+@[\w-]+(\.[\w-]+)+")  # no shell metacharacters: it goes into the undo command


def run(ctx, args) -> dict:
    if not _UPN.fullmatch(args.user):
        raise UsageError(f"{args.user!r} is not a sign-in name (user@domain)")
    ws = common.workspace(ctx, args.workspace)
    if ws["id"] == common.ME:
        raise UsageError("My workspace cannot be shared; pick a workspace")
    out = {"user": args.user, "role": args.role, "workspace": ws["name"]}
    try:
        # The undo removes the person, so it is only right for someone who had no role before.
        members, _ = http.paged(common.url(ws, "users"), audience=apis.PBI_RESOURCE, timeout=ctx.timeout)
        wanted = args.user.lower()
        current = next((m.get("groupUserAccessRight") for m in members if wanted in
                        (str(m.get("emailAddress") or "").lower(), str(m.get("identifier") or "").lower())), None)
        if current == args.role:
            return {**out, "unchanged": True}
        if current:
            raise NeedsUser(f"{args.user} already has the {current} role in {ws['name']}; ppx share only adds "
                            "people. Change the role in the workspace's Manage access pane",
                            data={"current_role": current})
        ctx.changes.add(f"give {args.user} the {args.role} role", f"workspace {ws['name']}",
                        undo=f"ppx raw pbi DELETE {common.url(ws, 'users/' + quote(args.user, safe='@'))}")
        if ctx.dry_run:
            return out
        http.request("POST", common.url(ws, "users"), audience=apis.PBI_RESOURCE, op="write",
                     json_body={"emailAddress": args.user, "identifier": args.user, "principalType": "User",
                                "groupUserAccessRight": args.role}, timeout=ctx.timeout)
    except http.HttpError as exc:
        if exc.status in (401, 403):
            raise NeedsUser(f"adding people to {ws['name']} needs the Admin or Member role there ({exc.status})",
                            data={"status": exc.status, "error_code": exc.code}) from None
        raise
    ctx.warn("new workspace permissions can take a few minutes to apply")
    return out
