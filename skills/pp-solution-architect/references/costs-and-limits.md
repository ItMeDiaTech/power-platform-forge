# Costs and limits

Name every licence, capacity and meter a plan needs before the user approves it. Prices are US list prices read on the dates shown; tenants with agreements pay differently, so present them as "list price" and let the user check with IT. Sources are the pages named in each row, read 2026-10-25 unless a row gives another date.

## Licences

| Need | Licence | List price | Source |
|---|---|---|---|
| Run an app or flow that uses Dataverse or any premium connector | Power Apps Premium (apps), Power Automate Premium (flows); either satisfies Managed Environments | $20 and $15 per user per month (yearly) | powerapps.microsoft.com pricing, powerautomate pricing pages; learn.microsoft.com/power-platform/admin/managed-environment-licensing |
| One app for occasional users | Power Apps per app, pay-as-you-go meter | $10 per active user per app per month | learn.microsoft.com/power-platform/admin/pay-as-you-go-meters |
| Canvas app on SharePoint, flows with standard connectors | included with Microsoft 365 | none extra | |
| Build and share Power BI on shared capacity | Power BI Pro for authors and every viewer | per user | learn.microsoft.com/fabric/enterprise/licenses |
| Fabric capacity below F64 | viewers still need Pro (or PPU); F64 and up lets free users read | F2 about $0.36 per hour (about $263 per month), billed per second, pausable | learn.microsoft.com/fabric/enterprise/licenses |
| Developer Plan environment | free; 2 GB database, 750 flow runs per month; from 2026-11-15 sign-up no longer creates an environment | free | learn.microsoft.com/power-platform/developer/plan (2026-10-17) |
| Code app users | Power Apps Premium, pay-as-you-go, App Pass or auto-claim | as above | learn.microsoft.com/power-apps/developer/code-apps/overview |

## Billing warnings (say these out loud)

- **Fabric capacity bills while it runs**, per second, whether or not anyone uses it. Pause it when idle; a paused capacity makes its content unavailable.
- **Pay-as-you-go meters** (per app, flow runs at $0.60 premium cloud run, Dataverse storage beyond 1 GB at $48 per GB per month for database) charge the Azure subscription linked by an admin.
- **Dataverse storage** beyond the tenant's quota is billed; Dataverse database capacity add-on lists at $40 per GB per month.
- **Copilot Credits**: Copilot Studio agents and Dataverse MCP tools used by agents outside Copilot Studio are metered (from 2025-12-15), $0.01 per credit pay-as-you-go.
- **Managed Environments** (needed for pipelines and Git integration) require every user of apps there to hold a premium licence; enforcement blocks unlicensed users from February 2027.

## Limits that shape designs

| Area | Limit | Source |
|---|---|---|
| Power BI refresh, shared capacity | 8 per day per model including scheduled; 2-hour run limit; only `notifyOption` in the API body | learn.microsoft.com/rest/api/power-bi/datasets/refresh-dataset-in-group |
| Power BI executeQueries | 1 query per call, 100,000 rows or 1,000,000 values, 15 MB, 120 requests per minute per user; tenant setting "Semantic Model Execute Queries REST API" | learn.microsoft.com/rest/api/power-bi/datasets/execute-queries |
| Power BI capacity features | XMLA read-write: Premium, PPU or Fabric capacity (not Pro); export to PNG or PDF: Premium, Embedded or Fabric capacity (not PPU, not Pro); enhanced refresh: not on shared capacity | learn.microsoft.com/power-bi/developer/embedded/export-to |
| Dataverse service protection | 6,000 requests, 1,200 seconds of execution, 52 concurrent requests per user per 5 minutes | learn.microsoft.com/power-apps/developer/data-platform/api-limits |
| Dataverse daily entitlement | 40,000 requests per day per Premium user; 6,000 for per app, pay-as-you-go and Microsoft 365 | learn.microsoft.com/power-platform/admin/api-request-limits-allocations (2026-10-14) |
| Dataverse to Power BI (TDS) | 80 MB per query result | learn.microsoft.com/power-apps/maker/data-platform/data-platform-powerbi-connector |
| Solution file | 95 MB per import | learn.microsoft.com/power-apps/maker/data-platform/import-update-export-solutions |
| SharePoint list | 5,000-item list view threshold (filter on indexed columns); 12 lookup columns per query on the Power BI 2.0 connector; 90 MB uploads through the connector | learn.microsoft.com/power-apps/maker/canvas-apps/connections/connection-sharepoint-online |
| Canvas delegation | data row limit 500 by default, settable up to 10,000 for non-delegable formulas | same page (2026-10-07) |
| Environment variables | value up to 2,000 characters; values can take up to an hour to propagate | learn.microsoft.com/power-apps/maker/data-platform/environmentvariables |

## Admin-only settings a plan may need

Turning these on is never a user task; list them as requests for IT with the exact name:

- Environment feature **Enable TDS endpoint** (Power BI over Dataverse).
- Environment feature **Power BI visualization embedding** (only for Power BI tiles on personal dashboards).
- Power BI tenant settings **Semantic Model Execute Queries REST API**, **Embed content in apps**, **Publish to web** (never use publish to web for internal data).
- **Managed Environments**, **pipelines host**, **code apps** enablement, **user consent** for the Tier B app.
