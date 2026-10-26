"""Workspaces and items for every pbi verb: the cached inventory, name resolution and capacity.

Inventories are dated snapshots (ppx.cache, area "pbi", one key per workspace id). A verb answers
from the newest one unless --refresh is given or there is none, and a name that is not found
triggers one fresh fetch of that workspace before the verb gives up. "My workspace" has no group
id in Power BI REST: its id here is "me" and its paths drop the groups/{id} prefix.

Power BI REST is the source; Fabric REST (one paged item list per workspace) is the second source
when Power BI REST refuses the token or the caller.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ppx import apis, cache, http
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, UsageError
from ppx.resolve import NotFound, pick

AREA = "pbi"
WORKSPACES_KEY = "_workspaces"
ME = "me"
MY_WORKSPACE = {"id": ME, "name": "My workspace", "onCapacity": False}
TYPES = {"reports": "report", "models": "model", "dashboards": "dashboard", "dataflows": "dataflow"}
FABRIC_TYPES = {"Report": "report", "PaginatedReport": "report", "SemanticModel": "model",
                "Dashboard": "dashboard", "Dataflow": "dataflow"}
SERVICE = "https://app.powerbi.com"
HISTORY_WORKERS = 8  # concurrent last-refresh reads while building an inventory
PAGES_SEEN = "pages.json"  # last page count report show saw per report, beside the dated snapshots
# Characters that neither Bash nor PowerShell expand inside double quotes; no leading dash.
_INERT = re.compile(r"\w[\w .()&'-]*")


def url(ws: dict, path: str) -> str:
    """Power BI REST URL of `path` inside workspace `ws`."""
    return apis.pbi().url(("" if ws["id"] == ME else f"groups/{ws['id']}/") + path)


def get(ctx, ws: dict, path: str, params: dict | None = None) -> dict:
    return http.call("GET", url(ws, path), audience=apis.PBI_RESOURCE, params=params, timeout=ctx.timeout) or {}


def on_capacity(ws: dict) -> bool:
    """True for Premium, PPU, Embedded or Fabric capacity; False for shared (Pro) capacity."""
    return bool(ws.get("onCapacity"))


def settings_url(ws: dict, model_id: str) -> str:
    return f"{SERVICE}/groups/{ws['id']}/settings/datasets/{model_id}"


def shell_arg(item: dict) -> str:
    """The item's name in double quotes when no shell can expand any of it, else its id.

    Names are set by any coworker and end up in next_actions commands an agent runs, so a name
    holding $, a backtick, a backslash, a quote or similar is never emitted.
    """
    name = item.get("name")
    return f'"{name}"' if isinstance(name, str) and _INERT.fullmatch(name) else item["id"]


def command(verb: str, model: dict, ws: dict, extra: str = "") -> str:
    """A ppx command line naming the model and workspace (see shell_arg)."""
    return f"ppx pbi {verb} --model {shell_arg(model)} --workspace {shell_arg(ws)}{extra}"


def _refused(exc: PpxError) -> bool:
    """Power BI REST said no to this token or caller (as opposed to a fault worth reporting)."""
    return getattr(exc, "status", None) in (401, 403) or (isinstance(exc, NeedsUser)
                                                            and exc.data.get("audience") == "pbi")


def _load(ctx, key: str, fetch, what: str, fresh: bool = False) -> tuple[dict, bool]:
    """Newest snapshot under `key`, or a new one; returns (payload, fetched_now)."""
    data_dir = ctx.need_data_dir()
    if not (fresh or ctx.refresh):
        snap = cache.latest(data_dir, AREA, key)
        if isinstance(snap, dict) and isinstance(snap.get("items"), list):
            if cache.is_stale(snap):
                ctx.warn(f"{what} is from {snap.get('fetched_at', 'an unknown time')}; pass --refresh for current data")
            return snap, False
    snap = fetch()
    return cache.read_json(cache.write_snapshot(data_dir, AREA, key, snap), snap), True


# -- workspaces ---------------------------------------------------------------------------------

def _fetch_workspaces(ctx) -> dict:
    try:
        groups, _ = http.paged(apis.pbi().url("groups"), audience=apis.PBI_RESOURCE, timeout=ctx.timeout)
        found = [{"id": g["id"], "name": g.get("name"), "onCapacity": bool(g.get("isOnDedicatedCapacity")),
                  **({"capacityId": g["capacityId"]} if g.get("capacityId") else {}),
                  **({"readOnly": True} if g.get("isReadOnly") else {})} for g in groups]
        source = "pbi"
    except PpxError as exc:
        if not _refused(exc):
            raise
        try:
            spaces, _ = http.paged(apis.fabric().url("workspaces"), audience=apis.FABRIC_RESOURCE, timeout=ctx.timeout)
        except PpxError:
            raise exc from None
        # My workspace is always the synthetic "me" entry; Fabric's Personal one would duplicate it.
        found = [{"id": w["id"], "name": w.get("displayName"), "onCapacity": bool(w.get("capacityId")),
                  **({"capacityId": w["capacityId"]} if w.get("capacityId") else {})}
                 for w in spaces if w.get("type") == "Workspace"]
        source = "fabric"
        ctx.warn(f"Power BI REST refused the workspace list ({exc.message}); listed through Fabric instead")
    return {"source": source, "items": [MY_WORKSPACE, *found]}


def workspaces(ctx, fresh: bool = False) -> tuple[list[dict], bool]:
    snap, fetched = _load(ctx, WORKSPACES_KEY, lambda: _fetch_workspaces(ctx), "the workspace list", fresh)
    return snap["items"], fetched


def workspace(ctx, wanted: str | None) -> dict:
    """The workspace named or given by id, or the profile's default one."""
    wanted = wanted or (ctx.profile.workspace if ctx.profile else None)
    if not wanted:
        raise UsageError("pass --workspace <name|id>, or set a default with `ppx env set --workspace <name>`",
                         next_actions=[NextAction("list the workspaces", command="ppx pbi ws ls")])
    if wanted.strip().lower() in (ME, "my workspace"):
        return MY_WORKSPACE
    items, fetched = workspaces(ctx)
    try:
        return pick("workspace", wanted, items)
    except NotFound:
        if fetched:
            raise
    items, _ = workspaces(ctx, fresh=True)
    return pick("workspace", wanted, items)


