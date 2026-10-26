# PBIR edit loop (reports)

`ppx pbi pull report "<report>" --workspace "<ws>"` writes the report in PBIR format to
`<out>/<report>.Report/`. Learn says the definition comes back in the format the service stores it
in, so a PBIR-Legacy report may come back as one `report.json` at the folder root instead of
`definition/`: the envelope warns, and that file is edited directly (the PBIR report CLI and the
schema check do not apply). Whether pushing a PBIR copy of a PBIR-Legacy report changes its stored
format is not yet verified on a tenant.

## Folder layout (Learn, "Report definition", Fabric REST API)

```text
<report>.Report/
  definition.pbir                        which semantic model the report uses (byConnection from the service)
  .platform
  definition/
    report.json, version.json
    pages/pages.json                     page order and the active page
    pages/<pageId>/page.json             one page: displayName, size, filters
    pages/<pageId>/visuals/<visualId>/visual.json
    bookmarks/
  StaticResources/                       themes, images, custom visuals
```

Page and visual folder names are 20-character ids; `displayName` is what users see.

## Editing

- Visual, page, filter and bookmark changes: `/powerbi-authoring:powerbi-report-cli`, which knows
  the visual catalog, formatting objects and query projections. Do not hand-write `visual.json`
  queries from memory.
- Renaming a page or changing its size: a direct edit of `page.json` is fine.
- Every JSON file under `<name>.Report/definition/` you write is checked by the plugin's PostToolUse
  hook against the PBIR schema its `$schema` names (vendored in `templates/pbir-schemas/`). A failed
  check shows the JSON path and the problem; the file is already saved, so fix it and save again.
  A `$schema` version that is not vendored is skipped; `templates/pbir-schemas/refresh-schemas.ps1
  -Schema <path>` vendors it.
- The report CLI's own `powerbi-report-author validate <folder>` is a further check (optional).

## Push

```bash
ppx pbi push report "<report>" --workspace "<ws>" --from "<folder>" --dry-run
ppx pbi push report "<report>" --workspace "<ws>" --from "<folder>"
```

Deleting a page or visual folder removes those parts in the service: push exits 2 until you pass
`--allow-part-removal`. To see the result, `ppx pbi screenshot` (Desktop) or `ppx pbi export-png`
(capacity only).

Limits (Learn): 1,000 pages per report, 1,000 visuals per page, 300 MB of report files; files are
UTF-8 without BOM.
