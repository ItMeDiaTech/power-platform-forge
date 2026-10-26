---
name: pp-solution-architect
description: Plan a new Power Platform build before anything is created - choose SharePoint list or Dataverse table, model-driven or canvas app, how Power BI reads the data and how users reach the report from the app, which flows, and which solution carries it all; then hand each piece to the right Microsoft skill or FlowAgent tool and ppx verb in order. Use when the user says build me an app, design a solution, architect, plan, which product should I use, SharePoint or Dataverse, model-driven or canvas, Power BI over Dataverse, embed a report in an app, add a flow, or gives a one-prompt build like "an app with a report and a flow".
---

# pp-solution-architect

You plan; you do not build in this step. The answer is an ordered hand-off list the user approves before anything runs. Building happens afterwards through the named Microsoft skills, FlowAgent tools and ppx verbs, each of which shows the user a change notice.

`ppx <verb>` is short for `uv run --project "${CLAUDE_PLUGIN_ROOT}/scripts/ppx" ppx --data-dir "${CLAUDE_PLUGIN_DATA}" <verb>`; the MCP tool `run` takes the same verb and arguments. For a long design, delegate to the `pp-architect` agent, which works read-only.

## 1. Gather facts with read verbs only

| Need to know | Verb |
|---|---|
| Which APIs and tools work for this user | `ppx auth doctor` |
| Target environment and defaults | `ppx env ls` |
| Existing solutions (reuse, publisher prefix) | `ppx solution ls` |
| Power BI workspaces and whether they are on capacity | `ppx pbi ws ls` |
| An existing SharePoint list's columns, if one is involved | `ppx sp fields ls --site <site> --list <list>` |
| Apps that already read that list | `ppx pa ls --list <site>/<list>` |

Ask the user only what these cannot answer: who uses it, how many rows, whether users hold premium licences, whether IT will enable settings.

## 2. Route each need

| Need | Build with | ppx around it | Watch |
|---|---|---|---|
| Tables, forms, views, a model-driven app | `/model-apps:app-builder` (pass the solution name and publisher prefix) | `ppx solution ls` before; `ppx mda ls`, `ppx mda show <app>` after | needs `pac` and `az` signed in as the same user, System Customizer; users need premium licences |
| A page inside a model-driven app | `/model-apps:genpage` | `ppx mda sitemap <app>` | same as above |
| Canvas app, attended edit | `/canvas-apps:canvas-app` after `/canvas-apps:configure-canvas-mcp` | `ppx pa get`, `ppx pa inspect` (read-only source) | .NET 10 SDK, an existing saved app with coauthoring on, Studio tab open the whole session |
| Canvas app on a SharePoint list | `/canvas-apps:add-data-source`, then `/canvas-apps:canvas-app` | `ppx sp fields ls`, `ppx pa inspect` for delegation | standard licence; delegation limits (references/costs-and-limits.md) |
| Store data | SharePoint list or Dataverse table (references/integration-patterns.md) | `ppx sp list ...` / `ppx data query` | licence and size decide it |
| Seed rows | `ppx data fx --file seed.fx` (Power Fx `Collect` into the new tables), or a CSV import the user runs in make.powerapps.com | `ppx data query --table <t> --top 5` to confirm | Dataverse API limits for bulk loads |
| Semantic model (Import over Dataverse or SharePoint) | `/powerbi-authoring:semantic-model-authoring` | `ppx pbi deploy`, `ppx pbi datasources bind --transient`, `ppx pbi refresh --wait`, `ppx pbi query` | 8 refreshes a day on shared capacity; durable credentials are a browser step |
| Report pages and visuals | `/powerbi-authoring:powerbi-report-cli` | `ppx pbi deploy`, `ppx pbi report show` | PBIR format; Desktop round trip on Pro when Fabric writes fail |
| Model from a SharePoint list | ppx alone | `ppx pbi model-from-sp`, `ppx sp m-snippet` | 2.0 connector, 12 lookup columns per query |
| Report reachable from the app | references/embed-options.md | `ppx pbi report show` for the URL | system dashboard is a browser step |
| Cloud flow | FlowAgent tools (`create_flow`, `edit_flow` with `dryRun`, `publish_flow`) via `/power-automate:create-flow` | `ppx pa flows ls`, `ppx pa flows runs <flow>` | Dataverse trigger is a premium connector; flows inside the solution use connection references |
| Website for external users | `/power-pages:create-site` | none | separate licensing; out of ppx scope |
| Code app | `/code-apps-preview:create-code-app` | none | admin enables code apps per environment; no Power BI integration function |
| Package and move it | `pp-solution-alm` skill | `ppx solution` export, unpack, check, settings, import | import is user-confirmed |
| Copilot Studio agent, custom connector, DLP | not built by this plugin yet | none | say so; admin requests where needed |

