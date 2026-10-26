# SharePoint list limits that change what you do

Sources: SharePoint Online limits (<https://learn.microsoft.com/en-us/office365/servicedescriptions/sharepoint-online-service-description/sharepoint-online-limits>, ms.date 2026-10-06), Graph list items (<https://learn.microsoft.com/en-us/graph/api/listitem-list>), CLI for Microsoft 365 `spo listitem list` remarks, Power Apps delegation overview (ms.date 2026-10-07). Read 2026-10-25 and 2026-10-26.

| Limit | Value | What it means for ppx |
|---|---|---|
| List view threshold | 5,000 items, fixed in SharePoint Online | On a bigger list, filters and sorts must use indexed columns or SharePoint refuses the query. `sp list ls` and `sp list get` warn; `sp items ls --filter` checks the columns first and warns |
| List view lookup threshold | 12 lookup-type columns per query (lookup, person or group, workflow status; Created By and Modified By count) | The Power BI 2.0 connector fails past it; `sp fields ls` counts them; `sp export` stops expanding person and lookup columns past it |
| Items per list | 30 million | Paging, never "read it all" except through `sp export` |
| Lists and libraries per site collection | 2,000 | `sp list ls` stays one call |
| Attachment per list item | 250 MB (the Power Platform SharePoint connector caps uploads at 90 MB) | relevant to writes, later |
| Graph `$filter` on list items | one indexed field at a time | another reason items go through m365 |

## Paging with ppx

- `sp items ls` returns one page: `--page-size` (default 500, at most 5,000) or `--top`, and `--page` from 1. `data.more` is true when the page came back full; `next_actions` holds the exact next command. A page that starts past item 5,000 is refused (m365 finds the page start with one request, which SharePoint caps at 5,000 rows); use `sp export`.
- `sp export` reads every row in one call and writes a file. Use it instead of walking many pages.
- A page over about 3 KB of JSON is saved to a file and the envelope returns `saved_to` and a digest.

## Indexed columns

`sp fields ls` marks `indexed: true`. `ID` always behaves as indexed. Only someone who can manage the list can add an index (list settings > Indexed columns); suggest it to the user, it is not a ppx verb.

## Power Apps

- Data row limit for non-delegable queries: 500 by default, up to 10,000. A non-delegable formula silently sees only those rows.
- Setting the limit to 1 while testing exposes non-delegable formulas.
- `With`, `UpdateContext` and `Set` build in-memory collections that never delegate.
