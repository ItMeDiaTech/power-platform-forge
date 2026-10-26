"""pbi pull and pbi push: a report's PBIR or a model's TMDL on disk, and back without silent overwrites.

Which path edits definitions on this tenant is a probe fact (routing.json): branch A (default) uses
Fabric getDefinition/updateDefinition; branch F writes through `fab deploy` (the Fabric CLI's own
sign-in) because the REST write was refused; branch D has no definition write at all, so `pull`
exports the PBIX for an attended Power BI Desktop round trip and `publish` sends it back.

Facts (Learn, read 2026-10-26): Items - Get/Update Report Definition and Get/Update Semantic Model
Definition (POST .../getDefinition?format=, .../updateDefinition?updateMetadata=; 200, or 202 with
Location, x-ms-operation-id, Retry-After; parts are {path, payload, payloadType: InlineBase64};
`options.allowPurgeData` on models only, default false; error OperationNotSupportedForItem); Report
definition (PBIR `definition/` or PBIR-Legacy `report.json`, plus definition.pbir); Reports - Export
Report In Group (GET .../Export?downloadType=IncludeModel|LiveConnect; Contributor; models with
incremental refresh or large storage format cannot be exported through the API).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from ppx import apis, auth, cache, http, proc
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, UsageError
from ppx.pbi import backup, common
from ppx.redact import scrub_text

# kind -> (Fabric collection, pull format, PBIP folder suffix, Fabric item type)
KINDS = {"report": ("reports", "PBIR", ".Report", "Report"),
         "model": ("semanticModels", "TMDL", ".SemanticModel", "SemanticModel")}
PULL_FILE = ".ppx-pull.json"
BRANCH_KEY = "pbi_edit_branch"  # optional routing.json value the probe may write: A, F or D
SLOW = 3  # definitions and PBIX downloads get this many --timeout spans
LISTED = 10
ZERO_ID = "00000000-0000-0000-0000-000000000000"
_JSON_PARTS = (".json", ".pbir", ".pbism", ".platform")
# Folder and file names come from coworker-chosen item names and reach next_actions commands inside double
# quotes, so only characters neither Bash nor PowerShell expands there survive (as common.shell_arg allows).
_UNSAFE = re.compile(r"[^\w .()&'-]+")
# Learn names OperationNotSupportedForItem; the rest is how a capacity or read-only refusal reads.
_UNSUPPORTED = re.compile(r"OperationNotSupportedForItem|capacity|FeatureNotAvailable|not supported|read-?only", re.I)
_PURGE = re.compile(r"purge", re.I)


# -- which path edits definitions here ----------------------------------------------------------

def branch(ctx) -> str:
    """A (Fabric REST), F (fab deploy) or D (Desktop round trip), from routing.json; A without a probe."""
    data_dir = ctx.need_data_dir()
    raw = cache.read_json(data_dir / auth.ROUTING_FILE, {})
    chosen = str(raw.get(BRANCH_KEY) or "").upper() if isinstance(raw, dict) else ""
    if chosen in ("A", "F", "D"):
        return chosen
    # The probe records an empty write list when every token source was refused a Fabric write.
    writes = (auth.routing(data_dir) or {}).get("fabric", {}).get("write")
    return "D" if writes == [] else "A"


def fabric_workspace_id(ctx, ws: dict) -> str:
    """Fabric has no "me" alias: My workspace is the one workspace Fabric lists with type Personal."""
    if ws["id"] != common.ME:
        return ws["id"]
    spaces, _ = http.paged(apis.fabric().url("workspaces"), audience=apis.FABRIC_RESOURCE, timeout=ctx.timeout)
    personal = next((w for w in spaces if w.get("type") == "Personal"), None)
    if personal is None:
        raise NeedsUser("Fabric lists no personal workspace for you, so My workspace cannot be reached through it",
                        next_actions=[NextAction("work in a shared workspace, or use the Desktop round trip "
                                                 "(pbi pull ... --desktop)")])
    return personal["id"]


def desktop_actions(kind: str, item: dict, ws: dict) -> list[NextAction]:
    return [NextAction("use the Power BI Desktop round trip instead: export the PBIX, edit and save it in Desktop, "
                       "then publish it", command=f"ppx pbi pull {kind} {common.shell_arg(item)} "
                                              f"--workspace {common.shell_arg(ws)} --desktop"),
            NextAction("record which sign-in can write definitions on this tenant (the probe takes the scratch "
                       "model over, so use one you own and nobody else relies on)",
                       command="ppx probe --area pbi --write --workspace <scratch workspace> --model <scratch model> "
                               "--report <scratch report> --yes")]


def refused(exc: PpxError, what: str, kind: str, item: dict, ws: dict) -> PpxError:
    """A Fabric permission or capacity refusal becomes exit 2 naming the Desktop round trip; anything else stands."""
    text = f"{exc.data.get('error_code') or ''} {exc.message}"
    if getattr(exc, "status", None) in (401, 403) or (not isinstance(exc, NeedsUser) and _UNSUPPORTED.search(text)):
        return NeedsUser(f"Fabric refused to {what} {kind} {item.get('name')} ({exc.message}); this workspace or "
                         "sign-in cannot edit definitions through the Fabric API",
                         next_actions=desktop_actions(kind, item, ws), data={k: v for k, v in exc.data.items()
                                                                             if k in ("status", "error_code")})
    return exc


# -- Fabric definitions -------------------------------------------------------------------------

def _item_url(wid: str, kind: str, item_id: str, action: str) -> str:
    return apis.fabric().url(f"workspaces/{wid}/{KINDS[kind][0]}/{item_id}/{action}")


def get_definition(ctx, wid: str, kind: str, item_id: str) -> dict:
    """The item's definition {parts, format?}; raises PpxError (map it with refused())."""
    resp = http.request("POST", _item_url(wid, kind, item_id, "getDefinition"), audience=apis.FABRIC_RESOURCE,
                        params={"format": KINDS[kind][1]}, body=b"", timeout=ctx.timeout)
    body = http.wait_operation(resp, audience=apis.FABRIC_RESOURCE, timeout=ctx.timeout * SLOW, fetch_result=True)
    definition = (body or {}).get("definition") if isinstance(body, dict) else None
    if not isinstance(definition, dict) or not isinstance(definition.get("parts"), list) or not definition["parts"]:
        raise PpxError(f"getDefinition returned no definition parts for {kind} {item_id}")
    return definition


