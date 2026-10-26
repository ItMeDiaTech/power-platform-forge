"""solution verbs. Data only; see verbdefs for the fields and effects."""

from __future__ import annotations

from ppx.verbdefs import Verb, a

_ZIP = a("--zip", required=True, help="solution zip path")

VERBS: tuple[Verb, ...] = (
    Verb(("solution", "ls"), "List the solutions in the profile's Dataverse environment (cached; --refresh)", "read",
         "ppx.solution.inventory:ls"),
    Verb(("solution", "export"), "Export a solution to a zip on this computer; --managed adds <name>_managed.zip beside it",
         "local", "ppx.solution.inventory:export",
         notice="Export solution {name} from Dataverse to a zip on this computer",
         args=(a("--name", required=True, help="solution unique name or display name"),
               a("--managed", kind="flag", help="also export the managed package as <name>_managed.zip (unpack needs both)"),
               a("--out", help="zip path (default: <data>/exports/solutions/<time>/<name>.zip)"))),
    Verb(("solution", "unpack"), "Unpack <name>.zip and <name>_managed.zip into one folder for source control "
         "(packagetype Both; canvas apps stay .msapp)", "local", "ppx.solution.package:unpack",
         notice="Unpack solution zip {zip} into folder {folder} (overwrites files of the same name there)",
         args=(_ZIP, a("--folder", required=True, help="folder to write the unpacked components to")),
         escalate=(("--yes", "irreversible"),)),
    Verb(("solution", "pack"), "Pack an unpacked solution folder into zips (Both: <zip> and <zip>_managed.zip)", "local",
         "ppx.solution.package:pack",
         notice="Pack {folder} into zip {zip} (packagetype {packagetype}; default Both, plus a _managed zip, "
                "for an unpacked folder); same-name zips are replaced",
         args=(a("--folder", required=True, help="folder made by `ppx solution unpack`"),
               a("--zip", required=True, help="zip path to write"),
               a("--packagetype", kind="choice", choices=("Both", "Unmanaged", "Managed"),
                 help="which packages to build (default: Both for a folder from `ppx solution unpack`, "
                      "Unmanaged for a Git-integration YAML folder)")),
         escalate=(("--yes", "irreversible"),)),
    Verb(("solution", "check"), "Run solution checker on a zip and summarise the issues by severity", "local",
         "ppx.solution.check:run",
         notice="Upload solution zip {zip} to Microsoft's solution checker service and save the results on this "
                "computer (results folder: {out})",
         args=(_ZIP, a("--out", help="folder for the checker results (default: <data>/exports/solution-check/...)"))),
    Verb(("solution", "settings"), "Write the deployment settings file (connection references, environment variables) "
         "for a zip, keeping values already filled in", "local", "ppx.solution.deploy:settings",
         notice="Write deployment settings for solution zip {zip} to {out} (values already filled in are kept)",
         args=(_ZIP, a("--out", required=True, help="settings JSON to create or update"))),
    Verb(("solution", "import"), "Import a solution zip into the profile's environment; exits 2 while connection "
         "references or environment variables have no value", "irreversible", "ppx.solution.deploy:import_",
         notice="Import solution zip {zip} into the Dataverse environment (settings file: {settings}); "
                "ppx cannot undo an import",
         args=(_ZIP, a("--settings", help="deployment settings JSON from `ppx solution settings`"),
               a("--async", kind="flag", help="import asynchronously (large solutions); pac waits up to 60 minutes"),
               a("--activate-plugins", kind="flag", help="activate plug-ins and classic workflows (cloud flows turn on "
                                                         "by themselves once their connection references have connections)"),
               a("--publish-changes", kind="flag", help="publish customizations after the import (unmanaged)"))),
)