# -- items --------------------------------------------------------------------------------------

def _report(r: dict) -> dict:
    return {"id": r["id"], "name": r.get("name"), "type": "report", "modelId": r.get("datasetId"),
            "format": r.get("format"), "reportType": r.get("reportType"), "isOwnedByMe": r.get("isOwnedByMe")}


def _model(m: dict) -> dict:
    return {"id": m["id"], "name": m.get("name"), "type": "model", "configuredBy": m.get("configuredBy"),
            "isRefreshable": m.get("isRefreshable"), "createdDate": m.get("createdDate"),
            "targetStorageMode": m.get("targetStorageMode")}


def _dashboard(d: dict) -> dict:
    return {"id": d["id"], "name": d.get("displayName"), "type": "dashboard", "isReadOnly": d.get("isReadOnly")}


def _dataflow(d: dict) -> dict:
    return {"id": d["objectId"], "name": d.get("name"), "type": "dataflow", "configuredBy": d.get("configuredBy")}


def _items_pbi(ctx, ws: dict) -> list[dict]:
    def listing(path: str) -> list[dict]:
        found, _ = http.paged(url(ws, path), audience=apis.PBI_RESOURCE, timeout=ctx.timeout)
        return found

    items = [_report(r) for r in listing("reports")]
    models = [_model(m) for m in listing("datasets")]
    for kind, path, shape in (("dashboards", "dashboards", _dashboard), ("dataflows", "dataflows", _dataflow)):
        if kind == "dataflows" and ws["id"] == ME:
            continue  # dataflows cannot live in My workspace and have no endpoint there
        try:
            items += [shape(x) for x in listing(path)]
        except PpxError as exc:  # the user's own app may lack the Dashboard or Dataflow scope
            if not _refused(exc):
                raise
            ctx.warn(f"{kind} not listed: {exc.message}")

    def last_refresh(m: dict) -> list | str | None:
        try:
            return get(ctx, ws, f"datasets/{m['id']}/refreshes", {"$top": 1}).get("value") or []
        except http.HttpError as exc:  # the listing still stands either way
            # 401/403: history needs write permission. Anything else (429 after retries, 5xx) is not a
            # permission answer and must not be cached as one.
            return None if exc.status in (401, 403) else "failed"

    # One history call per model and no batch read: run them side by side so big workspaces stay fast.
    refreshable = [m for m in models if m.get("isRefreshable")]
    with ThreadPoolExecutor(max_workers=HISTORY_WORKERS) as pool:
        found = list(pool.map(last_refresh, refreshable))
    unread = failed = 0
    for m, latest in zip(refreshable, found):
        if latest is None:
            unread += 1
        elif latest == "failed":
            failed += 1
            m["lastStatus"] = "not read (throttled or a service error); pass --refresh to retry"
        elif latest:
            m["lastRefresh"] = latest[0].get("endTime") or latest[0].get("startTime")
            m["lastStatus"] = latest[0].get("status")
    if unread:
        ctx.warn(f"last refresh unknown for {unread} model(s): refresh history needs write permission on the model")
    if failed:
        ctx.warn(f"last refresh not read for {failed} model(s) because the service throttled or failed; "
                 "pass --refresh later to retry")
    return [{k: v for k, v in i.items() if v is not None} for i in items + models]


def _items_fabric(ctx, ws: dict) -> list[dict]:
    found, _ = http.paged(apis.fabric().url(f"workspaces/{ws['id']}/items"), audience=apis.FABRIC_RESOURCE,
                          timeout=ctx.timeout)
    return [{"id": i["id"], "name": i.get("displayName"), "type": FABRIC_TYPES[i.get("type")]}
            for i in found if i.get("type") in FABRIC_TYPES]


