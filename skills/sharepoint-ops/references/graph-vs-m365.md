# Which backend each sp verb uses, and why

| Verb | Backend | Call |
|---|---|---|
| `sp site search <q>` | Microsoft Graph | `GET /v1.0/sites?search=<q>` (delegated `Sites.Read.All`) |
| `sp site get` | m365 | `m365 spo web get --url <site>` |
| `sp list ls` | m365 | `m365 spo list list --webUrl <site> --properties ... --filter "Hidden eq false"` |
| `sp list get` | m365 | `m365 spo list get --webUrl <site>` with `--title`, `--id` or `--url <list>`, and `--properties ...` |
| `sp fields ls` | m365 | `m365 spo field list --webUrl <site>` with `--listTitle`, `--listId` or `--listUrl <list>` |
| `sp items ls` | m365 | `m365 spo listitem list ... --fields <f> --pageSize <n> --pageNumber <p-1> [--filter <odata>]` |
| `sp items get` | m365 | `m365 spo listitem list ... --filter "Id eq <n>" [--fields <f>]`, first row |
| `sp files ls` | m365 | `m365 spo file list --folderUrl` and `m365 spo folder list --parentFolderUrl`, run side by side |
| `sp files get` | m365 | `m365 spo file get --url <path> --asFile --path <local>` |
| `sp export` | m365 | `m365 spo field list`, then `m365 spo listitem list ... --fields <all visible>` without a page size |

Every m365 call adds `--output json` (collections always arrive as arrays), and ppx strips any line printed before the JSON result. Command reference: <https://pnp.github.io/cli-microsoft365/> (CLI for Microsoft 365 v11).

## Why m365 for lists and items

- It speaks SharePoint REST with the user's own app, so list titles resolve server-side (`GetByTitle`, case-insensitive), `--fields Owner/Title` expands lookups, and `--filter` passes OData through unchanged.
- Without `--pageSize`, `spo listitem list` follows SharePoint's paging itself and returns every item, including lists over 5,000; `sp export` relies on this. With `--pageSize`, `--pageNumber` is zero-based; ppx's `--page` counts from 1. To reach page N the command first reads `pageSize × (N-1)` ids in one request, and SharePoint serves at most 5,000 per request, so ppx refuses a page that starts past item 5,000 and offers `sp export` instead.
- `sp items get` uses `listitem list` with an `Id eq <n>` filter, because `spo listitem get` on v11 takes `--properties` without lookup expansion and silently ignores an unknown `--fields`.
- `spo listitem list` drops the duplicate `ID` key and keeps `Id`; a column whose internal name starts with `_` comes back as `OData__name`. ppx maps both back to the name you asked for.

## Why Graph for site search

The tenant-wide site list (`m365 spo site list`) needs the SharePoint administrator role. Graph search works for any user and returns `displayName`, `name` and `webUrl`; ppx remembers them in `<data>/cache/sp/sites.json` so later `--site <name>` needs no call.

## Tokens

- m365 holds its own sign-in (the Tier B app); ppx never sees that token.
- Graph calls use `ppx.auth` with the Tier B source first, because the Tier B app carries `Sites.ReadWrite.All` and az's Graph scopes are not documented. After `ppx probe` records a working Graph route in `routing.json`, that route is used instead.
- `ppx raw graph GET <path>` and `ppx raw spo GET <path>` reach any other documented endpoint with the same tokens; non-GET methods produce a change notice.

## Each m365 call starts Node

Expect one to two seconds per call. Verbs make one call except: `sp files ls` (two, in parallel), `sp export` (two), `sp items ls --filter` on a list over 5,000 items (up to three: list size, columns, items).
