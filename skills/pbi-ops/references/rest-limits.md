# Power BI and Fabric REST: limits and permissions ppx respects

Read on Microsoft Learn on 2026-10-26. Where a fact is ppx's own assumption, it says so.

## Execute Queries (`ppx pbi query`, the Build check in `pbi model show`)

Source: <https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries-in-group>

| Limit | Value | What ppx does |
|---|---|---|
| Queries per call | 1, and one result table per query | more than one `EVALUATE` exits 1 before any call |
| Result size | 100,000 rows or 1,000,000 values (whichever first) | the service returns 200 with an error and partial rows; ppx exits 2 "aggregate or add TOPN/ORDER BY" |
| Result bytes | 15 MB | no error: the service completes the current row and writes no more (Learn, Execute Queries In Group); ppx warns when a result reaches about 14.5 MB |
| Rate | 120 query requests per minute per user, across all models | file-based sliding window under `<data>/ratelimit/executeQueries.json`, shared by every ppx process; waits up to `--timeout` |
| Permissions | workspace access plus Read and Build on the model; tenant setting "Dataset Execute Queries REST API" (Integration settings, shown as "Semantic Model Execute Queries REST API") | 401/403 exits 2 naming both causes |
| Not supported | Azure Analysis Services models and live connections to on-premises AAS; MDX, INFO functions and DMVs | the service error is passed through (exit 1) |
| Scope | `Dataset.ReadWrite.All` or `Dataset.Read.All` | |

DAX errors are 400 (exit 1 with the message). Columns come back as `Table[Column]`; renamed or computed columns as `[Name]`. `serializerSettings.includeNulls: true` is always sent so every row has every column.

## Refresh (`ppx pbi refresh`, `ppx.pbi.refresh.gated`)

Source: <https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/refresh-dataset-in-group>

- Shared capacity: at most eight refresh requests per day, scheduled refreshes included; only `notifyOption` may be in the body; enhanced refresh is not supported.
- Premium, PPU, Embedded or Fabric capacity: the daily count is limited only by capacity resources; an enhanced refresh is triggered by any body field other than `notifyOption` (and then `notifyOption` must be left out).
- Response: 202 with `x-ms-request-id` and `Location` headers. Scope `Dataset.ReadWrite.All`.
- ppx sends `{"notifyOption": "NoNotification"}`, counts every history entry that started on the current UTC day (any type, any status), and needs `--yes` from the 8th on. The UTC day boundary is ppx's assumption; Learn does not state the time zone of the daily count.
- Shared-capacity refreshes time out after two hours (five on Premium): <https://learn.microsoft.com/en-us/power-bi/connect-data/refresh-troubleshooting-refresh-scenarios>.
- Credentials set through the Gateways API with OAuth2 expire in about an hour; the durable fix is the model's settings page, `https://app.powerbi.com/groups/<workspace id|me>/settings/datasets/<model id>`.

## Refresh history (`ppx pbi history`, `pbi model show`, the gate)

Source: <https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-history-in-group>

- Caller must have **write permission** on the model; OneDrive refresh history is not returned.
- `$top` (minimum 1); without it the last 60 entries.
- `status`: `Unknown` (running or unknown), `Completed`, `Failed` (`serviceExceptionJson` holds the error code as a JSON string), `Disabled` (selective refresh).
- `refreshType`: `Scheduled`, `OnDemand`, `ViaApi`, `ViaXmlaEndpoint`, `ViaEnhancedApi`, `OnDemandTraining`.
- Each entry may carry `refreshAttempts[]` with their own `serviceExceptionJson`; ppx uses the last attempt's when the entry has none.

Cause mapping in `parse_refresh_entry` is ppx's own heuristic over the error code and message (credential/OAuth words, gateway, time-out or a run of 115 minutes or more, too many refreshes, Power Query and processing errors). Recorded error bodies from the work tenant will refine it.

## Inventory (`ppx pbi ws ls`, `ppx pbi ls`)

| Call | Source | Notes |
|---|---|---|
| `GET /groups` | <https://learn.microsoft.com/en-us/rest/api/power-bi/groups/get-groups> | `isOnDedicatedCapacity`, `capacityId`, `isReadOnly`; My workspace is not listed (ppx adds it as `me`) |
| `GET /groups/{id}/reports` | <https://learn.microsoft.com/en-us/rest/api/power-bi/reports/get-reports-in-group> | `datasetId`, `format` (`PBIR`, `PBIRLegacy`, `RDL`), `reportType`, `isOwnedByMe` |
| `GET /groups/{id}/datasets` | <https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-datasets-in-group> | full fields (`configuredBy`, `isRefreshable`) only with write permission; readers get `id` and `name` |
| `GET /groups/{id}/dashboards` | <https://learn.microsoft.com/en-us/rest/api/power-bi/dashboards/get-dashboards-in-group> | `displayName`, `isReadOnly`; scope `Dashboard.Read.All` |
| `GET /groups/{id}/dataflows` | <https://learn.microsoft.com/en-us/rest/api/power-bi/dataflows/get-dataflows> | `objectId`, `name`, `configuredBy`; scope `Dataflow.Read.All`; no My workspace endpoint |
| `GET /groups/{id}/datasets/{id}/refreshes?$top=1` | per refreshable model | gives `lastRefresh`/`lastStatus`; needs write permission |
| Fabric `GET /v1/workspaces`, `GET /v1/workspaces/{id}/items` | <https://learn.microsoft.com/en-us/rest/api/fabric/core/items/list-items> | second source when Power BI REST refuses; pages with `continuationToken`/`continuationUri`; 500 calls per minute; viewer role |

My workspace uses the same paths without `groups/{id}/` (`/reports`, `/datasets`, `/dashboards`).

## Show

- Pages: <https://learn.microsoft.com/en-us/rest/api/power-bi/reports/get-pages-in-group> (`name`, `displayName`, `order`).
- Data sources: <https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-datasources-in-group>, **write permission required**.
- Parameters: <https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-parameters-in-group>; not supported for SQL, Oracle, Teradata and SAP HANA DirectQuery models or models changed through XMLA.

## Capacity-only features (not in pbi-ops)

XMLA read/write, enhanced refresh and ExportTo (PNG/PDF) need Premium, Embedded or Fabric capacity; ExportTo is not supported on PPU. On Pro these verbs exit 2 with the reason.
