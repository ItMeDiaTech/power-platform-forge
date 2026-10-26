"""Change-notice rules for MCP tools: this plugin's ppx server, the Power BI Modeling MCP and FlowAgent.
Stdlib only."""

from __future__ import annotations

import re

from ppx.classify_cli import _ppx
from ppx.classify_common import READ, Classification, _clip

# Identical to the PreToolUse MCP matcher in hooks/hooks.json (a test checks): Power Platform servers only.
# Each term must be a whole token of the server name, so pnpm or fabricator do not match.
MCP_MATCHER = (r"^mcp__(?:(?!__).)*(?<![A-Za-z0-9])"
               r"(?:power-platform-forge_ppx|powerbi|flowagent|dataverse|fabric|m365|microsoft365|pnp)"
               r"(?![A-Za-z0-9])(?:(?!__).)*__")

# ---- MCP tools -----------------------------------------------------------------------------------

_MCP_NAME = re.compile(r"^mcp__((?:(?!__).)*)__(.+)$")
_OUR_SERVER = "plugin_power-platform-forge_ppx"

# Power BI Modeling MCP 1.0.0 (docs/PLATFORM-FACTS.md fact 17): tools multiplexed by `operation`.
_PBI_READ = re.compile(r"(?i)^(help|get.*|list.*|find|test|checkstatusofrefreshwithapi|exporttmdl|exporttmsl|execute"
                       r"|validate|report|getstatus|listactive|importfromtmdlfolder|importfrombimfile)$")
_PBI_FOLDER_WRITE = {"exporttotmdlfolder", "exporttobimfile"}

# FlowAgent in power-automate 3.2.1 (fact 18): 17 tenant changes, 5 local-state tools, 40 reads.
_FLOW_IRREVERSIBLE = {"delete_flow", "delete_connection", "cancel_all_runs"}
_FLOW_CHANGE = {"create_flow", "update_flow", "edit_flow", "copy_flow", "publish_flow", "disable_flow", "run_flow",
                "cancel_run", "resubmit_run", "pick_or_create_connection", "fix_connection", "smoke_test",
                "run_desktop_flow", "restore_backup"}
_FLOW_LOCAL = {"reconnect", "switch_account", "set_current_env", "set_current_flow", "clear_current_flow"}
_FLOW_READ = {
    "whoami", "doctor", "list_accounts", "list_environments", "resolve_environment", "get_current_env", "list_flows",
    "get_flow", "get_flow_context", "preview_update", "get_run_history", "get_run_details", "get_run_actions",
    "get_run_action_content", "get_run_action_repetitions", "diagnose_run", "get_expression_help", "get_current_flow",
    "get_past_trigger_inputs", "preflight_flow", "list_trigger_emulators", "list_connections", "resolve_refs",
    "resolve_params", "resolve_entity", "test_connection", "list_connectors", "get_connector", "search_operations",
    "get_operation_details", "invoke_operation", "list_datasets", "list_tables", "list_templates", "scaffold_flow",
    "validate_flow", "list_desktop_flows", "list_machine_groups", "list_backups", "get_backup",
}


def _mcp(tool_name: str, args: dict) -> Classification:
    m = _MCP_NAME.match(tool_name)
    if m is None or not re.match(MCP_MATCHER, tool_name):
        return READ
    server, tool = m.groups()
    if server == _OUR_SERVER:
        return _ppx_mcp(tool, args)
    if "powerbi-modeling" in server:
        return _pbi_modeling(tool, args)
    if "flowagent" in server:
        return _flowagent(tool, args)
    return Classification("unknown", _clip(f"{tool} on MCP server {server}"), server)


def _targets(args: dict, suffixes: tuple[str, ...]) -> str:
    """Up to three name-like argument values (one level of nesting), to say what a call touches."""
    pairs = list(args.items()) + [kv for v in args.values() if isinstance(v, dict) for kv in v.items()]
    found = [v for k, v in pairs if isinstance(v, str) and v and k.lower() != "operation" and k.lower().endswith(suffixes)]
    return _clip(", ".join(found[:3]), 120)


def _ppx_mcp(tool: str, args: dict) -> Classification:
    if tool in ("doctor", "verbs"):
        return READ
    verb, extra = args.get("verb"), args.get("args") or []
    if tool != "run" or not isinstance(verb, str) or not isinstance(extra, list) or not all(isinstance(a, str) for a in extra):
        return Classification("unknown", _clip(f"ppx MCP tool {tool} with arguments it does not take"), "ppx")
    tokens = verb.split() + extra + (["--dry-run"] if args.get("dry_run") else []) + (["--yes"] if args.get("yes") else [])
    result = _ppx(tokens)
    if result.kind == "interactive":
        return Classification("interactive", _clip("ppx " + " ".join(tokens), 300), "ppx")
    return result


def _pbi_modeling(tool: str, args: dict) -> Classification:
    binary = "powerbi-modeling-mcp"
    if tool == "accept_eula":
        return Classification("local", "Accept the Power BI Modeling MCP licence terms on this computer", binary)
    request = args.get("request") if isinstance(args.get("request"), dict) else args
    op = next((v for k, v in request.items() if k.lower() == "operation" and isinstance(v, str)), None)
    if op is None:
        return Classification("unknown", f"Power BI Modeling MCP {tool} without an operation", binary)
    if tool == "connection_operations" or _PBI_READ.match(op):
        return READ
    targets = _targets(request, ("name", "path"))
    if op.lower() in _PBI_FOLDER_WRITE:  # powerbi-modeling-mcp issue #96: existing definition files were deleted
        return Classification("irreversible", f"{op} writes the model to {targets or 'a local folder'} and can delete "
                                              "definition files already there", binary)
    where = "a Fabric workspace" if op.lower() == "deploytofabric" else "the connected Power BI model"
    noun = tool.removesuffix("_operations").replace("_", " ")
    return Classification("change", f"{noun} {op} on {where}" + (f" ({targets})" if targets else ""), binary)


def _flowagent(tool: str, args: dict) -> Classification:
    if tool in _FLOW_READ or (tool == "edit_flow" and args.get("dryRun") is True):
        return READ
    kind = ("irreversible" if tool in _FLOW_IRREVERSIBLE else "change" if tool in _FLOW_CHANGE
            else "local" if tool in _FLOW_LOCAL else "unknown")
    forced = args.get("forceManaged") is True and kind in ("change", "local")
    if forced:
        kind = "irreversible"
    targets = _targets(args, ("name", "id"))
    summary = f"{tool.replace('_', ' ').capitalize()} with FlowAgent" + (f" ({targets})" if targets else "")
    return Classification(kind, summary + ("; forceManaged overrides the managed-solution guard" if forced else ""),
                          "flowagent")
