"""pa index: download canvas app source once and keep what it says (SharePoint lists and sites, screens,
delegation findings) in <data>/cache/pa/index.json, so later questions need no download."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from ppx import cache
from ppx.errors import PpxError
from ppx.pa import inspect, source
from ppx.pa.inventory import find_app, index_path, inventory, load_index


def _folder(data_dir: Path, app_id: str) -> Path:
    return Path(data_dir) / "cache" / "pa" / "src" / (re.sub(r"[^A-Za-z0-9._-]+", "-", app_id)[:80].strip(".-") or "_")


def stale(app: dict, entry: dict | None) -> bool:
    """Missing from the index, or changed since; an app without a modified time always counts as changed."""
    return entry is None or not app.get("modified") or app.get("modified") != entry.get("modified")


def index_one(ctx, app: dict, index: dict) -> dict:
    data_dir = ctx.need_data_dir()
    folder = _folder(data_dir, str(app["id"]))
    msapp = source.download(ctx, app, folder)
    found, info = inspect.findings(folder, app.get("lists") or [])
    entry = {"name": app.get("name"), "modified": app.get("modified"),
             "indexed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "src": str(folder), "msapp": str(msapp), "screens": info["screens"], "sites": info["sites"],
             "lists": sorted(set(app.get("lists") or []) | {n for n, sp in info["data_sources"].items() if sp}),
             "findings": len(found)}
    index["apps"][str(app["id"])] = entry
    cache.write_json(index_path(data_dir), index)
    return entry


def ensure(ctx, wanted: str, environment: str | None = None) -> tuple[dict, dict | None]:
    """The app and its index entry, downloading the source first when it is missing or out of date
    (the entry is None on a dry run that would download)."""
    app = find_app(ctx, "canvas", wanted, environment)
    data_dir = ctx.need_data_dir()
    index = load_index(data_dir)
    entry = index["apps"].get(str(app["id"]))
    if stale(app, entry) or not Path(entry["src"]).is_dir():
        folder = _folder(data_dir, str(app["id"]))
        ctx.changes.add("download the app's source into the ppx index", str(folder), undo=f"delete the folder {folder}")
        if ctx.dry_run:
            return app, None
        entry = index_one(ctx, app, index)
    return app, entry


def run(ctx, args) -> dict:
    data_dir = ctx.need_data_dir()
    apps = inventory(ctx, "canvas", args.environment)
    index = load_index(data_dir)
    if not args.all and apps and not any(a.get("modified") for a in apps):
        ctx.warn("the inventory has no modified times, so every app is downloaded (--stale works like --all)")
    todo = apps if args.all else [a for a in apps if stale(a, index["apps"].get(str(a["id"])))]
    ctx.changes.add(f"download the source of {len(todo)} canvas apps into the local index",
                    str(index_path(data_dir).parent), undo=f"delete {index_path(data_dir).parent}")
    if ctx.dry_run:
        return {"count": len(todo), "up_to_date": len(apps) - len(todo),
                "items": [{"id": a["id"], "name": a.get("name")} for a in todo[:50]]}
    done, failed = 0, []
    for app in todo:
        try:
            index_one(ctx, app, index)
            done += 1
        except PpxError as exc:
            if exc.exit_code == 2:  # a sign-in or setup step fails every remaining download the same way
                exc.data.update({"indexed": done, "remaining": len(todo) - done})
                raise
            failed.append({"id": app["id"], "name": app.get("name"), "error": exc.message[:200]})
    if failed:
        ctx.warn(f"{len(failed)} apps could not be downloaded; see data.failed")
    return {"indexed": done, "up_to_date": len(apps) - len(todo), "failed": failed[:10], "failed_count": len(failed),
            "index": str(index_path(data_dir)), "apps_in_index": len(index["apps"])}
