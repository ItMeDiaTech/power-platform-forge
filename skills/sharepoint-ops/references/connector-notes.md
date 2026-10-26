# Column types, Power Apps delegation and the Power BI connector

Sources: Connect to SharePoint from a canvas app (<https://learn.microsoft.com/en-us/power-apps/maker/canvas-apps/connections/connection-sharepoint-online>, ms.date 2026-10-07); Power Query SharePoint Online list connector (<https://learn.microsoft.com/en-us/power-query/connectors/sharepoint-online-list>, ms.date 2025-11-24); `SharePoint.Tables` (<https://learn.microsoft.com/en-us/powerquery-m/sharepoint-tables>). Read 2026-10-25 and 2026-10-26.

## SharePoint type to Power Apps type

`sp fields ls` shows SharePoint's `TypeAsString`; this is how Power Apps sees each one.

| `type` in `sp fields ls` | SharePoint column | Power Apps type |
|---|---|---|
| `Text`, `Note`, `Calculated` | single line, multiple lines, calculated | Text |
| `Number`, `Currency`, `Integer` | number, currency | Number |
| `Counter` | ID | Number on screen, Text underneath (see ID below) |
| `Boolean` | Yes/No | Boolean |
| `DateTime` | date and time | DateTime |
| `URL`, `Thumbnail` | hyperlink or picture, image | Image |
| `Choice`, `MultiChoice`, `Lookup`, `LookupMulti`, `User`, `UserMulti`, `TaxonomyFieldType(Multi)`, `OutcomeChoice` | choice, lookup, person or group, managed metadata, task outcome | Complex (a record with subfields such as `.Value`, `.Email`, `.DisplayName`) |

## What does not delegate to SharePoint

`sp fields ls` puts the Power Apps type in each column's `pa` (only for types with a restriction) and the rule for each type present in `data.delegation`, a `{type: rule}` map. By Power Apps type:

| Power Apps type | Delegates | Does not delegate |
|---|---|---|
| Number | `=`, `<`, `<=`, `<>`, `>`, `>=`, Filter, LookUp, Sort, SortByColumns, UpdateIf/RemoveIf | nothing listed |
| Text | `=`, Filter, LookUp, Sort, SortByColumns, StartsWith | `<`, `<=`, `<>`, `>`, `>=`, IsBlank, UpdateIf/RemoveIf |
| Boolean | `=`, Filter, LookUp, Sort, SortByColumns | `<`, `<=`, `<>`, `>`, `>=` |
| DateTime | `=`, comparisons, Filter, LookUp, Sort, SortByColumns | nothing listed |
| Complex | `=`, comparisons and Filter/LookUp through a subfield of a delegable type | Sort, SortByColumns, IsBlank, UpdateIf/RemoveIf; StartsWith on Choice or Lookup subfields; a Person column delegates only `Email` and `DisplayName` |
| ID | `=` only | every comparison other than `=`: the column is Text in SharePoint |
| Image | not in Microsoft's table | treat as non-delegable |

Also from the same page:

- `And` and `Or` delegate; `Not` does not.
- `Filter(..., IsBlank(Col))` does not delegate; `Filter(..., Col = Blank())` does for `=` (not `<>`), but treats `""` differently.
- SharePoint system fields generally do not delegate (for example Name, Path, FullPath, ContentType, IsFolder, VersionNumber, ModerationStatus).
- UpdateIf and RemoveIf work locally up to the Data row limit (500 by default, up to 10,000).

When the user asks "which columns are non-delegable", answer per column: each column with a `pa` value, its display name (`title`, else `name`), and the operations in `data.delegation[pa]` that will not delegate. A column is a problem only for the operations the app actually uses on it, so check the formula when one is given.

## Power BI: SharePoint Online list connector

- Use implementation 2.0: `SharePoint.Tables("<site url>", [Implementation = "2.0", ViewMode = "Default"])`. `ViewMode` is `"All"` (default: every user and system column) or `"Default"` (the columns of the list's default view). The site URL is the site root, without a list or folder path.
- 2.0 is not backward compatible with 1.0 queries; changing a model's implementation means rewriting its navigation steps.
- 2.0 is subject to the 12-join limit: a query that touches more than 12 lookup-type columns (lookup, person or group, workflow status) fails. Give the list a default view with fewer and use `ViewMode = "Default"`. `sp fields ls` reports the count in `data.power_bi`, and their names once there are more than 12.
- If the default view is a Calendar or Board view, only that view's columns come back, even with `"All"`.
- 1.0 returns date/times in UTC without conversion; convert with `DateTimeZone.ToLocal` after typing the column as `datetimezone`.
- Cloud SharePoint sources refresh in the service without a gateway; credentials are OAuth2 set on the semantic model's settings page (a browser step).
- Use the internal names from `sp fields ls` when selecting or renaming columns in M; display names can change.
