---
name: pp-testing
description: Test and pre-deploy checks for Power Platform work with ppx - run DAX regression suites (query/expect pairs) against a Power BI semantic model, scaffold, validate and deploy custom connectors (OpenAPI 2.0 / swagger), confirm a Copilot Studio agent was really published, check which DLP data policies mention connectors (or produce the request for IT), and adapt the GitHub Actions OIDC deployment template. Use when the user says test, regression test, DAX test, assert, expected value, measure check, test suite, custom connector, OpenAPI, swagger, validate connector, deploy connector, Copilot Studio publish, did the agent publish, DLP, data policy, connector blocked, Business or Non-business group, CI, GitHub Actions, pipeline, OIDC, federated credential, or asks to review changes before a deploy.
---

# pp-testing

`ppx <verb>` below is short for:

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}/scripts/ppx" ppx --data-dir "${CLAUDE_PLUGIN_DATA}" <verb> ...
```

The MCP tool `run` (`mcp__plugin_power-platform-forge_ppx__run`) takes the same verb and arguments, with `dry_run` and `yes` as fields. Each verb answers with one JSON envelope; read `data`, `warnings` and `next_actions`. If a verb fails on sign-in, run the `pp-auth-doctor` skill.

## Which verb answers what

| Question or task | Verb | Notes |
|---|---|---|
| Do the model's numbers still match what we expect? | `ppx test dax --suite <file.json>` | exit 1 lists failures first, each with `why` and the first rows it `got` (`got_columns` when rows are wide) |
| Start a custom connector | `ppx connector scaffold --folder <new dir> --title <title> --host <host or https base URL> [--auth ApiKey]` | pac writes the API properties file; ppx adds an OpenAPI 2.0 skeleton |
| Is the connector definition valid? | `ppx connector validate --folder <dir>` | offline; exit 1 with `errors`, `warnings` never block |
| Create or update the connector | `ppx connector deploy --folder <dir> [--connector-id <guid>] [--solution <unique name>]` | validates first; an update downloads the live one to a backup first |
| Did the agent publish? | `ppx copilot publish-check <agent> --since <ISO time>` | reads `publishedon` from Dataverse; exit 1 when older than `--since` or never published |
| Are these connectors allowed together? | `ppx dlp check shared_sharepointonline,shared_office365` | needs an admin role; otherwise exit 2 with `data.admin_request` |
| Is the solution healthy? | `ppx solution check --zip <zip>` | see the `pp-solution-alm` skill |
| Does the canvas app have delegation problems? | `ppx pa inspect <app>` | see the `powerapps-ops` skill |

## DAX suites

A suite is a JSON file next to the model's source (keep it in Git):

```json
{"model": "Sales Model", "workspace": "Finance",
 "tests": [
  {"name": "row count", "dax": "EVALUATE ROW(\"n\", COUNTROWS('Sales'))", "expect": 1200},
  {"name": "regions", "dax": "EVALUATE VALUES('Geo'[Region]) ORDER BY 'Geo'[Region]",
   "expect": [{"Geo[Region]": "East"}, {"Geo[Region]": "West"}]},
  {"name": "total 2025", "dax": "EVALUATE ROW(\"t\", CALCULATE([Total Sales], 'Date'[Year] = 2025))",
   "expect": 1234.5, "tolerance": 0.01}]}
