"""sp items add|set|rm: list item writes with the user's own identity.

add and set go through Microsoft Graph with the Tier B token: Create listItem (POST .../items {fields}, 201)
and Update listItem (PATCH .../items/{id}/fields with If-Match: <eTag>; a stale eTag gets 412 Precondition
Failed and nothing changes), https://learn.microsoft.com/en-us/graph/api/listitem-create and .../listitem-update;
the site id comes from GET /sites/{hostname}:/{path} (.../site-getbypath). rm goes through
`m365 spo listitem remove --recycle --force` (https://pnp.github.io/cli-microsoft365/cmd/spo/listitem/listitem-remove/),
so deletes land in the site's recycle bin unless --permanent --yes.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ppx import apis, cache, exports, http, m365
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, UsageError
from ppx.sp import export, lists, sites

BULK = 50              # deleting more items than this needs --yes
DRY_RUN_TTL = timedelta(hours=1)
REMOVE_WORKERS = 4     # each m365 call starts Node; a few side by side
BACKUP_CHUNK = 40      # ids per `Id eq .. or ..` filter when saving items before a permanent delete
_ID_LIST = re.compile(r"\d+(?:\s*,\s*\d+)*")


def backup_folder(data_dir: Path, key: str) -> tuple[str, Path]:
    """A new backup folder <data>/backups/sp/<key>/<UTC timestamp>/ as (timestamp, path)."""
    base = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parent = Path(data_dir) / "backups" / "sp" / (re.sub(r"[^A-Za-z0-9._-]+", "-", key).strip("-") or "item")
    for n in range(1000):
        stamp = base if n == 0 else f"{base}-{n}"
        if not (parent / stamp).exists():
            return stamp, parent / stamp
    raise PpxError(f"too many backups of {key} in one second")


def values(text: str) -> dict:
    """--set: Name=Value (text), a JSON object, or @file.json holding one (for numbers, booleans, null)."""
    text = text.strip()
    if text.startswith("@") or text.startswith("{"):
        try:
            found = json.loads(Path(text[1:]).read_text(encoding="utf-8-sig") if text.startswith("@") else text)
        except (OSError, ValueError) as exc:
            raise UsageError(f"cannot read --set {text[:60]}: {exc}") from exc
        if not isinstance(found, dict) or not found:
            raise UsageError("--set JSON must be one object of column internal names and values")
        return found
    name, eq, value = text.partition("=")
    if not eq or not name.strip():
        raise UsageError("--set takes Name=Value, a JSON object, or @file.json (column internal names)")
    return {name.strip(): value}


def _graph(ctx, url: str, method: str = "GET", **kw):
    return http.call(method, url, audience=apis.GRAPH_RESOURCE, timeout=ctx.timeout,
                     token_provider=sites.graph_tokens(ctx.need_data_dir()), **kw)


def graph_list(ctx, site: str, lst: str) -> tuple[str, dict]:
    """(Graph URL of the list, m365 list info {Title, Id}) for a site URL and a list title, id or URL."""
    info = lists.fetch(ctx, site, lst, "Title,Id")
    if not info.get("Id"):
        raise PpxError(f"m365 returned no id for list {lst}")
    parts = urllib.parse.urlsplit(site)
    found = _graph(ctx, apis.graph().url(f"sites/{parts.hostname}:{urllib.parse.quote(parts.path or '/')}"),
                   params={"$select": "id"}) or {}
    if not found.get("id"):
        raise PpxError(f"Graph returned no id for site {site}")
    return apis.graph().url(f"sites/{found['id']}/lists/{info['Id'].strip('{}')}"), info


def _refused(exc: http.HttpError, what: str) -> PpxError:
    if exc.status in (401, 403):
        return NeedsUser(f"SharePoint refused to {what} ({exc.status}): you need edit permission on the list, and your "
                         "app needs Sites.ReadWrite.All", data={"status": exc.status, "error_code": exc.code},
                         next_actions=[NextAction("see what blocks SharePoint writes",
                                                  command="ppx auth doctor --audience sharepoint")])
    return exc


def add(ctx, args) -> dict:
    site = sites.resolve(ctx, args.site)
    fields = values(args.set)
    base, info = graph_list(ctx, site, args.list)
    title = info.get("Title") or args.list
    ctx.changes.add(f"add an item ({', '.join(fields)})", f"list {title} on {site}",
                    undo=f'ppx sp items rm --site "{site}" --list "{args.list}" --id <new item id>')
    out = {"site": site, "list": title, "fields": list(fields)}
    if ctx.dry_run:
        return out
    try:
        created = _graph(ctx, f"{base}/items", "POST", op="write", json_body={"fields": fields}) or {}
    except http.HttpError as exc:
        raise _refused(exc, "add the item") from None
    ctx.next("undo: move it to the recycle bin", command=f'ppx sp items rm --site "{site}" --list "{args.list}" '
                                                         f"--id {created.get('id')}")
    return {**out, "id": created.get("id"), "eTag": created.get("eTag")}


def etag(ctx, site: str, list_ref: str, item_id) -> str | None:
    """The item's current eTag through Graph, for `items get` to hand to `items set --etag`; None if unreadable."""
    try:
        base, _ = graph_list(ctx, site, list_ref)
        return (_graph(ctx, f"{base}/items/{item_id}", params={"$select": "id,eTag"}) or {}).get("eTag")
    except PpxError:
        return None


