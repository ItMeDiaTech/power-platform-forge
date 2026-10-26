"""pa ls and app resolution: canvas apps from the maker module, model-driven apps from Dataverse.

Canvas apps come from `Get-PowerApp` (structured, with connection references, so "which apps read
list X" needs no download); when the maker module is unavailable, from `pac canvas list`, whose
table columns ppx takes from the probe's record of this pac version. Inventories are cached per day
(`--refresh` refetches); `pa index` adds what only the downloaded source shows.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from ppx import cache, pac, ps5, resolve
from ppx.data.dataverse import Dataverse
from ppx.envelope import NextAction
from ppx.errors import PpxError, SetupNeeded, UsageError
from ppx.pa.env import pac_environment

SP_CONNECTOR = "sharepointonline"
DEFAULT_TOP = 50
MAX_SOURCES = 10  # lists and sites shown per app
# Field paths of the documented app resource (Power Platform API "PowerApp": name, properties.*) under the
# cmdlet's Internal object, plus the cmdlet's own top-level names; ppx takes whichever is present.
_PS_APPS = ("$p = @{}; if ($in.env) { $p.EnvironmentName = $in.env }; Get-PowerApp @p | ForEach-Object { "
            "$q = $_.Internal.properties; [pscustomobject]@{ id = $_.Internal.name; app_name = $_.AppName; "
            "display = $q.displayName; display_name = $_.DisplayName; env = $q.environment.name; "
            "env_name = $_.EnvironmentName; modified = [string]$q.lastModifiedTime; "
            "last_modified = [string]$_.LastModifiedTime; owner = $q.owner.userPrincipalName; "
            "refs = $q.connectionReferences } }")
_MDA_SELECT = ("appmoduleid", "appmoduleidunique", "name", "uniquename", "clienttype", "modifiedon")


def index_path(data_dir: Path) -> Path:
    return Path(data_dir) / "cache" / "pa" / "index.json"


def load_index(data_dir: Path) -> dict:
    found = cache.read_json(index_path(data_dir), {})
    return found if isinstance(found, dict) and isinstance(found.get("apps"), dict) else {"apps": {}}


def _first(o: dict, *keys):
    return next((o[k] for k in keys if o.get(k) not in (None, "")), None)


def canvas_item(o: dict) -> dict:
    refs = o.get("refs") or {}
    refs = list(refs.values()) if isinstance(refs, dict) else refs if isinstance(refs, list) else []
    lists, sites, connectors = set(), set(), set()
    for r in (r for r in refs if isinstance(r, dict)):
        connectors.add(str(r.get("displayName") or r.get("id", "")).split("/")[-1])
        if SP_CONNECTOR in str(r.get("id", "")).lower():
            lists.update(str(s) for s in r.get("dataSources") or [])
            for key in ("datasets", "dataSets"):  # site URLs, when the payload carries them
                if isinstance(r.get(key), dict):
                    sites.update(k for k in r[key] if str(k).lower().startswith("https://"))
    return {"id": _first(o, "id", "app_name"), "name": _first(o, "display", "display_name"), "type": "canvas",
            "environment": _first(o, "env", "env_name"), "modified": _first(o, "modified", "last_modified"),
            "owner": o.get("owner"), "connectors": sorted(connectors), "lists": sorted(lists), "sites": sorted(sites)}


def _maker_apps(ctx, environment: str | None) -> list[dict]:
    body = ps5.run(_PS_APPS, ctx.timeout * 3, {"env": environment},
                   attended_lines=[ps5.cmdlet_line("Get-PowerApp", {"EnvironmentName": environment})])
    body = [body] if isinstance(body, dict) else body or []
    return [canvas_item(o) for o in body if isinstance(o, dict)]


def _probe_header(data_dir: Path) -> list[str] | None:
    """The pac canvas list columns the newest probe saw on this computer."""
    for raw in sorted(Path(data_dir).glob("probe/*/raw.json"), reverse=True):
        rec = ((cache.read_json(raw, {}) or {}).get("results") or {}).get("pa", {}).get("pac-canvas-list") or {}
        header = (rec.get("summary") or {}).get("header")
        if rec.get("status") == "ok" and header:
            return header
    return None


def _pac_apps(ctx, environment: str | None) -> list[dict]:
    header = _probe_header(ctx.need_data_dir())
    if not header:
        raise SetupNeeded("ppx reads pac canvas list's table by the columns the probe recorded, and no probe has run",
                          next_actions=[NextAction("record them", command="ppx probe --area pa")])
    env = pac_environment(ctx, environment)
    rows = pac.parse_table(pac.run(["canvas", "list", *(["--environment", env] if env else [])],
                                   timeout=ctx.timeout).stdout, header, command="canvas list")
    col = {w: next((h for h in header if w in h.lower()), None) for w in ("id", "name", "modif")}
    if not col["id"] or not col["name"]:
        raise PpxError(f"the recorded pac canvas list columns {header} have no id or name column")
    return [{"id": r[col["id"]], "name": r[col["name"]], "type": "canvas", "environment": environment,
             "modified": r[col["modif"]] if col["modif"] else None, "connectors": [], "lists": [], "sites": []}
            for r in rows]


def _canvas_apps(ctx, environment):
    try:
        return _maker_apps(ctx, environment), "maker"
    except PpxError as exc:
        try:
            items = _pac_apps(ctx, environment)
        except PpxError as fallback:
            fallback.next_actions += [a for a in exc.next_actions if a not in fallback.next_actions]
            fallback.message = f"{fallback.message} (the maker module failed first: {exc.message})"
            raise fallback from exc
        ctx.warn(f"maker module unavailable ({exc.message}); listed through pac canvas list, which shows no "
                 "data sources: run `ppx pa index` for those")
        return items, "pac"


def _mda_apps(ctx, environment):
    dv = Dataverse(ctx)
    rows = dv.rows("appmodules", "appmodule", _MDA_SELECT)
    return [{"id": r.get("appmoduleid"), "name": r.get("name"), "type": "mda", "unique_name": r.get("uniquename"),
             "id_unique": r.get("appmoduleidunique"), "modified": r.get("modifiedon")} for r in rows], dv.source


def inventory(ctx, kind: str, environment: str | None = None, refresh: bool = False) -> list[dict]:
    data_dir = ctx.need_data_dir()
    key = f"{kind}-{environment or (ctx.profile.name if ctx.profile and kind == 'mda' else 'default')}"
    snap = None if (refresh or ctx.refresh) else cache.latest(data_dir, "pa", key)
    if snap is None:
        items, source = (_canvas_apps if kind == "canvas" else _mda_apps)(ctx, environment)
        if not items and source == "maker":  # Get-PowerApp swallows HTTP errors, so none may mean a failed call
            ctx.warn("the maker module listed no canvas apps; it does not report HTTP errors, so this may be a "
                     "failed call: retry, or compare with `pac canvas list` (not cached)")
            return items
        cache.write_snapshot(data_dir, "pa", key, {"items": items, "source": source})
        return items
    if cache.is_stale(snap):
        ctx.warn(f"{kind} app inventory from {snap.get('fetched_at')}; pass --refresh for a fresh one")
    return snap.get("items") or []


def _pick(kind: str, wanted: str, items: list[dict]) -> dict:
    label = "canvas app" if kind == "canvas" else "model-driven app"
    try:
        return resolve.pick(label, wanted, items, context_keys=("environment",))
    except resolve.NotFound:
        if kind != "mda":
            raise
        return resolve.pick(label, wanted, items, name_key="unique_name")


def find_app(ctx, kind: str, wanted: str, environment: str | None = None) -> dict:
    """One app by id or name; a miss refetches the inventory once."""
    try:
        return _pick(kind, wanted, inventory(ctx, kind, environment))
    except resolve.NotFound:
        if ctx.refresh:
            raise
        return _pick(kind, wanted, inventory(ctx, kind, environment, refresh=True))


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", urllib.parse.unquote(str(text))).lower()


def parse_list_spec(spec: str) -> tuple[str | None, str]:
    """`<site>/Lists/<list>`, `<site>/<list>` or `<list>` as (site or None, list)."""
    spec = urllib.parse.unquote(spec).strip().rstrip("/")
    m = re.match(r"(?i)^(.*?)/lists/([^/]+)$", spec)
    if m:
        return m.group(1).rstrip("/") or None, m.group(2)
    site, _, name = spec.rpartition("/")
    return (site or None), name


def _site_matches(wanted: str, sites: list[str]) -> bool:
    w = wanted.lower().rstrip("/")
    w = w.split("://", 1)[1] if "://" in w else w
    return any((s := str(x).lower().rstrip("/").split("://", 1)[-1]) == w or s.endswith("/" + w) for x in sites)


def _merge_index(items: list[dict], index: dict) -> None:
    for app in items:
        entry = index["apps"].get(str(app.get("id")))
        if entry:
            app["lists"] = sorted(set(app.get("lists") or []) | set(entry.get("lists") or []))
            app["sites"] = sorted(set(app.get("sites") or []) | set(entry.get("sites") or []))
            app["screens"] = entry.get("screens") or []
            app["indexed"] = True


def ls(ctx, args) -> dict:
    if args.environment and args.type == "mda":
        raise UsageError("model-driven apps are listed from the profile's Dataverse environment: drop --environment, "
                         "or switch profiles with `ppx env use <name>`")
    # Only canvas apps read lists, and only canvas apps are listed per environment.
    kinds = [args.type] if args.type else ["canvas"] if args.list or args.environment else ["canvas", "mda"]
    if args.environment and not args.type and not args.list:
        ctx.warn("model-driven apps not listed: they come from the profile's environment; run without --environment")
    items, errors = [], []
    for kind in kinds:
        try:
            items += inventory(ctx, kind, args.environment)
        except PpxError as exc:
            if len(kinds) == 1:
                raise
            errors.append((kind, exc))
    if errors and len(errors) == len(kinds):
        raise errors[0][1]
    for kind, exc in errors:
        ctx.warn(f"{kind} apps not listed: {exc.message}")
        for action in exc.next_actions:
            ctx.next(action.action, action.command, action.url)
    data: dict = {}
    if args.list or args.grep:
        index = load_index(ctx.need_data_dir())
        canvas = [i for i in items if i["type"] == "canvas"]
        _merge_index(canvas, index)
        not_indexed = sum(1 for i in canvas if not i.get("indexed"))
        data["not_indexed"] = not_indexed
        if not_indexed:
            ctx.next(f"{not_indexed} canvas apps are not indexed; index them to search their source as well",
                     command="ppx pa index")
        items = _by_list(canvas, args.list) if args.list else items
        if args.grep:
            g = args.grep.lower()
            items = [i for i in items if any(g in str(v).lower() for k in ("name", "lists", "sites", "screens",
                                                                          "connectors") for v in _flat(i.get(k)))]
    top = ctx.top or DEFAULT_TOP
    keep = {"id", "name", "type", "environment", "modified", "lists", "unique_name", "site_checked"}
    keep |= ({"sites"} if args.list else set()) | set(ctx.fields or ())
    shown = [_shown(i, keep) for i in items[:top]]
    return {"count": len(items), "items": shown, "more": len(items) > top, **data}


def _shown(item: dict, keep: set) -> dict:
    """The kept fields, with long lists and sites cut so one app with many data sources cannot fill 4 KB."""
    out = {k: v for k, v in item.items() if k in keep}
    for k in ("lists", "sites"):
        if len(out.get(k) or []) > MAX_SOURCES:
            out[f"{k}_count"], out[k] = len(out[k]), out[k][:MAX_SOURCES]
    return out


def _flat(value) -> list:
    return value if isinstance(value, list) else [] if value is None else [value]


def _by_list(apps: list[dict], spec: str) -> list[dict]:
    site, name = parse_list_spec(spec)
    out = []
    for app in apps:
        if _norm(name) not in {_norm(x) for x in app.get("lists") or []}:
            continue
        known = app.get("sites") or []
        if site and known and not _site_matches(site, known):
            continue
        out.append({**app, "site_checked": bool(site and known)})
    return out
