# Hand-off map

Which Microsoft plugin builds what, how the user installs it, what it needs, and the ppx verbs before and after. Names come from Microsoft's marketplace manifests (`microsoft/power-platform-skills` and `microsoft/skills-for-fabric`, `.claude-plugin/marketplace.json`, read 2026-10-25). None of these is a dependency of this plugin; each adds MCP tools to the session, so suggest installing only what the plan uses.

## Install lines (the user runs them)

```
/plugin marketplace add microsoft/power-platform-skills
/plugin install model-apps@power-platform-skills
/plugin install canvas-apps@power-platform-skills
/plugin install power-automate@power-platform-skills
/plugin install power-pages@power-platform-skills
/plugin install code-apps-preview@power-platform-skills

/plugin marketplace add microsoft/skills-for-fabric
/plugin install powerbi-authoring@fabric-collection
```

## Map

| Product piece | Plugin | Skill or tool | Needs | ppx before | ppx after |
|---|---|---|---|---|---|
| Model-driven app, tables, forms, views | `model-apps` | `/model-apps:app-builder` | pac newer than 2.10.0, `az login` as the same user as the pac profile, Node LTS, System Customizer in the environment | `ppx auth doctor`, `ppx solution ls` (pick solution and publisher prefix) | `ppx mda ls`, `ppx mda show <app>`, `ppx mda forms <app>`, `ppx mda views <app>` |
| Generative page in a model-driven app | `model-apps` | `/model-apps:genpage` | as above | `ppx mda sitemap <app>` | `ppx mda sitemap <app>` |
| Canvas app edits | `canvas-apps` | `/canvas-apps:configure-canvas-mcp` (paste the Studio edit URL), then `/canvas-apps:canvas-app` | .NET 10 SDK, an existing saved app, coauthoring on, Power Apps Studio open for the whole session | `ppx pa ls`, `ppx pa get <app> --out <dir>`, `ppx pa inspect <dir>` | `ppx pa versions <app>`, `ppx pa inspect` again |
| Canvas data source | `canvas-apps` | `/canvas-apps:add-data-source` | as above | `ppx sp fields ls` (column types, indexed columns) | `ppx pa inspect` (delegation findings) |
| Cloud flow | `power-automate` | `/power-automate:create-flow`; FlowAgent tools `create_flow`, `edit_flow` (`dryRun: true` previews), `publish_flow`, `run_flow`; `/power-automate:debug-flow` and `/power-automate:diagnose-flow` for failures | Node 18+, `az login` | `ppx pa flows ls` | `ppx pa flows runs <flow>` |
| Semantic model | `powerbi-authoring` | `/powerbi-authoring:semantic-model-authoring` (Power BI Modeling MCP) | Windows; local PBIP/TMDL or Desktop; service models need XMLA read-write on capacity | `ppx pbi ws ls`, `ppx pbi model show <m>` | `ppx pbi deploy`, `ppx pbi datasources bind --transient`, `ppx pbi refresh --wait`, `ppx pbi query` |
| Report pages and visuals | `powerbi-authoring` | `/powerbi-authoring:powerbi-report-cli` | PBIR report folder | `ppx pbi pull report <r>` | `ppx pbi deploy` or `ppx pbi push report <r>`, `ppx pbi report show <r>` |
| External website | `power-pages` | `/power-pages:create-site`, `/power-pages:deploy-site` | Power Pages licensing | none | none |
| Code app (React/TypeScript) | `code-apps-preview` | `/code-apps-preview:create-code-app`, `/code-apps-preview:deploy` | admin enables code apps in the environment; users need premium | none | none |
| Solution packaging and moves | this plugin | `pp-solution-alm` skill | pac signed in | `ppx solution ls` | `ppx solution export`, `unpack`, `check`, `settings`, `import` |
| SharePoint list to Power BI model | this plugin | `ppx pbi model-from-sp` | Tier B sign-in for SharePoint | `ppx sp fields ls` | `ppx pbi refresh --wait` |

## Hand-off rules

- Pass the solution name and publisher prefix to every authoring skill that creates Dataverse components, so everything lands in one solution. After the step, check with `ppx solution ls` (version and component changes show up on the next export) and record in the plan if a skill ignored the solution.
- FlowAgent mutating tools refuse flows in managed solutions unless `forceManaged` is true; never set it without the user's yes.
- The Power BI Modeling MCP edits whatever model it is connected to; connect it to the local PBIP folder, not the service model, unless the workspace is on capacity and the user agreed.
- After any hand-off, verify with the read verb in the "ppx after" column before telling the user a step is done.
