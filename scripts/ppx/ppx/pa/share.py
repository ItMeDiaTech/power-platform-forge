"""pa share: share a canvas app with one user through the maker module (Set-PowerAppRoleAssignment)."""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from ppx import apis, cache, http, maker_session, ps5
from ppx.envelope import NextAction
from ppx.errors import PpxError, UsageError
from ppx.pa.inventory import find_app

USERS = Path("cache") / "users.json"
# Apostrophes and guests' #EXT# are valid in a UPN's local part; the Graph path segment is fully encoded.
_UPN = re.compile(r"^[^@\s/\\?\"%]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


def object_id(ctx, upn: str) -> str:
    """The user's Entra object id from Microsoft Graph, cached in <data>/cache/users.json."""
    if not _UPN.match(upn):
        raise UsageError(f"{upn!r} is not a user principal name such as someone@contoso.com")
    path = ctx.need_data_dir() / USERS
    users = cache.read_json(path, {})
    users = users if isinstance(users, dict) else {}
    if upn.lower() in users:
        return users[upn.lower()]
    try:
        body = http.call("GET", apis.graph().url(f"users/{urllib.parse.quote(upn, safe='')}"), audience=apis.GRAPH_RESOURCE,
                         params={"$select": "id"}, timeout=ctx.timeout) or {}
    except http.HttpError as exc:
        if exc.status == 404:
            raise UsageError(f"no user {upn} in this tenant") from exc
        if exc.status == 403:  # User.Read reads only the signed-in user (learn.microsoft.com/graph/api/user-get)
            raise PpxError(f"Microsoft Graph refused to read {upn} ({exc.message}): reading another user needs the "
                           "delegated permission User.ReadBasic.All on the sign-in ppx used for Graph") from exc
        raise
    if not body.get("id"):
        raise PpxError(f"Microsoft Graph returned no object id for {upn}")
    users[upn.lower()] = body["id"]
    cache.write_json(path, users)
    return body["id"]


def run(ctx, args) -> dict:
    app = find_app(ctx, "canvas", args.app, args.environment)
    env = app.get("environment") or args.environment
    if not env:
        raise UsageError(f"ppx does not know which environment {app.get('name')} is in (pac listed it); "
                         "pass --environment <environment id>")
    oid = object_id(ctx, args.user)
    # Set-PowerAppRoleAssignment's -Notify defaults to Notify, as sharing in the maker portal does.
    ctx.changes.add(f"share as {args.role} with {args.user} (object id {oid}); they are e-mailed about it",
                    f"canvas app {app.get('name')} ({app['id']}) in environment {env}",
                    undo="remove the user under Share in make.powerapps.com (Apps > the app > Share)")
    if app.get("lists"):
        ctx.warn(f"the app reads SharePoint lists {', '.join(app['lists'][:5])}: sharing the app does not give "
                 f"{args.user} access to them; check the lists' permissions in SharePoint")
    summary = {"app": {"id": app["id"], "name": app.get("name"), "environment": env},
               "user": args.user, "object_id": oid, "role": args.role}
    if ctx.dry_run:
        return summary
    try:
        result = ps5.maker("Set-PowerAppRoleAssignment", {"AppName": app["id"], "EnvironmentName": env,
                                                          "RoleName": args.role, "PrincipalType": "User",
                                                          "PrincipalObjectId": oid}, ctx.timeout * 3)
    except maker_session.Busy as exc:  # the window may still finish it, and the user would be e-mailed twice
        raise PpxError(f"{exc.message}. The share may still complete: check the app's sharing before you retry",
                       next_actions=[NextAction("check who the app is shared with", url=ps5.MAKER_URL)]) from None
    ps5.check_response("Set-PowerAppRoleAssignment", result)
    return {**summary, "shared": True}
