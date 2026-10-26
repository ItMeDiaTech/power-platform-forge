"""sp items ls|get: list items one page at a time, and one item, through `m365 spo listitem list`.

https://pnp.github.io/cli-microsoft365/cmd/spo/listitem/listitem-list/: --pageSize with a zero-based
--pageNumber returns one page; --fields with `Column/Sub` expands lookups; the command drops the
duplicate `ID` key and keeps `Id`. SharePoint returns a column whose internal name starts with `_`
under an `OData_` prefix. `spo listitem get` is not used: on v11 it takes --properties without
lookup expansion and silently ignores --fields (its allowUnknownOptions() is true; source at v11.11.0).
For --pageNumber above 0 the command first reads `$top=pageSize*pageNumber` ids to find the page
start (listitem-list.ts at v11.11.0), and SharePoint serves at most 5,000 rows per request.
"""

from __future__ import annotations

import re

from ppx import exports, m365
from ppx.envelope import NextAction
from ppx.errors import PpxError, UsageError
from ppx.sp import fields as fields_mod
from ppx.sp import lists, sites

DEFAULT_FIELDS = ("Id", "Title", "Modified")
MAX_PAGE = 5000
# Plumbing keys of a list item that say nothing about its data.
NOISE = {"FileSystemObjectType", "ServerRedirectedEmbedUri", "ServerRedirectedEmbedUrl", "ContentTypeId",
         "ComplianceAssetId", "GUID", "ID"}
_LITERAL = re.compile(r"'(?:[^']|'')*'")
_COMPARED = re.compile(r"\b([A-Za-z_]\w*)(?:/\w+)?\s+(?:eq|ne|lt|gt|le|ge)\b")
_FUNCTION = re.compile(r"\b(?:startswith|substringof)\(\s*(?:''\s*,\s*)?([A-Za-z_]\w*)", re.I)


def _value(row: dict, name: str):
    if name in row:
        return row[name]
    if name == "ID":
        return row.get("Id")
    return row.get("OData_" + name)


def flatten(row: dict, fields) -> dict:
    """Keep exactly the requested fields; `Author/Title` becomes one key holding the nested value."""
    out = {}
    for name in fields:
        head, _, sub = name.partition("/")
        value = _value(row, head)
        if sub and isinstance(value, list):
            value = [v.get(sub) if isinstance(v, dict) else v for v in value]
        elif sub and isinstance(value, dict):
            value = value.get(sub)
        out[name] = value
    return out


def filter_columns(text: str) -> set[str]:
    """Column names an OData filter compares or passes to startswith/substringof."""
    bare = _LITERAL.sub("''", text)
    return set(_COMPARED.findall(bare)) | set(_FUNCTION.findall(bare))


def _check_filter(ctx, site: str, lst: str, text: str) -> None:
    info = lists.fetch(ctx, site, lst, "Title,ItemCount")
    count = info.get("ItemCount") or 0
    if count <= lists.THRESHOLD:
        return
    cols = {f.get("InternalName", "").lower(): f for f in fields_mod.load(ctx, site, lst)}
    used = [c for c in sorted(filter_columns(text)) if c.lower().removeprefix("odata_") in cols]
    plain = [c for c in used if c.lower() not in ("id", "odata_id")
             and not cols[c.lower().removeprefix("odata_")].get("Indexed")]
    if plain:
        ctx.warn(f"{info.get('Title') or lst} has {count:,} items, over the 5,000-item list view threshold, and "
                 f"--filter uses columns that are not indexed ({', '.join(plain)}): SharePoint refuses such queries. "
                 "Filter on an indexed column (`ppx sp fields ls` marks them), ask the list owner to index "
                 f"{plain[0]}, or read every row with `ppx sp export`")


def ls(ctx, args) -> dict:
    site = sites.resolve(ctx, args.site)
    size = ctx.top or args.page_size
    if not 1 <= size <= MAX_PAGE:
        raise UsageError(f"--page-size (or --top) must be 1 to {MAX_PAGE}, not {size}")
    if args.page < 1:
        raise UsageError(f"--page counts from 1, not {args.page}")
    if size * (args.page - 1) > MAX_PAGE:
        raise UsageError(f"page {args.page} of {size} starts past item {MAX_PAGE:,}, which SharePoint cannot page to; "
                         f"use a larger --page-size, or read every row with "
                         f"`ppx sp export --site \"{site}\" --list \"{args.list}\"`")
    if args.filter:
        _check_filter(ctx, site, args.list, args.filter)
    names = ctx.fields or list(DEFAULT_FIELDS)
    argv = ["spo", "listitem", "list", "--webUrl", site, *lists.ref(args.list, prefix="list"),
            "--fields", ",".join(names), "--pageSize", str(size), "--pageNumber", str(args.page - 1)]
    if args.filter:
        argv += ["--filter", args.filter]
    rows = [flatten(r, names) for r in m365.run(argv, timeout=ctx.timeout) or []]
    more = len(rows) == size
    if more and size * args.page > MAX_PAGE:
        ctx.next("more rows than SharePoint can page to; write them all to a file",
                 command=f'ppx sp export --site "{site}" --list "{args.list}"')
    elif more:
        again = f'ppx sp items ls --site "{site}" --list "{args.list}" --page {args.page + 1} --page-size {size}'
        again += f' --filter "{args.filter}"' if args.filter else ""
        again += f" --fields {','.join(ctx.fields)}" if ctx.fields else ""
        ctx.next("next page", command=again)
    if not ctx.fields:
        ctx.next("pick columns with --fields (internal names)",
                 command=f'ppx sp fields ls --site "{site}" --list "{args.list}"')
    data = {"site": site, "list": args.list, "page": args.page, "count": len(rows), "more": more}
    saved = exports.inline_or_file(ctx, f"sp-items-{args.list}-p{args.page}", rows)
    data.update({"items": saved["body"]} if "body" in saved else saved)
    return data


def get(ctx, args) -> dict:
    site = sites.resolve(ctx, args.site)
    argv = ["spo", "listitem", "list", "--webUrl", site, *lists.ref(args.list, prefix="list"),
            "--filter", f"Id eq {args.id}"]
    if ctx.fields:
        argv += ["--fields", ",".join(ctx.fields)]
    found = m365.run(argv, timeout=ctx.timeout) or []
    if not found:
        raise PpxError(f"no item with id {args.id} in list {args.list}",
                       next_actions=[NextAction("see the list's item ids",
                                                command=f'ppx sp items ls --site "{site}" --list "{args.list}"')])
    row = found[0]
    if ctx.fields:
        item = flatten(row, ctx.fields)
    elif ctx.raw:
        item = row
    else:
        item = {k: v for k, v in row.items() if k not in NOISE and not k.startswith(("OData__", "odata."))}
    saved = exports.inline_or_file(ctx, f"sp-item-{args.list}-{args.id}", item)
    from ppx.sp import items_write  # imported here: items_write imports this module's neighbours

    # Read right after the fields, so `items set --etag` refuses if anyone edits the item in between.
    tag = items_write.etag(ctx, site, args.list, args.id)
    if tag is None:
        ctx.warn("no eTag read (Graph unavailable); `sp items set` will read one itself just before it writes")
    return {"site": site, "list": args.list, "id": args.id, **({"eTag": tag} if tag else {}),
            **({"item": saved["body"]} if "body" in saved else saved)}
