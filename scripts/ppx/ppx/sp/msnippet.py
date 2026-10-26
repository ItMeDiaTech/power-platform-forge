"""sp m-snippet: a SharePoint list as a TMDL table whose partition reads it through the 2.0 connector.

The partition is `SharePoint.Tables(site, [Implementation="2.0", ViewMode=...])` (Learn, SharePoint.Tables,
https://learn.microsoft.com/en-us/powerquery-m/sharepoint-tables: ViewMode "All" or "Default", 2.0 only),
navigated to the list by its id, then Table.SelectColumns with MissingField.UseNull so every declared
column exists even when the Default view leaves it out, then typed. TMDL syntax (tab indentation, quoted
names, `partition X = m` with a multi-line `source =`): https://learn.microsoft.com/en-us/analysis-services/tmdl/tmdl-overview.
The 2.0 connector names columns by their display names (an inference from Desktop output, to confirm on
the first live refresh); person, lookup, multi-value, link and taxonomy columns hold records or lists that
need expanding in Power Query, so they are left out and listed.
"""

from __future__ import annotations

import re
from pathlib import Path
from string import Template

from ppx import exports
from ppx.errors import PpxError, UsageError
from ppx.sp import fields as fields_mod
from ppx.sp import lists, sites

TEMPLATE = Path(__file__).resolve().parents[4] / "templates" / "tmdl" / "sharepoint-table.tmdl"
# SharePoint column type -> (TMDL dataType, M type, summarizeBy)
TYPES = {
    "Text": ("string", "type text", "none"), "Note": ("string", "type text", "none"),
    "Choice": ("string", "type text", "none"), "Calculated": ("string", "type text", "none"),
    "Number": ("double", "type number", "sum"), "Currency": ("decimal", "Currency.Type", "sum"),
    "Integer": ("int64", "Int64.Type", "sum"), "Counter": ("int64", "Int64.Type", "none"),
    "DateTime": ("dateTime", "type datetime", "none"), "Boolean": ("boolean", "type logical", "none"),
}
PBISM = ('{\n  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/'
         '1.0.0/schema.json",\n  "version": "5.0",\n  "settings": {\n    "qnaEnabled": false\n  }\n}\n')
MODEL = "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\tsourceQueryCulture: en-US\n"
DATABASE = "database\n\tcompatibilityLevel: 1567\n"
_PLAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def m_text(value: str) -> str:
    """An M text literal."""
    return '"' + value.replace('"', '""') + '"'


def tmdl_name(value: str) -> str:
    """A TMDL object name, single-quoted unless it is a plain identifier."""
    return value if _PLAIN.fullmatch(value) else "'" + value.replace("'", "''") + "'"


def columns(cols: list[dict]) -> tuple[list[dict], list[dict]]:
    """(typed columns, skipped columns with the reason) from `sp fields ls` rows of the visible columns."""
    out, skipped, seen = [], [], set()
    for f in cols:
        kind, name = f.get("TypeAsString"), f.get("Title") or f.get("InternalName")
        if not name:
            continue
        if kind not in TYPES:
            skipped.append({"column": name, "type": kind, "why": "holds a record or list; expand it in Power Query"})
        elif name.lower() in seen:
            skipped.append({"column": name, "type": kind, "why": "another column has the same display name"})
        else:
            seen.add(name.lower())
            data_type, m_type, summarize = TYPES[kind]
            out.append({"name": name, "dataType": data_type, "m_type": m_type, "summarizeBy": summarize})
    return out, skipped


def render(site: str, list_title: str, list_id: str, cols: list[dict], view: str = "Default") -> str:
    """The TMDL table file for the list, from templates/tmdl/sharepoint-table.tmdl."""
    if not cols:
        raise UsageError(f"list {list_title} has no column ppx can type; nothing to model")
    try:
        template = Template(TEMPLATE.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PpxError(f"the TMDL template is missing from the plugin ({TEMPLATE}): {exc}") from exc
    blocks = "".join(f"\tcolumn {tmdl_name(c['name'])}\n\t\tdataType: {c['dataType']}\n\t\tsummarizeBy: "
                     f"{c['summarizeBy']}\n\t\tsourceColumn: \"{c['name'].replace(chr(34), chr(34) * 2)}\"\n\n"
                     for c in cols)
    return template.substitute(
        table=tmdl_name(list_title), columns=blocks, partition=tmdl_name(list_title), site=m_text(site.rstrip("/")),
        view=m_text(view), list_id=m_text(list_id.strip("{}").lower()),
        names=", ".join(m_text(c["name"]) for c in cols),
        types=", ".join(f"{{{m_text(c['name'])}, {c['m_type']}}}" for c in cols))


def file_name(name: str) -> str:
    """A table's file name under definition/tables/ (no separators or reserved characters)."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", name).strip(" .") or "table"


def model_files(table_title: str, table_tmdl: str) -> dict[str, str]:
    """The semantic model definition parts (Fabric TMDL format) holding one table, by part path."""
    return {"definition.pbism": PBISM, "definition/database.tmdl": DATABASE, "definition/model.tmdl": MODEL,
            f"definition/tables/{file_name(table_title)}.tmdl": table_tmdl}


def load(ctx, site: str, lst: str) -> tuple[dict, list[dict], list[dict]]:
    """(list {Title, Id, ItemCount}, typed columns, skipped columns) of one list."""
    info = lists.fetch(ctx, site, lst, "Title,Id,ItemCount")
    if not info.get("Id"):
        raise PpxError(f"m365 returned no id for list {lst}")
    cols, skipped = columns([f for f in fields_mod.load(ctx, site, lst) if fields_mod.shown(f)])
    return info, cols, skipped


def warn_view(ctx, view: str, skipped: list[dict]) -> None:
    if view == "Default":
        ctx.warn("ViewMode Default returns the columns of the list's default view only; the others load as empty "
                 "(use --view All to read every column)")
    if skipped:
        ctx.warn(f"{len(skipped)} column(s) left out because they hold records or lists: "
                 + ", ".join(s["column"] for s in skipped[:5]) + (" and more" if len(skipped) > 5 else ""))


def run(ctx, args) -> dict:
    site = sites.resolve(ctx, args.site)
    info, cols, skipped = load(ctx, site, args.list)
    text = render(site, info.get("Title") or args.list, info["Id"], cols, args.view)
    warn_view(ctx, args.view, skipped)
    out = {"site": site, "list": info.get("Title"), "view": args.view, "columns": len(cols),
           **({"skipped": skipped[:10]} if skipped else {})}
    if args.out:
        target = Path(args.out).expanduser()
        target = target / f"{file_name(info.get('Title') or args.list)}.tmdl" if target.is_dir() else target
        if target.exists():
            ctx.changes.add("overwrite a local file", str(target), reversible=False)
            ctx.changes.require_yes(ctx.yes)
        if ctx.dry_run:
            return {**out, "path": str(target)}
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise PpxError(f"cannot write {target}: {exc}") from exc
        return {**out, "path": str(target)}
    if ctx.raw or len(text) <= exports.INLINE_LIMIT or ctx.data_dir is None:
        return {**out, "tmdl": text}
    return {**out, "saved_to": str(exports.save(ctx.data_dir, f"tmdl-{info.get('Title')}", text, ".tmdl"))}
