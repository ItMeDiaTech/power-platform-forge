"""Core verbs: sign-in, profiles, raw API calls, the probe and the verb list. Data only."""

from __future__ import annotations

from ppx.verbdefs import Verb, a

BACKENDS = ("az", "pac", "m365", "maker", "fab")
AREAS = ("device", "tokens", "toolchain", "pbi", "sp", "pa", "flows")  # probe order; "all" means every one
RAW_APIS = ("graph", "spo", "pbi", "fabric", "dataverse")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

VERBS: tuple[Verb, ...] = (
    Verb(("auth", "doctor"), "Check sign-ins, token reach per API, prerequisites and what is degraded", "read",
         "ppx.cmd_auth:doctor",
         args=(a("--audience", help="probe only this API: pbi, fabric, dataverse, powerplatform, graph, sharepoint"),),
         needs_data=False),
    Verb(("auth", "login"), "Sign in with one backend; opens a browser, so run it in your own terminal", "interactive",
         "ppx.cmd_auth:login", notice="Open a {backend} sign-in on this computer",
         args=(a("--backend", kind="choice", choices=BACKENDS, required=True, help="which tool to sign in"),
               a("--environment", help="Dataverse environment URL (pac)"),
               a("--tenant", help="tenant id or domain (m365, az)"),
               a("--app-id", help="client id of your own app registration (m365)"))),
    Verb(("auth", "logout"), "Sign out of one backend on this computer", "local", "ppx.cmd_auth:logout",
         notice="Sign out of {backend} on this computer",
         args=(a("--backend", kind="choice", choices=BACKENDS, required=True, help="which tool to sign out"),)),
    Verb(("auth", "app-spec"), "Print the exact app registration for SharePoint and Graph access (Tier B)", "read",
         "ppx.cmd_auth:app_spec", needs_data=False),
    Verb(("env", "add"), "Save a profile of defaults (environment, workspace, site)", "local", "ppx.cmd_env:add",
         notice="Save ppx profile {name} on this computer",
         args=(a("name", help="profile name"), a("--url", help="Dataverse environment URL"),
               a("--workspace", help="default Power BI workspace (name or id)"),
               a("--site", help="default SharePoint site URL"),
               a("--use", kind="flag", help="make it the active profile"))),
    Verb(("env", "set"), "Change a profile's defaults or the notice mode", "local", "ppx.cmd_env:set_",
         notice="Update ppx profile defaults or the notice mode on this computer",
         args=(a("name", help="profile name (default: the active one)", required=False),
               a("--url", help="Dataverse environment URL"), a("--workspace", help="default workspace"),
               a("--site", help="default site URL"),
               a("--notice-mode", kind="choice", choices=("notify", "confirm"),
                 help="notify: tell me what changes; confirm: ask before every change"))),
    Verb(("env", "rm"), "Delete a profile", "local", "ppx.cmd_env:rm",
         notice="Delete ppx profile {name} from this computer", args=(a("name", help="profile name"),)),
    Verb(("env", "use"), "Make a profile the active one", "local", "ppx.cmd_env:use",
         notice="Make {name} the active ppx profile", args=(a("name", help="profile name"),)),
    Verb(("env", "ls"), "List profiles and the notice mode", "read", "ppx.cmd_env:ls"),
    Verb(("raw",), "Call any documented endpoint of one API with your token", "by-method", "ppx.cmd_raw:run",
         notice="{method} {path} on the {api} API",
         args=(a("api", kind="choice", choices=RAW_APIS, help="which API"),
               a("method", kind="choice", choices=METHODS, help="HTTP method"),
               a("path", help="path under the API's base URL, or a full URL on the same host"),
               a("--body", help="JSON body, or @file to read it from a file"),
               a("--site", help="SharePoint site URL for spo (default: the profile's site)"))),
    # A plain probe only reads the tenant, but it rewrites routing.json on this computer, which
    # changes which sign-in later verbs use; --write also creates and deletes a scratch report.
    Verb(("probe",), "Discover what this user can reach on the tenant; writes routing.json and a scrubbed report",
         "local", "ppx.probe:run",
         notice="Probe what this user can reach and rewrite routing.json on this computer"
                " (write probes use scratch workspace {workspace})",
         args=(a("--area", help=f"comma-separated areas to probe: {','.join(AREAS)} or all (default: all)"),
               a("--write", kind="flag", help="also run write probes against the scratch items given"),
               a("--workspace", help="scratch workspace (name or id) for write probes"),
               a("--model", help="scratch semantic model in that workspace"),
               a("--report", help="scratch report in that workspace"),
               a("--site", help="scratch SharePoint site URL"), a("--list", help="scratch SharePoint list title"),
               a("--app", help="scratch canvas app name")),
         escalate=(("--write", "change"),)),
    Verb(("verbs",), "List ppx verbs, their arguments and what each changes", "read", "ppx.cmd_verbs:run",
         args=(a("group", help="only this group, e.g. pbi", required=False),), needs_data=False),
)
