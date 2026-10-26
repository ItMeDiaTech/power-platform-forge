"""pbi model-from-sp: a SharePoint list becomes a semantic model and an empty report in one call.

Branch A (Fabric REST writes work): create the model from TMDL parts (sp m-snippet's table, model.tmdl,
database.tmdl, definition.pbism), create an empty report bound to it through `creationPayload`, then
`pbi datasources bind --transient` (your sign-in for about an hour) and one gated refresh to prove it
loads; durable credentials stay a browser step, returned as the settings URL. Branches F and D: write a
Power BI project (PBIP) folder for `pbi deploy` or Power BI Desktop instead. PBIR file shapes follow
Learn's Power BI Desktop project report folder page and the vendored schemas in templates/pbir-schemas.
"""

from __future__ import annotations

import argparse
import base64
import json
import uuid
from pathlib import Path

from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, UsageError
from ppx.pbi import common, datasources, definition, model_create
from ppx.sp import msnippet, sites

SCHEMAS = "https://developer.microsoft.com/json-schemas/fabric"
PLATFORM = f"{SCHEMAS}/gitIntegration/platformProperties/2.0.0/schema.json"


def _json(obj: dict) -> str:
    return json.dumps(obj, indent=2) + "\n"


def _platform(kind: str, name: str) -> str:
    return _json({"$schema": PLATFORM, "metadata": {"type": kind, "displayName": name},
                  "config": {"version": "2.0", "logicalId": str(uuid.uuid4())}})


def project_files(name: str, model_files: dict[str, str]) -> dict[str, str]:
    """A PBIP project: the model folder, an empty one-page PBIR report bound to it by path, and the .pbip file."""
    folder = definition.safe_name(name)
    page = uuid.uuid4().hex[:20]
    report = f"{folder}.Report"
    files = {f"{folder}.SemanticModel/{p}": text for p, text in model_files.items()}
    files[f"{folder}.SemanticModel/.platform"] = _platform("SemanticModel", name)
    files.update({
        f"{report}/.platform": _platform("Report", name),
        f"{report}/definition.pbir": _json({"$schema": f"{SCHEMAS}/item/report/definitionProperties/2.0.0/schema.json",
                                            "version": "4.0",
                                            "datasetReference": {"byPath": {"path": f"../{folder}.SemanticModel"}}}),
        f"{report}/definition/report.json": _json({"$schema": f"{SCHEMAS}/item/report/definition/report/3.1.0/schema.json",
                                                   "themeCollection": {}}),
        f"{report}/definition/version.json": _json({"$schema": f"{SCHEMAS}/item/report/definition/versionMetadata/"
                                                               "1.0.0/schema.json", "version": "2.0.0"}),
        f"{report}/definition/pages/pages.json": _json({"$schema": f"{SCHEMAS}/item/report/definition/pagesMetadata/"
                                                                   "1.0.0/schema.json",
                                                        "pageOrder": [page], "activePageName": page}),
        f"{report}/definition/pages/{page}/page.json": _json({"$schema": f"{SCHEMAS}/item/report/definition/page/"
                                                                         "2.0.0/schema.json",
                                                              "name": page, "displayName": "Page 1",
                                                              "displayOption": "FitToPage", "height": 720,
                                                              "width": 1280}),
        f"{folder}.pbip": _json({"$schema": f"{SCHEMAS}/pbip/pbipProperties/1.0.0/schema.json", "version": "1.0",
                                 "artifacts": [{"report": {"path": report}}], "settings": {"enableAutoRecovery": True}}),
    })
    return files


