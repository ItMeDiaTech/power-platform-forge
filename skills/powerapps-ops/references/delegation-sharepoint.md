# SharePoint delegation in canvas apps

Source: Microsoft Learn, "Connect to SharePoint from a canvas app", delegable functions and operations
(learn.microsoft.com/power-apps/maker/canvas-apps/connections/connection-sharepoint-online, ms.date 2026-10-07).

A formula that does not delegate runs in the app on the first rows only: the **Data row limit**,
500 by default and up to 10,000 (Settings > General). Results past that are silently missing.

## What delegates to SharePoint

| Operation | Number | Text | Boolean | DateTime | Complex (subfield) |
|---|---|---|---|---|---|
| `<`, `<=`, `<>`, `>`, `>=` | Yes (not ID) | No | No | Yes | Yes |
| `=` | Yes | Yes | Yes | Yes | Yes |
| Filter, LookUp | Yes | Yes | Yes | Yes | Yes |
| Sort, SortByColumns | Yes | Yes | Yes | Yes | No |
| StartsWith | - | Yes | - | - | Yes, but not on Choice or Lookup subfields |
| IsBlank | - | No | - | - | No |
| UpdateIf, RemoveIf | Yes | No | - | - | No |

`And` and `Or` delegate; `Not` does not. Complex types (Choice, Lookup, Person, Group, Managed
Metadata) delegate by the subfield used; a Person column delegates only `Email` and `DisplayName`.
The ID column shows as a number but is text underneath: only `=` delegates on it. SharePoint system
fields (`'{Name}'`, `'{Path}'`, `'{FullPath}'`, `'{IsFolder}'`, `'{Thumbnail}'`, `'{Link}'`,
`'{Identifier}'`, `'{FilenameWithExtension}'`, `'{ModerationStatus}'`, `'{ModerationComment}'`,
`'{ContentType}'`, `'{IsCheckedOut}'`, `'{VersionNumber}'`) generally do not delegate.

## What `ppx pa inspect` flags

It checks the predicate of every Filter, LookUp, Search, Sort, SortByColumns, CountIf, RemoveIf and
UpdateIf whose first argument is a SharePoint data source (from the app's connection references and
the source's `DataSources`; when none can be identified, every such call, with a warning).

| Rule | Pattern | Fix to suggest |
|---|---|---|
| `startswith-choice-lookup` | `StartsWith(Col.Value, ...)` or `.Id` | filter on a text column, or `=` on the choice value |
| `in-operator` | `x in Col`, `Col in [...]`, `exactin` | `=` per value joined with `Or`, or StartsWith |
| `id-relational` | `ID > 100`, `ID <> x` | a number column (copy ID with a flow), or `=` |
| `person-subfield` | `.Department`, `.JobTitle`, `.Claims`, `.Picture` | filter on `.Email` or `.DisplayName` |
| `isblank` | `IsBlank(Col)` | `Col = Blank()` (does not treat "" as empty; works for `=`, not `<>`) |
| `not` | `Not(...)`, `!` | invert the condition with `=`/`<>` where the type allows |
| `system-field` | `'{Name}'` and the other system fields | a regular column |
| `search` | `Search(List, ...)` | `Filter(List, StartsWith(Title, text))` |
| `countif` | `CountIf(List, ...)` | count in a view, a flow, or keep the list under the row limit |

Findings carry `file`, `line` (of the call), `control` and `property`, never the formula text. Sort
on a Complex column and comparisons on Text columns also do not delegate, but need the column types:
run `ppx sp fields ls --site <s> --list <list>` and match its `delegation` groups to the columns in
the reported formulas.

## Patterns that are not findings

- `LookUp(List, ID = x)`, `Filter(List, Status.Value = "Open")`, `Filter(List, Owner.Email = User().Email)`.
- Calls on collections (`ClearCollect` results) and on non-SharePoint sources.
- `With`, `Set`, `UpdateContext` results are in-memory and never delegate; they are not reported
  because the delegation already happened (or not) where the collection was filled.

To expose non-delegable formulas while testing, set the Data row limit to 1 in Studio: any screen
that then shows one row too few is affected.
