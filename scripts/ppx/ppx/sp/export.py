"""sp export: every row of a list to a CSV or JSON file; the envelope carries the path and counts only.

Without --pageSize, `m365 spo listitem list` follows SharePoint's paging itself and returns every item,
including lists over 5,000 (https://pnp.github.io/cli-microsoft365/cmd/spo/listitem/listitem-list/).
Columns are the ones `sp fields ls` shows; person and lookup columns are expanded to their display
value unless that would pass SharePoint's 12-join limit.
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from ppx import m365
from ppx.errors import PpxError
from ppx.sp import fields, items, lists, sites

EXPORT_TIMEOUT = 600  # one call reads the whole list; big lists take minutes
_DISPLAY_KEYS = ("Title", "Label", "Url", "Value", "EMail")


def _columns(ctx, site: str, lst: str) -> tuple[list[str], list[str]]:
    """(select paths for m365 --fields, CSV headers) from the list's visible columns."""
    cols = [f for f in fields.load(ctx, site, lst) if fields.shown(f)]
    lookups = sum(f.get("TypeAsString") in fields.LOOKUP_TYPES for f in cols)
    expand = lookups <= fields.LOOKUP_LIMIT
    if not expand:
        ctx.warn(f"{lookups} lookup and person columns pass SharePoint's {fields.LOOKUP_LIMIT}-join limit, so they "
                 "are exported as item ids (<Column>Id); pass --fields to choose up to 12 to expand")
    select, headers = [], []
    for f in cols:
        name = "Id" if f.get("InternalName") == "ID" else f.get("InternalName")
        kind = f.get("TypeAsString")
        if kind in ("User", "UserMulti"):
            name = f"{name}/Title" if expand else f"{name}Id"
        elif kind in ("Lookup", "LookupMulti"):
            name = f"{name}/{f.get('LookupField') or 'Title'}" if expand else f"{name}Id"
        header = f.get("Title") or name
        headers.append(header if header not in headers else f.get("InternalName"))
        select.append(name)
    return select, headers


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(_cell(v) for v in value)
    if isinstance(value, dict):
        key = next((k for k in _DISPLAY_KEYS if k in value), None)
        return _cell(value[key]) if key else json.dumps(value, ensure_ascii=False)
    return str(value)


def _target(ctx, args, site: str) -> Path:
    day = datetime.now(timezone.utc).date().isoformat()
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{sites.slug(site)}-{args.list}-{day}").strip("-") + f".{args.format}"
    if args.out:
        out = Path(args.out).expanduser()
        return out / name if out.is_dir() else out
    return ctx.need_data_dir() / "exports" / name


def _write(out: Path, fmt: str, headers: list[str], select: list[str], rows: list[dict]) -> None:
    tmp = out.with_name(f".{out.name}.tmp")  # a failed write never leaves half a file where the old one was
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "csv":
            with tmp.open("w", encoding="utf-8-sig", newline="") as f:  # the BOM makes Excel read UTF-8
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerows([_cell(r[s]) for s in select] for r in rows)
        else:
            tmp.write_text(json.dumps([{h: r[s] for h, s in zip(headers, select)} for r in rows],
                                      ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, out)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise PpxError(f"cannot write {out}: {exc}") from exc


def run(ctx, args) -> dict:
    started = time.monotonic()
    site = sites.resolve(ctx, args.site)
    out = _target(ctx, args, site)
    # A same-day re-export into ppx's own exports folder replaces the earlier one; a file the user named does not.
    if args.out and out.exists():
        ctx.changes.add("overwrite a local file", str(out), reversible=False)
        ctx.changes.require_yes(ctx.yes)
    if ctx.dry_run:
        return {"site": site, "list": args.list, "path": str(out)}
    select, headers = (list(ctx.fields), list(ctx.fields)) if ctx.fields else _columns(ctx, site, args.list)
    if not select:
        raise PpxError(f"{args.list} has no visible columns to export; pass --fields")
    rows = m365.run(["spo", "listitem", "list", "--webUrl", site, *lists.ref(args.list, prefix="list"),
                     "--fields", ",".join(select)], timeout=max(ctx.timeout, EXPORT_TIMEOUT)) or []
    _write(out, args.format, headers, select, [items.flatten(r, select) for r in rows])
    return {"path": str(out), "rows": len(rows), "columns": len(select), "format": args.format,
            "elapsed": round(time.monotonic() - started, 1)}
