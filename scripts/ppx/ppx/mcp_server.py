"""The plugin's local MCP server `ppx`: three tools over `ppx.cli.dispatch`, stdlib JSON-RPC on stdio.

One UTF-8 JSON object per line (MCP 2025-11-25 stdio transport,
https://modelcontextprotocol.io/specification/2025-11-25/basic/transports). Requests run one at a
time. Nothing but protocol messages reaches stdout; anything printed goes to stderr.
"""

from __future__ import annotations

import json
import shlex
import sys
import traceback

from ppx import __version__, cli, prefs, verbtable
from ppx.envelope import Envelope, NextAction, Result, dumps
from ppx.errors import UsageError
from ppx.redact import scrub, scrub_text

VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
LATEST = "2025-11-25"
INSTRUCTIONS = (
    "ppx: Power BI, Power Apps, SharePoint, Dataverse and Fabric work for the signed-in user. "
    "Call verbs first to see each verb, its arguments and what it changes, then run. "
    "Every change produces a notice for the user; irreversible or costly changes need yes=true "
    "(dry_run=true lists them first). Sign-ins (auth login) are run by the user in their own terminal; "
    "doctor shows what works. Before you build or change a report, app, form, view or list, read the user's "
    "design preferences (resource ppx://design-preferences, or run verb prefs) and follow them."
)
PREFS_URI = "ppx://design-preferences"
RESOURCES = [{"uri": PREFS_URI, "name": "design-preferences", "title": "Design preferences",
              "description": "The user's rules for how Power BI reports, Power Apps and SharePoint lists are designed",
              "mimeType": "text/markdown"}]
