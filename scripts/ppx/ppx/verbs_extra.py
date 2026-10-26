"""test, connector, copilot and dlp verbs: checks and hand-offs. Data only; see verbdefs for the fields and effects."""

from __future__ import annotations

from ppx.verbdefs import Verb, a

CONNECTION_TEMPLATES = ("NoAuth", "BasicAuth", "ApiKey", "OAuthGeneric", "OAuthAAD")  # pac connector init -ct
_FOLDER = a("--folder", required=True, help="custom connector folder (the OpenAPI 2.0 definition and the API "
                                            "properties file, plus an optional icon .png and script .csx)")

VERBS: tuple[Verb, ...] = (
    Verb(("test", "dax"), "Run a suite of DAX query/expect pairs against a semantic model; exits 1 when any fails",
         "read", "ppx.testing.dax:run",
         args=(a("--suite", required=True,
                 help='JSON file: {"model", "workspace", "tests": [{"name", "dax", "expect", "tolerance"}]}'),
               a("--model", help="semantic model name or id (default: the suite's model)"),
               a("--workspace", help="workspace name or id (default: the suite's, then the profile's)"))),
    Verb(("connector", "scaffold"), "Start a custom connector folder: pac connector init plus an OpenAPI 2.0 "
                                    "definition skeleton", "local", "ppx.connector.commands:scaffold",
         notice="Create custom connector files in folder {folder} on this computer",
         args=(a("--folder", required=True, help="new or empty folder for the connector files"),
               a("--title", required=True, help="connector title (OpenAPI info.title)"),
               a("--host", required=True, help="API host name, or the API's base URL such as https://api.contoso.com/v1"),
               a("--auth", kind="choice", choices=CONNECTION_TEMPLATES, default="NoAuth",
                 help="connection parameters pac generates (default NoAuth)"),
               a("--script", kind="flag", help="also generate a C# script file (script.csx)"))),
    Verb(("connector", "validate"), "Check a custom connector folder: OpenAPI 2.0 structure, connector limits and the "
                                    "API properties file; exits 1 on errors", "read",
         "ppx.connector.commands:validate", args=(_FOLDER,)),
    Verb(("connector", "deploy"), "Create a custom connector from a folder, or update one by --connector-id after "
                                  "backing it up", "change", "ppx.connector.commands:deploy",
         notice="Create or update a custom connector from folder {folder} in the Dataverse environment "
                "(connector id: {connector_id}; an update backs up the current definition first)",
         args=(_FOLDER, a("--connector-id", help="id (GUID) of the connector to update; omit to create a new one"),
               a("--solution", help="unique name of the solution to add the connector to")),
         # --yes is only needed (and accepted) when an update replaces the connector's icon or script.
         escalate=(("--yes", "irreversible"),)),
    Verb(("copilot", "publish-check"), "Check that a Copilot Studio agent was published after a given time "
                                       "(Dataverse bot.publishedon); exits 1 when it was not", "read",
         "ppx.copilot:publish_check",
         args=(a("bot", help="agent name, schema name or id"),
               a("--since", required=True, help="ISO 8601 time such as 2026-10-26T09:30:00Z (UTC when no offset)"))),
    Verb(("dlp", "check"), "Find which DLP policies mention these connectors; needs a Power Platform admin role, "
                           "otherwise exits 2 with the request for IT", "read", "ppx.dlp:check",
         args=(a("connectors", help="comma-separated connector ids, e.g. shared_sharepointonline,shared_office365"),)),
)
