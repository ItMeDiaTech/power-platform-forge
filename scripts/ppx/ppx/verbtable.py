"""The verb table: every ppx verb, its arguments, what it changes and the notice text.

Single source of truth for the CLI parser, the MCP `verbs` tool and the change-notice hook.
Stdlib-only with no ppx imports, because the hook loads it on every shell call.

Effects:
  read          changes nothing anywhere; no notice
  local         changes only this computer (profiles, sign-in caches); notice
  change        changes the tenant reversibly (backups or undo exist); notice
  irreversible  overwrites, deletes or costs something; the hook asks first and ppx needs --yes
  interactive   opens a sign-in or prompt; the user should run it in their own terminal
  by-method     ppx raw: GET reads, DELETE is irreversible, other methods change
"""

from __future__ import annotations

from dataclasses import dataclass

EFFECTS = ("read", "local", "change", "irreversible", "interactive", "by-method")


@dataclass(frozen=True)
class Arg:
    flags: tuple[str, ...]
    help: str
    kind: str = "str"  # str | int | flag | choice | multi
    choices: tuple[str, ...] = ()
    required: bool | None = None  # None: positionals are required, options are not
    default: object = None

    @property
    def dest(self) -> str:
        name = self.flags[-1] if self.flags[0].startswith("-") else self.flags[0]
        return name.lstrip("-").replace("-", "_")

    @property
    def positional(self) -> bool:
        return not self.flags[0].startswith("-")

    @property
    def is_required(self) -> bool:
        return self.positional if self.required is None else self.required


@dataclass(frozen=True)
class Verb:
    path: tuple[str, ...]
    summary: str
    effect: str
    handler: str
    notice: str = ""
    args: tuple[Arg, ...] = ()
    escalate: tuple[tuple[str, str], ...] = ()  # (flag or flag=value, effect) that raise the effect
    needs_data: bool = True

    @property
    def name(self) -> str:
        return " ".join(self.path)


def _a(*flags, help="", **kw) -> Arg:
    return Arg(tuple(flags), help, **kw)


BACKENDS = ("az", "pac", "m365", "maker", "fab")
AREAS = ("device", "toolchain", "tokens", "pbi", "sp", "pa", "flows", "all")
RAW_APIS = ("graph", "spo", "pbi", "fabric", "dataverse")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

GLOBAL_ARGS: tuple[Arg, ...] = (
    _a("--data-dir", help="ppx data directory (default: the plugin's data directory)"),
    _a("--profile", help="profile to use instead of the active one"),
    _a("--dry-run", kind="flag", help="list the changes without making them"),
    _a("--yes", kind="flag", help="confirm irreversible or costly changes"),
    _a("--fields", help="comma-separated fields to keep in each item"),
    _a("--top", kind="int", help="return at most this many items"),
    _a("--timeout", kind="int", default=60, help="seconds to wait for each call"),
    _a("--raw", kind="flag", help="return full payloads instead of summaries (no 4 KB limit)"),
    _a("--refresh", kind="flag", help="ignore cached inventories and fetch again"),
)

VERBS: tuple[Verb, ...] = (
    Verb(("auth", "doctor"), "Check sign-ins, token reach per API, prerequisites and what is degraded", "read",
         "ppx.cmd_auth:doctor",
         args=(_a("--audience", help="probe only this API: pbi, fabric, dataverse, powerplatform, graph, sharepoint"),)),
    Verb(("auth", "login"), "Sign in with one backend; opens a browser, so run it in your own terminal", "interactive",
         "ppx.cmd_auth:login", notice="Open a {backend} sign-in on this computer",
         args=(_a("--backend", kind="choice", choices=BACKENDS, required=True, help="which tool to sign in"),
               _a("--environment", help="Dataverse environment URL (pac)"),
               _a("--tenant", help="tenant id or domain (m365, az)"),
               _a("--app-id", help="client id of your own app registration (m365)"))),
    Verb(("auth", "logout"), "Sign out of one backend on this computer", "local", "ppx.cmd_auth:logout",
         notice="Sign out of {backend} on this computer",
         args=(_a("--backend", kind="choice", choices=BACKENDS, required=True, help="which tool to sign out"),)),
    Verb(("auth", "app-spec"), "Print the exact app registration for SharePoint and Graph access (Tier B)", "read",
         "ppx.cmd_auth:app_spec", needs_data=False),
    Verb(("env", "add"), "Save a profile of defaults (environment, workspace, site)", "local", "ppx.cmd_env:add",
         notice="Save profile {name} on this computer",
         args=(_a("name", help="profile name"), _a("--url", help="Dataverse environment URL"),
               _a("--workspace", help="default Power BI workspace (name or id)"),
               _a("--site", help="default SharePoint site URL"),
               _a("--use", kind="flag", help="make it the active profile"))),
    Verb(("env", "set"), "Change a profile's defaults or the notice mode", "local", "ppx.cmd_env:set_",
         notice="Update profile {name} on this computer",
         args=(_a("name", help="profile name (default: the active one)", required=False),
               _a("--url", help="Dataverse environment URL"), _a("--workspace", help="default workspace"),
               _a("--site", help="default site URL"),
               _a("--notice-mode", kind="choice", choices=("notify", "confirm"),
                  help="notify: tell me what changes; confirm: ask before every change"))),
    Verb(("env", "rm"), "Delete a profile", "local", "ppx.cmd_env:rm", notice="Delete profile {name} from this computer",
         args=(_a("name", help="profile name"),)),
    Verb(("env", "use"), "Make a profile the active one", "local", "ppx.cmd_env:use",
         notice="Make {name} the active profile", args=(_a("name", help="profile name"),)),
    Verb(("env", "ls"), "List profiles and the notice mode", "read", "ppx.cmd_env:ls"),
    Verb(("raw",), "Call any documented endpoint of one API with your token", "by-method", "ppx.cmd_raw:run",
         notice="{method} {path} on {api}",
         args=(_a("api", kind="choice", choices=RAW_APIS, help="which API"),
               _a("method", kind="choice", choices=METHODS, help="HTTP method"),
               _a("path", help="path under the API's base URL, or a full URL on the same host"),
               _a("--body", help="JSON body, or @file to read it from a file"),
               _a("--site", help="SharePoint site URL for spo (default: the profile's site)"))),
    Verb(("probe",), "Discover what this user can reach on the tenant; writes routing.json and a scrubbed report",
         "read", "ppx.probe:run", notice="Run write probes against scratch items {workspace} {model} {report}",
         args=(_a("--area", kind="multi", choices=AREAS, help="areas to probe (default: all)"),
               _a("--write", kind="flag", help="also run write probes against the scratch items given"),
               _a("--workspace", help="scratch workspace (name or id) for write probes"),
               _a("--model", help="scratch semantic model in that workspace"),
               _a("--report", help="scratch report in that workspace"),
               _a("--site", help="scratch SharePoint site URL"), _a("--list", help="scratch SharePoint list title"),
               _a("--app", help="scratch canvas app name")),
         escalate=(("--write", "change"),)),
    Verb(("verbs",), "List ppx verbs, their arguments and what each changes", "read", "ppx.cmd_verbs:run",
         args=(_a("group", help="only this group, e.g. pbi", required=False),), needs_data=False),
)


def find(tokens: list[str]) -> tuple[Verb | None, int]:
    """Return the verb with the longest path matching the start of `tokens`, and the path length."""
    best: tuple[Verb | None, int] = (None, 0)
    for verb in VERBS:
        n = len(verb.path)
        if n > best[1] and tuple(tokens[:n]) == verb.path:
            best = (verb, n)
    return best
