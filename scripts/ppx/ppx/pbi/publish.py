"""pbi publish (a PBIX through Imports) and pbi deploy (a PBIP folder through Fabric items, or fab deploy).

Facts (Learn, read 2026-10-26): Imports - Post Import In Group (multipart/form-data; datasetDisplayName is
the file name with its extension; nameConflict Ignore|Abort|Overwrite|CreateOrOverwrite, service default
Ignore; Overwrite fails without exactly one same-named model; overrideReportLabel and overrideModelLabel
default true; 202 {id}; files of 1 to 10 GB need a temporary upload location on Premium capacity);
Imports - Get Import In Group (importState Publishing|Succeeded|Failed, error.code); Items - Create
Semantic Model and Create Report (POST /workspaces/{id}/semanticModels|reports {displayName, definition};
201, or 202 LRO; Contributor role). Report definition: deploys through the REST API need definition.pbir
`byConnection` ("semanticmodelid=<id>"), while Desktop saves `byPath`.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from pathlib import Path, PurePosixPath

from ppx import apis, cache, http
from ppx.envelope import NextAction
from ppx.errors import NeedsConfirmation, NeedsUser, PpxError, UsageError
from ppx.pbi import backup, common, definition

MAX_UPLOAD = 1 << 30  # 1 GB: larger imports need a temporary upload location, Premium only
POLL_SECONDS = 5.0
DEPLOYED = "deployed"  # cache folder of the part hashes each deploy sent, the baseline for the next one


def _live(ctx, ws: dict, path: str) -> list[dict]:
    found, _ = http.paged(common.url(ws, path), audience=apis.PBI_RESOURCE, timeout=ctx.timeout)
    return found


# -- pbi publish --------------------------------------------------------------------------------

def _matches(ctx, ws: dict, name: str) -> tuple[list[dict], list[dict]]:
    """Same-named models, and same-named reports bound to other models, from live listings (not the cache: a
    colleague may have published since)."""
    models = [m for m in _live(ctx, ws, "datasets") if str(m.get("name") or "").lower() == name.lower()]
    reports = _live(ctx, ws, "reports")
    named = [r for r in reports if str(r.get("name") or "").lower() == name.lower()]
    out = []
    for m in models:
        bound = [r for r in reports if r.get("datasetId") == m["id"]]
        same = next((r for r in named if r.get("datasetId") == m["id"]), None)
        out.append({"model": m["id"], "configuredBy": m.get("configuredBy"),
                    **({"report": {"id": same["id"], "isOwnedByMe": same.get("isOwnedByMe")}} if same else {}),
                    "reports_using_model": len(bound), "_backup_from": same or (bound[0] if bound else None)})
    paired = {m["report"]["id"] for m in out if "report" in m}
    others = [{"report": {"id": r["id"], "isOwnedByMe": r.get("isOwnedByMe")}, "model": r.get("datasetId"), "_report": r}
              for r in named if r["id"] not in paired]
    return out, others


def _wait_import(ctx, ws: dict, import_id: str) -> dict:
    deadline = time.monotonic() + ctx.timeout * definition.SLOW
    while True:
        body = common.get(ctx, ws, f"imports/{import_id}")
        state = body.get("importState")
        if state == "Succeeded" or (state != "Failed" and time.monotonic() >= deadline):
            return body
        if state == "Failed":
            err = body.get("error") or {}
            detail = "; ".join(d.get("message") or d.get("code") or "" for d in err.get("details") or []
                               if isinstance(d, dict))
            raise PpxError(f"the import failed: {err.get('code') or 'no error code'}" + (f" ({detail})" if detail else ""),
                           data={"import": import_id})
        http.sleep(POLL_SECONDS)


def publish(ctx, args) -> dict:
    pbix = Path(args.pbix)
    if pbix.suffix.lower() != ".pbix" or not pbix.is_file():
        raise UsageError(f"{pbix} is not a .pbix file")
    size = pbix.stat().st_size
    if size > MAX_UPLOAD:
        raise NeedsUser(f"{pbix.name} is over 1 GB; the API imports such files only through a temporary upload "
                        "location on Premium capacity, so publish it from Power BI Desktop")
    ws = common.workspace(ctx, args.workspace)
    name, conflict = pbix.stem, args.name_conflict
    matches, others = _matches(ctx, ws, name)
    every = [{k: v for k, v in m.items() if not k.startswith("_")} for m in matches + others]
    shown = {"matches": every[:definition.LISTED],
             **({"more_matches": len(every) - definition.LISTED} if len(every) > definition.LISTED else {})}
    if len(matches) > 1:
        raise NeedsUser(f"{len(matches)} models are named {name} in workspace {ws['name']}, so the service cannot "
                        "replace one; rename the file or remove the duplicate first", data=dict(shown))
    if matches and conflict == "Abort":
        raise NeedsUser(f"a model named {name} already exists in workspace {ws['name']} and Abort would cancel the "
                        "import; to replace it (ppx saves a PBIX backup first) pass --name-conflict "
                        "CreateOrOverwrite --yes", data=dict(shown),
                        next_actions=[NextAction("replace it", command=f"ppx pbi publish --pbix \"{pbix}\" --workspace "
                                                                       f"{common.shell_arg(ws)} --name-conflict "
                                                                       "CreateOrOverwrite --yes")])
    if not matches and conflict == "Overwrite":
        raise UsageError(f"there is no model named {name} in workspace {ws['name']} to overwrite; use "
                         "--name-conflict CreateOrOverwrite or Abort")
    data_dir = ctx.need_data_dir()
    target = matches[0] if matches else None
    if target:
        stamp, folder = backup.reserve(data_dir, target["model"])
        ids = f"model {target['model']}" + (f", report {target['report']['id']}" if "report" in target else "")
        ctx.changes.add(f"replace model and report {name} with {pbix.name}",
                        f"workspace {ws['name']}: {ids} (owner {target.get('configuredBy') or 'unknown'})",
                        reversible=False, backup=str(folder / pbix.name),
                        undo=f"ppx pbi publish --pbix \"{folder / pbix.name}\" --workspace {common.shell_arg(ws)} "
                             "--name-conflict Overwrite --yes")
    else:
        # CreateOrOverwrite needs --yes even with no match now: a colleague may publish that name before the import runs.
        ctx.changes.add(f"publish {pbix.name} as a new model and report named {name}", f"workspace {ws['name']}",
                        reversible=conflict == "Abort", undo=f"delete report and model {name} in the workspace")
    listed = ", ".join(o["report"]["id"] for o in others[:5]) + (f" and {len(others) - 5} more" if len(others) > 5 else "")
    if others and conflict == "Abort":
        # nameConflict looks at models only, so Abort never stops at a same-named report on another model.
        ctx.warn(f"a second report named {name}, bound to a new model from {pbix.name}, will be created next to "
                 f"report {listed}, which uses another model")
    elif others:
        # A same-named report on another model may be replaced too; its live-connected PBIX is the backup.
        for other in others:
            other["_stamp"], other["_folder"] = backup.reserve(data_dir, other["report"]["id"])
        ctx.changes.add(f"possibly replace {len(others)} report(s) named {name} that use other models with the report "
                        f"in {pbix.name}", f"workspace {ws['name']}: report {listed}", reversible=False,
                        backup=str(others[0]["_folder"] / pbix.name) + (f" (one per report, {len(others)} in all)"
                                                                         if len(others) > 1 else ""),
                        undo="publish each report's live-connected backup again")
    try:
        ctx.changes.require_yes(ctx.yes)
    except NeedsConfirmation as exc:
        exc.data.update(shown)  # the pre-flight shows what would be replaced, with owners
        raise
    out = {"pbix": str(pbix), "bytes": size, "workspace": ws["name"], "name_conflict": conflict, **shown}
    if ctx.dry_run:
        return out
    if target:
        source = target["_backup_from"]
        if source is None:
            raise NeedsUser(f"no report uses model {name}, so ppx cannot save a PBIX backup of it through the API; "
                            "publish refused. Download the model in the service first, or publish from Desktop")
        try:
            content = definition.export_pbix(ctx, ws, source, include_model=True)
        except PpxError as exc:
            raise NeedsUser(f"publish refused: no backup of the model it would replace: {exc.message}",
                            next_actions=exc.next_actions, data=exc.data) from None
        backup.save_bytes(folder, pbix.name, content)
    for other in others if conflict != "Abort" else []:
        try:
            content = definition.export_pbix(ctx, ws, other["_report"], include_model=False)
        except PpxError as exc:
            raise NeedsUser(f"publish refused: no backup of report {other['report']['id']}, which shares the name: "
                            f"{exc.message}", next_actions=exc.next_actions, data=exc.data) from None
        backup.save_bytes(other["_folder"], pbix.name, content)
    boundary = uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{pbix.name.replace(chr(34), '')}\""
            "\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
    labels = "true" if args.override_labels else "false"
    try:
        resp = http.request("POST", common.url(ws, "imports"), audience=apis.PBI_RESOURCE, op="write",
                            params={"datasetDisplayName": pbix.name, "nameConflict": conflict,
                                    "overrideReportLabel": labels, "overrideModelLabel": labels},
                            body=head + pbix.read_bytes() + f"\r\n--{boundary}--\r\n".encode(),
                            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                            timeout=ctx.timeout * definition.SLOW)
    except http.HttpError as exc:
        if exc.status in (401, 403):
            raise NeedsUser(f"publishing to {ws['name']} needs Contributor or higher in the workspace",
                            data={"status": exc.status, "error_code": exc.code}) from None
        raise
    import_id = (resp.json() or {}).get("id")
    if not import_id:
        raise PpxError("the import was accepted without an import id")
    done = _wait_import(ctx, ws, import_id)
    out.update(importId=import_id, state=done.get("importState"),
               models=[{"id": d.get("id"), "name": d.get("name")} for d in done.get("datasets") or []],
               reports=[{"id": r.get("id"), "name": r.get("name")} for r in done.get("reports") or []])
    if done.get("importState") != "Succeeded":
        ctx.warn("still publishing; check again before refreshing")
        ctx.next("check the import", command=f"ppx raw pbi GET {common.url(ws, 'imports/' + import_id)}")
    if target:
        out["replaced"] = {"model": target["model"], **({"report": target["report"]["id"]} if "report" in target else {})}
        out["backup"] = str(folder / pbix.name)
    if others and conflict != "Abort":
        out["report_backups"] = [str(o["_folder"] / pbix.name) for o in others]
    return out


# -- pbi deploy ---------------------------------------------------------------------------------

def _rebound(parts: list[dict], model_id: str) -> list[dict]:
    """definition.pbir pointed at the model by id: Fabric REST takes byConnection only."""
    out = []
    for part in parts:
        if part["path"] == "definition.pbir":
            pbir = json.loads(base64.b64decode(part["payload"]).decode("utf-8-sig"))
            pbir["datasetReference"] = {"byConnection": {"connectionString": f"semanticmodelid={model_id}"}}
            part = {**part, "payload": base64.b64encode(json.dumps(pbir, indent=2).encode()).decode("ascii")}
        out.append(part)
    return out


def _model_ref(folder: Path, parts: list[dict]) -> str | None:
    """The model folder name a report's definition.pbir points at by path, or None for byConnection."""
    pbir = next((p for p in parts if p["path"] == "definition.pbir"), None)
    if pbir is None:
        raise UsageError(f"{folder.name} has no definition.pbir")
    ref = (json.loads(base64.b64decode(pbir["payload"]).decode("utf-8-sig")).get("datasetReference") or {})
    by_path = (ref.get("byPath") or {}).get("path")
    return PurePosixPath(str(by_path).replace("\\", "/")).name if by_path else None


