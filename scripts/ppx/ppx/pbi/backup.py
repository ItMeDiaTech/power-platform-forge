"""Backups ppx takes before it overwrites anything in Power BI: <data>/backups/pbi/<item id>/<UTC timestamp>/.

A definition backup is the item's getDefinition result as `definition.json`, which
`pbi push ... --restore <timestamp>` sends back. PBIX exports and data source snapshots sit beside it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ppx.envelope import NextAction
from ppx.errors import UsageError

AREA = "pbi"
DEFINITION = "definition.json"
_STAMP = re.compile(r"^\d{8}T\d{6}Z(-\d+)?$")


def _item_dir(data_dir: Path, item_id: str) -> Path:
    return Path(data_dir) / "backups" / AREA / (re.sub(r"[^A-Za-z0-9._-]+", "-", item_id).strip(".-") or "_")


def reserve(data_dir: Path, item_id: str) -> tuple[str, Path]:
    """A new, empty backup folder for the item, as (timestamp, path). Two in one second get a -N suffix."""
    base = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parent = _item_dir(data_dir, item_id)
    for n in range(1000):
        stamp = base if n == 0 else f"{base}-{n}"
        if not (parent / stamp).exists():
            return stamp, parent / stamp
    raise UsageError(f"too many backups of {item_id} in one second")


def save_definition(folder: Path, *, kind: str, item: dict, workspace: dict, definition: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / DEFINITION
    path.write_text(json.dumps({"kind": kind, "item": {"id": item["id"], "name": item.get("name")},
                                "workspace": {"id": workspace["id"], "name": workspace.get("name")},
                                "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "definition": definition}), encoding="utf-8")
    return path


def save_bytes(folder: Path, name: str, content: bytes) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(content)
    return path


def stamps(data_dir: Path, item_id: str) -> list[str]:
    parent = _item_dir(data_dir, item_id)
    return sorted(p.name for p in parent.iterdir() if _STAMP.match(p.name)) if parent.is_dir() else []


def load_definition(data_dir: Path, item_id: str, stamp: str) -> dict:
    """The saved definition backup {kind, item, workspace, definition}, or a usage error listing the ones that exist."""
    path = _item_dir(data_dir, item_id) / stamp / DEFINITION
    try:
        saved = json.loads(path.read_text(encoding="utf-8")) if _STAMP.match(stamp) else None
    except (OSError, ValueError):
        saved = None
    if not isinstance(saved, dict) or not (saved.get("definition") or {}).get("parts"):
        have = [s for s in stamps(data_dir, item_id) if (_item_dir(data_dir, item_id) / s / DEFINITION).exists()]
        raise UsageError(f"no definition backup {stamp!r} for this item",
                         data={"backups": have[-10:]},
                         next_actions=[NextAction("pass one of the listed timestamps to --restore")] if have else [])
    return saved
