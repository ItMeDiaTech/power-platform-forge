"""The one JSON object every verb prints, and the 4 KB output budget."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

BUDGET_BYTES = 4096


@dataclass(frozen=True)
class NextAction:
    """Something the agent or the user should do next. `command` runs in a terminal; `url` opens in a browser."""

    action: str
    command: str | None = None
    url: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class Result:
    envelope: dict
    exit_code: int = 0


@dataclass
class Envelope:
    ok: bool
    operation: str
    data: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    next_actions: list[NextAction] = field(default_factory=list)
    target_env: dict | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "operation": self.operation,
            "target_env": self.target_env or {},
            "data": self.data,
            "warnings": list(self.warnings),
            "next_actions": [a.to_dict() for a in self.next_actions],
        }


def dumps(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def fit_budget(env: dict, limit: int = BUDGET_BYTES) -> dict:
    """Shrink the longest lists in `data` until the envelope fits, recording what was cut.

    Verbs summarise on their own; this is the backstop that keeps one oversized answer
    from flooding the conversation. `--raw` skips it.
    """
    if len(dumps(env).encode()) <= limit:
        return env
    cut: dict[str, int] = {}
    lists = _lists(env.get("data", {}), "data")
    for _ in range(64):
        if len(dumps(env).encode()) <= limit or not lists:
            break
        path, holder, key = max(lists, key=lambda t: len(dumps(t[1][t[2]])))
        items = holder[key]
        if len(items) <= 1:
            lists.remove((path, holder, key))
            continue
        cut.setdefault(path, len(items))
        holder[key] = items[: max(1, len(items) // 2)]
    if cut:
        env.setdefault("data", {})["truncated"] = cut
        env.setdefault("warnings", []).append(
            "output shortened to fit 4 KB: "
            + ", ".join(f"{p} kept {len(_get(env, p))} of {n}" for p, n in cut.items())
            + "; narrow with --top or --fields, or pass --raw"
        )
    return env


def _lists(obj, path):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list):
                found.append((f"{path}.{k}", obj, k))
            found.extend(_lists(v, f"{path}.{k}"))
    return found


def _get(env: dict, path: str):
    cur = env
    for part in path.split("."):
        cur = cur[part]
    return cur
