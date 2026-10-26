---
name: pbi-authoring
description: Edit and publish Power BI from the terminal for an ordinary user - add or change a measure, description or format string in a semantic model (TMDL), change report pages and visuals (PBIR), push the edit back without overwriting a colleague's changes, publish a PBIX or deploy a PBIP project, repoint a data source, change Power Query parameters, set data source credentials, copy or rebind a report, share a workspace, copy dashboard tiles, take screenshots or export pages as PNG. Use when the user says "add a measure", "change the format", "edit the report", "move this visual", "publish this pbix", "deploy the project", "point the model at the new SharePoint site", "change the parameter", "fix the credentials", "clone the report", "give X access to the workspace", "screenshot page 1". Reads and refresh questions belong to pbi-ops.
---

# pbi-authoring

Pull a definition to disk, edit files, push it back: ppx guards every overwrite (drift check, backup, undo), and Microsoft's `powerbi-authoring` plugin does the structural editing. Never edit through the browser when a verb exists.

Run commands like this (the MCP tool `run` with `verb` and `args` is the same thing without a shell):

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}/scripts/ppx" ppx --data-dir "${CLAUDE_PLUGIN_DATA}" pbi pull model "Sales" --workspace "Finance"
```

Below, `ppx` stands for that prefix. Run `ppx auth doctor` once per session first (skill `pp-auth-doctor`).

## Which path this tenant allows

`routing.json` (written by `ppx probe`) decides; the envelope's `branch` says which ran.

| Branch | When | Edit loop |
|---|---|---|
| A (default) | Fabric definitions can be read and written | `pbi pull` -> edit files -> `pbi push` |
| F | the REST write is refused but the Fabric CLI can deploy | same, push runs `fab deploy` (no `--purge`, no `--restore`) |
| D | no definition write at all | `pbi pull ... --desktop` -> edit in Power BI Desktop -> save -> `pbi publish` |

When a push or pull exits 2 saying Fabric refused, switch to the `--desktop` command it gives (details: `references/desktop-round-trip.md`).

## The edit loop

| Step | Call |
|---|---|
| Get the model's TMDL | `ppx pbi pull model "Sales" --workspace "Finance"` -> `data.folder` (`<name>.SemanticModel`) |
| Get the report's PBIR | `ppx pbi pull report "Sales" --workspace "Finance"` -> `<name>.Report` folder |
| Measure, description, format string | edit `definition/tables/<Table>.tmdl` directly (`references/tmdl-edit-loop.md`); no MCP needed |
| New tables, relationships, calculation groups, RLS | `/powerbi-authoring:semantic-model-authoring` on the pulled folder |
| Pages, visuals, filters, bookmarks | `/powerbi-authoring:powerbi-report-cli` on the `.Report` folder (`references/pbir-edit-loop.md`) |
| Write it back | `ppx pbi push model "Sales" --workspace "Finance" --from "<folder>"` |
| Check it | `ppx pbi query --model "Sales" --workspace "Finance" --dax 'EVALUATE ROW("v", [Total Sales])'` |

Pulls refuse a folder that already holds files: push it or pass another `--out`. `data.files` lists the first parts; read files from disk instead of asking for more output.

## Push rules (never silent)

- ppx re-reads the service copy first and saves it to `<data>/backups/pbi/<item id>/<timestamp>/`; every push returns `undo`: `ppx pbi push ... --restore <timestamp>`.
- Exit 2 "changed since the pull": someone edited it in the service. Pull into a new folder and redo the edit. Only pass `--force` when the user agrees to overwrite their change.
- Exit 2 "parts would be deleted": a file is missing locally. Restore it, or pass `--allow-part-removal` when the deletion is the edit.
- Exit 2 "needs the model's data cleared": re-run with `--purge --yes` only after the user agrees; the model is empty until `ppx pbi refresh` (8 a day on shared capacity).
- Nothing changed locally: the push is a no-op (`unchanged: true`). `--dry-run` lists the changed, added and removed parts without writing.
- The PostToolUse hook checks every PBIR JSON file you write against its schema; when it reports problems, fix them before pushing.

## Publish and deploy

- `ppx pbi publish --pbix "<file>.pbix" --workspace X`: the file name is the model and report name. Default `--name-conflict Abort` never replaces anything; when a same-named model exists it exits 2 listing its ids and owner. Replacing needs `--name-conflict CreateOrOverwrite --yes`, which first saves a PBIX backup of the current model (no backup, no publish). Sensitivity labels in the service are kept unless `--override-labels`.
- `ppx pbi deploy --pbip "<project folder>" --workspace X`: creates or updates the `.SemanticModel` and `.Report` items of that name (Fabric needs definition.pbir by connection; ppx rewrites `byPath`). Updating an item ppx never pulled or deployed needs `--force`; every update is backed up first.
- PBIX files over 1 GB cannot go through the API: publish from Desktop.

## Data sources, parameters, credentials

- Credentials: `ppx pbi datasources bind --model X` returns the model's settings page URL (exit 2). The user signs in there once; that is the durable fix. `--transient` takes over the model and binds the user's own token through the API for about an hour, for one check refresh; it refuses when scheduled refresh worked in the last 7 days (`references/bind-credentials.md`).
- Repoint one data source: `ppx pbi datasources update --model X --url <new site>` (or `--server/--database/--path`). It exits 2 with the right fix when the source comes from a parameter, the model has several sources, the type would change, or an incremental refresh policy exists. The undo command (`ppx raw pbi POST ... --body @reverse.json`) is in the envelope and the audit log.
- Parameters: `ppx pbi params set --model X --set "Name=Value"` (or `--set @params.json` with an object of names and values). Names are case-sensitive.
- Each of these ends with one refresh through the 8-a-day gate; `--no-refresh` skips it and hands back the refresh command. Only the model's owner may change sources or parameters; exit 2 names the take-over command.

## Reports, dashboards, sharing, pictures

| Need | Call |
|---|---|
| Copy a report (other workspace, other model) | `ppx pbi clone "Sales" --workspace X --name "Sales copy" [--to-workspace Y] [--model M]` |
| Point a report at another model | `ppx pbi rebind "Sales" --workspace X --model "Sales v2"` (undo in the envelope) |
| Pinned-tile dashboards | `ppx pbi dashboards ls`, `tiles <dashboard>`, `create <name>`, `clone-tile <dashboard> --tile <title> --to <dashboard>` |
| Give someone access | `ppx pbi share --workspace X --user kim@contoso.com --role Viewer` (Viewer, Contributor, Member, Admin); adds people only, exit 2 when they already have another role |
| Screenshot from Power BI Desktop | `ppx pbi screenshot "Sales" --workspace X [--page <page id>]`: exit 2 with the report URL when no Desktop is connected to the Desktop Bridge |
| PNG from the service | `ppx pbi export-png "Sales" --workspace X [--page "Overview"]`: capacity workspaces only (`references/capacity-gated.md`) |

"Dashboard" in a user's words usually means a report: use the report verbs unless they mean pinned tiles.

## Exit codes and hand-offs

0 done; 1 failed (bad arguments, the API error is in the message); 2 the user or an admin must act (overwrite guard, `--yes` needed, sign-in, permission, capacity, settings page). Relay `next_actions` as given and never add `--yes`, `--force` or `--purge` without the user's agreement. Admin-only fixes point at `docs/ADMIN-REQUESTS.md`.

- Model structure: `/powerbi-authoring:semantic-model-authoring` (plugin `powerbi-authoring@fabric-collection`, local Modeling MCP).
- Report visuals and PBIR validation: `/powerbi-authoring:powerbi-report-cli` (`powerbi-report-author validate <folder>`).
- Desktop automation: the `powerbi-desktop` bridge CLI (npm `@microsoft/powerbi-desktop-bridge-cli`).
- Reads, queries, refresh history and refreshes: skill `pbi-ops`.
