---
name: pbi-ops
description: Power BI from the terminal for an ordinary user - list workspaces, reports, semantic models (datasets), dashboards and dataflows; show a report's pages and bound model or a model's data sources, parameters and owner; run a DAX query (row counts, totals, measure values); read refresh history and explain why a refresh failed; refresh a model within the 8-a-day shared-capacity budget; see what changed in a workspace since last week. Use when the user asks "what's in workspace X", "when did model Y last refresh", "why did the refresh fail", "how many rows", "what does this measure return", "refresh the dataset", "what changed", or names a Power BI report, model, dataset or workspace. Answers in a few compact ppx calls instead of the browser. Editing models or reports hands off to pbi-authoring.
---

# pbi-ops

Every answer here is one or two `ppx pbi` calls returning one small JSON envelope. Never open app.powerbi.com for a question ppx can answer, and never scrape the portal.

Run commands like this (the MCP tool `run` with `verb` and `args` is the same thing without a shell):

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}/scripts/ppx" ppx --data-dir "${CLAUDE_PLUGIN_DATA}" pbi ls --workspace "Sales"
```

Below, `ppx` stands for that prefix.

## Start of a session

1. `ppx auth doctor` once per session (skill `pp-auth-doctor`). If the `pbi` row is blocked, stop and hand the user its fix line.
2. Refresh the inventory of the workspaces you will work in once per session: `ppx pbi ls --workspace "<name>" --refresh`. Later calls answer from that cache, and daily snapshots are what `pbi diff` compares.

## Which verb answers which question

| Question | Call |
|---|---|
| Which workspaces can I see, which are on a capacity? | `ppx pbi ws ls` |
| What reports/models are in workspace X? | `ppx pbi ls --workspace X [--type reports\|models\|dashboards\|dataflows]` |
| When did the models last refresh? | `ppx pbi ls --workspace X --type models --fields name,lastRefresh,lastStatus` |
| Which model does each report use? | `ppx pbi ls --workspace X --type reports --fields name,model` |
| Pages, format (PBIR or PBIRLegacy), bound model of a report | `ppx pbi report show "<report>" --workspace X` |
| Data sources, parameters, owner, last 3 refreshes, can I query it? | `ppx pbi model show "<model>" --workspace X` |
| Why did the refresh fail? | `ppx pbi history --model "<model>" --workspace X --failures` |
| Row count, a total, a measure value | `ppx pbi query --model "<model>" --workspace X --dax 'EVALUATE ROW("rows", COUNTROWS(Sales))'` |
| What changed since last week? | `ppx pbi diff --workspace X [--since 7d]` |
| Refresh it now | `ppx pbi refresh --model "<model>" --workspace X [--wait]` (see the budget below) |

"What reports are in X and when did their models last refresh" is two calls: `pbi ls --type reports --fields name,model` and `pbi ls --type models --fields name,lastRefresh,lastStatus`.

## Names, workspaces and the cache

- Pass names, not GUIDs. Names match case-insensitively; an id always works too. Quote names with spaces.
- `--workspace` takes a name or id; `"My workspace"` (or `me`) is the personal workspace. The profile's default workspace (`ppx env set --workspace <name>`) is used when you leave it out; with neither, ppx searches the cached inventories only.
- Two items with the same name exit 1 listing up to five candidates with ids: re-run with the id.
- A name that is not in the cache triggers one automatic re-fetch of that workspace before "not found".
- A warning that the inventory is over 24 hours old means: add `--refresh` if the answer must be current.

## Keep answers small

- Lists show 15 items (30 workspaces) by default with `count`, `by_type` and `more`. Narrow with `--type`, pick columns with `--fields name,lastRefresh`, page with `--top N`; `--raw` removes the limits (avoid it).
- A model's `lastRefresh`/`lastStatus` appear only when you have write permission on it; otherwise the warning says so.
- Sizes and the cheapest call sequences: `references/token-budget.md`.

## DAX queries

- One `EVALUATE` per call (exit 1 otherwise). One result table per query.
- Default 50 rows (`--top N` changes it). A plain `EVALUATE <table> [ORDER BY ...]` is wrapped in `TOPN` on the server, keeping your order. Queries with `DEFINE` are sent as written and cut client-side, with the warning "truncated, rows unordered" unless they have `ORDER BY`.
- Rows come back as CSV in `data.csv`; a larger result is saved to a `.csv` file (`data.saved_to`, first lines in `data.head`). Read the file instead of re-running with a bigger `--top`.
- Aggregate in DAX (`COUNTROWS`, `SUMMARIZECOLUMNS`) instead of pulling rows to count them.
- Long queries: put them in a file and pass `--dax '@query.dax'` (quoted: a bare `@` is a PowerShell parse error).
- Quoting: wrap the DAX in single quotes in Bash and PowerShell. A table name that needs quotes (`'Sales Data'`) breaks that: in PowerShell double each embedded single quote (`''Sales Data''`), in Bash use the `@file` form.
- Limits: 100,000 rows or 1,000,000 values per query (exit 2: aggregate or add TOPN/ORDER BY); past 15 MB the service stops adding rows without an error, so ppx warns when a result reaches that size; 120 queries per minute per user, shared by every ppx process (ppx waits for a free slot up to `--timeout`).
- Exit 2 "refused (403)" names both causes: Build permission on the model, or the tenant setting "Semantic Model Execute Queries REST API". `pbi model show` reports `build: true|false` first.

## Refresh: the budget and the rules

- Shared capacity (Pro, `onCapacity: false`) allows **8 refreshes per model per day**, counting scheduled, portal, API and failed ones. ppx counts today's (UTC) entries and refuses the 8th without `--yes` (exit 2 with the count). Only add `--yes` after the user agrees to spend it.
- Never refresh to find out why a refresh failed: `pbi history --failures` answers that for free.
- A refresh already running exits 2; poll with `pbi history` instead of starting another.
- `--wait` polls until the refresh ends or `--timeout` seconds pass (default 60). On timeout you get the `requestId` and a `pbi history` command: poll, do not refresh again. Keep `--timeout` below your shell tool's own timeout.
- `--dry-run` shows the count and the body without refreshing.
- Shared capacity accepts only `{notifyOption}`; enhanced refresh (tables, partitions, commit mode) needs a capacity workspace.

## Why did it fail

`pbi history --failures` and `pbi model show` parse each failure into `errorCode`, `message` (300 characters) and `cause`, and put one fix in `next_actions`:

| cause | Fix |
|---|---|
| `credentials` | the user signs in to the data source in the model's settings page (the `url` in `next_actions`), then runs the `ppx pbi refresh` command given. Credentials set through the API expire in about an hour, so the settings page is the durable fix. Never bind credentials through the API to "fix" this |
| `gateway` | gateway offline or not mapped: the settings page `url` |
| `timeout_2h` | shared capacity stops a refresh at two hours: less data, or incremental refresh (hand off to pbi-authoring) |
| `quota_8_per_day` | wait for the next UTC day |
| `query` | a Power Query or source error named in the message: hand off to pbi-authoring |
| `unknown` | read the message to the user; the settings page has the full text |

Hand the user a `url` to open; verbs never open a browser.

## Exit codes

0 done; 1 failed (message says why: ambiguous name, DAX error, unexpected API error); 2 the user or an admin must act first (sign-in, permission, tenant setting, the 8th refresh, a refresh in progress, no old snapshot for `diff`). Relay `next_actions` as given. Admin-only items point at `docs/ADMIN-REQUESTS.md`; never suggest admin roles or service principals.

## Hand-offs

- Editing measures, tables, relationships or the model definition: `/powerbi-authoring:semantic-model-authoring` (plugin `powerbi-authoring@fabric-collection`), via the `pbi-authoring` skill.
- Editing report visuals and pages (PBIR): `/powerbi-authoring:powerbi-report-cli`.
- Anything ppx has no verb for: `ppx raw pbi GET <path>` on a documented Power BI REST path (reads only without the user's consent).

## Limits and facts

`references/rest-limits.md` has the Power BI and Fabric REST limits, permissions and the Learn pages they come from.