def set_(ctx, args) -> dict:
    site = sites.resolve(ctx, args.site)
    fields = values(args.set)
    base, info = graph_list(ctx, site, args.list)
    title = info.get("Title") or args.list
    try:
        item = _graph(ctx, f"{base}/items/{args.id}",
                      params={"$select": "id,eTag", "$expand": f"fields($select={','.join(fields)})"}) or {}
    except http.HttpError as exc:
        if exc.status == 404:
            raise PpxError(f"no item with id {args.id} in list {title}") from None
        raise
    etag = args.etag or item.get("eTag")
    if not etag:
        raise PpxError(f"Graph returned no eTag for item {args.id}, so the update cannot be made safely")
    before = {k: (item.get("fields") or {}).get(k) for k in fields}
    stamp, folder = backup_folder(ctx.need_data_dir(), f"{info['Id'].strip('{}')}-item-{args.id}")
    saved = folder / "fields.json"
    ctx.changes.add(f"set {', '.join(fields)} of item {args.id}", f"list {title} on {site}", backup=str(saved),
                    undo=f'ppx sp items set --site "{site}" --list "{args.list}" --id {args.id} --set "@{saved}"')
    out = {"site": site, "list": title, "id": args.id, "from": before, "to": fields}
    if ctx.dry_run:
        return out
    cache.write_json(saved, before)
    try:
        after = _graph(ctx, f"{base}/items/{args.id}/fields", "PATCH", op="write", json_body=fields,
                       headers={"If-Match": etag}) or {}
    except http.HttpError as exc:
        if exc.status == 412:
            raise NeedsUser(f"item {args.id} changed since it was read (eTag {etag}), so nothing was written; "
                            "refetch it and retry", data={"status": 412},
                            next_actions=[NextAction("refetch the item", command=f'ppx sp items get --site "{site}" '
                                                                                f'--list "{args.list}" --id {args.id}'),
                                          NextAction("then set it again (without --etag, ppx reads the current one)")]
                            ) from None
        raise _refused(exc, "update the item") from None
    return {**out, "to": {k: after.get(k, v) for k, v in fields.items()}, "backup": stamp}


# -- rm -----------------------------------------------------------------------------------------

def _dry_run_record(data_dir: Path, site: str, lst: str, text: str) -> Path:
    key = hashlib.sha256(f"{site.lower()}|{lst.lower()}|{text}".encode()).hexdigest()[:24]
    return cache.folder(data_dir, "sp", "rm-dry-runs") / f"{key}.json"


def _matches(ctx, site: str, args) -> list[dict]:
    if args.id:
        if not _ID_LIST.fullmatch(args.id.strip()):
            raise UsageError("--id takes an item id or a comma-separated list of ids, e.g. 4,7,9")
        return [{"id": int(i)} for i in dict.fromkeys(args.id.replace(" ", "").split(","))]
    rows = m365.run(["spo", "listitem", "list", "--webUrl", site, *lists.ref(args.list, prefix="list"),
                     "--filter", args.filter, "--fields", "Id,Title"],
                    timeout=max(ctx.timeout, export.EXPORT_TIMEOUT)) or []
    return [{"id": r.get("Id"), "title": r.get("Title")} for r in rows if r.get("Id") is not None]


