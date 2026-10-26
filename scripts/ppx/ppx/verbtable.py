"""The verb table: every ppx verb, its arguments, what it changes and the notice text.

Single source of truth for the CLI parser, the MCP `verbs` tool and the change-notice hook.
Each verb group lives in its own data-only module (verbs_<group>.py); this module joins them.
Stdlib-only, because the hook loads it on every shell call. Effects are described in verbdefs.
"""

from __future__ import annotations

from ppx import verbs_core, verbs_extra, verbs_pa, verbs_pbi, verbs_solution, verbs_sp
from ppx.verbdefs import EFFECTS, Arg, Verb, a

__all__ = ["EFFECTS", "GLOBAL_ARGS", "VERBS", "Arg", "Verb", "find"]

GLOBAL_ARGS: tuple[Arg, ...] = (
    a("--data-dir", help="ppx data directory (default: the plugin's data directory)"),
    a("--profile", help="profile to use instead of the active one"),
    a("--dry-run", kind="flag", help="list the changes without making them"),
    a("--yes", kind="flag", help="confirm irreversible or costly changes"),
    a("--fields", help="comma-separated fields to keep in each item"),
    a("--top", kind="int", help="return at most this many items"),
    a("--timeout", kind="int", default=60, help="seconds to wait for each call"),
    a("--raw", kind="flag", help="return full payloads instead of summaries (no 4 KB limit)"),
    a("--refresh", kind="flag", help="ignore cached inventories and fetch again"),
)

VERBS: tuple[Verb, ...] = (verbs_core.VERBS + verbs_pbi.VERBS + verbs_sp.VERBS + verbs_pa.VERBS
                           + verbs_solution.VERBS + verbs_extra.VERBS)


def find(tokens: list[str]) -> tuple[Verb | None, int]:
    """Return the verb with the longest path matching the start of `tokens`, and the path length."""
    best: tuple[Verb | None, int] = (None, 0)
    for verb in VERBS:
        n = len(verb.path)
        if n > best[1] and tuple(tokens[:n]) == verb.path:
            best = (verb, n)
    return best
