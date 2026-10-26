# Importing a solution

Read this before any `ppx solution import`. An import is irreversible for ppx: the change notice asks the user, and ppx refuses without `--yes`.

## Before

1. The user asked for this import, into this environment, in the conversation.
2. `ppx env ls`: the active profile's `url` is the target. Switch with `ppx env use <name>`. The envelope's `target_env` and `data.changes[].target` name it again.
3. The importing user needs a role that can create every component type in the solution. System Customizer covers most, but not plug-in assemblies; those need System Administrator (learn.microsoft.com/power-apps/maker/data-platform/import-update-export-solutions).
4. The zip is at most 95 MB.
5. Managed zip for test and production. An unmanaged import overwrites existing customizations of the same components and cannot be undone; use it only between development environments.

## Steps

```bash
ppx solution settings --zip <name>_managed.zip --out settings/test.json   # once per target; keeps filled values
# the user fills every empty ConnectionId and Value
ppx solution import --zip <name>_managed.zip --settings settings/test.json --dry-run
# show the user the envelope; on their yes:
ppx solution import --zip <name>_managed.zip --settings settings/test.json --async --yes
```

Add `--publish-changes` for an unmanaged import (it lands as a draft). Add `--activate-plugins` when the solution carries plug-in steps or classic workflows that should start active.

`ppx` exits 2 without running pac while any connection reference lacks a `ConnectionId` or any environment variable has neither a `Value` nor a default value in the solution, and lists them in `data.unset`. A variable whose definition carries a default shows `"set": true, "default": true` and needs no `Value`: Dataverse uses the default when no current value exists (learn.microsoft.com/power-apps/maker/data-platform/environmentvariables). Leave its `Value` empty unless the target needs something else, because a current value pins it and later default changes no longer reach that environment.

## Connection references

- Each `ConnectionId` must be a connection in the target environment that the importing user owns or that is shared with them; the import validates this (learn.microsoft.com/power-platform/alm/conn-ref-env-variables-build-tools).
- OAuth connections cannot be shared with other users, only with a service principal. When the importing user does not own a needed connection, the user creates their own in the target (make.powerapps.com > Connections > New connection) and uses its id.
- Custom connectors must arrive in an earlier, separate solution before the solution whose connection references use them.

## Cloud flows after the import

Flows that were on at export turn on during import when every connection reference has a connection the importing user can use. An update to a flow that already exists keeps the flow's current state (learn.microsoft.com/power-automate/import-flow-solution). To see what is off:

```bash
ppx pa flows ls
```

Or directly from Dataverse (category 5 is a cloud flow; `statecode` 0 is off, 1 is on, 2 suspended):

```bash
ppx raw dataverse GET "workflows?%24select=name,statecode,workflowid&%24filter=category%20eq%205%20and%20statecode%20ne%201"
```

To turn one on, fix its connection first, then either the user switches it on in make.powerautomate.com, or (with the user's yes; this is a change) `ppx raw dataverse PATCH "workflows(<workflowid>)" --body '{"statecode": 1}'` (learn.microsoft.com/power-automate/manage-flows-with-code). Check with `ppx pa flows runs <flow>` after the first trigger.

## When it fails

- `data.import_job.result` is `failure` and `data.import_job.error` holds Dataverse's message. Missing dependencies mean the target lacks a solution or component this one needs: import that first.
- "Another solution is being installed" style errors: concurrent solution operations; wait and retry once.
- pac timed out: the import continues on the server. Re-read the job with the `ppx raw dataverse GET` command in `next_actions` before doing anything else; never start a second import of the same solution while one is running.

## Pipelines

Power Platform pipelines run imports for makers once an administrator has set them up. Every environment in a pipeline except development and the host must be a Managed Environment, and Managed Environments need premium licences (learn.microsoft.com/power-platform/alm/pipelines). ppx does not set any of this up. When the user wants pipelines, give them this request for IT, filled in:

> Please set up a Power Platform deployment pipeline for solution `<name>`: development environment `<dev url>`, target stages `<test url>`, `<prod url>`. The targets need to be Managed Environments. I need to be able to run deployments for this solution from the development environment.