```

- `--model` and `--workspace` override the suite's values; names or ids both work.
- A single-value `expect` needs exactly one row with one column: wrap measures in `ROW("name", [Measure])`.
- A list `expect` is compared row by row in order, so give the query an `ORDER BY`. Column keys are what executeQueries returns (`Table[Column]`, `[name]` for ROW). Run the query once with `ppx pbi query --model <m> --dax "<query>"` and copy the keys from its CSV header.
- One `EVALUATE` per test. Booleans match only booleans; numbers match within `tolerance` (default: float rounding only).
- Each test is one executeQueries call and shares the 120-per-minute budget with `pbi query`; it needs Build permission on the model. A sign-in or permission failure stops the suite with exit 2 naming the fix, and a throttle stops it with exit 1; neither says anything about the data. Results above 100,000 rows or 1,000,000 values fail; aggregate in DAX instead.
- Write tests from values the user confirms, never from the first result you see: a suite that copies today's numbers only proves nothing changed.

## Custom connectors

1. `ppx connector scaffold ...` into a new folder. The definition has no operations yet.
2. Add operations to the definition: each with a unique `operationId`, a `summary`, `responses`, and a path parameter (`required: true`) for every `{name}` in its path. Prefer the API vendor's own OpenAPI 2.0 file when one exists. OpenAPI 3 is not accepted by custom connectors.
3. `ppx connector validate --folder <dir>` until `errors` is empty.
4. `ppx connector deploy --folder <dir> --dry-run`, show the user the change, then run it without `--dry-run`. Keep the returned `connector_id`; later deploys pass `--connector-id` and update in place, with the previous version in `<data>/backups/connector/<id>/<time>/` and the undo command in `data.changes`. The backup holds only the definition and properties, so an update from a folder with an icon or `script.csx` replaces the live ones for good and exits 2 until the user agrees to `--yes`.

OAuth client secrets are never passed on a command line: the user enters them in make.powerapps.com > Custom connectors > Security. Certification and sharing are browser steps.

## Copilot Studio agents

Authoring and publishing happen in Copilot Studio. `pac copilot publish` is known to exit 1 after a successful publish and to fail in non-English environments, so never trust its exit code: note the time before the user publishes, then `ppx copilot publish-check "<agent>" --since <that time>`. `data.status` `MissingLicense` means the environment cannot publish at all.

## DLP data policies

Makers cannot read DLP policies. `ppx dlp check <ids>` then exits 2; give the user `data.admin_request` verbatim to send to IT, and design within the answer. With an admin role, each `data.connectors[]` entry gives `mentioned_in_count` and either `in_all_read: true` or up to five policy ids in `mentioned_in`; `data.policies` lists the ids read and `saved_to` holds every policy's text: read the group (Business, Non-business, Blocked) around each connector there before answering. Connectors in different groups of one policy cannot be used in the same app or flow; a connector a policy never mentions may sit in that policy's default group. Advanced connector policies (rule-based, preview) are not read.

## Deploying from GitHub Actions

`${CLAUDE_PLUGIN_ROOT}/templates/github-actions/deploy.yml` packs an unpacked solution folder and imports it with `microsoft/powerplatform-actions`, signing in with GitHub OIDC (app id and tenant id only, no client secret). Copy it into the solution repository's `.github/workflows/`, then follow the TEMPLATE notes at its top: the app registration's federated credential, the repository variables, the GitHub environment, and the application user in the target environment (an admin request: `docs/ADMIN-REQUESTS.md`). Check the edited file with `actionlint` before committing it. Never add a `client-secret` input: when present it silently replaces the federated sign-in.

## Not available here

- Canvas UI tests (Playwright) and flow run tests: not built. Never use `pac test run`; Test Engine is deprecated.
- Best Practice Analyzer and TMDL or Power Fx parsing: not built; hand model edits to `/powerbi-authoring:semantic-model-authoring`.

## Exit codes

| Exit | Envelope says | Do |
|---|---|---|
| 1 | `N of M DAX tests failed` | show the failed `items` (`why`, `got`); ask the user whether the data or the expectation is wrong |
| 1 | `errors in the connector` / `not deployed` | fix each `errors` entry in the definition, validate again |
| 1 | agent `published at ... before ...` or `never been published` | ask the user to publish in Copilot Studio (`next_actions` url), then check again |
| 2 | `needs a Power Platform administrator role` | hand the user `data.admin_request` |
| 2 | executeQueries refused (Build permission or tenant setting) | follow `next_actions`; the rest of the suite was not run |
| 1 | `rate-limit` / `calls per minute` | wait a minute and re-run the suite |
