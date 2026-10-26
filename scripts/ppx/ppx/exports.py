"""Large results go to disk; the envelope carries the path and a short digest."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

INLINE_LIMIT = 3000


def save(data_dir: Path, name: str, content: str | bytes, suffix: str = ".json") -> Path:
    folder = Path(data_dir) / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")[:60] or "export"
    # The suffix can come from an API response (an export's file extension): letters and digits only.
    suffix = "." + (re.sub(r"[^A-Za-z0-9]", "", suffix)[:10] or "bin")
    path = folder / f"{stamp}-{safe}{suffix}"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def digest(value) -> dict:
    """A few lines that say what a JSON value holds, without its contents."""
    if isinstance(value, dict):
        return {"type": "object", "keys": list(value)[:20], "key_count": len(value)}
    if isinstance(value, list):
        first = value[0] if value else None
        return {"type": "array", "length": len(value),
                "item_keys": list(first)[:20] if isinstance(first, dict) else None}
    return {"type": type(value).__name__}


def inline_or_file(ctx, name: str, value) -> dict:
    """Return the value inline when it is small (or --raw), else save it and return path plus digest."""
    text = json.dumps(value, ensure_ascii=False, default=str)
    if ctx.raw or len(text) <= INLINE_LIMIT or ctx.data_dir is None:
        return {"body": value}
    path = save(ctx.data_dir, name, text)
    return {"saved_to": str(path), "bytes": len(text.encode()), "digest": digest(value)}
