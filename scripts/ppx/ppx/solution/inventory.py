"""ppx solution ls and export: the environment's solutions, cached per environment, and zips of one."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ppx import cache, pac
from ppx.errors import PpxError, UsageError
from ppx.resolve import NotFound, pick
from ppx.solution import common

AREA = "solution"
# shortcut: Learn documents `pac solution list --json` but not its field names; these are matched
# case-insensitively, and the probe's pac-solution-list check records the real ones on the work laptop.
FIELDS = {"name": ("solutionuniquename", "uniquename"), "display": ("friendlyname", "displayname"),
          "version": ("versionnumber", "version"), "managed": ("ismanaged", "managed")}


def _row(raw: dict) -> dict:
    low = {str(k).lower(): v for k, v in raw.items()}
    return {field: next((low[k] for k in keys if low.get(k) not in (None, "")), None) for field, keys in FIELDS.items()}


def inventory(ctx, refresh: bool = False) -> tuple[list[dict], bool]:
    """(solutions, fetched_now). Served from today's cache unless `refresh`; stale caches warn."""
    data_dir = ctx.need_data_dir()
    key = (ctx.profile.url if ctx.profile else None) or "pac-active-environment"
    cached = None if refresh else cache.latest(data_dir, AREA, key)
    if cached is not None:
        if cache.is_stale(cached):
            ctx.warn(f"solution list cached at {cached.get('fetched_at')} (over 24 h old); pass --refresh for a fresh one")
        return cached.get("items") or [], False
    raw = pac.run(["solution", "list", *common.env_args(ctx)], json_out=True, timeout=ctx.timeout)
    if not isinstance(raw, list):
        raise PpxError(f"pac solution list --json returned a {type(raw).__name__}, not a list; "
                       f"ppx was written for pac {pac.PINNED}")
    items = [_row(r) for r in raw if isinstance(r, dict)]
    if items and not any(i["name"] for i in items):
        keys = ", ".join(sorted(next(r for r in raw if isinstance(r, dict))))[:200]
        raise PpxError(f"pac solution list --json rows have no unique-name field (keys: {keys}); "
                       f"ppx was written for pac {pac.PINNED}, so the output format probably changed")
    cache.write_snapshot(data_dir, AREA, key, {"items": items})
    return items, True


def find(ctx, wanted: str) -> dict:
    """One solution by unique name or display name; a miss refreshes the cached list once."""
    items, fresh = inventory(ctx, ctx.refresh)
    try:
        return pick("solution", wanted, items, id_key="name", name_key="display")
    except NotFound:
        if fresh:
            raise
        return pick("solution", wanted, inventory(ctx, True)[0], id_key="name", name_key="display")


def ls(ctx, args) -> dict:
    items, _ = inventory(ctx, ctx.refresh)
    return {"count": len(items), "items": items[:ctx.top] if ctx.top else items}


def export(ctx, args) -> dict:
    solution = find(ctx, args.name)
    name = solution["name"]
    if args.out:
        out = Path(args.out).expanduser().resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = ctx.need_data_dir() / "exports" / "solutions" / stamp / f"{name}.zip"
    if out.suffix.lower() != ".zip":
        raise UsageError(f"--out must end in .zip: {out}")
    targets = [(out, False)] + ([(common.managed_sibling(out), True)] if args.managed else [])
    existing = [str(p) for p, _ in targets if p.exists()]
    if existing:
        raise UsageError(f"{', '.join(existing)} already exists; choose another --out (ppx never overwrites an export)")
    ctx.changes.add(f"export solution {name} to {', '.join(p.name for p, _ in targets)}", str(out.parent))
    if ctx.dry_run:
        return {"solution": name, "version": solution.get("version"), "files": [str(p) for p, _ in targets]}
    out.parent.mkdir(parents=True, exist_ok=True)
    files = []
    for path, managed in targets:
        # --async avoids the synchronous export timeout on large solutions; pac still waits for the result.
        pac.run(["solution", "export", "--name", name, "--path", str(path), "--async",
                 *(["--managed"] if managed else []), *common.env_args(ctx)], timeout=common.long_timeout(ctx))
        if not path.is_file():
            raise PpxError(f"pac solution export reported success but wrote no file at {path}")
        files.append({"path": str(path), "managed": managed, "bytes": path.stat().st_size})
    if args.managed:
        ctx.next("unpack both packages for source control",
                 command=f'ppx solution unpack --zip "{out}" --folder <repo>/solutions/{name}')
    else:
        ctx.next("to unpack for source control, export with --managed (unpack needs both packages)",
                 command=f"ppx solution export --name {name} --managed")
    return {"solution": name, "version": solution.get("version"), "files": files}
