"""ppx solution unpack and pack: SolutionPackager through pac, always with both package types.

Unpacking <name>.zip together with <name>_managed.zip keeps one folder that can rebuild either
package. Canvas apps stay as the .msapp files the zip holds; `--processCanvasApps` and
`pac canvas pack|unpack` are never used.
"""

from __future__ import annotations

from pathlib import Path

from ppx import pac
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, UsageError
from ppx.solution import common


def unpack(ctx, args) -> dict:
    given = common.solution_zip(args.zip)
    unmanaged = given.with_name(given.stem[:-len("_managed")] + given.suffix) if given.stem.endswith("_managed") else given
    managed = common.managed_sibling(unmanaged)
    missing = [p.name for p in (unmanaged, managed) if not p.is_file()]
    if missing:
        name = common.manifest(given)["name"]
        raise NeedsUser(f"unpack uses packagetype Both, which needs {unmanaged.name} and {managed.name} in the same "
                        f"folder; missing {', '.join(missing)}",
                        next_actions=[NextAction("export both packages", command=f"ppx solution export --name {name} --managed")])
    folder = Path(args.folder).expanduser().resolve()
    if folder.exists() and not folder.is_dir():
        raise UsageError(f"{folder} exists and is not a folder")
    occupied = folder.is_dir() and any(folder.iterdir())  # pac overwrites same-named files without asking
    ctx.changes.add(f"unpack {unmanaged.name} and {managed.name} (packagetype Both); files of the same name are overwritten",
                    str(folder), reversible=not occupied)
    if ctx.dry_run:
        return {"zip": str(unmanaged), "folder": str(folder)}
    ctx.changes.require_yes(ctx.yes)
    before = common.snapshot(folder)
    pac.run(["solution", "unpack", "--zipfile", str(unmanaged), "--folder", str(folder), "--packagetype", "Both"],
            timeout=common.long_timeout(ctx))
    files = [p for p, mtime in common.snapshot(folder).items() if before.get(p) != mtime]
    if not files and not before:
        raise PpxError(f"pac solution unpack reported success but wrote nothing to {folder}")
    if not files:
        ctx.warn(f"no files in {folder} changed; it already matches the zips")
    canvas = sorted(p.name for p in files if p.suffix.lower() == ".msapp")
    ctx.next("check the solution before importing it", command=f'ppx solution check --zip "{unmanaged}"')
    return {"zip": str(unmanaged), "folder": str(folder), "file_count": len(files), "canvas_apps": canvas[:20]}


def _layout(folder: Path) -> str | None:
    """'xml' for Other/Solution.xml, 'yaml' for the Git-integration solutions/*/solution.yml layout."""
    if (folder / "Other" / "Solution.xml").is_file():
        return "xml"
    if any(folder.glob("solutions/*/solution.yml")):
        return "yaml"
    return None


def pack(ctx, args) -> dict:
    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        raise UsageError(f"no folder {folder}")
    layout = _layout(folder)
    if layout is None:
        raise UsageError(f"{folder} is not an unpacked solution: it has neither Other/Solution.xml nor "
                         "solutions/<name>/solution.yml")
    zip_path = Path(args.zip).expanduser().resolve()
    if zip_path.suffix.lower() != ".zip":
        raise UsageError(f"--zip must end in .zip: {zip_path}")
    # A folder from `ppx solution unpack` holds both packages; a Git-integration folder packs as Unmanaged (pac's default).
    packagetype = args.packagetype or ("Both" if layout == "xml" else "Unmanaged")
    outputs = [zip_path, common.managed_sibling(zip_path)] if packagetype == "Both" else [zip_path]
    ctx.changes.add(f"pack {folder.name} ({packagetype}) into {', '.join(p.name for p in outputs)}; "
                    "zips of the same name are replaced", str(zip_path.parent),
                    reversible=not any(p.exists() for p in outputs))
    if ctx.dry_run:
        return {"folder": str(folder), "layout": layout, "packagetype": packagetype, "zips": [str(p) for p in outputs]}
    ctx.changes.require_yes(ctx.yes)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    before = {p: p.stat().st_mtime_ns if p.is_file() else None for p in outputs}
    pac.run(["solution", "pack", "--zipfile", str(zip_path), "--folder", str(folder), "--packagetype", packagetype],
            timeout=common.long_timeout(ctx))
    missing = [str(p) for p in outputs if not p.is_file() or p.stat().st_mtime_ns == before[p]]
    if missing:
        raise PpxError(f"pac solution pack reported success but did not write {', '.join(missing)}")
    return {"folder": str(folder), "layout": layout, "packagetype": packagetype,
            "zips": [{"path": str(p), "bytes": p.stat().st_size} for p in outputs]}
