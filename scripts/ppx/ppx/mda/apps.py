"""mda ls|show|forms|views|sitemap: model-driven apps and their components (Dataverse Web API).

Components come from appmodulecomponent (componenttype 1 tables, 26 views, 29 business process
flows, 48 ribbons, 59 charts, 60 forms, 62 sitemap; learn.microsoft.com/power-apps/developer/
data-platform/reference/entities/appmodulecomponent). The XML verbs save sitemapxml, formxml and a
view's fetchxml/layoutxml as files named <kind>-<id>[.<column>].xml, which `mda publish --from`
writes back.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from ppx import resolve
from ppx.data.dataverse import Dataverse
from ppx.pa.inventory import find_app, inventory

COMPONENT = {1: "tables", 26: "views", 29: "business_process_flows", 48: "ribbons", 59: "charts", 60: "forms",
             62: "sitemap"}
_LABEL = {"entities": 1, "views": 26, "business process flows": 29, "charts": 59, "forms": 60, "sitemap": 62}
CHUNK = 50  # ids per In() filter, to keep URLs short


def _type(value) -> int | None:
    text = str(value).strip()
    return int(text) if text.isdigit() else _LABEL.get(text.lower())  # pac env fetch may print the label


def _app(app: dict) -> dict:
    return {"id": app["id"], "name": app.get("name"), "unique_name": app.get("unique_name")}


def load(ctx, wanted: str) -> tuple[dict, Dataverse, dict[int, list[str]]]:
    """The app, a Dataverse client and the app's component ids by component type."""
    app = find_app(ctx, "mda", wanted)
    dv = Dataverse(ctx)
    rows = dv.rows("appmodulecomponents", "appmodulecomponent", ("componenttype", "objectid"),
                   where=[("_appmoduleidunique_value", app["id_unique"])])
    comps: dict[int, list[str]] = {}
    for r in rows:
        comps.setdefault(_type(r.get("componenttype")), []).append(r.get("objectid"))
    return app, dv, comps


def by_ids(dv: Dataverse, entity_set: str, entity: str, key: str, select, ids: list[str]) -> list[dict]:
    rows = []
    for i in range(0, len(ids), CHUNK):
        rows += dv.rows(entity_set, entity, select, where=[(key, ids[i:i + CHUNK])])
    return rows


def folder(ctx, app: dict, out: str | None) -> Path:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(app.get("unique_name") or app["id"])).strip("-") or "app"
    return Path(out) if out else ctx.need_data_dir() / "exports" / "mda" / name


def save(path: Path, xml: str) -> str:
    """Write the value byte for byte: text mode on Windows would turn a stored CRLF into CR CR LF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(xml.encode("utf-8"))
    return str(path)


def keep(ctx, path: Path, xml: str, etag: str | None = None) -> str:
    """Save a live value for editing; a different file already there (unpublished edits) is kept as
    <name>.<UTC time>.bak first, a name `mda publish --from` ignores. The row's eTag goes to
    <name>.etag, so publish can refuse when someone changed the row after this save. A dry run writes nothing."""
    ctx.changes.add(f"save {path.name} for editing", str(path), undo=f"delete {path}")
    if ctx.dry_run:
        return str(path)
    if path.is_file() and path.read_bytes() != xml.encode("utf-8"):
        bak = path.with_name(f"{path.name}.{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.bak")
        path.replace(bak)
        ctx.warn(f"{path.name} held different XML, perhaps unpublished edits: kept it as {bak.name}")
    tag = path.with_name(path.name + ".etag")
    if etag:
        tag.write_text(etag, encoding="utf-8")
    else:
        tag.unlink(missing_ok=True)
    return save(path, xml)


def _next_publish(ctx, app: dict, where: Path) -> None:
    ctx.next("edit the saved XML, then write it back and publish (backups first)",
             command=f'ppx mda publish "{app.get("unique_name") or app["id"]}" --from "{where}"')


def ls(ctx, args) -> dict:
    items = inventory(ctx, "mda")
    top = ctx.top or 50
    return {"count": len(items), "more": len(items) > top,
            "items": [{**_app(i), "modified": i.get("modified")} for i in items[:top]]}


def show(ctx, args) -> dict:
    app, dv, comps = load(ctx, args.app)
    return {"app": {**_app(app), "modified": app.get("modified")}, "token_source": dv.source,
            "components": {COMPONENT.get(t, str(t)): len(ids) for t, ids in comps.items()},
            "sitemap_id": (comps.get(62) or [None])[0]}


def forms(ctx, args) -> dict:
    app, dv, comps = load(ctx, args.app)
    rows = by_ids(dv, "systemforms", "systemform", "formid", ("formid", "name", "objecttypecode", "type"),
                  comps.get(60, []))
    items = [{"id": r.get("formid"), "name": r.get("name"), "table": r.get("objecttypecode"), "type": r.get("type")}
             for r in rows]
    data = {"app": _app(app), "count": len(items), "items": items[:ctx.top or 50], "token_source": dv.source}
    if not items:
        ctx.warn("the app lists no forms, so its tables show all their forms")
    if args.form:
        form = resolve.pick("form", args.form, items, context_keys=("table",))
        body = dv.get(f"systemforms({form['id']})", {"$select": "formxml"}) or {}
        where = folder(ctx, app, args.out)
        data["saved"] = keep(ctx, where / f"form-{form['id']}.xml", body.get("formxml") or "", body.get("@odata.etag"))
        _next_publish(ctx, app, where)
    return data


def views(ctx, args) -> dict:
    app, dv, comps = load(ctx, args.app)
    rows = by_ids(dv, "savedqueries", "savedquery", "savedqueryid",
                  ("savedqueryid", "name", "returnedtypecode", "querytype"), comps.get(26, []))
    items = [{"id": r.get("savedqueryid"), "name": r.get("name"), "table": r.get("returnedtypecode"),
              "query_type": r.get("querytype")} for r in rows]
    data = {"app": _app(app), "count": len(items), "items": items[:ctx.top or 50], "token_source": dv.source}
    if not items:
        ctx.warn("the app lists no views, so its tables show all their views")
    if args.view:
        view = resolve.pick("view", args.view, items, context_keys=("table",))
        body = dv.get(f"savedqueries({view['id']})", {"$select": "fetchxml,layoutxml"}) or {}
        where = folder(ctx, app, args.out)
        data["saved"] = [keep(ctx, where / f"view-{view['id']}.{col}.xml", body.get(col) or "", body.get("@odata.etag"))
                         for col in ("fetchxml", "layoutxml")]
        _next_publish(ctx, app, where)
    return data


def sitemap(ctx, args) -> dict:
    app, dv, comps = load(ctx, args.app)
    ids = comps.get(62) or []
    if not ids:
        ctx.warn("the app has no sitemap component")
        return {"app": _app(app), "saved": None, "token_source": dv.source}
    body = dv.get(f"sitemaps({ids[0]})", {"$select": "sitemapname,sitemapxml"}) or {}
    xml = body.get("sitemapxml") or ""
    where = folder(ctx, app, args.out)
    _next_publish(ctx, app, where)
    return {"app": _app(app), "sitemap": {"id": ids[0], "name": body.get("sitemapname")},
            "areas": xml.count("<Area "), "groups": xml.count("<Group "), "subareas": xml.count("<SubArea "),
            "saved": keep(ctx, where / f"sitemap-{ids[0]}.xml", xml, body.get("@odata.etag")),
            "token_source": dv.source}
