"""Resolve the one data directory that ppx, its hooks and its MCP server share.

Order: --data-dir, PPX_DATA_DIR, CLAUDE_PLUGIN_DATA, then the pointer file the plugin's
SessionStart hook writes. Commands Claude runs through the Bash or PowerShell tool do not
receive CLAUDE_PLUGIN_DATA, which is why the pointer file exists.
"""

from __future__ import annotations

import os
from pathlib import Path

POINTER_DIR = Path.home() / ".claude" / "power-platform-forge"


class DataDirMissing(Exception):
    pass


def resolve(cli_value: str | None = None, environ: dict | None = None, pointer_dir: Path | None = None) -> Path:
    env = os.environ if environ is None else environ
    for candidate in (cli_value, env.get("PPX_DATA_DIR"), env.get("CLAUDE_PLUGIN_DATA")):
        if candidate:
            path = Path(candidate).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            return path
    try:
        text = ((pointer_dir or POINTER_DIR) / "datadir").read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    if text and Path(text).is_dir():
        return Path(text)
    raise DataDirMissing(
        "no ppx data directory: start a Claude Code session with the plugin enabled, "
        "or pass --data-dir <path> (or set PPX_DATA_DIR)"
    )


def write_pointer(data_dir: str, plugin_root: str | None = None, pointer_dir: Path | None = None) -> None:
    """Record where the data directory (and plugin root) live, atomically."""
    target = pointer_dir or POINTER_DIR
    target.mkdir(parents=True, exist_ok=True)
    for name, value in (("datadir", data_dir), ("root", plugin_root)):
        if value:
            tmp = target / f".{name}.tmp"
            tmp.write_text(value, encoding="utf-8")
            os.replace(tmp, target / name)
