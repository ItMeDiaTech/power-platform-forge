"""pa, mda and data verbs (Power Apps, flows, model-driven apps, Dataverse data). Data only; see verbdefs."""

from __future__ import annotations

from ppx.verbdefs import Verb, a

_APP = a("app", help="canvas app name or id")
_MDA = a("app", help="model-driven app name, unique name or id")
_ENV = a("--environment", help="Power Apps environment id, e.g. Default-<tenant id> (default: every environment "
                                "you can see)")
_OUT = a("--out", help="folder to write the files to (default: under the ppx data directory)")

VERBS: tuple[Verb, ...] = (
    Verb(("pa", "env", "ls"), "List the Dataverse environments you can reach", "read", "ppx.pa.env:ls"),
    Verb(("pa", "env", "who"), "Show who you are in the profile's environment and your security roles", "read",
         "ppx.pa.env:who"),
    Verb(("pa", "ls"), "List canvas and model-driven apps; --list finds the apps that read a SharePoint list",
         "read", "ppx.pa.inventory:ls",
         args=(a("--type", kind="choice", choices=("canvas", "mda"), help="only this kind of app"),
               a("--list", help="<site>/<list> or a list name: apps whose data sources include it"),
               a("--grep", help="text to find in app names, data sources and screens"), _ENV)),
    Verb(("pa", "index"), "Download canvas app source into the local index (data sources, screens, findings)",
         "local", "ppx.pa.index:run",
         notice="Download canvas app source into the ppx index on this computer",
         args=(a("--all", kind="flag", help="every app, not only new or changed ones"),
               a("--stale", kind="flag", help="only apps that are new or changed since they were indexed (default)"),
               _ENV)),
    Verb(("pa", "get"), "Download one canvas app's .msapp and read-only .pa.yaml source", "local",
         "ppx.pa.source:get", notice="Download the source of canvas app {app} on this computer "
                                    "(with --yes: replace an app.msapp already in --out)",
         args=(_APP, _OUT, _ENV), escalate=(("--yes", "irreversible"),)),
    Verb(("pa", "inspect"), "Find SharePoint delegation problems in a canvas app (findings, not source)", "local",
         "ppx.pa.inspect:run",
         notice="Download the source of canvas app {target} into the ppx index on this computer if it is missing",
         args=(a("target", help="canvas app name or id, or a folder of .pa.yaml source"), _ENV)),
    Verb(("pa", "share"), "Share a canvas app with a user (the user is e-mailed)", "change", "ppx.pa.share:run",
         notice="Share canvas app {app} with {user} as {role}; they are e-mailed about it",
         args=(_APP, a("--user", required=True, help="user principal name (sign-in e-mail)"),
               a("--role", kind="choice", choices=("CanView", "CanEdit"), required=True,
                 help="CanView: use the app; CanEdit: co-owner"), _ENV)),
    Verb(("pa", "versions"), "List a canvas app's saved versions", "read", "ppx.pa.versions:ls", args=(_APP, _ENV)),
    Verb(("pa", "restore"), "Restore a canvas app to an earlier version (records the current one for undo)",
         "irreversible", "ppx.pa.versions:restore",
         notice="Restore canvas app {app} to its earlier version {version}; the version it replaces is recorded",
         args=(_APP, a("--version", required=True, help="version id from `ppx pa versions`"), _ENV)),
    Verb(("pa", "flows", "ls"), "List the solution-aware cloud flows in the profile's environment", "read",
         "ppx.pa.flows:ls"),
    Verb(("pa", "flows", "runs"), "Show recent runs of one cloud flow (kept 28 days)", "read", "ppx.pa.flows:runs",
         args=(a("flow", help="flow name or id"),)),
    Verb(("mda", "ls"), "List model-driven apps in the profile's environment", "read", "ppx.mda.apps:ls"),
    Verb(("mda", "show"), "Show a model-driven app and its components", "read", "ppx.mda.apps:show", args=(_MDA,)),
    Verb(("mda", "forms"), "List an app's forms; with --form, save that form's XML for editing", "read",
         "ppx.mda.apps:forms", notice="Save the XML of form {form} of model-driven app {app} on this computer",
         args=(_MDA, a("--form", help="form name or id to save"), _OUT), escalate=(("--form", "local"),)),
    Verb(("mda", "views"), "List an app's views; with --view, save that view's FetchXML and layout for editing",
         "read", "ppx.mda.apps:views",
         notice="Save the FetchXML and layout of view {view} of model-driven app {app} on this computer",
         args=(_MDA, a("--view", help="view name or id to save"), _OUT), escalate=(("--view", "local"),)),
    Verb(("mda", "sitemap"), "Save a model-driven app's sitemap XML for editing", "local", "ppx.mda.apps:sitemap",
         notice="Save the sitemap XML of model-driven app {app} on this computer", args=(_MDA, _OUT)),
    Verb(("mda", "publish"), "Write edited sitemap, form and view XML back, add components, and publish once",
         "change", "ppx.mda.publish:run",
         notice="Write edited XML and any --add components to model-driven app {app}, then publish it (backups "
                "first; publishing also publishes other makers' pending drafts, which no backup undoes)",
         args=(_MDA, a("--from", help="folder of edited files saved by mda sitemap|forms|views"),
               a("--add", help="components to add, comma-separated: view:<id>,form:<id>")),
         escalate=(("--yes", "irreversible"),)),
    Verb(("data", "query"), "Read rows from a Dataverse table (use --fields and --top)", "read", "ppx.data.query:query",
         args=(a("--table", required=True, help="entity set name, e.g. accounts"),
               a("--filter", help="OData $filter, e.g. statecode eq 0"))),
    # Irreversible as a whole: the notice hook cannot read the file, and Remove/Patch/Clear in it cannot be undone.
    Verb(("data", "fx"), "Run a file of Power Fx against Dataverse (pac power-fx run)", "irreversible",
         "ppx.data.query:fx",
         notice="Run the Power Fx in {file} against the profile's Dataverse environment (it can delete or "
                "overwrite rows)",
         args=(a("--file", required=True, help="file of Power Fx formulas, one per line"),)),
)
