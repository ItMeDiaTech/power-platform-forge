"""mda publish: write edited sitemap, form and view XML back, add components, then publish once.

Each changed file is compared with the live value first; unchanged files are skipped and every
overwrite is backed up to <data>/backups/mda/<app>/<UTC time>/ under the same file name, so
`mda publish <app> --from <backup folder>` is the undo. One PublishXml covers the whole batch:
the app module, its sitemap and the tables whose forms or views changed (element order from the
publish request schema, learn.microsoft.com/power-apps/developer/model-driven-apps/publish-request-schema).
Needs the System Customizer role; without a Dataverse token every write exits 2 naming the consent request.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from ppx.data.dataverse import Dataverse, is_guid
from ppx.errors import NeedsUser, UsageError
from ppx.mda.apps import _app, save
from ppx.pa.inventory import find_app

_FILE = re.compile(r"^(sitemap|form|view)-([0-9a-fA-F-]{36})(?:\.(fetchxml|layoutxml))?\.xml$")
# kind: (entity set, XML column when the file name has none, name column, table column)
TARGET = {"sitemap": ("sitemaps", "sitemapxml", "sitemapname", None),
          "form": ("systemforms", "formxml", "name", "objecttypecode"),
          "view": ("savedqueries", None, "name", "returnedtypecode")}
ADD = {"view": ("savedqueryid", "Microsoft.Dynamics.CRM.savedquery"),
       "form": ("formid", "Microsoft.Dynamics.CRM.systemform")}


def edits(folder: Path) -> list[dict]:
    """The well-formed XML files in `folder` named as the mda read verbs save them."""
    if not folder.is_dir():
        raise UsageError(f"{folder} is not a folder; save files with `ppx mda sitemap|forms|views <app> --out`")
    found = []
    for path in sorted(folder.glob("*.xml")):
        m = _FILE.match(path.name)
        if not m or (m.group(1) == "view") != bool(m.group(3)):
            continue
        try:  # bytes, not text mode: newlines must compare equal to the live value; -sig drops an editor's BOM
            text = path.read_bytes().decode("utf-8-sig")
            ET.fromstring(text)
        except UnicodeDecodeError as exc:
            raise UsageError(f"{path.name} is not UTF-8 ({exc.reason}); nothing was written") from exc
        except ET.ParseError as exc:
            raise UsageError(f"{path.name} is not well-formed XML ({exc}); nothing was written") from exc
        kind = m.group(1)
        tag = path.with_name(path.name + ".etag")  # written by the save, when Dataverse returned one
        found.append({"kind": kind, "id": m.group(2).lower(), "column": m.group(3) or TARGET[kind][1],
                      "file": path.name, "xml": text,
                      "etag": tag.read_text(encoding="utf-8").strip() if tag.is_file() else None})
    return found


def parameter_xml(app_id: str, sitemaps: list[str], tables: list[str]) -> str:
    parts = []
    if tables:
        parts.append("<entities>" + "".join(f"<entity>{escape(t)}</entity>" for t in sorted(set(tables))) + "</entities>")
    if sitemaps:
        parts.append("<sitemaps>" + "".join(f"<sitemap>{s}</sitemap>" for s in sitemaps) + "</sitemaps>")
    parts.append(f"<appmodules><appmodule>{app_id}</appmodule></appmodules>")
    return "<importexportxml>" + "".join(parts) + "</importexportxml>"


def _adds(spec: str | None) -> list[tuple[str, str]]:
    out = []
    for item in filter(None, (s.strip() for s in (spec or "").split(","))):
        kind, _, oid = item.partition(":")
        if kind not in ADD or not is_guid(oid):
            raise UsageError(f"--add takes view:<id> or form:<id>, comma-separated; not {item!r}")
        out.append((kind, oid.strip().lower()))
    return out


def run(ctx, args) -> dict:
    src = getattr(args, "from")
    files = edits(Path(src)) if src else []
    adds = _adds(args.add)
    app, dv = find_app(ctx, "mda", args.app), Dataverse(ctx)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backups = ctx.need_data_dir() / "backups" / "mda" / str(app["id"]) / stamp
    writes, sitemaps, tables, skipped, changed = [], [], [], [], []
    for f in files:
        entity_set, _, name_col, table_col = TARGET[f["kind"]]
        live = dv.get(f"{entity_set}({f['id']})",
                      {"$select": ",".join(c for c in (f["column"], name_col, table_col) if c)}) or {}
        if (live.get(f["column"]) or "") == f["xml"]:
            skipped.append(f["file"])
            continue
        f["live_etag"] = live.get("@odata.etag")
        if f["etag"] and f["live_etag"] and f["etag"] != f["live_etag"]:
            changed.append(f["file"])
            continue
        ctx.changes.add(f"replace the {f['kind']}'s {f['column']}", f"{f['kind']} {live.get(name_col)} ({f['id']})",
                        backup=str(backups / f["file"]),
                        undo=f'ppx mda publish "{app.get("unique_name") or app["id"]}" --from "{backups}"')
        writes.append((f, entity_set, live.get(f["column"]) or ""))
        if f["kind"] == "sitemap":
            sitemaps.append(f["id"])
        elif live.get(table_col):
            tables.append(str(live[table_col]))
    if changed:
        raise NeedsUser(f"{len(changed)} saved file(s) changed in Dataverse after you saved them, so nothing was "
                        f"written: {', '.join(changed[:5])}; save them again with ppx mda sitemap|forms|views (your "
                        "edited copies are kept as .bak files), re-apply your edits, then publish",
                        data={"changed": changed})
    for kind, oid in adds:
        ctx.changes.add(f"add the {kind} to the app", f"{kind} {oid}",
                        undo="remove it in the app designer, or RemoveAppComponents with the same id")
    # PublishXml publishes everything pending on what it names, other makers' drafts included; no backup undoes that.
    ctx.changes.add("publish; this also publishes any other unpublished changes to the app"
                    + (", its sitemap" if sitemaps else "") + (" and the listed tables" if tables else ""),
                    f"model-driven app {app.get('name')} ({app['id']})"
                    + (f" and tables {', '.join(sorted(set(tables)))}" if tables else ""), reversible=False)
    summary = {"app": _app(app), "writes": len(writes), "unchanged": skipped, "added": len(adds)}
    if ctx.dry_run:
        return summary
    ctx.changes.require_yes(ctx.yes)
    if writes:
        backups.mkdir(parents=True, exist_ok=True)
    for f, _, old in writes:  # every backup exists before the first write
        save(backups / f["file"], old)
    for f, entity_set, _ in writes:
        # The eTag read just above: a change by someone else between that read and this write is refused (412).
        dv.write("PATCH", f"{entity_set}({f['id']})", {f["column"]: f["xml"]},
                 headers={"If-Match": f.get("live_etag") or "*"})
        (Path(src) / (f["file"] + ".etag")).unlink(missing_ok=True)  # this folder's eTag is now out of date
    if adds:
        dv.write("POST", "AddAppComponents", {"AppId": app["id"], "Components": [
            {ADD[kind][0]: oid, "@odata.type": ADD[kind][1]} for kind, oid in adds]})
    dv.write("POST", "PublishXml", {"ParameterXml": parameter_xml(app["id"], sitemaps, tables)})
    return {**summary, "published": True, "backup": str(backups) if writes else None, "token_source": dv.source}
