# Design preferences

Your rules for how the agent designs Power BI reports, Power Apps and SharePoint lists. The values below are
defaults: change them to yours, delete a line to drop a rule. The agent reads this file before it builds or
changes anything, follows it, and says so when a request breaks a rule.

## Everywhere
- Dates `yyyy-mm-dd`; numbers with thousands separators; currency `$#,0`; percentages one decimal.
- Colours: primary `#1F4E79`, accent `#2E86AB`, good `#2E7D32`, warning `#ED6C02`, bad `#C62828`, text `#252423`, background `#FFFFFF`.
- Never use colour as the only signal: pair it with an icon, label or position.
- Font Segoe UI: titles 14 pt bold, body 11 pt.
- Names in plain English, Title Case, no abbreviations except ID.

## Power BI reports
- Pages 16:9 (1280 × 720); a title top-left and a "Data as of" date on every page.
- At most 6 visuals per page; KPI cards in the top row, detail below; slicers in a left pane.
- Every measure lives in a `_Measures` table, has a format string and a description; no implicit measures.
- Measure names state the unit when it is not obvious: `Revenue (USD)`, `Orders (#)`.
- Hide ID and key columns; mark the date table.
- Alt text on every visual; set the tab order.

## Power Apps (canvas)
- Screens `<Name>Screen`; controls prefixed `btn` `lbl` `gal` `txt` `ico` `frm` `cmb` `dt`.
- Colours from "Everywhere" through a theme set in `App.OnStart`; no hard-coded colours on controls.
- Only delegable filters on lists that can pass 500 items.
- A loading indicator while data loads; errors shown with `Notify`, never ignored.
- Tablet layout 16:9 unless the app is for phones.

## Model-driven apps
- Main forms: at most 3 tabs; the most used fields first.
- Default views sorted by Modified On, newest first; at most 8 columns.

## SharePoint lists
- Create columns without spaces (`DueDate`), then set the display name (`Due Date`).
- Index every column used to filter, sort or look up.
- Status and category columns are Choice columns, not free text.
- At most 8 lookup and person columns per list (the Power BI 2.0 connector reads 12 per query).
