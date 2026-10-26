---
name: sharepoint-ops
description: SharePoint lists, Microsoft Lists, document libraries and sites from the terminal with ppx. Use when the user asks what lists or columns a site has, wants list items, a CSV or JSON export of a list, a file from a library, which columns are indexed, which columns or formulas are non-delegable in Power Apps, why a list query hits the 5,000-item list view threshold, how many lookup or person columns a list has for the Power BI SharePoint Online list 2.0 connector, or before writing a canvas app formula or Power Query against a list. Also handles writes, such as creating a list or column, adding, updating or deleting items (eTag-safe, recycle bin), uploading a file, rendering the Power Query/TMDL for a list, turning a list into a Power BI semantic model and report, or repointing a model to another list.
---

# sharepoint-ops

`ppx sp ...` answers SharePoint questions in one call each, through CLI for Microsoft 365 signed in with the user's own app (Tier B) and Microsoft Graph. Every verb prints one compact JSON envelope; big results go to disk and the envelope returns the path.

Run verbs like this (or call the MCP tool `run` with `verb` and `args`, e.g. `{"verb": "sp fields ls", "args": ["--site", "Operations", "--list", "Tasks"]}`):

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}/scripts/ppx" ppx --data-dir "${CLAUDE_PLUGIN_DATA}" sp fields ls --site Operations --list Tasks
```

## Before the first SharePoint call

- Go straight to the verb. A missing sign-in or consent makes it exit 2 with the fix, so no doctor run is needed first. When a verb does exit 2 about tokens, run `ppx auth doctor --audience sharepoint` (it checks the profile's site; `--audience graph` covers `sp site search`) and follow the row's `fix`; Tier B setup is in `references/app-setup.md`.
- Sign-in is the user's job: hand them `ppx auth login --backend m365` to run in their own terminal. Never run `m365 login`, and never call `m365` or Graph directly; `ppx` is the only surface.

## Which verb answers which question

| Question | Verb (fewest calls) |
|---|---|
| What is the URL of the "Operations" site? | `sp site search Operations` (remembers names for `--site`) |
| What is this site? | `sp site get <url or name>` |
| Which lists and libraries does it have, how big? | `sp list ls --site <s>` |
| How big is one list; is it over 5,000 items? | `sp list get --site <s> --list <title>` |
| What columns, types, indexes; what will not delegate; how many lookups for Power BI? | `sp fields ls --site <s> --list <title>` |
| Show me some rows | `sp items ls --site <s> --list <title> --fields Title,Status,Owner/Title --top 50` |
| One row | `sp items get --site <s> --list <title> --id 42` |
| Every row, to a file | `sp export --site <s> --list <title> [--format json] [--out <path>]` |
| What is in this folder? | `sp files ls --site <s> --folder "Shared Documents/Reports"` |
| Get a file | `sp files get "Shared Documents/Reports/q3.xlsx" --site <s> [--out <path>]` |

`--site` takes a URL or a name `sp site search` or `sp site get` remembered; without it the profile's site is used. A name not remembered yet triggers one Graph search. `--list` takes a title, an id or a server-relative URL.

## Rules

- **Run `sp fields ls` before writing a canvas formula or a Power BI query against a list.** It gives internal names (what `--fields`, `--filter` and Power Query use), display names (what Power Apps formulas use), indexed columns, and each restricted column's Power Apps type in `pa`; `data.delegation` maps each such type to the operations that will not delegate to SharePoint. Answer "which columns are non-delegable" from the columns with `pa`, naming the column and `data.delegation[pa]`. A wide list's columns are saved to a file (`saved_to`); read it for the names.
- **Prefer `items ls --fields` over dumping.** Without `--fields` you get `Id,Title,Modified`. `Owner/Title` expands a person or lookup column. Pages default to 500 items; `--top N` sets a smaller page. A page over 3 KB is saved to a file (`saved_to`); read it only if needed. SharePoint cannot page past item 5,000, so beyond that use `sp export`.
- **Exports go to disk.** `sp export` writes every row (it pages past 5,000 itself) and returns `{path, rows, columns, format, elapsed}` (elapsed in seconds). Person and lookup columns are exported as display names unless the list has more than 12 of them (then ids, with a warning). Never paste an export into the conversation; read the file in slices if asked.
- **Filters use internal names and OData:** `--filter "Status eq 'Open' and Qty gt 5"`. On a list over 5,000 items SharePoint refuses a filter on a column that is not indexed; ppx warns before the call and names the fix (filter an indexed column, ask the owner to index it, or export).
- **Custom list forms (Power Apps forms on a list, JSON form formatting) are browser-only.** Say so and give the list URL; do not attempt them.
- `--out` to a file that exists stops with exit 2 until the user agrees and you re-run with `--yes`.

## Writes

Every write prints a change notice for the user and returns `data.changes` (what, target, backup, undo). Use `--dry-run` first whenever more than one item is touched.

| Task | Verb |
|---|---|
| New list or library | `sp list add --site <s> --title Tasks [--template DocumentLibrary]` |
| New column (added to the default view) | `sp fields add --site <s> --list Tasks --name Phase --type Choice --choices "Plan;Build"` |
| Add an item | `sp items add --site <s> --list Tasks --set '{"Title": "New", "Qty": 3}'` |
| Change an item | `sp items set --site <s> --list Tasks --id 42 --set Status=Done [--etag <eTag you read>]` |
| Delete items (to the recycle bin) | `sp items rm --site <s> --list Tasks --id 4,7`; by filter: run with `--dry-run`, show the matches, then the same line without it |
| Upload a file (up to 90 MB) | `sp files put report.xlsx --site <s> --folder "Shared Documents/Reports"` |
| Power Query/TMDL for a list | `sp m-snippet --site <s> --list Tasks [--view All] [--out t.tmdl]` |
| List to Power BI model + report | `pbi model-from-sp --site <s> --list Tasks --workspace <ws>` |
| Move a model to another list | `pbi repoint --model <m> --workspace <ws> --from-list Tasks --to-list "Tasks 2027" [--to-site <url>]` |

- **Values use internal names.** `--set Name=Value` sends text; numbers, booleans and null need a JSON object or `@file.json`. Get the names from `sp fields ls`.
- **Updates are eTag-safe.** `sp items get` returns the item's `eTag`; pass it to `items set --etag` so the update is refused if anyone edited the item after you read it (without `--etag`, `set` reads the current eTag just before writing, which protects only that moment). Exit 2 "changed since it was read" means someone else edited it: `sp items get` again, decide with the user, then set again with the new eTag. The old values are saved; `changes[0].undo` restores them.
- **Deletes go to the recycle bin.** `--permanent` skips it, needs `--yes`, and saves the items first. More than 50 items need `--yes`. A `--filter` delete refuses to run unless a `--dry-run` of the same filter listed exactly the same items within the hour: show the user `data.items` from the dry run before re-running.
- **Files are never replaced silently.** An existing name exits 2; `--overwrite --yes` backs the old file up first.
- **`model-from-sp`** creates the model (typed columns; person, lookup and multi-value columns are listed in `skipped`), an empty report bound to it, binds the user's sign-in for about an hour and starts one refresh (counts toward 8 a day on shared capacity). Durable credentials are a browser step: give the user the settings URL from `next_actions`. When the tenant cannot create items through Fabric it writes a Power BI project folder and names `pbi deploy` or Desktop.
- **`repoint`** compares both lists' columns first and stops (exit 2) on a missing or retyped column. It sets a list parameter if the model has one, else rewrites only the navigation step in the model's query (backup and `--restore` undo); it never rebuilds the query. Exit 2 "never re-renders" means the query is custom: hand off to `/powerbi-authoring:semantic-model-authoring`. With `--to-site` it does not refresh (the new site has no credentials yet): give the user the settings URL, or run the `pbi datasources bind --transient` command from `next_actions`.
- Power Automate's SharePoint connector is Standard tier (no premium licence); suggest a flow when the user wants writes on a schedule or trigger.

## Reading the envelope

| Field | Meaning |
|---|---|
| `warnings` | threshold and lookup-limit warnings, a non-indexed filter, output shortened to fit 4 KB |
| `next_actions` | the next page command, the sign-in line, or what to send IT |
| `data.more` (items ls) | another page exists; the exact command is in `next_actions` |
| `data.power_bi.lookup_columns` (fields ls) | person, lookup and workflow-status columns; the 2.0 connector fails a query past 12 (then `names` lists them) |
| `data.admin_request` | text the user sends IT when consent is blocked (`docs/ADMIN-REQUESTS.md#consent`) |

