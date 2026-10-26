"""pbi dashboards ls|tiles|create|clone-tile: service dashboards (pinned tiles), inventory and copies only.

"Dashboard" in this plugin means a Power BI report; these verbs cover the classic pinned-tile dashboards,
for which the API offers listing, an empty create and tile cloning. Facts (Learn, read 2026-10-26):
Dashboards - Get Tiles In Group (GET .../dashboards/{id}/tiles: id, title, reportId, datasetId); Add
Dashboard In Group (POST .../dashboards {name}); Clone Tile In Group (POST .../dashboards/{id}/tiles/
{tileId}/Clone {targetDashboardId, targetWorkspaceId?, targetReportId?, targetModelId?,
positionConflictAction Tail|Abort}; across workspaces without a target report and model the tile breaks).
"""

from __future__ import annotations

from ppx import apis, http
from ppx.errors import NeedsUser
from ppx.pbi import clone, common
from ppx.resolve import pick


def _denied(exc: http.HttpError, what: str) -> NeedsUser:
    return NeedsUser(f"{what} needs edit rights in the workspace (Contributor or higher) ({exc.status})",
                     data={"status": exc.status, "error_code": exc.code})


def ls(ctx, args) -> dict:
    ws = common.workspace(ctx, args.workspace)
    snap, _ = common.inventory(ctx, ws)
    items = [{k: i[k] for k in ("id", "name", "isReadOnly") if k in i} for i in snap["items"]
             if i.get("type") == "dashboard"]
    return {"workspace": ws["name"], **common.listing(ctx, items, default_top=30)}


def _tiles(ctx, ws: dict, dashboard: dict) -> list[dict]:
    body = common.get(ctx, ws, f"dashboards/{dashboard['id']}/tiles")
    return [{k: t[k] for k in ("id", "title", "reportId", "datasetId") if t.get(k)} for t in body.get("value") or []]


def tiles(ctx, args) -> dict:
    ws, dashboard = common.resolve(ctx, "dashboard", args.dashboard, args.workspace)
    found = _tiles(ctx, ws, dashboard)
    return {"dashboard": dashboard.get("name"), "workspace": ws["name"], **common.listing(ctx, found, default_top=30)}


def create(ctx, args) -> dict:
    ws = common.workspace(ctx, args.workspace)
    ctx.changes.add(f"create an empty dashboard named {args.name}", f"workspace {ws['name']}",
                    undo=f"delete dashboard {args.name} in the workspace")
    if ctx.dry_run:
        return {"dashboard": args.name, "workspace": ws["name"]}
    try:
        made = http.call("POST", common.url(ws, "dashboards"), audience=apis.PBI_RESOURCE, op="write",
                         json_body={"name": args.name}, timeout=ctx.timeout) or {}
    except http.HttpError as exc:
        if exc.status in (401, 403):
            raise _denied(exc, "creating a dashboard") from None
        raise
    return {"dashboard": made.get("displayName") or args.name, "id": made.get("id"), "workspace": ws["name"]}


def clone_tile(ctx, args) -> dict:
    ws, source = common.resolve(ctx, "dashboard", args.dashboard, args.workspace)
    tile = pick("tile", args.tile, _tiles(ctx, ws, source), name_key="title", where=f" on {source.get('name')}")
    dest_ws = common.workspace(ctx, args.to_workspace) if args.to_workspace else ws
    dest = common.resolve(ctx, "dashboard", args.to, dest_ws["id"])[1]
    body: dict = {"targetDashboardId": dest["id"], "positionConflictAction": "Tail"}
    if dest_ws["id"] != ws["id"]:
        body["targetWorkspaceId"] = clone.MY_WORKSPACE_ID if dest_ws["id"] == common.ME else dest_ws["id"]
        if not (args.report or args.model):
            ctx.warn("cloned to another workspace without --report or --model, the tile loses its links and shows "
                     "as broken")
    if args.report:
        body["targetReportId"] = common.resolve(ctx, "report", args.report, dest_ws["id"])[1]["id"]
    if args.model:
        body["targetModelId"] = common.resolve(ctx, "model", args.model, dest_ws["id"])[1]["id"]
    ctx.changes.add(f"copy tile {tile.get('title') or tile['id']} from dashboard {source.get('name')}",
                    f"dashboard {dest.get('name')} in workspace {dest_ws['name']}",
                    undo="delete the copied tile on the dashboard in the service")
    out = {"tile": tile.get("title") or tile["id"], "to": dest.get("name"), "workspace": dest_ws["name"]}
    if ctx.dry_run:
        return out
    try:
        made = http.call("POST", common.url(ws, f"dashboards/{source['id']}/tiles/{tile['id']}/Clone"),
                         audience=apis.PBI_RESOURCE, op="write", json_body=body, timeout=ctx.timeout) or {}
    except http.HttpError as exc:
        if exc.status in (401, 403):
            raise _denied(exc, "copying a tile") from None
        raise
    return {**out, "id": made.get("id")}
