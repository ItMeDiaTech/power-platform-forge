"""Profiles: defaults for which environment, workspace and site a verb targets.

Profiles hold defaults only. They never hold credentials and never gate anything; the
change notice tells the user what a command changes whichever profile is active.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ppx.errors import PpxError, UsageError

FILE = "envs.json"
NOTICE_MODES = ("notify", "confirm")
_FIELDS = ("url", "workspace", "site")


@dataclass
class Profile:
    name: str
    url: str | None = None
    workspace: str | None = None
    site: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class State:
    current: str | None = None
    notice_mode: str = "notify"
    profiles: dict[str, Profile] = field(default_factory=dict)

    def active(self) -> Profile | None:
        return self.profiles.get(self.current) if self.current else None

    def to_dict(self) -> dict:
        return {
            "current": self.current,
            "notice_mode": self.notice_mode,
            "profiles": {name: {k: v for k, v in p.to_dict().items() if k != "name"} for name, p in self.profiles.items()},
        }


def load(data_dir: Path) -> State:
    path = Path(data_dir) / FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return State()
    except (ValueError, OSError) as exc:
        raise PpxError(f"{path} is unreadable ({exc}); fix or delete it, then run `ppx env add` again") from exc
    if not isinstance(raw, dict):
        raise PpxError(f"{path} must hold a JSON object; fix or delete it")
    mode = raw.get("notice_mode", "notify")
    if mode not in NOTICE_MODES:
        raise PpxError(f"{path}: notice_mode must be one of {', '.join(NOTICE_MODES)}, not {mode!r}")
    profiles = {
        name: Profile(name=name, **{k: v for k, v in (body or {}).items() if k in _FIELDS})
        for name, body in (raw.get("profiles") or {}).items()
    }
    current = raw.get("current")
    return State(current=current if current in profiles else None, notice_mode=mode, profiles=profiles)


def save(data_dir: Path, state: State) -> None:
    path = Path(data_dir) / FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, path)


def check_url(url: str | None, what: str) -> str | None:
    if url is None:
        return None
    if not url.startswith("https://") or any(ch.isspace() for ch in url):
        raise UsageError(f"{what} must be an https:// URL, not {url!r}")
    return url.rstrip("/")