def _baseline(ctx, folder: Path, item_id: str) -> dict | None:
    deployed = cache.read_json(cache.folder(ctx.need_data_dir(), "pbi", DEPLOYED) / f"{item_id}.json")
    if isinstance(deployed, dict) and isinstance(deployed.get("parts"), dict):
        return deployed["parts"]
    pulled = cache.read_json(folder / definition.PULL_FILE)
    if isinstance(pulled, dict) and (pulled.get("item") or {}).get("id") == item_id:
        return pulled.get("parts")
    return None


def _plan(ctx, args, ws: dict, wid: str, folders: list[Path]) -> list[dict]:
    items, _ = http.paged(apis.fabric().url(f"workspaces/{wid}/items"), audience=apis.FABRIC_RESOURCE,
                          timeout=ctx.timeout)
    plan = []
    for folder in folders:
        meta = definition.platform(folder)["metadata"]
        typ, display = meta.get("type"), meta.get("displayName") or folder.stem
        kind = {"SemanticModel": "model", "Report": "report"}.get(typ)
        if kind is None:
            raise UsageError(f"{folder.name}/.platform names item type {typ!r}; deploy handles reports and models")
        same = [i for i in items if i.get("type") == typ and str(i.get("displayName") or "").lower() == display.lower()]
        if len(same) > 1:
            raise NeedsUser(f"{len(same)} {kind}s are named {display} in workspace {ws['name']}; rename one first",
                            data={"candidates": [i["id"] for i in same[:5]]})
        step = {"kind": kind, "name": display, "folder": folder, "parts": definition.read_parts(folder),
                "existing": same[0] if same else None}
        if kind == "report":
            step["model_ref"] = _model_ref(folder, step["parts"])
        if step["existing"]:
            item = {"id": same[0]["id"], "name": display}
            try:
                step["current"] = definition.get_definition(ctx, wid, kind, item["id"])
            except PpxError as exc:
                raise definition.refused(exc, "read", kind, item, ws) from None
            base = _baseline(ctx, folder, item["id"])
            service = definition.hashes(step["current"]["parts"])
            if not args.force:
                if base is None:
                    raise NeedsUser(f"{kind} {display} already exists in {ws['name']} and ppx has no record of what "
                                    "was last deployed or pulled, so it cannot tell whether the service copy changed; "
                                    "pass --force to replace it (a backup is taken first)")
                moved = sum(len(v) for v in definition.compare(base, service).values())
                if moved:
                    raise NeedsUser(f"the service copy of {kind} {display} changed since the last deploy or pull "
                                    f"({moved} parts differ); pass --force to replace it (a backup is taken first)")
            # The same rule as push: updateDefinition replaces the whole definition, so a part missing from the
            # project (a table, a page) is deleted in the service.
            step["removed"] = definition.compare(definition.hashes(step["parts"]), service)["removed"]
            if step["removed"] and not args.allow_part_removal:
                raise NeedsUser(f"{len(step['removed'])} parts of {kind} {display} in the service are missing from the "
                                f"project and would be deleted: {', '.join(step['removed'][:5])}; restore them, or "
                                "pass --allow-part-removal", data={"removed": step["removed"][:20]})
        plan.append(step)
    return plan