def _write_project(ctx, args, ws: dict, name: str, model_files: dict[str, str], branch: str) -> dict:
    root = (Path(args.out).expanduser() if args.out else ctx.need_data_dir() / "exports" / "pbi") / definition.safe_name(name)
    if root.exists() and any(root.iterdir()):
        raise UsageError(f"{root} already holds files; remove it or pass another --out")
    files = project_files(name, model_files)
    pbip = root / next(p for p in files if p.endswith(".pbip"))
    ctx.changes.add(f"write a Power BI project for {name} (model and an empty report)", str(root),
                    undo=f"delete the folder {root}")
    out = {"model": name, "workspace": ws["name"], "branch": branch, "project": str(root), "files": len(files)}
    if ctx.dry_run:
        return out
    try:
        for rel, text in files.items():
            path = root.joinpath(*rel.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise PpxError(f"cannot write the project to {root}: {exc}") from exc
    if branch == "F":
        ctx.next("deploy it (fab deploy creates the model and the report)",
                 command=f"ppx pbi deploy --pbip \"{root}\" --workspace {common.shell_arg(ws)}")
    else:
        ctx.next("open it in Power BI Desktop, then File > Publish to the workspace",
                 command=f"powerbi-desktop open \"{pbip}\"")
    ctx.next("then bind your sign-in and refresh once", command=f"ppx pbi datasources bind --model "
                                                                f"{common.shell_arg({'name': name, 'id': '<model id>'})} "
                                                                f"--workspace {common.shell_arg(ws)} --transient")
    return {**out, "pbip": str(pbip)}


def run(ctx, args) -> dict:
    site = sites.resolve(ctx, args.site)
    info, cols, skipped = msnippet.load(ctx, site, args.list)
    title = info.get("Title") or args.list
    name = (args.name or title).strip()
    model_files = msnippet.model_files(title, msnippet.render(site, title, info["Id"], cols, args.view))
    msnippet.warn_view(ctx, args.view, skipped)
    ws = common.workspace(ctx, args.workspace)
    branch = definition.branch(ctx)
    if branch != "A":
        return {**_write_project(ctx, args, ws, name, model_files, branch), "columns": len(cols)}
    wid = model_create.target(ctx, ws, name)
    model_create.target(ctx, ws, name, "report")
    parts = [{"path": p, "payload": base64.b64encode(t.encode("utf-8")).decode("ascii"), "payloadType": "InlineBase64"}
             for p, t in model_files.items()]
    where = f"workspace {ws['name']}"
    ctx.changes.add(f"create semantic model {name} reading SharePoint list {title} ({len(cols)} columns)", where,
                    undo=f"delete model {name} in the workspace")
    ctx.changes.add(f"create an empty report {name} bound to it", where, undo=f"delete report {name} in the workspace")
    out = {"model": name, "workspace": ws["name"], "branch": "A", "site": site, "list": title,
           "columns": [c["name"] for c in cols][:30], **({"skipped": skipped[:10]} if skipped else {})}
    if ctx.dry_run:
        if not args.no_refresh:
            ctx.changes.add("bind your sign-in to the SharePoint data source (about an hour), then refresh once",
                            f"model {name} in {where}")
        return out
    model_id = model_create.create(ctx, ws, wid, "model", name, parts)
    model = {"id": model_id, "name": name}
    try:
        report_id = model_create.create(ctx, ws, wid, "report", name, model_id=model_id)
    except PpxError as exc:  # a retry would stop on "already exists": name the half-built model
        kind = NeedsUser if isinstance(exc, NeedsUser) else PpxError
        raise kind(f"model {name} was created (id {model_id}) but its report was not: {exc.message}. Delete the "
                   "model in the workspace and run this again, or build a report on it in the service",
                   data={**exc.data, "model_id": model_id},
                   next_actions=[*exc.next_actions, NextAction("open the model's settings",
                                                               url=common.settings_url(ws, model_id))]) from None
    out.update(model_id=model_id, report_id=report_id)
    settings = common.settings_url(ws, model_id)
    if args.no_refresh:
        ctx.next("set the SharePoint credentials (once, durable)", url=settings)
        ctx.next("then refresh", command=common.command("refresh", model, ws))
        return out
    try:
        # owned: the user just created this model, so there is nothing to take over
        bound = datasources.bind(ctx, argparse.Namespace(model=model_id, workspace=ws["id"], transient=True,
                                                         no_refresh=False, owned=True))
        out["refresh"] = bound.get("refresh")
    except PpxError as exc:  # the model and report exist; say what is left instead of failing the whole call
        ctx.warn(f"created, but the check refresh did not run: {exc.message}")
        ctx.next_actions.extend(a for a in exc.next_actions if a.url != settings)
        out["bind_error"] = exc.message
    if not any(a.url == settings for a in ctx.next_actions):
        ctx.next_actions.append(NextAction("set the SharePoint credentials once in the settings page for scheduled "
                                           "refresh (the API binding lasts about an hour)", url=settings))
    return out
