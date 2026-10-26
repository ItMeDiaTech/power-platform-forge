"""ppx verbs: the verb table as compact JSON (also the MCP `verbs` tool)."""

from __future__ import annotations

from ppx.errors import UsageError
from ppx.verbtable import VERBS, Arg


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


def describe(group: str | None = None) -> list[dict]:
    rows = [v for v in VERBS if group is None or v.path[0] == group]
    return [{"verb": v.name, "effect": v.effect, "summary": v.summary, "args": " ".join(_arg(a) for a in v.args)}
            for v in rows]


def run(ctx, args) -> dict:
    items = describe(args.group)
    if args.group and not items:
        raise UsageError(f"no verb group {args.group!r}; groups: {', '.join(sorted({v.path[0] for v in VERBS}))}")
    return {"count": len(items), "items": items,
            "global_flags": "--profile --dry-run --yes --fields --top --timeout --raw --refresh --data-dir"}
