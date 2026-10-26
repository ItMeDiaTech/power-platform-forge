# Capacity-gated features

`ppx pbi ws ls` shows `onCapacity` per workspace. Shared capacity (Pro) is the baseline; these need
more, and ppx exits 2 naming what is missing instead of trying.

| Feature | Needs (Learn, read 2026-10-26) | On Pro instead |
|---|---|---|
| `ppx pbi export-png` (Export To File) | workspace on Premium, Embedded or Fabric capacity (Premium Per User is not enough) and the tenant setting "Export reports as image files" (off by default); at most 50 pages per job, 250 MB, result kept 24 hours | `ppx pbi screenshot` from Power BI Desktop through the Desktop Bridge |
| Enhanced refresh (tables, partitions, commit mode) | Premium, PPU or Embedded | a full refresh with `ppx pbi refresh` |
| XMLA read/write (Tabular Editor, TMSL deploys) | Premium or PPU with the XMLA endpoint set to Read Write | `ppx pbi pull`/`push` (TMDL through Fabric) or the Desktop round trip |
| PBIX imports of 1 to 10 GB | Premium (temporary upload location) | publish from Desktop |

When export-png is refused on a capacity workspace (403), the tenant setting is the likely cause:
the request text is in `docs/ADMIN-REQUESTS.md#power-bi-tenant-settings`. Never suggest buying or
assigning capacity as a fix; it is the user's or IT's decision.
