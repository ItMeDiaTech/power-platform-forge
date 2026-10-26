"""ppx verbs: the verb table as compact JSON (also the MCP `verbs` tool and `--help`).

Three levels so every answer fits the 4 KB envelope as the table grows: no words lists the groups
and their verb names, a group lists its verbs with effect and summary, and a full verb path gives
that verb's arguments.
"""

from __future__ import annotations

from ppx.errors import UsageError
from ppx.verbtable import GLOBAL_ARGS, VERBS, Arg, Verb, find

GLOBAL_FLAGS = " ".join(a.flags[0] for a in GLOBAL_ARGS)


def _arg(a: Arg) -> str:
    name = a.flags[0] if a.positional else "/".join(a.flags)
    if a.kind == "flag":
        return f"[{name}]"
    if a.positional:
        shown = f"<{name}:{'|'.join(a.choices)}>" if a.choices else f"<{name}>"
    else:
        shown = f"{name} {'|'.join(a.choices)}" if a.choices else f"{name} <{a.dest}>"
    if a.kind == "multi":
        shown += "..."
    return shown if a.is_required else f"[{shown}]"


def describe(verb: Verb) -> dict:
    return {"verb": verb.name, "effect": verb.effect, "summary": verb.summary,
            "args": " ".join(_arg(a) for a in verb.args)}


def listing(words: list[str]) -> dict:
    """The answer for `ppx verbs [words]`: groups, one group, or one verb in full."""
    groups: dict[str, list[Verb]] = {}
    for v in VERBS:
        groups.setdefault(v.path[0], []).append(v)
    if not words:
        items = [{"group": g, "count": len(vs), "verbs": ", ".join(" ".join(v.path[1:]) or g for v in vs)}
                 for g, vs in groups.items()]
        return {"count": len(VERBS), "items": items, "global_flags": GLOBAL_FLAGS,
                "next": "ppx verbs <group> for summaries; ppx verbs <group> <verb> for arguments"}
    verb, n = find(words)
    if verb is not None and n == len(words):
        return {"count": 1, "items": [describe(verb)], "global_flags": GLOBAL_FLAGS}
    if words[0] not in groups or len(words) > 1:
        raise UsageError(f"no verb or group {' '.join(words)!r}; groups: {', '.join(sorted(groups))}")
    rows = [{"verb": v.name, "effect": v.effect, "summary": v.summary} for v in groups[words[0]]]
    return {"count": len(rows), "items": rows, "next": f"ppx verbs {words[0]} <verb> for its arguments"}


def run(ctx, args) -> dict:
    return listing(" ".join(args.group or []).split())
