"""Append-only JSONL record of every call that changed something (or tried to)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ppx.redact import scrub

FILE = "audit.jsonl"


def record(data_dir: Path, *, operation: str, argv: list[str], profile: str | None, changes: list[dict],
           ok: bool, exit_code: int, dry_run: bool) -> None:
    safe_argv = scrub(list(argv))
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "operation": operation,
        "args_sha256": hashlib.sha256("\x1f".join(safe_argv).encode()).hexdigest()[:16],
        "args": safe_argv,
        "profile": profile,
        "dry_run": dry_run,
        "ok": ok,
        "exit_code": exit_code,
        "changes": scrub(changes),
    }
    with open(Path(data_dir) / FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
