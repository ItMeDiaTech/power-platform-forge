"""sp files put: upload one local file to a SharePoint folder, never replacing a file without a backup and --yes.

`m365 spo file add --webUrl --folder --path [--fileName] [--overwrite true|false]`; --overwrite defaults to
true there, so ppx always passes it (https://pnp.github.io/cli-microsoft365/cmd/spo/file/file-add/). The
replaced file is first saved with `spo file get --asFile --path`. Uploads stop at 90 MB, the limit the
SharePoint connector in Power Apps and Power Automate enforces (SharePoint Online limits, read 2025-10-25).
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path

from ppx import m365
from ppx.errors import NeedsUser, PpxError, UsageError
from ppx.sp import files, sites
from ppx.sp.items_write import backup_folder

MAX_BYTES = 90 * 1024 * 1024


def put(ctx, args) -> dict:
    src = Path(args.file).expanduser()
    if not src.is_file():
        raise UsageError(f"{src} is not a file")
    size = src.stat().st_size
    if size > MAX_BYTES:
        raise UsageError(f"{src.name} is {size / 1048576:.0f} MB; ppx uploads files up to 90 MB, the SharePoint "
                         "connector's limit, so apps and flows can still open it. Upload larger files in SharePoint")
    site = sites.resolve(ctx, args.site)
    folder = urllib.parse.unquote(args.folder).rstrip("/")
    name = args.name or src.name
    if "/" in name or "\\" in name:
        raise UsageError("--name is a file name, not a path; put the folder in --folder")
    listed = m365.run(["spo", "file", "list", "--webUrl", site, "--folderUrl", folder,
                       "--fields", "Name,ServerRelativeUrl,Length"], timeout=ctx.timeout) or []
    hit = next((f for f in listed if str(f.get("Name") or "").lower() == name.lower()), None)
    target = f"{folder}/{name} on {site}"
    if hit and not args.overwrite:
        raise NeedsUser(f"{name} already exists in {folder}; pass --overwrite --yes to replace it (ppx saves the "
                        "current file first)", data={"existing": hit.get("ServerRelativeUrl"),
                                                     "bytes": files._int(hit.get("Length"))})
    if hit:
        stamp, saved = backup_folder(ctx.need_data_dir(), f"files-{name}")
        copy = saved / name
        ctx.changes.add(f"replace {name} ({files._int(hit.get('Length'))} bytes) with {src.name} ({size} bytes)",
                        target, reversible=False, backup=str(copy),
                        undo=f'ppx sp files put "{copy}" --site "{site}" --folder "{folder}" --overwrite --yes')
    else:
        ctx.changes.add(f"upload {src.name} ({size} bytes) as {name}", target,
                        undo=f"delete {folder}/{name} in SharePoint (it goes to the recycle bin)")
    ctx.changes.require_yes(ctx.yes)
    out = {"site": site, "folder": folder, "name": name, "bytes": size, "replaces": bool(hit)}
    if ctx.dry_run:
        return out
    timeout = max(ctx.timeout, files.DOWNLOAD_TIMEOUT)
    if hit:
        saved.mkdir(parents=True, exist_ok=True)
        m365.run(["spo", "file", "get", "--webUrl", site, "--url", hit["ServerRelativeUrl"], "--asFile",
                  "--path", str(copy)], timeout=timeout)
        if not copy.is_file():
            raise PpxError(f"could not save the current {name} before replacing it; nothing was uploaded")
    result = m365.run(["spo", "file", "add", "--webUrl", site, "--folder", folder, "--path", str(src),
                       "--fileName", name, "--overwrite", "true" if hit else "false"], timeout=timeout) or {}
    return {**out, "path": result.get("ServerRelativeUrl"), **({"backup": stamp} if hit else {})}