## Errors

| Exit | Message | Do |
|---|---|---|
| 2 | CLI for Microsoft 365 is not signed in | hand the user `ppx auth login --backend m365` |
| 2 | AADSTS65001 or AADSTS90094 consent | user signs in again and accepts consent; if an admin must approve, give them `data.admin_request` |
| 2 | AADSTS53003 and other policy codes | `ppx auth doctor --audience sharepoint`, then the doctor's fix |
| 1 | exceeds the list view threshold | filter on an indexed column (`sp fields ls`), or `sp export` |
| 1 | no site named ... | `sp site search "<other words>"`, or pass the URL |
| 1 | several sites are named ... | pass the URL from `data.candidates` |
| 1 | List '...' does not exist | `sp list ls --site <s>` to see the titles |
| 2 | item ... changed since it was read | `sp items get`, then set again |
| 2 | a delete by --filter needs a dry run first / matches different items | run the same line with `--dry-run`, show the matches |
| 2 | ... already exists in ... | ask the user, then `--overwrite --yes` |
| 2 | does not have the columns of ... (repoint) | `sp fields add` the missing columns, or pick another list |

## Limits that matter

- 5,000-item list view threshold (fixed in SharePoint Online): filter and sort only on indexed columns of big lists.
- 12 lookup columns per query (person, lookup, workflow status), including Created By and Modified By: the Power BI SharePoint Online list 2.0 connector fails past it.
- Power Apps: non-delegable formulas see only the first 500 rows (Data row limit, up to 10,000).

Details: `references/list-limits.md`. Column types, delegation and the Power BI connector: `references/connector-notes.md`. Which backend each verb uses and why: `references/graph-vs-m365.md`.

## Hand-offs

- Canvas app formulas or screens against the list: `/canvas-apps:canvas-app` (Microsoft's canvas-apps plugin, with the app open in Studio), after `sp fields ls`.
- A Power BI semantic model on the list: `pbi model-from-sp` for the plain case; for measures, relationships or expanding person and lookup columns afterwards, `/powerbi-authoring:semantic-model-authoring` (start from `sp m-snippet`).
- Which Power Apps read this list: the `powerapps-ops` skill.

## Never

- Print or paste tokens; never run `m365 util accesstoken get` or `az account get-access-token` yourself.
- Open SharePoint in a browser for a question a verb answers.
