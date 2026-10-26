# Integration patterns

## Where the data lives: SharePoint list or Dataverse table

| Question | SharePoint list | Dataverse table |
|---|---|---|
| Licence for app and flow users | included with Microsoft 365 (standard connector) | premium (Power Apps Premium, per app, or pay-as-you-go) |
| Size and filtering | list view threshold 5,000 items: filter only on indexed columns beyond that | large tables; server-side filtering on any column |
| Relationships | lookup columns; at most 12 lookups per query through the Power BI 2.0 connector | real relationships, cascades, many-to-many |
| Security | list and item permissions | security roles, business units, row ownership, column security |
| Canvas delegation | `=`, `Filter`, `LookUp` delegate; ID is really Text (only `=`); Person delegates Email and DisplayName only; data row limit 500 by default, up to 10,000 | broad delegation; same row limit setting for non-delegable formulas |
| App types | canvas | canvas, model-driven, generative pages |
| Flows | SharePoint trigger (standard) | Dataverse trigger (premium), flows can live in the solution |
| Power BI | `SharePoint.Tables(site, [Implementation="2.0", ViewMode="Default"])` (`ppx sp m-snippet`, `ppx pbi model-from-sp`) | Dataverse connector over the TDS endpoint |
| ALM | lists are not solution components; move data and schema by hand or script | tables, columns, forms and views travel in the solution |

Rule of thumb: keep SharePoint when the data is already there, users are on standard licences and the list stays small; choose Dataverse for anything relational, secured per row, model-driven, or large.

## Power BI over Dataverse (Import)

- Connector: **Dataverse** in Power BI Desktop, which uses the environment's TDS endpoint. The environment setting **Enable TDS endpoint** must be on (Power Platform admin center > the environment > Settings > Product > Features; an environment admin changes it, so it is an admin request when it is off). Ports 1433 or 5558 must be reachable; most proxies do not pass TDS.
- Import is recommended; DirectQuery enforces Dataverse security more strictly but every visual queries Dataverse.
- A single query result is capped at 80 MB ("Return records size can't exceed 83886080"): select only needed columns and filter. The legacy Common Data Service connector pages larger results.
- Choice columns arrive as two columns (`x` and `xname`); multi-select choices arrive as comma-separated integers only. Lookups arrive as `<lookup>id` and `<lookup>id-name`; build relationships on the GUID columns.
- After `ppx pbi deploy`, the service needs credentials: `ppx pbi datasources bind --transient` binds an OAuth credential that expires in about an hour (enough for one validation refresh); the user then sets durable credentials at the dataset-settings URL ppx returns.
- Source: learn.microsoft.com/power-apps/maker/data-platform/data-platform-powerbi-connector (2026-04-27).

## Power BI over a SharePoint list

- 2.0 connector partitions (`Implementation="2.0"`, `ViewMode="Default"`), typed columns from `ppx sp fields ls`; `ppx pbi model-from-sp` builds the model and an empty report and runs one gated refresh.
- The 2.0 connector is not backward compatible with 1.0 models; repoint between lists with `ppx pbi repoint`, never by hand-editing M.

## Report reachable from the app

See `embed-options.md`. Default for a model-driven app: a Power BI embedded system dashboard added in make.powerapps.com; fallback: a sitemap link to the report URL.

## Flows

- Build with FlowAgent inside the same solution so the flow uses connection references and moves with the solution.
- A Dataverse-triggered flow ("When a row is added, modified or deleted") needs a premium licence for the flow owner; a SharePoint trigger is standard.
- After a move, flows come back on only when every connection reference has a connection the importing user can use (pp-solution-alm, `references/import.md`).

## One solution for everything

- Create one unmanaged solution with the user's publisher prefix in the development environment, and pass its name to every authoring skill.
- Power BI items are not carried by this solution: models and reports move with `ppx pbi deploy` per workspace. (Power BI items in solutions are preview and not supported by pipelines.)
- Environment variables hold anything that differs per environment (site URLs, list names, a report id for the embedded dashboard); connection references hold connections.