def update_definition(ctx, wid: str, kind: str, item_id: str, definition: dict, *, purge: bool = False) -> None:
    body: dict = {"definition": definition}
    if purge and kind == "model":  # Update Report Definition takes no options
        body["options"] = {"allowPurgeData": True}
    resp = http.request("POST", _item_url(wid, kind, item_id, "updateDefinition"), audience=apis.FABRIC_RESOURCE,
                        op="write", params={"updateMetadata": "false"}, json_body=body, timeout=ctx.timeout)
    http.wait_operation(resp, audience=apis.FABRIC_RESOURCE, timeout=ctx.timeout * SLOW)


def purge_needed(exc: PpxError, kind: str) -> bool:
    return kind == "model" and bool(_PURGE.search(f"{exc.data.get('error_code') or ''} {exc.message}"))


def purge_error(exc: PpxError, command: str) -> NeedsUser:
    return NeedsUser(f"this model change needs the model's imported data cleared ({exc.message}); nothing was "
                     "changed. Re-run with --purge --yes; the model is then empty until its next refresh, which "
                     "counts toward the 8 a day on shared capacity",
                     next_actions=[NextAction("push again, allowing the purge", command=f"{command} --purge --yes")],
                     data={k: v for k, v in exc.data.items() if k in ("status", "error_code")})


# -- parts on disk ------------------------------------------------------------------------------

def _canonical(path: str, data: bytes) -> bytes:
    """JSON parts compared by content (key order and spacing ignored), text parts with LF line ends."""
    if path.endswith(_JSON_PARTS):
        try:
            return json.dumps(json.loads(data.decode("utf-8-sig")), sort_keys=True, separators=(",", ":")).encode()
        except (UnicodeDecodeError, ValueError):
            pass
    return data.replace(b"\r\n", b"\n")


def _decode(part: dict) -> bytes:
    if part.get("payloadType", "InlineBase64") != "InlineBase64":
        raise PpxError(f"part {part.get('path')} has payload type {part.get('payloadType')}, which ppx cannot read")
    try:
        return base64.b64decode(part.get("payload") or "", validate=True)
    except ValueError as exc:
        raise PpxError(f"part {part.get('path')} is not valid base64") from exc


def hashes(parts: list[dict]) -> dict[str, str]:
    return {p["path"]: hashlib.sha256(_canonical(p["path"], _decode(p))).hexdigest() for p in parts}