TOOLS = [
    {"name": "doctor", "description": "Check ppx sign-ins, token reach per API and prerequisites. Read-only.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": True}},
    {"name": "verbs", "description": "List ppx verb groups; with group, that group's verbs and what each changes "
                                     "(read, local, change, irreversible, interactive); with a verb, its arguments. "
                                     "Read-only.",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "properties": {"group": {"type": "string",
                                              "description": "a group (\"pbi\") or a verb (\"pbi push\")"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "run", "description": "Run one ppx verb and return its JSON envelope. Pass the verb path and its "
                                   "arguments as separate strings; call verbs to see them.",
     "inputSchema": {"type": "object", "required": ["verb"], "additionalProperties": False, "properties": {
         "verb": {"type": "string", "description": "verb path, e.g. \"pbi ls\" or \"env add\""},
         "args": {"type": "array", "items": {"type": "string"}, "default": [],
                  "description": "one string per argument, e.g. [\"work\", \"--url\", \"https://org.crm.dynamics.com\"]"},
         "dry_run": {"type": "boolean", "default": False, "description": "list the changes without making them"},
         "yes": {"type": "boolean", "default": False, "description": "confirm an irreversible or costly change"}}}},
]
_SCHEMAS = {t["name"]: t["inputSchema"] for t in TOOLS}
_TYPES = {"string": str, "boolean": bool, "array": list}


def _error(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _failed(operation: str, message: str, action: str, command: str | None = None, exit_code: int = 1) -> Result:
    env = Envelope(False, operation, {"error": message}, next_actions=[NextAction(action, command)])
    return Result(scrub(env.to_dict()), exit_code)


def _bad_arguments(schema: dict, arguments: dict) -> str | None:
    props = schema["properties"]
    for key, value in arguments.items():
        kind = props.get(key, {}).get("type")
        if kind is None:
            return f"unknown argument {key!r}; allowed: {', '.join(props) or 'none'}"
        if not isinstance(value, _TYPES[kind]) or (kind == "array" and not all(isinstance(v, str) for v in value)):
            return f"argument {key!r} must be {'an array of strings' if kind == 'array' else 'a ' + kind}"
    missing = [k for k in schema.get("required", ()) if k not in arguments]
    return f"missing required argument {missing[0]!r}" if missing else None


def _interactive(argv: list[str]):
    """The sign-in verb `argv` could reach, if any. Table lookup, not a parse, so a line with missing or
    bad arguments (`auth login` alone) is refused too. dispatch moves global flags (and their values) out
    from anywhere in the line, so any line it runs as a verb holds that verb's path tokens in order."""
    for verb in verbtable.VERBS:
        tokens = iter(argv)
        if verb.effect == "interactive" and all(tok in tokens for tok in verb.path):  # in-order subsequence
            return verb
    return None


def _run_tool(name: str, arguments: dict) -> Result:
    arguments = {k: v for k, v in arguments.items() if v is not None}  # some clients send null for unset options
    problem = _bad_arguments(_SCHEMAS[name], arguments)
    if problem:
        return _failed(f"mcp.{name}", problem, "call verbs to see each verb's arguments")
    if name == "doctor":
        argv = ["auth", "doctor"]
    elif name == "verbs":
        argv = ["verbs"] + ([arguments["group"]] if arguments.get("group") else [])
    else:
        argv = (arguments["verb"].split() + list(arguments.get("args", []))
                + (["--dry-run"] if arguments.get("dry_run") else []) + (["--yes"] if arguments.get("yes") else []))
        verb = _interactive(argv)
        if verb is not None:
            return _failed(".".join(verb.path), f"ppx {verb.name} opens a sign-in, so it cannot run here",
                           "ask the user to run this in their own terminal, then call doctor",
                           # not argv: a dry_run/yes flag would make the user's own run skip the sign-in
                           command="ppx " + shlex.join(arguments["verb"].split() + list(arguments.get("args", []))),
                           exit_code=2)
    return cli.dispatch(argv, interactive=False)


def _tools_call(params: dict) -> dict:
    name, arguments = params.get("name"), params.get("arguments") or {}
    if not isinstance(name, str) or name not in _SCHEMAS:
        raise UsageError(f"unknown tool: {name}; tools: {', '.join(_SCHEMAS)}")
    if not isinstance(arguments, dict):
        raise UsageError("arguments must be an object")
    result = _run_tool(name, arguments)
    # Exit 2 carries next_actions for the user, so only exit 1 is a tool error. No structuredContent:
    # Claude ignores text blocks when it is set.
    return {"content": [{"type": "text", "text": dumps(result.envelope)}], "isError": result.exit_code == 1}


def handle(line: bytes) -> dict | None:
    """Answer one JSON-RPC line; None for notifications and anything that is not a request."""
    try:
        msg = json.loads(line)
    except ValueError:
        return _error(None, -32700, "parse error: not valid JSON")
    if not isinstance(msg, dict):
        return _error(None, -32600, "invalid request: expected a JSON object")
    if "id" not in msg or "method" not in msg:
        return None
    rid, method, params = msg["id"], msg["method"], msg.get("params") or {}
    if not isinstance(params, dict):
        return _error(rid, -32602, "params must be an object")
    try:
        if method == "initialize":
            version = params.get("protocolVersion")
            result = {"protocolVersion": version if version in VERSIONS else LATEST, "capabilities": {"tools": {}, "resources": {}},
                      "serverInfo": {"name": "ppx", "version": __version__}, "instructions": INSTRUCTIONS}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "resources/list":
            result = {"resources": RESOURCES}
        elif method == "resources/read":
            if params.get("uri") != PREFS_URI:
                raise UsageError(f"unknown resource: {params.get('uri')}; resources: {PREFS_URI}")
            text, _, _ = prefs.read()
            result = {"contents": [{"uri": PREFS_URI, "mimeType": "text/markdown", "text": text}]}
        elif method == "tools/call":
            result = _tools_call(params)
        else:
            return _error(rid, -32601, f"method not found: {method}")
    except UsageError as exc:
        return _error(rid, -32602, exc.message)
    except Exception as exc:
        print(scrub_text(traceback.format_exc()), file=sys.stderr)
        return _error(rid, -32603, scrub_text(" ".join(f"{type(exc).__name__}: {exc}".split())))
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def serve(inp, out) -> None:
    """Read requests from the binary stream `inp` until EOF; write replies to the binary stream `out`."""
    for line in inp:
        if not line.strip():
            continue
        reply = handle(line)
        if reply is not None:
            out.write(dumps(reply).encode("utf-8", "replace") + b"\n")  # a lone surrogate in a request echoes as ?
            out.flush()


def main() -> int:
    out = sys.stdout.buffer
    sys.stdout = sys.stderr  # a stray print anywhere must never corrupt the protocol stream
    serve(sys.stdin.buffer, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
