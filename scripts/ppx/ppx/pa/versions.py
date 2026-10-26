"""pa versions|restore: a canvas app's saved versions through the maker module.

Version objects follow the documented app-version resource (name, properties.appVersion,
properties.lifeCycleId; learn.microsoft.com/connectors/powerappsforappmakers Get-AppVersions),
read from the cmdlet's Internal object when it has one. A restore saves the chosen version as a
new latest version, so the version it replaces is recorded first and is the undo.
"""

from __future__ import annotations

from ppx import maker_session, ps5
from ppx.envelope import NextAction
from ppx.errors import PpxError, UsageError
from ppx.pa.inventory import find_app


def version_item(o: dict) -> dict:
    inner = o["Internal"] if isinstance(o.get("Internal"), dict) else o
    props = inner.get("properties") if isinstance(inner.get("properties"), dict) else {}
    return {"id": inner.get("name"), "created": props.get("appVersion"), "lifecycle": props.get("lifeCycleId")}


def versions(ctx, app: dict) -> list[dict]:
    """Newest first."""
    # The version routes are keyed by app alone: neither version cmdlet takes an EnvironmentName.
    body = ps5.maker("Get-PowerAppVersion", {"AppName": app["id"]}, ctx.timeout * 3)
    raw = [body] if isinstance(body, dict) else body or []
    if not raw:  # every saved app has a version; the module swallows HTTP errors and returns nothing
        raise PpxError(f"Get-PowerAppVersion returned no versions for {app.get('name')}; the maker module does not "
                       "report HTTP errors, so the call probably failed: retry, or see the versions in the maker "
                       "portal (Apps > the app > Details > Versions)",
                       next_actions=[NextAction("open the maker portal", url=ps5.MAKER_URL)])
    items = [version_item(o) for o in raw if isinstance(o, dict)]
    if raw and not any(v["id"] for v in items):
        keys = sorted(raw[0]) if isinstance(raw[0], dict) else []
        raise PpxError(f"Get-PowerAppVersion returned versions without a name ppx can read (fields: {keys[:10]})",
                       data={"fields": keys})
    return sorted((v for v in items if v["id"]), key=lambda v: str(v.get("created") or ""), reverse=True)


def _app(app: dict) -> dict:
    return {"id": app["id"], "name": app.get("name"), "environment": app.get("environment")}


def ls(ctx, args) -> dict:
    app = find_app(ctx, "canvas", args.app, args.environment)
    items = versions(ctx, app)
    top = ctx.top or 20
    return {"app": _app(app), "count": len(items), "items": items[:top], "more": len(items) > top}


def restore(ctx, args) -> dict:
    app = find_app(ctx, "canvas", args.app, args.environment)
    if not ps5.has_session():  # the whole restore as lines: note the current version, restore, then confirm
        listing = ps5.cmdlet_line("Get-PowerAppVersion", {"AppName": app["id"]})
        raise ps5.attended("restoring a version", [
            listing, ps5.cmdlet_line("Restore-PowerAppVersion", {"AppName": app["id"], "AppVersionName": args.version}),
            listing])
    items = versions(ctx, app)  # always first: the version being replaced is recorded before anything changes
    if args.version not in {v["id"] for v in items}:
        raise UsageError(f"{app.get('name')} has no version {args.version!r}; `ppx pa versions` lists them",
                         data={"latest": [v["id"] for v in items[:5]]})
    previous = items[0]["id"]
    undo = f"ppx pa restore {app['id']} --version {previous} --yes"
    ctx.changes.add(f"restore version {args.version} as the latest version (replacing {previous})",
                    f"canvas app {app.get('name')} ({app['id']}) in environment {app.get('environment') or 'default'}",
                    reversible=False, undo=undo)
    ctx.changes.require_yes(ctx.yes)
    if ctx.dry_run:
        return {"app": _app(app), "version": args.version, "previous": previous}
    try:
        ps5.maker("Restore-PowerAppVersion", {"AppName": app["id"], "AppVersionName": args.version}, ctx.timeout * 3)
    except maker_session.Busy as exc:  # the window may still finish it: retrying could restore twice
        raise PpxError(f"{exc.message}. The restore may still complete: list the versions before you retry",
                       data={"previous": previous}, next_actions=[
                           NextAction("check whether it happened", command=f"ppx pa versions {app['id']}"),
                           NextAction("if it did and you want it back, restore the version it replaced",
                                      command=undo)]) from None
    # The cmdlet discards the service's answer, so success is the new latest version the restore saves.
    known = {v["id"] for v in items}
    new = [v for v in versions(ctx, app) if v["id"] not in known]
    if not new:
        raise PpxError("the maker module reported no error but no new version appeared; the restore was not "
                       "confirmed", data={"previous": previous},
                       next_actions=[NextAction("check the versions", command=f"ppx pa versions {app['id']}")])
    ctx.next("undo", command=undo)
    ctx.next("the restored version is saved, not published: publish it in Studio to make it live",
             url=ps5.MAKER_URL)
    return {"app": _app(app), "restored": args.version, "new_version": new[0]["id"], "previous": previous}