def _check_dry_run(ctx, site: str, args, ids: list[int]) -> None:
    """A filter delete runs only after a dry run within the hour that listed exactly the same items."""
    record = cache.read_json(_dry_run_record(ctx.need_data_dir(), site, args.list, args.filter))
    rerun = NextAction("list what the filter matches now",
                       command=f'ppx sp items rm --site "{site}" --list "{args.list}" --filter "{args.filter}" --dry-run')
    if not isinstance(record, dict) or not record.get("at"):
        raise NeedsUser("a delete by --filter needs a dry run first, so the matched items are seen before they go",
                        next_actions=[rerun])
    when = datetime.fromisoformat(record["at"])
    if datetime.now(timezone.utc) - when > DRY_RUN_TTL:
        raise NeedsUser(f"the dry run for this filter is from {record['at']}, over an hour ago; run it again",
                        next_actions=[rerun])
    if sorted(record.get("ids") or []) != sorted(ids):
        new, gone = set(ids) - set(record.get("ids") or []), set(record.get("ids") or []) - set(ids)
        raise NeedsUser(f"the filter now matches different items than the dry run listed ({len(new)} new, "
                        f"{len(gone)} no longer matching); nothing was deleted. Run the dry run again",
                        data={"new": sorted(new)[:10], "gone": sorted(gone)[:10]}, next_actions=[rerun])


def _save_items(ctx, site: str, args, ids: list[int], folder: Path) -> None:
    """Every field of the items about to be deleted for good, as JSON in the backup folder."""
    rows = []
    for i in range(0, len(ids), BACKUP_CHUNK):
        chunk = " or ".join(f"Id eq {n}" for n in ids[i:i + BACKUP_CHUNK])
        rows += m365.run(["spo", "listitem", "list", "--webUrl", site, *lists.ref(args.list, prefix="list"),
                          "--filter", chunk], timeout=max(ctx.timeout, export.EXPORT_TIMEOUT)) or []
    cache.write_json(folder / "items.json", rows)


def rm(ctx, args) -> dict:
    if bool(args.id) == bool(args.filter):
        raise UsageError("pass --id <id[,id...]> or --filter <OData filter>, not both")
    site = sites.resolve(ctx, args.site)
    matched = _matches(ctx, site, args)
    ids = [m["id"] for m in matched]
    out = {"site": site, "list": args.list, "count": len(ids), "recycle": not args.permanent}
    if not ids:
        ctx.warn("nothing matches, so nothing would be deleted")
        return out
    target = f"list {args.list} on {site}"
    if args.permanent:
        stamp, folder = backup_folder(ctx.need_data_dir(), f"{args.list}-deleted")
        ctx.changes.add(f"permanently delete {len(ids)} item(s), skipping the recycle bin", target, reversible=False,
                        backup=str(folder / "items.json"), undo="re-add them from the backup with ppx sp items add")
    else:
        ctx.changes.add(f"move {len(ids)} item(s) to the site's recycle bin", target, costly=len(ids) > BULK,
                        undo=f"restore them from the recycle bin ({site}/_layouts/15/RecycleBin.aspx)")
    if ctx.dry_run:
        if args.filter:
            cache.write_json(_dry_run_record(ctx.need_data_dir(), site, args.list, args.filter),
                             {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ids": ids})
        if args.permanent or len(ids) > BULK:
            out["needs_yes"] = True
        saved = exports.inline_or_file(ctx, f"sp-rm-{args.list}", matched)
        return {**out, **({"items": saved["body"]} if "body" in saved else saved)}
    if args.filter:
        _check_dry_run(ctx, site, args, ids)
    ctx.changes.require_yes(ctx.yes)
    if args.permanent:
        _save_items(ctx, site, args, ids, folder)
    flags = ["--force"] if args.permanent else ["--recycle", "--force"]

    def remove(item_id: int) -> str | None:
        try:
            m365.run(["spo", "listitem", "remove", "--webUrl", site, *lists.ref(args.list, prefix="list"),
                      "--id", str(item_id), *flags], timeout=ctx.timeout)
            return None
        except PpxError as exc:
            return exc.message

    with ThreadPoolExecutor(REMOVE_WORKERS) as pool:
        errors = list(pool.map(remove, ids))
    failed = [{"id": i, "error": e} for i, e in zip(ids, errors) if e]
    done = len(ids) - len(failed)
    if args.filter:
        _dry_run_record(ctx.data_dir, site, args.list, args.filter).unlink(missing_ok=True)
    if failed:
        raise PpxError(f"deleted {done} of {len(ids)} item(s); {len(failed)} failed: {failed[0]['error']}",
                       data={"removed": done, "failed": failed[:5]})
    if not args.permanent:
        ctx.next("restore from the recycle bin if needed", url=f"{site}/_layouts/15/RecycleBin.aspx")
    return {**out, "removed": done, **({"backup": stamp} if args.permanent else {})}