def _deployed(ctx, wid: str, kind: str, item_id: str, parts: list[dict]) -> None:
    """Record the service's copy as the next drift baseline, as push does: what the service fills in itself
    (lineage tags, normalised TMDL) is then not drift. Falls back to the parts sent."""
    try:
        parts = definition.get_definition(ctx, wid, kind, item_id)["parts"]
    except PpxError as exc:
        ctx.warn(f"deployed, but could not re-read {kind} {item_id} ({exc.message}); if the service adjusted the "
                 "definition, the next deploy reports a change and needs --force")
    cache.write_json(cache.folder(ctx.need_data_dir(), "pbi", DEPLOYED) / f"{item_id}.json",
                     {"parts": definition.hashes(parts)})


def _fab_deployed(ctx, wid: str, plan: list[dict]) -> None:
    """fab deploy names no item ids: find each deployed item by type and name, then record its baseline."""
    try:
        items, _ = http.paged(apis.fabric().url(f"workspaces/{wid}/items"), audience=apis.FABRIC_RESOURCE,
                              timeout=ctx.timeout)
    except PpxError as exc:
        ctx.warn(f"deployed, but could not list the workspace's items ({exc.message}); the next deploy over them "
                 "needs --force")
        return
    for step in plan:
        typ = {"model": "SemanticModel", "report": "Report"}[step["kind"]]
        found = next((i for i in items if i.get("type") == typ
                      and str(i.get("displayName") or "").lower() == step["name"].lower()), None)
        if found is None:
            ctx.warn(f"deployed, but {step['kind']} {step['name']} is not listed in the workspace yet; the next deploy "
                     "over it needs --force")
        else:
            _deployed(ctx, wid, step["kind"], found["id"], step["parts"])