Exact plugin names, install lines and prerequisites: `references/handoff-map.md`.

## 3. Decide the two forks first

- **SharePoint list or Dataverse table?** SharePoint when the data already lives there, users have no premium licence, rows stay in the low thousands and relationships are simple. Dataverse when you need relationships, row-level security, model-driven apps, more than a few thousand rows with filtering, or Dataverse-triggered flows. Details and the Power BI connector for each: `references/integration-patterns.md`.
- **Model-driven or canvas?** Model-driven for data-entry and process apps over Dataverse (forms, views, dashboards, built by `/model-apps:app-builder`); canvas for task apps with a custom layout or SharePoint data. Model-driven needs Dataverse.

## 4. Answer in this shape

1. One paragraph: what will exist (app, tables or list, model, report, flow, solution) and where (environment, workspace, site).
2. A numbered hand-off list. Each step: who does it (a `/plugin:skill`, a FlowAgent tool, a `ppx` verb, or "you in the browser" with the URL), what it creates or changes, what must be true first.
3. Licences and costs that apply (`references/costs-and-limits.md`): name every premium licence, capacity or metered charge the plan needs, and every admin setting.
4. What stays manual (browser steps, admin requests) and why.
5. Ask: "Shall I start with step 1?" Do not run any step until the user agrees.

## 5. Example: "a Dataverse app with seeded data, a Power BI report over it reachable from the app, and one flow"

1. `ppx auth doctor`, `ppx env ls`, `ppx solution ls`, `ppx pbi ws ls` (read only).
2. `/model-apps:app-builder`: three tables, forms, views and the app, inside a new solution with the user's publisher prefix.
3. Seed rows with `ppx data fx --file seed.fx` (Power Fx `Collect`); confirm with `ppx data query --table <t> --top 5`.
4. `/powerbi-authoring:semantic-model-authoring`: an Import-mode PBIP over the Dataverse connector (TDS endpoint on); `/powerbi-authoring:powerbi-report-cli` for the report pages.
5. `ppx pbi deploy` to the workspace, `ppx pbi datasources bind --transient`, `ppx pbi refresh --model <m> --wait`, `ppx pbi query --model <m> --dax "EVALUATE ROW(\"rows\", COUNTROWS('<table>'))"` for the row count.
6. The user sets durable credentials at the dataset-settings URL that `datasources bind` returns (OAuth credentials set by API expire in about an hour).
7. Make the report reachable (`references/embed-options.md`): a Power BI embedded system dashboard added to the app in make.powerapps.com, or a sitemap link to the report URL.
8. FlowAgent `create_flow` for one Dataverse-triggered flow in the same solution; `ppx pa flows runs <flow>` after a test row.
9. `pp-solution-alm`: export with `--managed`, unpack to Git, check; import elsewhere only when the user asks.

Costs to state: premium licences for every app user (Dataverse), Power BI Pro for report authors and viewers on shared capacity, Dataverse storage beyond the environment's quota; no capacity needed for this design.

## Never

- Run a change, an import, a refresh or a Microsoft authoring skill while planning.
- Promise a step that needs an admin (tenant settings, Managed Environments, environment features, consent) as if the user could do it; list it as an admin request.
- Reimplement what the Microsoft skills own: canvas YAML, TOM model edits, PBIR visuals, flow definitions.
