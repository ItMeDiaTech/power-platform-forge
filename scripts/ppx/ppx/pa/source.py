"""pa get: a canvas app's .msapp and its read-only Src/*.pa.yaml, via pac canvas download."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from ppx import pac, ps5
from ppx.errors import PpxError, UsageError
from ppx.pa import inspect
from ppx.pa.env import pac_environment
from ppx.pa.inventory import find_app


def download(ctx, app: dict, folder: Path) -> Path:
    """Download `app` into `folder` (app.msapp plus the extracted source) and return the .msapp path."""
    folder.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(folder / "src", ignore_errors=True)  # screens removed in Studio must not linger from the last download
    msapp = folder / "app.msapp"
    env = pac_environment(ctx, app.get("environment"))
    pac.run(["canvas", "download", "--name", str(app["id"]), "--file-name", str(msapp),
             "--extract-to-directory", str(folder / "src"), "--overwrite", *(["--environment", env] if env else [])],
            timeout=ctx.timeout * 3)
    # pac's exit code is not the whole story: an index built on an empty download would hide the app until it changes.
    if not msapp.is_file() or not any((folder / "src").rglob("*.pa.yaml")):
        raise PpxError(f"pac canvas download reported success for {app.get('name') or app['id']} but wrote no "
                       f"{'app.msapp' if not msapp.is_file() else '.pa.yaml source'} into {folder}")
    return msapp


def get(ctx, args) -> dict:
    app = find_app(ctx, "canvas", args.app, args.environment)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(app.get("name") or app["id"])).strip(".-")[:60] or "app"
    out = Path(args.out) if args.out else ctx.need_data_dir() / "exports" / "pa" / safe
    if args.out and (out / "src").is_dir() and any((out / "src").iterdir()):  # ppx clears only its own folders
        raise UsageError(f"{out / 'src'} is not empty, and files from an earlier download would stay there: "
                         "delete that folder or pass an empty --out")
    ctx.changes.add("download the app's .msapp and read-only .pa.yaml source", str(out),
                    undo=f"delete the folder {out}")
    if args.out and (out / "app.msapp").exists():  # pac downloads with --overwrite
        ctx.changes.add("replace the app.msapp already in that folder", str(out / "app.msapp"), reversible=False)
    summary = {"app": {"id": app["id"], "name": app.get("name"), "environment": app.get("environment")},
               "out": str(out)}
    if ctx.dry_run:
        return summary
    ctx.changes.require_yes(ctx.yes)
    download(ctx, app, out)
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.pa.yaml"))
    ctx.next("for edits, open the app in Studio (Apps > the app > Edit), keep the tab open and use "
             f"/canvas-apps:canvas-app; then confirm with `ppx pa inspect {app['id']} --refresh`", url=ps5.MAKER_URL)
    return {**summary, "msapp": str(out / "app.msapp"), "pa_yaml_count": len(files), "pa_yaml": files[:30],
            "screens": inspect.outline(out)["screens"]}