def _safe_part(path: str) -> PurePosixPath:
    rel = PurePosixPath(path)
    if not path or rel.is_absolute() or ".." in rel.parts or ":" in path or "\\" in path:
        raise PpxError(f"refusing definition part path {path!r}: it would land outside the folder")
    return rel


def write_parts(folder: Path, parts: list[dict]) -> None:
    for part in parts:
        target = folder.joinpath(*_safe_part(part["path"]).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_decode(part))


def read_parts(folder: Path) -> list[dict]:
    """Every file under an item folder as a definition part, without ppx's record and Desktop's .pbi cache."""
    parts = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        if rel == PULL_FILE or ".pbi" in PurePosixPath(rel).parts[:-1]:
            continue
        parts.append({"path": rel, "payload": base64.b64encode(path.read_bytes()).decode("ascii"),
                      "payloadType": "InlineBase64"})
    if not parts:
        raise UsageError(f"{folder} holds no definition files")
    return parts


def compare(local: dict[str, str], service: dict[str, str]) -> dict[str, list[str]]:
    return {"added": sorted(set(local) - set(service)), "removed": sorted(set(service) - set(local)),
            "changed": sorted(p for p in set(local) & set(service) if local[p] != service[p])}


def safe_name(name: str) -> str:
    return _UNSAFE.sub("-", name).strip(" .-") or "item"


def _record(folder: Path) -> dict:
    record = cache.read_json(folder / PULL_FILE)
    if not isinstance(record, dict) or not isinstance(record.get("parts"), dict):
        raise UsageError(f"{folder} has no readable {PULL_FILE}; pass the folder `ppx pbi pull` wrote")
    return record


# -- pbi pull -----------------------------------------------------------------------------------

def _empty_target(folder: Path) -> Path:
    if folder.exists() and any(folder.iterdir()):
        raise UsageError(f"{folder} already holds files; push or remove them first, or pass another --out")
    return folder


