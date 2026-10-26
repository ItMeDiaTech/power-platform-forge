"""Dated JSON snapshots under <data>/cache/<area>/<key>/<YYYY-MM-DD>.json, kept 30 days.

Inventories are cached so repeated questions cost no API calls, and kept by day so
`diff` verbs can say what changed since an earlier snapshot.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KEEP_DAYS = 30
STALE_HOURS = 24


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", part).strip("-")[:80] or "_"


def folder(data_dir: Path, area: str, key: str) -> Path:
    return Path(data_dir) / "cache" / _safe(area) / _safe(key)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except ValueError:
        return default  # a torn or hand-edited cache file is refetched, never trusted


def write_snapshot(data_dir: Path, area: str, key: str, payload: dict, today: date | None = None) -> Path:
    day = today or datetime.now(timezone.utc).date()
    payload = {**payload, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path = folder(data_dir, area, key) / f"{day.isoformat()}.json"
    write_json(path, payload)
    for old in folder(data_dir, area, key).glob("*.json"):
        try:
            if date.fromisoformat(old.stem) < day - timedelta(days=KEEP_DAYS):
                old.unlink()
        except ValueError:
            continue
    return path


def _snapshots(data_dir: Path, area: str, key: str) -> list[tuple[date, Path]]:
    out = []
    for p in folder(data_dir, area, key).glob("*.json"):
        try:
            out.append((date.fromisoformat(p.stem), p))
        except ValueError:
            continue
    return sorted(out)


def latest(data_dir: Path, area: str, key: str):
    """Newest snapshot payload, or None."""
    snaps = _snapshots(data_dir, area, key)
    return read_json(snaps[-1][1]) if snaps else None


def at_least_days_old(data_dir: Path, area: str, key: str, days: int, today: date | None = None):
    """Newest snapshot dated `days` or more before today, as (date, payload), or None."""
    cutoff = (today or datetime.now(timezone.utc).date()) - timedelta(days=days)
    older = [(d, p) for d, p in _snapshots(data_dir, area, key) if d <= cutoff]
    if not older:
        return None
    d, p = older[-1]
    return d, read_json(p)


def is_stale(payload: dict | None, hours: int = STALE_HOURS) -> bool:
    if not payload or "fetched_at" not in payload:
        return True
    try:
        fetched = datetime.fromisoformat(payload["fetched_at"])
    except ValueError:
        return True
    return datetime.now(timezone.utc) - fetched > timedelta(hours=hours)
