"""sp list ls|get: lists and libraries of a site, through `m365 spo list list|get`.

Options and response keys: https://pnp.github.io/cli-microsoft365/cmd/spo/list/list-list/ and
.../list-get/ (CLI for Microsoft 365 v11). Both expand RootFolder for `RootFolder/...` properties.
"""

from __future__ import annotations

import re
import urllib.parse

from ppx import exports, m365
from ppx.resolve import MAX_CANDIDATES
from ppx.sp import sites

THRESHOLD = 5000  # SharePoint Online list view threshold; not changeable in SharePoint Online
_GUID = re.compile(r"\{?[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\}?", re.I)
LS_PROPS = "Title,Id,ItemCount,BaseType,BaseTemplate,LastItemModifiedDate,RootFolder/ServerRelativeUrl"
GET_PROPS = ("Title,Id,Description,ItemCount,BaseType,BaseTemplate,Hidden,Created,LastItemModifiedDate,"
             "EnableVersioning,EnableAttachments,RootFolder/ServerRelativeUrl")


def ref(value: str, *, prefix: str = "") -> list[str]:
    """m365 options naming one list: an id, a server- or site-relative URL (holds a /), else a title.

    prefix "list" gives --listId/--listUrl/--listTitle (listitem, field commands); "" gives
    --id/--url/--title (list get).
    """
    value = value.strip()
    if _GUID.fullmatch(value):
        kind, value = "Id", value.strip("{}")
    elif "/" in value:
        kind, value = "Url", urllib.parse.unquote(value)  # m365 wants it decoded; %20 also breaks .cmd quoting
    else:
        kind = "Title"
    return [f"--{prefix}{kind}" if prefix else f"--{kind.lower()}", value]


def fetch(ctx, site: str, lst: str, props: str) -> dict:
    return m365.run(["spo", "list", "get", "--webUrl", site, *ref(lst), "--properties", props],
                    timeout=ctx.timeout) or {}


def warn_threshold(ctx, title: str, count: int) -> None:
    if count > THRESHOLD:
        ctx.warn(f"{title} has {count:,} items, over SharePoint's 5,000-item list view threshold: filter and sort "
                 "only on indexed columns (`ppx sp fields ls` marks them), or use `ppx sp export` for every row")


def _compact(row: dict) -> dict:
    return {"title": row.get("Title"), "id": row.get("Id"), "kind": "library" if row.get("BaseType") == 1 else "list",
            "template": row.get("BaseTemplate"), "items": row.get("ItemCount"),
            "modified": (row.get("LastItemModifiedDate") or "")[:10] or None,
            "url": (row.get("RootFolder") or {}).get("ServerRelativeUrl")}


def ls(ctx, args) -> dict:
    site = sites.resolve(ctx, args.site)
    rows = m365.run(["spo", "list", "list", "--webUrl", site, "--properties", LS_PROPS, "--filter", "Hidden eq false"],
                    timeout=ctx.timeout) or []
    items = [_compact(r) for r in rows]
    big = [f"{i['title']} ({i['items']:,})" for i in items if (i["items"] or 0) > THRESHOLD]
    if big:
        named = ", ".join(big[:MAX_CANDIDATES]) + (f" and {len(big) - MAX_CANDIDATES} more"
                                                    if len(big) > MAX_CANDIDATES else "")
        ctx.warn(f"over the 5,000-item list view threshold: {named}; filter those on indexed columns only")
    saved = exports.inline_or_file(ctx, "sp-lists", items[:ctx.top])
    return {"site": site, "count": len(items), **({"items": saved["body"]} if "body" in saved else saved)}


def get(ctx, args) -> dict:
    site = sites.resolve(ctx, args.site)
    row = fetch(ctx, site, args.list, GET_PROPS)
    out = {**_compact(row), "description": row.get("Description") or None, "hidden": row.get("Hidden"),
           "created": (row.get("Created") or "")[:10] or None, "versioning": row.get("EnableVersioning"),
           "attachments": row.get("EnableAttachments"), "site": site}
    warn_threshold(ctx, out["title"] or args.list, out["items"] or 0)
    return out