def pull(ctx, args) -> dict:
    kind = ctx.verb.path[-1]
    ws, item = common.resolve(ctx, kind, getattr(args, kind), args.workspace)
    out = Path(args.out) if args.out else ctx.need_data_dir() / "exports" / "pbi"
    if ctx.dry_run:  # a pull writes files, so a dry run only says where
        return {kind: item.get("name"), "workspace": ws["name"], "would_write_to": str(out)}
    if args.desktop or branch(ctx) == "D":
        return _pull_pbix(ctx, ws, item, kind, out)
    target = _empty_target(out / f"{safe_name(item.get('name') or item['id'])}{KINDS[kind][2]}")
    wid = fabric_workspace_id(ctx, ws)
    try:
        definition = get_definition(ctx, wid, kind, item["id"])
    except PpxError as exc:
        raise refused(exc, "read", kind, item, ws) from None
    write_parts(target, definition["parts"])
    record = {"kind": kind, "item": {"id": item["id"], "name": item.get("name")},
              "workspace": {"id": ws["id"], "name": ws.get("name")}, "fabric_workspace": wid,
              "format": definition.get("format"), "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "parts": hashes(definition["parts"])}
    cache.write_json(target / PULL_FILE, record)
    # Learn: the definition "matches how it is stored in the service", so judge by the parts that came back.
    if kind == "report" and "report.json" in record["parts"]:
        ctx.warn("this report came back in the PBIR-Legacy format (one report.json): edit report.json directly; the "
                 "PBIR report CLI and the PBIR schema check do not apply to it")
    elif kind == "report" and item.get("format") == "PBIRLegacy":
        ctx.warn("the inventory lists this report as PBIR-Legacy but it came back as PBIR; whether pushing it changes "
                 "the stored format is not yet verified (probe fact)")
    paths = sorted(record["parts"])
    ctx.next("edit the files, then push them back", command=f"ppx pbi push {kind} {common.shell_arg(item)} "
                                                            f"--workspace {common.shell_arg(ws)} --from \"{target}\"")
    return {kind: item.get("name"), "workspace": ws["name"], "folder": str(target), "format": KINDS[kind][1],
            "parts": len(paths), "files": paths[:LISTED], **({"more": len(paths) - LISTED} if len(paths) > LISTED else {})}


def report_url(ws: dict, report_id: str) -> str:
    return f"{common.SERVICE}/groups/{ws['id']}/reports/{report_id}"


def export_pbix(ctx, ws: dict, report: dict, *, include_model: bool) -> bytes:
    """The report's PBIX (with its model, or live-connected to it); every PBIX download in ppx goes through here."""
    try:
        resp = http.request("GET", common.url(ws, f"reports/{report['id']}/Export"), audience=apis.PBI_RESOURCE,
                            params={"downloadType": "IncludeModel" if include_model else "LiveConnect"},
                            headers={"Accept": "application/zip, application/octet-stream"},
                            timeout=ctx.timeout * SLOW)
    except http.HttpError as exc:
        status = {"status": exc.status, "error_code": exc.code}
        if exc.status in (401, 403):
            raise NeedsUser(f"downloading {report.get('name')} as a PBIX needs Contributor or higher in its workspace "
                            "(and in its model's workspace)", data=status) from None
        if exc.status not in (400, 404, 409):  # a service fault or throttling is not a lasting export limit
            raise
        raise NeedsUser(f"the service would not export {report.get('name')} as a PBIX ({exc.message}). It cannot "
                        "export models with incremental refresh, the large semantic model storage format or Direct "
                        "Lake, nor reports built in the service on another workspace's model",
                        next_actions=[NextAction("download it in the service (File > Download this file) or open "
                                                 "the project in Desktop", url=report_url(ws, report["id"]))],
                        data=status) from None
    if resp.body[:2] != b"PK":  # a PBIX is a zip archive
        raise PpxError(f"the export of {report.get('name')} returned no PBIX file")
    return resp.body


def _pull_pbix(ctx, ws: dict, item: dict, kind: str, out: Path) -> dict:
    items = common.cached_items(ctx, ws)
    if kind == "model":
        bound = [i for i in items if i.get("type") == "report" and i.get("modelId") == item["id"]]
        report = next((r for r in bound if r.get("name") == item.get("name")), bound[0] if bound else None)
        if report is None:
            raise NeedsUser(f"no report in {ws['name']} uses model {item.get('name')}, and only a report can be "
                            "exported as a PBIX; download the model in the service instead")
    else:
        report = item
    model = next((i for i in items if i.get("type") == "model" and i.get("id") == report.get("modelId")), None)
    same_ws = model is not None
    content = export_pbix(ctx, ws, report, include_model=kind == "model" or same_ws)
    # Publishing replaces the model named like the file, so a model pull is named after the model.
    named = item if kind == "model" else report
    name = safe_name(named.get("name") or named["id"])
    path = out / f"{name}.pbix"
    if path.exists():
        raise UsageError(f"{path} already exists; remove it or pass another --out")
    out.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if name != named.get("name"):
        ctx.warn(f"saved as {path.name}: publishing it creates or replaces a model of that name, not "
                 f"{named.get('name')!r}")
    if kind == "model" and report.get("name") != item.get("name"):
        ctx.warn(f"the PBIX was exported through report {report.get('name')!r}; publishing it replaces model "
                 f"{item.get('name')!r} and creates or replaces a report named like the file, beside "
                 f"{report.get('name')!r}")
    if kind == "report" and report.get("modelId") and not same_ws:
        ctx.warn("the model lives in another workspace, so the PBIX is live-connected: only the report can be edited")
    if kind == "report" and same_ws and model.get("name") != report.get("name"):
        # Imports match only model names, so this file would add a model copy and a second report, not update these.
        ctx.warn(f"publishing this PBIX creates a new model named {path.stem!r} beside model {model.get('name')!r}, and "
                 f"a second report beside {report.get('name')!r}; to update the original report and model, pull the "
                 f"model instead: ppx pbi pull model {common.shell_arg(model)} --workspace {common.shell_arg(ws)} "
                 "--desktop")
    ctx.next("open it in Power BI Desktop", command=f"powerbi-desktop open \"{path}\"")
    ctx.next("edit it in Desktop (model changes through /powerbi-authoring:semantic-model-authoring), then save in "
             "Desktop (Ctrl+S): the one attended step")
    ctx.next("publish it back; ppx lists what it would replace first",
             command=f"ppx pbi publish --pbix \"{path}\" --workspace {common.shell_arg(ws)} "
                     "--name-conflict CreateOrOverwrite")
    return {kind: item.get("name"), "workspace": ws["name"], "branch": "D", "pbix": str(path), "bytes": len(content),
            "report": report.get("name")}


# -- branch F: fab deploy -----------------------------------------------------------------------

def platform(folder: Path) -> dict:
    meta = cache.read_json(folder / ".platform")
    if not isinstance(meta, dict) or not isinstance(meta.get("metadata"), dict):
        raise UsageError(f"{folder} has no readable .platform file; save the project with a current Power BI Desktop")
    return meta


def fab_deploy(ctx, wid: str, folders: list[Path]) -> dict:
    """Publish item folders with `fab deploy` (fabric-cicd) into workspace `wid`; fab signs in by itself.

    Facts (read 2026-10-26): microsoft.github.io/fabric-cli/commands/fs/deploy (publish and unpublish both run by
    default; `unpublish` takes exclude_regex and skip; without --target_env no field may be environment-keyed;
    --force skips the confirmation prompt) and fabric-cicd's config_deployment page (a single value applies to any
    environment; unpublish skip defaults to false).
    """
    for folder in folders:
        logical = str((platform(folder).get("config") or {}).get("logicalId") or "")
        if not logical or logical == ZERO_ID:
            raise UsageError(f"{folder.name}/.platform has no real logicalId ({logical or 'empty'}); fab deploy "
                             "refuses it. Give config.logicalId a new GUID, then deploy again")
    parent = ctx.need_data_dir() / "exports"
    parent.mkdir(parents=True, exist_ok=True)
    repo = Path(tempfile.mkdtemp(prefix="fab-deploy-", dir=parent))
    try:
        for folder in folders:
            shutil.copytree(folder, repo / folder.name, ignore=shutil.ignore_patterns(PULL_FILE, ".pbi"))
        types = sorted({platform(f)["metadata"].get("type") or "" for f in folders} - {""})
        config = repo / "config.yml"
        # fab deploy unpublishes by default every in-scope workspace item missing from the repository, and this
        # repository holds only the pushed items: unpublish stays off, skipped and (if skip were ignored) excluded.
        config.write_text("core:\n"
                          f"  workspace_id: {json.dumps(wid)}\n"
                          f"  repository_directory: {json.dumps(str(repo))}\n"
                          "  item_types_in_scope:\n" + "".join(f"    - {t}\n" for t in types) +
                          "unpublish:\n"
                          "  skip: true\n"
                          "  exclude_regex: \".*\"\n", encoding="utf-8")
        # --force skips fab's own "Are you sure" prompt, which would hang without a terminal.
        p = proc.run(["fab", "deploy", "--config", str(config), "--force"], timeout=ctx.timeout * SLOW,
                     install_hint=auth.INSTALL["fab"])
    finally:
        shutil.rmtree(repo, ignore_errors=True)
    tail = scrub_text(" ".join((p.stderr.strip() or p.stdout.strip()).splitlines()[-3:]))[:400]
    if p.code != 0:
        if re.search(r"auth login|not logged in|unauthori[sz]ed|sign in", tail, re.I):
            raise NeedsUser(f"fab is not signed in: {tail}",
                            next_actions=[NextAction("sign in to the Fabric CLI in your own terminal",
                                                     command="ppx auth login --backend fab")])
        raise PpxError(f"fab deploy failed: {tail}", data={"exit_code": p.code})
    return {"via": "fab deploy", "output": tail}


# -- pbi push -----------------------------------------------------------------------------------

def push(ctx, args) -> dict:
    kind = ctx.verb.path[-1]
    ws, item = common.resolve(ctx, kind, getattr(args, kind), args.workspace)
    data_dir = ctx.need_data_dir()
    path = branch(ctx)
    if path == "D":
        raise NeedsUser("on this tenant definitions cannot be written (routing.json: no Fabric write), so push is "
                        "not available", next_actions=desktop_actions(kind, item, ws)[:1])
    cmd = f"ppx pbi push {kind} {common.shell_arg(item)} --workspace {common.shell_arg(ws)}"
    purge = bool(getattr(args, "purge", False))
    source = getattr(args, "from")
    if path == "F" and purge:
        raise UsageError("--purge needs the Fabric REST write path; fab deploy does not send it")
    if args.restore:
        if path == "F":
            raise UsageError("--restore needs the Fabric REST write path; on this tenant copy the backup's parts into "
                             "a pulled folder and push that folder")
        saved = backup.load_definition(data_dir, item["id"], args.restore)
        local_parts, fmt, folder, record = saved["definition"]["parts"], saved["definition"].get("format"), None, None
    elif source:
        folder = Path(source)
        record = _record(folder)
        if record.get("kind") != kind or (record.get("item") or {}).get("id") != item["id"]:
            raise UsageError(f"{folder} was pulled from {record.get('kind')} {(record.get('item') or {}).get('name')}, "
                             f"not {kind} {item.get('name')}")
        local_parts, fmt = read_parts(folder), record.get("format")
    else:
        raise UsageError("pass --from <folder written by pbi pull>, or --restore <backup timestamp>")
    wid = fabric_workspace_id(ctx, ws)
    try:
        current = get_definition(ctx, wid, kind, item["id"])
    except PpxError as exc:
        raise refused(exc, "read", kind, item, ws) from None
    service, local = hashes(current["parts"]), hashes(local_parts)
    if record is not None and not args.force:
        drift = compare(record["parts"], service)
        moved = drift["added"] + drift["removed"] + drift["changed"]
        if moved:
            raise NeedsUser(f"the service copy of {kind} {item.get('name')} changed since the pull ({len(moved)} "
                            f"parts differ: {', '.join(moved[:5])}); pull again and redo the edit, or overwrite "
                            "those changes with --force (a backup is taken first)",
                            next_actions=[NextAction("pull the current copy into a new folder",
                                                     command=f"ppx pbi pull {kind} {common.shell_arg(item)} "
                                                             f"--workspace {common.shell_arg(ws)} --out <new folder>")],
                            data={"differ": moved[:LISTED]})
    diff = compare(local, service)
    if diff["removed"] and not args.restore and not args.allow_part_removal:
        raise NeedsUser(f"{len(diff['removed'])} parts in the service are missing locally and would be deleted: "
                        f"{', '.join(diff['removed'][:5])}; restore them, or pass --allow-part-removal",
                        data={"removed": diff["removed"][:LISTED]})
    summary = {k: len(v) for k, v in diff.items()}
    out = {kind: item.get("name"), "workspace": ws["name"], "branch": path, **summary}
    if not any(summary.values()):
        return {**out, "unchanged": True}
    stamp, folder_b = backup.reserve(data_dir, item["id"])
    # fab deploy (branch F) cannot take --restore, so the undo there is by hand.
    undo = (f"{cmd} --restore {stamp}" if path != "F" else
            f"copy the parts saved in {folder_b} into a pulled folder, then push that folder")
    ctx.changes.add(f"replace the {kind}'s definition ({summary['changed']} changed, {summary['added']} added, "
                    f"{summary['removed']} removed parts)" + (f" from backup {args.restore}" if args.restore else ""),
                    f"{kind} {item.get('name')} in workspace {ws['name']}", backup=str(folder_b), undo=undo)
    if purge:
        ctx.changes.add("clear the model's imported data where the new definition needs it; it stays empty until a "
                        "refresh (shared capacity: 8 a day)", f"model {item.get('name')} in workspace {ws['name']}",
                        reversible=False, costly=True)
    ctx.changes.require_yes(ctx.yes)
    if ctx.dry_run:
        return {**out, "would_change": {k: v[:LISTED] for k, v in diff.items() if v}}
    backup.save_definition(folder_b, kind=kind, item=item, workspace=ws, definition=current)
    definition = {"parts": local_parts, **({"format": fmt} if fmt else {})}
    if path == "F":
        out.update(fab_deploy(ctx, wid, [folder]))
    else:
        try:
            update_definition(ctx, wid, kind, item["id"], definition, purge=purge)
        except PpxError as exc:
            if purge_needed(exc, kind) and not purge:
                raise purge_error(exc, cmd + (f" --from \"{folder}\"" if folder else f" --restore {args.restore}")) from None
            raise refused(exc, "write", kind, item, ws) from None
    if record is not None:
        # The next push checks drift against what the service holds now, which may differ from the folder by
        # what the service fills in itself (lineage tags, say); re-read it, else fall back to what was sent.
        try:
            after = hashes(get_definition(ctx, wid, kind, item["id"])["parts"])
        except PpxError as exc:
            after = local
            ctx.warn(f"pushed, but could not re-read the {kind} ({exc.message}); if the service adjusted the "
                     "definition, the next push from this folder reports a change and needs a fresh pull")
        cache.write_json(folder / PULL_FILE, {**record, "parts": after,
                                              "pushed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    if purge:
        ctx.next("reload the model's data", command=common.command("refresh", item, ws))
    if path == "F":
        ctx.next(f"undo this push: {undo}")
    else:
        ctx.next("undo this push", command=undo)
    return {**out, "backup": stamp}
