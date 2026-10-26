"""PreToolUse hook: tell the user what a Bash, PowerShell or Power Platform MCP call will change.

Runs as `uv run --no-project --offline --python >=3.12 python -I -S <this file>`, so it uses only the
standard library and the stdlib-only ppx modules. Reads exit silently without loading any state.
Any failure exits 2, which blocks the call: a broken notice must never let a change through unseen.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ppx"))

_ASK = {
    "irreversible": "Irreversible: ",
    "unknown": "Unrecognised command; it may change something: ",
    "interactive": "This opens a sign-in. Run it in your own terminal instead: ",
}


def _notice_mode() -> str:
    from ppx import datadir, envs

    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    return envs.load(Path(data) if data else datadir.resolve()).notice_mode


def decide(event: dict) -> dict | None:
    """The hook's JSON answer for one PreToolUse event, or None to stay silent."""
    from ppx.classify import classify

    found = classify(event.get("tool_name") or "", event.get("tool_input") or {})
    if found.kind == "read":
        return None
    if found.kind in ("local", "change"):
        if _notice_mode() == "notify":
            # No permissionDecision: "allow" would skip Claude Code's prompt for every part of a compound line.
            return {"systemMessage": f"ppx notice: {found.summary}"}
        reason = found.summary
    else:
        reason = _ASK[found.kind] + found.summary
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                   "permissionDecisionReason": reason}}


def main() -> int:
    try:
        answer = decide(json.loads(sys.stdin.buffer.read()))
    except Exception as exc:  # fail closed: exit 2 blocks the call and shows this line
        detail = " ".join(str(exc).split())
        print(f"ppx change notice failed, so this call is blocked: {type(exc).__name__}: {detail}", file=sys.stderr)
        return 2
    if answer is not None:
        sys.stdout.write(json.dumps(answer) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
