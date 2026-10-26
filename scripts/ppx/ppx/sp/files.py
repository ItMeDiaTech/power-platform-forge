"""sp files ls|get: a folder's files and subfolders, and one file downloaded to disk.

`m365 spo file list --folderUrl`, `spo folder list --parentFolderUrl` and `spo file get --url --asFile
--path` (https://pnp.github.io/cli-microsoft365/cmd/spo/file/file-list/, .../folder/folder-list/,
.../file/file-get/); paths are server- or site-relative and decoded (spaces, not %20).
"""

from __future__ import annotations

import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from ppx import exports, m365
from ppx.errors import PpxError
from ppx.sp import sites

FILE_FIELDS = "Name,ServerRelativeUrl,Length,TimeLastModified"
FOLDER_FIELDS = "Name,ServerRelativeUrl,ItemCount,TimeLastModified"
DOWNLOAD_TIMEOUT = 300  # a large file over a slow link takes minutes


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ls(ctx, args) -> dict:
    site = sites.resolve(ctx, args.site)
    folder = urllib.parse.unquote(args.folder)  # a path pasted from the browser holds %20
    recursive = ["--recursive"] if args.recursive else []
    # Each m365 call starts Node, so the two listings run side by side.
    with ThreadPoolExecutor(2) as pool:
        files = pool.submit(m365.run, ["spo", "file", "list", "--webUrl", site, "--folderUrl", folder,
                                       "--fields", FILE_FIELDS, *recursive], timeout=ctx.timeout)
        folders = pool.submit(m365.run, ["spo", "folder", "list", "--webUrl", site, "--parentFolderUrl", folder,
                                         "--fields", FOLDER_FIELDS, *recursive], timeout=ctx.timeout)
        file_rows, folder_rows = files.result() or [], folders.result() or []
    items = ([{"kind": "folder", "name": d.get("Name"), "path": d.get("ServerRelativeUrl"),
               "items": d.get("ItemCount"), "modified": (d.get("TimeLastModified") or "")[:10] or None}
              for d in folder_rows]
             + [{"kind": "file", "name": f.get("Name"), "path": f.get("ServerRelativeUrl"), "size": _int(f.get("Length")),
                 "modified": (f.get("TimeLastModified") or "")[:10] or None} for f in file_rows])
    saved = exports.inline_or_file(ctx, "sp-files", items[:ctx.top])
    return {"site": site, "folder": folder, "count": len(items), "folders": len(folder_rows),
            "files": len(file_rows), **({"items": saved["body"]} if "body" in saved else saved)}


def _target(ctx, args, name: str) -> Path:
    if args.out:
        out = Path(args.out).expanduser()
        return out / name if out.is_dir() else out
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ctx.need_data_dir() / "exports" / f"{stamp}-{name}"


def get(ctx, args) -> dict:
    site = sites.resolve(ctx, args.site)
    file, folder = urllib.parse.unquote(args.file), urllib.parse.unquote(args.folder or "")
    path = file if "/" in file or not folder else f"{folder.rstrip('/')}/{file}"
    # The local name never carries a separator or `..`, so it stays inside the exports or --out folder.
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", path.rstrip("/").rsplit("/", 1)[-1]).strip(".-") or "download"
    out = _target(ctx, args, name)
    if out.exists():
        ctx.changes.add("overwrite a local file", str(out), reversible=False)
        ctx.changes.require_yes(ctx.yes)
    if ctx.dry_run:
        return {"site": site, "file": path, "path": str(out)}
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PpxError(f"cannot create {out.parent}: {exc}") from exc
    # m365 streams straight into --path, so a failed download must not land on the file being replaced.
    part = out.with_name(f".{out.name}.part")
    try:
        m365.run(["spo", "file", "get", "--webUrl", site, "--url", path, "--asFile", "--path", str(part)],
                 timeout=max(ctx.timeout, DOWNLOAD_TIMEOUT))
        if not part.is_file():
            raise PpxError(f"m365 reported success but {out} was not written")
        try:
            os.replace(part, out)
        except OSError as exc:  # e.g. the old file is open in Excel
            raise PpxError(f"cannot write {out}: {exc}") from exc
    finally:
        part.unlink(missing_ok=True)
    return {"site": site, "file": path, "path": str(out), "bytes": out.stat().st_size}
