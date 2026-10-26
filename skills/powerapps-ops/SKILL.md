---
name: powerapps-ops
description: Power Apps, Power Automate flows, model-driven apps and Dataverse data from the terminal with ppx. Use when the user asks which apps read a SharePoint list, what data sources or screens a canvas app has, whether an app has non-delegable filters (delegation warnings, the 500-row data row limit), to download or inspect canvas app source (.pa.yaml), to share an app with someone, to list or restore an earlier app version, which environments they can reach or who they are in one, which cloud flows exist and why a flow run failed, to list or edit a model-driven app's sitemap, forms or views and publish them, or to query Dataverse table rows or run Power Fx. Covers canvas inventory and index, inspect, share, versions, restore, flows, mda and data verbs.
---

# powerapps-ops

`ppx pa|mda|data ...` answers Power Apps questions in one call each, without the maker portal. Every verb prints one compact JSON envelope; source trees and big results go to disk and the envelope returns the path.

Run verbs like this (or call the MCP tool `run` with `verb` and `args`, e.g. `{"verb": "pa ls", "args": ["--list", "https://contoso.sharepoint.com/sites/ops/Lists/Assets"]}`):

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}/scripts/ppx" ppx --data-dir "${CLAUDE_PLUGIN_DATA}" pa ls --list "<site>/Lists/<list>"
```

## Design preferences come first

Before you create or change a canvas app, form, view or sitemap, run `ppx prefs` (or read the MCP resource `ppx://design-preferences`) and follow the user's rules. If a request breaks one, say which rule and ask before going ahead. `ppx prefs --init` gives the user their own copy to edit; its path is in the envelope.

## Which verb answers which question

| Question | Verb (fewest calls) |
|---|---|
| Which apps read SharePoint list X? | `pa ls --list <site>/Lists/<list>` (or `--list <list>`) |
| Do they have non-delegable filters? | `pa inspect <app>` for each app `pa ls --list` returned |
| What apps do I have? Find one by word | `pa ls [--type canvas\|mda]`, `pa ls --grep <text>` |
| Search source of every app (screens, sites) | `pa index` once, then `pa ls --grep` / `--list` |
| Get an app's source to read | `pa get <app> [--out <new or empty dir>]` (.msapp plus read-only `Src/*.pa.yaml`) |
| Share an app | `pa share <app> --user <upn> --role CanView\|CanEdit` |
| Earlier versions; roll back | `pa versions <app>`, then `pa restore <app> --version <id> --yes` |
| Which environments; am I System Customizer? | `pa env ls`, `pa env who` |
| Which cloud flows; why did a run fail? | `pa flows ls`, `pa flows runs <flow>` |
| A model-driven app's parts | `mda ls`, `mda show <app>` |
| Edit sitemap, a form or a view | `mda sitemap\|forms --form\|views --view <app> --out <dir>`, edit, `mda publish <app> --from <dir>` |
| Rows of a Dataverse table | `data query --table <entity set> --fields a,b --top 20 [--filter "<OData>"]` |
| Run Power Fx against Dataverse | `data fx --file <file>` |

Apps, flows, forms and views take a name or an id; names match case-insensitively, and a miss refreshes the inventory once. Two apps with one name exit 1 listing their ids: pass the id.

## "Which apps read list X, and do any have non-delegable filters?"

1. `pa ls --list <site>/Lists/<list>` answers from each app's connection references, with no download. `site_checked: false` means ppx knows the app reads a list of that name but not on which site; say so.
2. `data.not_indexed` counts apps whose source was never downloaded. Their connection references were still checked, so do not run `pa index` just to answer step 1. Run it (it downloads every new or changed app, which is slow) only when the user wants site-level certainty, `--grep` over screens, or the inventory came through pac (a warning says so; pac shows no data sources).
3. `pa inspect <app>` per matching app returns `findings` (file, line, control, property, rule) and `hints`, never formulas. Report each finding as "screen file:line, control.property: rule"; quote the hint. `data_row_limit.value: null` means the setting is not in the source: say the default is 500 (up to 10,000, Settings > General).

Rules and fixes: `references/delegation-sharepoint.md`. Reading `.pa.yaml` yourself: `references/pa-yaml-reading.md`.

## Canvas source is read-only

`.pa.yaml` files are for reading and diffs only; editing them changes nothing in the app. Never use `pac canvas pack|unpack`. Canvas edits are attended: the user opens the app in Studio and keeps the tab open, you use Microsoft's `/canvas-apps:canvas-app`, then `ppx pa inspect <app> --refresh` (or `ppx pa get <app>`) confirms the change; without `--refresh`, inspect reuses the source indexed before the edit. Steps: `references/attended-canvas-edit.md`.

## Share, versions, restore

