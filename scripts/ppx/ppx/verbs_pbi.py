"""pbi verbs. Data only; see verbdefs for the fields and effects."""

from __future__ import annotations

from ppx.verbdefs import Verb, a

TYPES = ("reports", "models", "dashboards", "dataflows")
WORKSPACE = a("--workspace", help="workspace name or id; 'My workspace' works (default: the profile's workspace)")
MODEL = a("--model", required=True, help="semantic model name or id")
OUT = a("--out", help="parent folder to write into (default: the ppx exports folder)")
DESKTOP = a("--desktop", kind="flag", help="export the PBIX for a Power BI Desktop round trip instead")
PUSH = (a("--from", help="the folder pbi pull wrote, after editing"),
        a("--force", kind="flag", help="overwrite even if the service copy changed since the pull"),
        a("--allow-part-removal", kind="flag", help="allow deleting parts that are missing locally"),
        a("--restore", help="push the backup with this timestamp instead (from an earlier push's undo)"))
PURGE = a("--purge", kind="flag", help="allow clearing the model's data when the change needs it (needs --yes and a "
                                       "refresh afterwards)")
NO_REFRESH = a("--no-refresh", kind="flag", help="skip the refresh that applies the change")

VERBS: tuple[Verb, ...] = (
    Verb(("pbi", "ws", "ls"), "List the Power BI workspaces you can see and which are on a capacity (cached daily)",
         "read", "ppx.pbi.inventory:ws_ls"),
    Verb(("pbi", "ls"), "List reports, models, dashboards and dataflows in a workspace, with each model's last "
                        "refresh (cached daily; --refresh fetches again)", "read", "ppx.pbi.inventory:ls",
         args=(WORKSPACE, a("--type", kind="choice", choices=TYPES, help="only this kind of item"))),
    Verb(("pbi", "diff"), "What changed in a workspace since an inventory at least N days old", "read",
         "ppx.pbi.diff:run",
         args=(WORKSPACE, a("--since", default="7d", help="compare with the newest snapshot at least this old (7d)"))),
    Verb(("pbi", "report", "show"), "One report: pages, bound model, format (PBIR or PBIRLegacy), ownership",
         "read", "ppx.pbi.show:report", args=(a("report", help="report name or id"), WORKSPACE)),
    Verb(("pbi", "model", "show"), "One semantic model: data sources, parameters, owner, last refreshes, "
                                   "whether you can query it", "read", "ppx.pbi.show:model",
         args=(a("model", help="semantic model name or id"), WORKSPACE)),
    Verb(("pbi", "query"), "Run one DAX query against a semantic model; rows come back as CSV (--top, default 50)",
         "read", "ppx.pbi.query:run",
         args=(a("--model", required=True, help="semantic model name or id"),
               a("--dax", required=True, help="the DAX query, or @file to read it from a file"), WORKSPACE)),
    Verb(("pbi", "history"), "A model's refresh history with the cause of each failure and its fix (--top, default 5)",
         "read", "ppx.pbi.refresh:history",
         args=(a("--model", required=True, help="semantic model name or id"), WORKSPACE,
               a("--failures", kind="flag", help="only failed refreshes, from the last 60"))),
    # The 8th refresh of a day on shared capacity needs --yes; ppx enforces that itself.
    Verb(("pbi", "refresh"), "Refresh a semantic model now; shared capacity allows 8 a day, scheduled ones included",
         "change", "ppx.pbi.refresh:run", notice="Refresh Power BI model {model} in workspace {workspace}",
         args=(a("--model", required=True, help="semantic model name or id"), WORKSPACE,
               a("--wait", kind="flag", help="wait for the result until --timeout (seconds)")),
         escalate=(("--yes", "irreversible"),)),
    # -- edit and publish loop (phase 3) --------------------------------------------------------
    Verb(("pbi", "pull", "report"), "Save a report's PBIR to a folder for editing (--desktop: PBIX)",
         "read", "ppx.pbi.definition:pull", args=(a("report", help="report name or id"), WORKSPACE, OUT, DESKTOP),
         notice="Save report {report} to folder {out} on this computer", escalate=(("--out", "local"),)),
    Verb(("pbi", "pull", "model"), "Save a model's TMDL to a folder for editing (--desktop: PBIX)",
         "read", "ppx.pbi.definition:pull", args=(a("model", help="semantic model name or id"), WORKSPACE, OUT, DESKTOP),
         notice="Save model {model} to folder {out} on this computer", escalate=(("--out", "local"),)),
    Verb(("pbi", "push", "report"), "Write a pulled report folder back (drift check, backup, undo)", "change",
         "ppx.pbi.definition:push",
         notice="Overwrite the definition of Power BI report {report} in workspace {workspace} (a backup is taken "
                "first)", args=(a("report", help="report name or id"), WORKSPACE, *PUSH)),
    Verb(("pbi", "push", "model"), "Write a pulled model folder back (drift check, backup, undo)", "change",
         "ppx.pbi.definition:push",
         notice="Overwrite the definition of Power BI model {model} in workspace {workspace} (a backup is taken "
                "first; with --purge the model's data is cleared until its next refresh)",
         args=(a("model", help="semantic model name or id"), WORKSPACE, *PUSH, PURGE),
         escalate=(("--purge", "irreversible"),)),
    Verb(("pbi", "publish"), "Publish a PBIX; never replaces a same-named model by default", "change",
         "ppx.pbi.publish:publish",
         notice="Publish {pbix} to Power BI workspace {workspace} (name conflict: {name_conflict}; Abort if not given)",
         args=(a("--pbix", required=True, help="the .pbix file; its name is the model and report name"), WORKSPACE,
               a("--name-conflict", kind="choice", choices=("Abort", "Overwrite", "CreateOrOverwrite"), default="Abort",
                 help="when a model of that name exists: Abort (default), or replace it (needs --yes)"),
               a("--override-labels", kind="flag", help="let the file's sensitivity labels replace the service's")),
         escalate=(("--name-conflict=Overwrite", "irreversible"), ("--name-conflict=CreateOrOverwrite", "irreversible"))),
    Verb(("pbi", "deploy"), "Deploy a PBIP project's model and report (backups first)", "change",
         "ppx.pbi.publish:deploy",
         notice="Deploy Power BI project {pbip} to workspace {workspace}, creating or replacing items of the same "
                "name (with --purge a model's data is cleared until its next refresh)",
         args=(a("--pbip", required=True, help="the project folder (or its .pbip file)"), WORKSPACE,
               a("--force", kind="flag", help="replace items even when the service copy changed or has no record"),
               a("--allow-part-removal", kind="flag", help="allow deleting service parts missing from the project"),
               PURGE),
         escalate=(("--purge", "irreversible"),)),
    Verb(("pbi", "datasources", "ls"), "A model's data sources and gateway ids",
         "read", "ppx.pbi.datasources:ls", args=(MODEL, WORKSPACE)),
    # --yes on bind, update and params set replaces durable credentials or spends the 8th shared-capacity
    # refresh, so the hook asks.
    Verb(("pbi", "datasources", "bind"), "Data source credentials: settings page, or --transient (1 h)", "change",
         "ppx.pbi.datasources:bind",
         notice="Set data source credentials of Power BI model {model} in workspace {workspace} (with --transient: "
                "take it over and bind your sign-in for about an hour)",
         args=(MODEL, WORKSPACE, a("--transient", kind="flag", help="bind through the API now (expires in about an "
                                                                    "hour), then refresh once"), NO_REFRESH),
         escalate=(("--yes", "irreversible"),)),
    Verb(("pbi", "datasources", "update"), "Repoint a model's one data source, then refresh",
         "change", "ppx.pbi.datasources:update",
         notice="Repoint the data source of Power BI model {model} in workspace {workspace}",
         args=(MODEL, WORKSPACE, a("--url", help="new URL (SharePoint site, OData feed)"),
               a("--server", help="new server"), a("--database", help="new database"), a("--path", help="new path"),
               NO_REFRESH),
         escalate=(("--yes", "irreversible"),)),
    Verb(("pbi", "params", "set"), "Set a model's Power Query parameters, then refresh", "change",
         "ppx.pbi.params:set_", notice="Set parameters {set} on Power BI model {model} in workspace {workspace}",
         args=(MODEL, WORKSPACE, a("--set", required=True, help="Name=Value, or @file.json holding {\"Name\": \"Value\"}"),
               NO_REFRESH),
         escalate=(("--yes", "irreversible"),)),
    Verb(("pbi", "clone"), "Copy a report, optionally to another workspace or model", "change",
         "ppx.pbi.clone:clone",
         notice="Copy Power BI report {report} as {name} (target workspace: {to_workspace}; the same if not given)",
         args=(a("report", help="report name or id"), WORKSPACE, a("--name", required=True, help="name of the copy"),
               a("--to-workspace", help="workspace for the copy (default: the same)"),
               a("--model", help="model for the copy, in the target workspace (default: the same model)"))),
    Verb(("pbi", "rebind"), "Point a report at another semantic model", "change", "ppx.pbi.clone:rebind",
         notice="Point Power BI report {report} in workspace {workspace} at model {model}",
         args=(a("report", help="report name or id"), WORKSPACE, MODEL,
               a("--model-workspace", help="workspace of the model (default: the report's)"))),
    Verb(("pbi", "dashboards", "ls"), "Service dashboards (pinned tiles) in a workspace", "read",
         "ppx.pbi.dashboards:ls", args=(WORKSPACE,)),
    Verb(("pbi", "dashboards", "tiles"), "A dashboard's tiles and the report or model behind each",
         "read", "ppx.pbi.dashboards:tiles", args=(a("dashboard", help="dashboard name or id"), WORKSPACE)),
    Verb(("pbi", "dashboards", "create"), "Create an empty service dashboard", "change", "ppx.pbi.dashboards:create",
         notice="Create Power BI dashboard {name} in workspace {workspace}",
         args=(a("name", help="dashboard name"), WORKSPACE)),
    Verb(("pbi", "dashboards", "clone-tile"), "Copy a tile onto another dashboard", "change",
         "ppx.pbi.dashboards:clone_tile",
         notice="Copy tile {tile} of Power BI dashboard {dashboard} onto dashboard {to}",
         args=(a("dashboard", help="source dashboard name or id"), WORKSPACE,
               a("--tile", required=True, help="tile title or id"), a("--to", required=True, help="target dashboard"),
               a("--to-workspace", help="workspace of the target dashboard (default: the same)"),
               a("--report", help="report the copy links to, in the target workspace"),
               a("--model", help="model the copy links to, in the target workspace"))),
    Verb(("pbi", "screenshot"), "Capture report pages from Power BI Desktop (Desktop Bridge)", "read",
         "ppx.pbi.screenshot:run",
         args=(a("report", required=False, help="report name or id, for the service URL when Desktop is not open"),
               WORKSPACE, a("--page", help="page id (from pbi pull or report show); default: every page"),
               a("--pid", kind="int", help="Desktop process id when several are open"),
               a("--scale", kind="int", help="1 to 3 (bridge default 2)"),
               a("--out", help="folder for the images (default: the ppx exports folder)")),
         notice="Save screenshots of report {report} to folder {out} on this computer", escalate=(("--out", "local"),)),
    Verb(("pbi", "export-png"), "Export report pages as PNG (capacity and tenant setting only)", "read",
         "ppx.pbi.export:run",
         args=(a("report", help="report name or id"), WORKSPACE, a("--page", help="page name or display name "
                                                                               "(default: every page, as a zip)"),
               a("--out", help="file to write (default: the ppx exports folder)")),
         notice="Save a PNG export of report {report} to {out} on this computer", escalate=(("--out", "local"),)),
    Verb(("pbi", "share"), "Give a person a role in a workspace", "change", "ppx.pbi.share:run",
         notice="Give {user} the role {role} (Viewer if not given) in Power BI workspace {workspace}",
         args=(WORKSPACE, a("--user", required=True, help="the person's sign-in name (user@domain)"),
               a("--role", kind="choice", choices=("Viewer", "Contributor", "Member", "Admin"), default="Viewer",
                 help="workspace role (default Viewer)"))),
    # -- SharePoint bridge (phase 5) ----------------------------------------------------------------
    Verb(("pbi", "model", "create"), "Create a semantic model from a TMDL folder (Fabric, branch A)", "change",
         "ppx.pbi.model_create:run", notice="Create Power BI model from {from} in workspace {workspace}",
         args=(a("--from", required=True, help="the <name>.SemanticModel folder (TMDL), or a project holding one"),
               WORKSPACE)),
    # --yes only matters for the check refresh (8th of the day on shared capacity), so the hook asks then.
    Verb(("pbi", "model-from-sp"), "Turn a SharePoint list into a semantic model and an empty report, bind and "
                                   "refresh once", "change", "ppx.pbi.from_sharepoint:run",
         notice="Create a Power BI model and report from SharePoint list {list} in workspace {workspace} (named "
                "after the list unless --name), then bind your sign-in for about an hour and refresh once",
         args=(a("--site", help="site URL or remembered name (default: the profile's site)"),
               a("--list", required=True, help="list title, id, or server-relative URL"), WORKSPACE,
               a("--name", help="model and report name (default: the list title)"),
               a("--view", kind="choice", choices=("Default", "All"), default="Default",
                 help="ViewMode: the list's default view (Default) or every column (All)"),
               a("--out", help="folder for the Power BI project when Fabric cannot create items here"), NO_REFRESH),
         escalate=(("--yes", "irreversible"),)),
    Verb(("pbi", "repoint"), "Point a model at another SharePoint list with the same columns, then refresh",
         "change", "ppx.pbi.repoint:run",
         notice="Take over Power BI model {model} in workspace {workspace} and point it from SharePoint list "
                "{from_list} to {to_list} (on another site with --to-site), then refresh it when the site stays the same",
         args=(MODEL, WORKSPACE, a("--from-list", required=True, help="the list the model reads now"),
               a("--to-list", required=True, help="the list to read instead"),
               a("--site", help="site of --from-list (default: the model's SharePoint data source)"),
               a("--to-site", help="site of --to-list (default: the same site)"), NO_REFRESH),
         escalate=(("--yes", "irreversible"),)),
)