def _fetch_items(ctx, ws: dict) -> dict:
    try:
        items, source = _items_pbi(ctx, ws), "pbi"
    except PpxError as exc:
        if ws["id"] == ME or not _refused(exc):
            raise
        try:
            items, source = _items_fabric(ctx, ws), "fabric"
        except PpxError:
            raise exc from None
        ctx.warn(f"Power BI REST refused the item list ({exc.message}); listed through Fabric instead, "
                 "without owners, formats or refresh status")
    # Page counts come only from `pbi report show`; carry the last known ones so diff can compare them.
    pages = {i["id"]: i["pages"] for i in cached_items(ctx, ws) if "pages" in i} | _seen_pages(ctx, ws)
    for i in items:
        if i["id"] in pages:
            i.setdefault("pages", pages[i["id"]])
    return {"workspace": ws, "source": source, "items": items}


def inventory(ctx, ws: dict, fresh: bool = False) -> tuple[dict, bool]:
    """The workspace's snapshot {workspace, source, items, fetched_at}, and whether it was fetched now."""
    return _load(ctx, ws["id"], lambda: _fetch_items(ctx, ws), f"the inventory of {ws['name']}", fresh)


def resolve(ctx, kind: str, wanted: str, workspace_name: str | None = None) -> tuple[dict, dict]:
    """(workspace, item) for a report, model, dashboard or dataflow given by name or id.

    Without a workspace (argument or profile default) every cached inventory is searched; with
    one, a miss refreshes that workspace's inventory once before reporting not found.
    """
    if not (workspace_name or (ctx.profile and ctx.profile.workspace)):
        return _resolve_cached(ctx, kind, wanted)
    ws = workspace(ctx, workspace_name)
    where = f" in workspace {ws['name']}"
    snap, fetched = inventory(ctx, ws)
    try:
        return ws, pick(kind, wanted, [i for i in snap["items"] if i.get("type") == kind], where=where)
    except NotFound:
        if fetched:
            raise
    snap, _ = inventory(ctx, ws, fresh=True)
    return ws, pick(kind, wanted, [i for i in snap["items"] if i.get("type") == kind], where=where)


def _resolve_cached(ctx, kind: str, wanted: str) -> tuple[dict, dict]:
    spaces, _ = workspaces(ctx)
    by_id = {w["id"]: w for w in spaces}
    pool = []
    for ws in spaces:
        snap = cache.latest(ctx.need_data_dir(), AREA, ws["id"])
        if isinstance(snap, dict):
            pool += [{**i, "workspace": ws["name"], "_ws": ws["id"]} for i in snap.get("items", [])
                     if i.get("type") == kind]
    try:
        item = pick(kind, wanted, pool, where=" in the cached inventories", context_keys=("workspace",))
    except NotFound as exc:
        exc.next_actions.append(NextAction("name the workspace so ppx can fetch its inventory",
                                           command=f'ppx pbi ls --workspace "<workspace>" --type {kind}s'))
        raise
    ws = by_id[item.pop("_ws")]
    item.pop("workspace")
    return ws, item


def cached_items(ctx, ws: dict) -> list[dict]:
    snap = cache.latest(ctx.need_data_dir(), AREA, ws["id"])
    return snap.get("items", []) if isinstance(snap, dict) else []


def _seen_pages(ctx, ws: dict) -> dict:
    seen = cache.read_json(cache.folder(ctx.need_data_dir(), AREA, ws["id"]) / PAGES_SEEN, {})
    return seen if isinstance(seen, dict) else {}


def remember(ctx, ws: dict, item_id: str, pages: int) -> None:
    """Record a report's page count in pages.json (later snapshots carry it) and in today's snapshot.

    A past snapshot is never rewritten: it is the baseline pbi diff compares with.
    """
    folder = cache.folder(ctx.need_data_dir(), AREA, ws["id"])
    cache.write_json(folder / PAGES_SEEN, {**_seen_pages(ctx, ws), item_id: pages})
    today = folder / f"{datetime.now(timezone.utc).date().isoformat()}.json"
    snap = cache.read_json(today)
    if not isinstance(snap, dict) or not isinstance(snap.get("items"), list):
        return
    for item in snap["items"]:
        if item.get("id") == item_id:
            item["pages"] = pages
    cache.write_json(today, snap)


def listing(ctx, items: list, default_top: int) -> dict:
    """{count, items} cut to --top (or `default_top` unless --raw), saying how many were left out."""
    top = ctx.top if ctx.top is not None else (None if ctx.raw else default_top)
    shown = items if top is None else items[:max(top, 0)]
    out = {"count": len(items), "items": shown}
    if len(shown) < len(items):
        out["more"] = len(items) - len(shown)
        ctx.next(f"{out['more']} more not shown: raise --top, narrow with --type, or keep fewer fields with --fields")
    return out


def parse_time(value) -> datetime | None:
    """A Power BI UTC timestamp (2017-06-13T09:25:43.153Z) as an aware datetime, or None."""
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
