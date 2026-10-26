"""ppx prefs: the user's design preferences, a short Markdown file of rules the agent follows when it builds.

The user's copy lives in the data directory, so plugin updates never overwrite it; until `ppx prefs --init`
creates it, the shipped defaults (design_preferences.md next to this module) are what the agent reads.
"""

from __future__ import annotations

from pathlib import Path

from ppx import datadir
from ppx.errors import UsageError

FILE = "design-preferences.md"
DEFAULTS = Path(__file__).with_name("design_preferences.md")
INLINE_BYTES = 3000


def path(data_dir: Path | None) -> Path | None:
    return Path(data_dir) / FILE if data_dir else None


def read(data_dir: Path | None = None) -> tuple[str, Path | None, bool]:
    """(text, the user's file path, whether that file exists); the defaults' text when it does not."""
    if data_dir is None:
        try:
            data_dir = datadir.resolve()
        except datadir.DataDirMissing:
            data_dir = None
    mine = path(data_dir)
    if mine and mine.is_file():
        return _decode(mine), mine, True
    return DEFAULTS.read_text(encoding="utf-8"), mine, False


def _decode(file: Path) -> str:
    """UTF-8, or UTF-16 with a BOM (what Windows PowerShell 5.1's > and Out-File write)."""
    raw = file.read_bytes()
    try:
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
        return text.replace("\r\n", "\n")  # as read_text would: a file saved on Windows reads the same
    except UnicodeDecodeError:
        raise UsageError(f"{file} is not UTF-8 text; save it as UTF-8 (or UTF-16) in your editor and try again") from None


def run(ctx, args) -> dict:
    mine = path(ctx.need_data_dir())
    if args.init and not mine.is_file():
        ctx.changes.add("create your design preferences file from the defaults", str(mine), undo=f"delete {mine}")
        if not ctx.dry_run:
            mine.write_text(DEFAULTS.read_text(encoding="utf-8"), encoding="utf-8")
    text, _, exists = read(ctx.data_dir)
    if not exists:
        ctx.next("make these rules yours: create the file, then edit it", command="ppx prefs --init")
    out = {"path": str(mine), "yours": exists}
    if len(text.encode("utf-8")) > INLINE_BYTES:  # a long file is read from disk, not squeezed into 4 KB
        return {**out, "rules_file": str(mine if exists else DEFAULTS), "bytes": len(text.encode("utf-8"))}
    return {**out, "rules": text}