def _create(ctx, wid: str, kind: str, name: str, parts: list[dict]) -> str:
    resp = http.request("POST", apis.fabric().url(f"workspaces/{wid}/{definition.KINDS[kind][0]}"),
                        audience=apis.FABRIC_RESOURCE, op="write",
                        json_body={"displayName": name, "definition": {"parts": parts}}, timeout=ctx.timeout)
    created = http.wait_operation(resp, audience=apis.FABRIC_RESOURCE, timeout=ctx.timeout * definition.SLOW,
                                  fetch_result=True) or {}
    if not created.get("id"):
        raise PpxError(f"creating {kind} {name} answered without its id; check the workspace before deploying again")
    return created["id"]


def deploy(ctx, args) -> dict:
    root = Path(args.pbip)
    root = root.parent if root.suffix.lower() == ".pbip" else root
    if not root.is_dir():
        raise UsageError(f"{root} is not a Power BI project folder")
    # Models before reports: a report's definition.pbir must name a model that exists.
    folders = sorted((d for d in root.iterdir() if d.is_dir() and d.suffix in (".SemanticModel", ".Report")),
                     key=lambda d: (d.suffix != ".SemanticModel", d.name))
    if not folders:
        raise UsageError(f"{root} holds no <name>.SemanticModel or <name>.Report folder")
    ws = common.workspace(ctx, args.workspace)
    path = definition.branch(ctx)
    if path == "D":
        pbip = next(root.glob("*.pbip"), root)
        raise NeedsUser("on this tenant definitions cannot be written through Fabric (routing.json), so deploy is not "
                        "available; publish the project from Power BI Desktop",
                        next_actions=[NextAction("open it in Desktop, then File > Publish",
                                                 command=f"powerbi-desktop open \"{pbip}\"")])
    if path == "F" and args.purge:
        raise UsageError("--purge needs the Fabric REST write path; fab deploy does not send it")
    wid = definition.fabric_workspace_id(ctx, ws)
    plan = _plan(ctx, args, ws, wid, folders)
    data_dir = ctx.need_data_dir()
    for step in plan:
        if step["existing"]:
            step["stamp"], step["backup"] = backup.reserve(data_dir, step["existing"]["id"])
            ctx.changes.add(f"replace the {step['kind']}'s definition with {step['folder'].name}"
                            + (f" ({len(step['removed'])} parts deleted)" if step.get("removed") else ""),
                            f"{step['kind']} {step['name']} ({step['existing']['id']}) in workspace {ws['name']}",
                            backup=str(step["backup"]),
                            undo=(f"ppx pbi push {step['kind']} {step['existing']['id']} --workspace "
                                  f"{common.shell_arg(ws)} --restore {step['stamp']}") if path != "F" else
                                 f"copy the parts saved in {step['backup']} into a pulled folder, then push that folder")
        else:
            ctx.changes.add(f"create {step['kind']} {step['name']} from {step['folder'].name}",
                            f"workspace {ws['name']}", undo=f"delete the new {step['kind']} {step['name']} in the workspace")
    if args.purge:
        ctx.changes.add("clear imported data where a model's new definition needs it; the model stays empty until "
                        "a refresh (shared capacity: 8 a day)", f"workspace {ws['name']}", reversible=False, costly=True)
    ctx.changes.require_yes(ctx.yes)
    out = {"workspace": ws["name"], "branch": path,
           "items": [{"kind": s["kind"], "name": s["name"], "action": "update" if s["existing"] else "create"}
                     for s in plan]}
    if ctx.dry_run:
        return out
    for step in plan:
        if step["existing"]:
            backup.save_definition(step["backup"], kind=step["kind"], item={**step["existing"], "name": step["name"]},
                                   workspace=ws, definition=step["current"])
    if path == "F":
        result = definition.fab_deploy(ctx, wid, folders)
        _fab_deployed(ctx, wid, plan)
        return {**out, **result}
    ids: dict[str, str] = {}  # model folder name -> id, filled as the models deploy (they go first)
    outside = {s["model_ref"] for s in plan if s.get("model_ref")} - {s["folder"].name for s in plan}
    known = {}  # models already in the workspace, by display name, for reports pointing outside the project
    if outside:
        models, _ = http.paged(apis.fabric().url(f"workspaces/{wid}/semanticModels"), audience=apis.FABRIC_RESOURCE,
                               timeout=ctx.timeout)
        known = {str(m.get("displayName") or "").lower(): m["id"] for m in models}
    done = []
    for step in plan:
        parts, kind = step["parts"], step["kind"]
        if kind == "report" and step["model_ref"]:
            ref = step["model_ref"]
            model_id = ids.get(ref) or known.get(ref.rsplit(".", 1)[0].lower())
            if not model_id:
                raise UsageError(f"{step['folder'].name} points at {ref}, which is neither in this project nor a model "
                                 f"in workspace {ws['name']}", data={"deployed": done})
            parts = _rebound(parts, model_id)
        item = {"id": step["existing"]["id"] if step["existing"] else None, "name": step["name"]}
        try:
            if step["existing"]:
                definition.update_definition(ctx, wid, kind, item["id"], {"parts": parts}, purge=args.purge)
            else:
                item["id"] = _create(ctx, wid, kind, step["name"], parts)
        except PpxError as exc:
            if definition.purge_needed(exc, kind) and not args.purge:
                raise definition.purge_error(exc, f"ppx pbi deploy --pbip \"{root}\" --workspace "
                                                  f"{common.shell_arg(ws)}") from None
            raise definition.refused(exc, "write", kind, item, ws) from None
        if kind == "model":
            ids[step["folder"].name] = item["id"]
        _deployed(ctx, wid, kind, item["id"], parts)
        done.append({"kind": kind, "name": step["name"], "id": item["id"],
                     "action": "updated" if step["existing"] else "created",
                     **({"backup": step["stamp"]} if step["existing"] else {})})
    if args.purge:
        ctx.next("reload the data of the deployed models", command="ppx pbi refresh --model <model> --workspace "
                                                                    f"{common.shell_arg(ws)}")
    return {**out, "items": done}
