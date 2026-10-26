"""pbi model create: a new semantic model from a TMDL folder, through Fabric (branch A only).

Facts (Learn, read 2026-10-26): Items - Create Semantic Model (POST /workspaces/{id}/semanticModels
{displayName, definition}; 201 or 202 LRO; Contributor; ItemDisplayNameAlreadyInUse); SemanticModel
definition (TMDL: `definition/` parts plus the required definition.pbism); Items - Create Report with
`creationPayload.semanticModelReference` {referenceType: ById, itemId, workspaceId} makes an empty report
bound to a model (Contributor plus read on the model). On branch F (fab deploy) or D (Desktop) Fabric
REST writes are refused, so creation goes through `pbi deploy` or Desktop instead.
"""

from __future__ import annotations

from pathlib import Path

from ppx import apis, http
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, UsageError
from ppx.pbi import common, definition, publish


def source_folder(path: str) -> tuple[Path, str]:
    """(the <name>.SemanticModel folder, the model's name) for --from: that folder or a project holding one."""
    folder = Path(path).expanduser()
    if folder.suffix.lower() == ".pbip":
        folder = folder.parent
    if not folder.is_dir():
        raise UsageError(f"{folder} is not a folder")
    if folder.suffix != ".SemanticModel":
        found = sorted(d for d in folder.iterdir() if d.is_dir() and d.suffix == ".SemanticModel")
        if len(found) != 1:
            raise UsageError(f"{folder} holds {len(found)} <name>.SemanticModel folders; pass the one to create")
        folder = found[0]
    if not (folder / "definition.pbism").is_file() or not (folder / "definition").is_dir():
        raise UsageError(f"{folder} has no definition.pbism and definition/ folder (TMDL); save the project with TMDL")
    name = folder.stem
    if (folder / ".platform").is_file():
        name = definition.platform(folder)["metadata"].get("displayName") or name
    return folder, name


def target(ctx, ws: dict, name: str, kind: str = "model") -> str:
    """The Fabric workspace id to create `name` in: branch A only, and no item of that name already there."""
    path = definition.branch(ctx)
    if path != "A":
        how = ("deploy the project folder with `ppx pbi deploy --pbip <folder>` (it uses fab deploy here)" if path == "F"
               else "open the project in Power BI Desktop and publish it (File > Publish)")
        raise NeedsUser(f"on this tenant Fabric REST cannot create items (routing.json: branch {path}); {how}")
    wid = definition.fabric_workspace_id(ctx, ws)
    typ = definition.KINDS[kind][3]
    items, _ = http.paged(apis.fabric().url(f"workspaces/{wid}/items"), audience=apis.FABRIC_RESOURCE,
                          params={"type": typ}, timeout=ctx.timeout)
    same = [i for i in items if i.get("type", typ) == typ and str(i.get("displayName") or "").lower() == name.lower()]
    if same:
        raise NeedsUser(f"a {kind} named {name} already exists in workspace {ws['name']}; pick another name, or "
                        "change that one with pbi pull / pbi push", data={"existing": same[0].get("id")},
                        next_actions=[NextAction("edit the existing one", command=f"ppx pbi pull {kind} "
                                                 f"{same[0].get('id')} --workspace {common.shell_arg(ws)}")])
    return wid


def create(ctx, ws: dict, wid: str, kind: str, name: str, parts: list[dict] | None = None,
           model_id: str | None = None) -> str:
    """Create a model from parts, or an empty report bound to `model_id`; returns the new item's id."""
    if kind == "model":
        try:
            return publish._create(ctx, wid, "model", name, parts)
        except PpxError as exc:
            raise definition.refused(exc, "write", "model", {"id": None, "name": name}, ws) from None
    body = {"displayName": name, "creationPayload": {"semanticModelReference": {
        "referenceType": "ById", "itemId": model_id, "workspaceId": wid}}}
    try:
        resp = http.request("POST", apis.fabric().url(f"workspaces/{wid}/reports"), audience=apis.FABRIC_RESOURCE,
                            op="write", json_body=body, timeout=ctx.timeout)
        made = http.wait_operation(resp, audience=apis.FABRIC_RESOURCE, timeout=ctx.timeout * definition.SLOW,
                                   fetch_result=True) or {}
    except PpxError as exc:
        raise definition.refused(exc, "write", "report", {"id": None, "name": name}, ws) from None
    if not made.get("id"):
        raise PpxError(f"creating report {name} answered without its id; check the workspace before trying again")
    return made["id"]


def run(ctx, args) -> dict:
    folder, name = source_folder(getattr(args, "from"))
    parts = definition.read_parts(folder)
    ws = common.workspace(ctx, args.workspace)
    wid = target(ctx, ws, name)
    ctx.changes.add(f"create semantic model {name} from {folder.name} ({len(parts)} parts)", f"workspace {ws['name']}",
                    undo=f"delete model {name} in the workspace")
    out = {"model": name, "workspace": ws["name"], "parts": len(parts)}
    if ctx.dry_run:
        return out
    model_id = create(ctx, ws, wid, "model", name, parts)
    model = {"id": model_id, "name": name}
    ctx.next("set data source credentials for scheduled refresh", url=common.settings_url(ws, model_id))
    ctx.next("or bind them for one check refresh now",
             command=common.command("datasources bind", model, ws, " --transient"))
    return {**out, "id": model_id}