- `pa share` resolves the UPN to an object id first (Microsoft Graph, cached); `--dry-run` shows it. The user is e-mailed about the share. A warning names the SharePoint lists the app reads: sharing the app does not give access to them, so tell the user to check the lists' permissions.
- `pa restore` is irreversible: run it without `--yes` first (exit 2 lists the change), show the user, then re-run with `--yes` only after they agree. The exit-2 change list names the app's environment (the change notice cannot); pass `--environment` from `pa ls` or `pa versions` output when the user should see it there too. It lists versions first and records the version it replaces; the `undo` next action restores that one. It reports success only when a new latest version appears afterwards; "not confirmed" means check `pa versions` before retrying. A restored version is saved, not published: tell the user to publish it in Studio.
- `pa share`, `pa versions`, `pa restore` and canvas `pa ls` use the Power Apps maker module in Windows PowerShell 5.1, whose sign-in lives only inside one PowerShell window. They run in the **maker session**: a window the user opens once a day with `ppx auth login --backend maker` (in their own terminal; they sign in there and leave it open). `ppx auth doctor` shows `maker_session.running`. Without it these verbs exit 2: hand the user the `ppx auth login --backend maker` line first; the next action holds the same step as `Add-PowerAppsAccount; ...` lines they can paste into Windows PowerShell (not pwsh) instead. If their sign-in is blocked, the step is done at make.powerapps.com. Without a session, `pa ls` lists canvas apps through `pac`, which has no data sources: run `pa index` for those.

## Model-driven apps

- `mda sitemap|forms|views <app> --out <dir>` saves `sitemap-<id>.xml`, `form-<id>.xml`, `view-<id>.fetchxml.xml` and `view-<id>.layoutxml.xml`. Edit those files in place; keep the names. Saving again over an edited file keeps the edit as `<file>.<UTC time>.bak`.
- `mda publish <app> --from <dir> [--add view:<id>,form:<id>]` skips unchanged files, backs up every value it replaces, writes the rest and publishes once. The undo is in each change: publish from the backup folder. Publishing also publishes any other unpublished changes to the app, its sitemap and the tables it names, other makers' drafts included, and no backup undoes that: say so to the user. Run it with `--dry-run` first when the user has not seen the change list.
- Editing needs the System Customizer (or System Administrator) role; `pa env who` shows directly assigned roles. On a 403 ppx returns `data.admin_request`, the text the user sends the environment admin.
- Building a new model-driven app or page: Microsoft's `/model-apps:app-builder`. Details: `references/mda-web-api.md`.

## Flows

`pa flows ls|runs` read solution-aware cloud flows only; run records belong to each flow's primary owner (a co-owner may see none) and are kept 28 days by default with possible gaps (the `warnings` say so). `duration_ms` is milliseconds. For flows outside solutions ("My flows"), or to edit or run a flow, use FlowAgent's tools from Microsoft's `power-automate` plugin.

## Data

- Always pass `--fields`; `--top` defaults to 50. Filters are OData on logical names: `--filter "statecode eq 0 and revenue gt 100000"`.
- `data fx` reads the file first: `Remove`, `RemoveIf`, `Clear`, `ClearCollect`, `Patch`, `Update`, `UpdateIf` overwrite or delete rows and need `--yes` after the user agrees; `Collect` adds rows. Run it with `--dry-run` first: every real run asks the user, because the notice cannot see inside the file.
- Rows too large for the envelope are saved to `data.saved_to` (path and digest inline); name fewer `--fields` to get them inline.

## Without a Dataverse token

When no sign-in can get a Dataverse token, list reads (`pa flows`, `mda` lists, `pa ls --type mda`) run through `pac env fetch` and say so in `warnings`; XML reads and all writes exit 2 with the consent steps (`ppx auth app-spec`, `docs/ADMIN-REQUESTS.md#consent`). `data query` falls back to Power Fx without `--filter` (tables then use display collection names, such as `Contacts`). `token_source` in the envelope says which sign-in answered.

## Limits that matter

- Canvas data row limit: 500 by default, up to 10,000; a non-delegable formula sees only that many rows.
- SharePoint: only `=` delegates on ID; Person delegates only Email and DisplayName; no StartsWith on Choice or Lookup subfields; `in`, `Not`, `IsBlank`, Search and CountIf do not delegate.
- Dataverse service protection: 6,000 requests, 1,200 s of execution and 52 concurrent requests per user per 5 minutes; avoid loops of single-row queries.

## Errors

| Exit | Message | Do |
|---|---|---|
| 2 | needs the Power Apps maker session | give the user `ppx auth login --backend maker` for their own terminal (or the `Add-PowerAppsAccount; ...` line to paste) |
| 1 | the maker session window gave no answer within N s, or is busy with other calls | ask the user to look at the ppx maker session window (it may ask them to sign in again); for share or restore, check whether it happened (`pa versions`) before retrying, since it may still complete |
| 2 | maker module is not installed | give the user the `Install-Module` line from `next_actions` |
| 2 | no Dataverse token for writes | the consent steps in `next_actions` |
| 2 | needs the System Customizer role | give the user `data.admin_request` |
| 2 | no probe has run (pac columns) | `ppx probe --area pa` on the work laptop |
| 1 | pac ... output has no header ... written for pac 2.14.1 | pac changed its table: report it, use the maker module path |
| 1 | several apps are named ... | pass the id from `data.candidates` |

## Never

- Print or paste tokens; never run `Add-PowerAppsAccount`, `pac auth create` or `az login` yourself.
- Open make.powerapps.com for a question a verb answers; give the URL from `next_actions` only for the steps that need it.
