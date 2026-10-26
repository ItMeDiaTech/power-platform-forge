"""pbi clone and pbi rebind: copy a report, or point a report at another semantic model.

Facts (Learn, read 2026-10-26): Reports - Clone Report In Group (POST .../reports/{id}/Clone {name,
targetModelId?, targetWorkspaceId?}; an all-zero workspace id means My workspace; needs Write on the report
and Build on the target model); Reports - Rebind Report In Group (POST .../reports/{id}/Rebind {datasetId};
needs Write on the report and Build on the model; paginated reports are not supported; a model in another
workspace becomes a shared model in the report's workspace).
"""

from __future__ import annotations

from ppx import apis, http
from ppx.errors import NeedsUser, UsageError
from ppx.pbi import common

MY_WORKSPACE_ID = "00000000-0000-0000-0000-000000000000"


def _denied(exc: http.HttpError, what: str) -> NeedsUser:
    return NeedsUser(f"{what} needs Write permission on the report and Build permission on the model ({exc.status})",
                     data={"status": exc.status, "error_code": exc.code})


def clone(ctx, args) -> dict:
    ws, report = common.resolve(ctx, "report", args.report, args.workspace)
    dest = common.workspace(ctx, args.to_workspace) if args.to_workspace else ws
    model = common.resolve(ctx, "model", args.model, dest["id"])[1] if args.model else None
    body: dict = {"name": args.name}
    if dest["id"] != ws["id"]:
        body["targetWorkspaceId"] = MY_WORKSPACE_ID if dest["id"] == common.ME else dest["id"]
    if model:
        body["targetModelId"] = model["id"]
    if any(i.get("type") == "report" and str(i.get("name") or "").lower() == args.name.lower()
           for i in common.cached_items(ctx, dest)):
        ctx.warn(f"workspace {dest['name']} already has a report named {args.name}; the copy will share the name")
    ctx.changes.add(f"copy report {report.get('name')} as {args.name}"
                    + (f", bound to model {model.get('name')}" if model else ""), f"workspace {dest['name']}",
                    undo=f"delete report {args.name} in workspace {dest['name']}")
    out = {"report": report.get("name"), "copy": args.name, "workspace": dest["name"]}
    if ctx.dry_run:
        return out
    try:
        made = http.call("POST", common.url(ws, f"reports/{report['id']}/Clone"), audience=apis.PBI_RESOURCE,
                         op="write", json_body=body, timeout=ctx.timeout) or {}
    except http.HttpError as exc:
        if exc.status in (401, 403):
            raise _denied(exc, "copying a report") from None
        raise
    return {**out, "id": made.get("id"), "modelId": made.get("datasetId")}


def _model_home(ctx, ws: dict, model_id: str) -> str | None:
    """Id of the workspace whose cached inventory holds the model (Learn's Report object has no model workspace)."""
    def holds(space: dict) -> bool:
        return any(i.get("id") == model_id and i.get("type") == "model" for i in common.cached_items(ctx, space))

    if holds(ws):
        return ws["id"]
    return next((s["id"] for s in common.workspaces(ctx)[0] if holds(s)), None)


def rebind(ctx, args) -> dict:
    ws, report = common.resolve(ctx, "report", args.report, args.workspace)
    if report.get("reportType") == "PaginatedReport":
        raise UsageError("paginated reports cannot be rebound through the API")
    mws, model = common.resolve(ctx, "model", args.model, args.model_workspace or ws["id"])
    out = {"report": report.get("name"), "workspace": ws["name"], "model": model.get("name")}
    old = report.get("modelId")
    if old == model["id"]:
        return {**out, "unchanged": True}
    shared = " (a link to it is created in the report's workspace)" if mws["id"] != ws["id"] else ""
    undo = None
    if old:
        undo = f"ppx pbi rebind {common.shell_arg(report)} --workspace {common.shell_arg(ws)} --model {old}"
        home = _model_home(ctx, ws, old)
        if home is None:
            ctx.warn(f"the current model {old} is in no cached inventory; if it lives outside {ws['name']}, add "
                     "--model-workspace <its workspace> to the undo")
        elif home != ws["id"]:
            undo += f" --model-workspace {home}"
    ctx.changes.add(f"bind report {report.get('name')} to model {model.get('name')}{shared}",
                    f"report in workspace {ws['name']}", undo=undo)
    if ctx.dry_run:
        return out
    try:
        http.request("POST", common.url(ws, f"reports/{report['id']}/Rebind"), audience=apis.PBI_RESOURCE,
                     op="write", json_body={"datasetId": model["id"]}, timeout=ctx.timeout)
    except http.HttpError as exc:
        if exc.status in (401, 403):
            raise _denied(exc, "rebinding a report") from None
        raise
    ctx.warn("the cached inventory still shows the old model until `ppx pbi ls --refresh`")
    return {**out, "previous_model": old}
