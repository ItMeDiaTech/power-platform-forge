"""A per-minute call budget shared by every ppx process of this user (CLI runs and the MCP server).

Power BI allows 120 executeQueries requests per minute per user, whichever process sends them,
so the budget lives in <data>/ratelimit/<bucket>.json and an OS file lock serialises updates.
It keeps the times of the calls in the last minute (a sliding window rather than a refilling
bucket), so no 60-second span ever holds more than `per_minute` calls.
"""

from __future__ import annotations

import contextlib
import sys
import time
from pathlib import Path

from ppx import cache
from ppx.envelope import NextAction
from ppx.errors import PpxError

WINDOW = 60.0
LOCK_WAIT = 10.0
clock = time.time  # wall clock, because the window is shared between processes
sleep = time.sleep


@contextlib.contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_WAIT
    with open(path, "a+b") as fh:
        if sys.platform == "win32":
            import msvcrt

            while True:
                fh.seek(0)
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise PpxError(f"rate-limit lock {path} is held by another process") from None
                    time.sleep(0.02)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def acquire(data_dir: Path, bucket: str, per_minute: int, *, timeout: float = 60.0) -> float:
    """Take one call from `bucket`'s budget, waiting for room; return the seconds waited."""
    folder = Path(data_dir) / "ratelimit"
    state = folder / f"{bucket}.json"
    waited = 0.0
    while True:
        with _locked(folder / f"{bucket}.lock"):
            now = clock()
            raw = cache.read_json(state, [])
            stamps = [t for t in (raw if isinstance(raw, list) else [])
                      if isinstance(t, (int, float)) and now - WINDOW < t <= now]
            if len(stamps) < per_minute:
                cache.write_json(state, stamps + [now])
                return waited
            delay = min(stamps) + WINDOW - now
        if waited + delay > timeout:
            raise PpxError(f"{bucket}: {per_minute} calls per minute per user already used; "
                           f"the next one is free in {delay:.0f} s",
                           next_actions=[NextAction("wait a minute and retry, or pass a longer --timeout")],
                           data={"bucket": bucket, "per_minute": per_minute, "retry_after": round(delay, 1)})
        sleep(delay)
        waited += delay
