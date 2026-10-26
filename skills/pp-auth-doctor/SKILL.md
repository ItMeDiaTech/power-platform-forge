---
name: pp-auth-doctor
description: Check which Power Platform APIs this user can reach before any Power BI, Fabric, Dataverse, Power Apps, Graph or SharePoint work, and fix sign-in problems. Use at the start of a session, when a ppx verb exits 2 about sign in or a token, when an AADSTS code appears (65001, 65002, 53003, 50076, 530036, 700082 and others), when Conditional Access or consent blocks a call, when the user asks "which APIs work", "am I signed in", "run the auth doctor", or needs the Tier B app registration (app-spec) for SharePoint and Graph access.
---

# pp-auth-doctor

`ppx auth doctor` tells you, per API, whether a token can be minted silently right now, from which source, what is degraded, and the exact line that fixes each gap. It never opens a browser and never prompts.

## Run it once per session

Before the first Power Platform task in a session (and again after the user says they signed in):

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}/scripts/ppx" ppx --data-dir "${CLAUDE_PLUGIN_DATA}" auth doctor
```

Or call the MCP tool `doctor` (`mcp__plugin_power-platform-forge_ppx__doctor`); it returns the same envelope.

Re-check one API only: add `--audience pbi|fabric|graph|powerplatform|dataverse|sharepoint`.

Do not run the doctor before every verb: verbs reuse memoised tokens and fail with their own fix line.

## Read the envelope

| Field | Meaning |
|---|---|
| `ok` | `true`: you can work. Before `ppx probe` has run, at least Power BI REST or pac itself (`pac auth token`) works. After a probe, every API that `routing.json` recorded as working still works. With `--audience`, that one API works. |
| exit code | 0 ok; 2 the user (or IT) must act first, see `next_actions`; 1 an unexpected error with the verbatim message |
| `data.audiences[]` | one row per API: `audience`, `read: {status, source}`, `write`, `aadsts`, `scp`, `elapsed_ms`, `fix` |
| `read.status` | `ok`, `blocked` (sign-in, consent or policy), `missing` (no token tool installed), `timeout` (the tool did not answer in time) |
| `read.source` | `az` (Azure CLI), `tier_b` (CLI for Microsoft 365 with the user's own app) or `pac` |
| `write` | from the last `ppx probe --write`; `"unknown (run ppx probe --write)"` until then |
| `scp` | delegated scopes in the token, for diagnosis only (az tokens carry `user_impersonation` by design) |
| `warnings` | one line per degraded verb group, e.g. `SharePoint verbs unavailable: no Tier B sign-in`; before a probe also `pac verbs (solutions, canvas source)` when pac cannot mint its own token |
| `next_actions` | the exact lines to hand to the user, in order, plus `ppx env add ...` when no profile exists |
| `data.prerequisites[]` | each tool with `found` and `version`, or the `install` command; `pypi.org` and `registry.npmjs.org` `reachable` |

`dataverse` and `sharepoint` rows appear only when the active profile has a `url` or `site`.

## Keep working with what works

A blocked API degrades only the verbs that need it. Map rows to verb groups:

| Row | Verbs that need it |
|---|---|
| `pbi` | Power BI REST: `pbi ls`, `pbi query`, `pbi refresh`, `pbi history` |
| `fabric` | item definitions: `pbi pull`, `pbi push`, report and model edits |
| `graph` | SharePoint files and lists through Graph |
| `sharepoint` | SharePoint REST (`sp ...`) |
| `powerplatform` | Power Platform API, environments |
| `dataverse` | `pa ...`, `mda ...`, `data ...` |
| `pac` (warning only, before a probe) | `solution ...`, canvas source |

Tell the user in one line what is unavailable and why (the `warnings` text), then carry on with the groups that work. Do not retry a blocked API in a loop; the same call will fail the same way until someone acts.

## Sign-in is the user's job

- Only `ppx auth login --backend az|pac|m365|maker|fab` prompts, and it opens a browser.
- **Never run `ppx auth login` through the Bash or PowerShell tool**, and never run `az login`, `pac auth create`, `m365 login` or `Add-PowerAppsAccount` yourself. Hand the exact `next_actions` line to the user and ask them to run it in their own terminal.
- If `ppx` is not on the user's PATH, give them the full form:
  `uv run --project "${CLAUDE_PLUGIN_ROOT}/scripts/ppx" ppx auth login --backend az`
- From the MCP server, `run` refuses `auth login` and returns the same line.
- Never suggest device-code sign-in: many tenants block it, and a device-code session can poison later sign-ins (AADSTS530036).
- After the user says they are done, re-run the doctor (with `--audience` if only one API was fixed).

Typical first-run order on the work laptop:

1. `ppx auth login --backend az` (Power BI, Fabric, Dataverse Web API tokens)
2. `ppx auth login --backend pac --environment https://<org>.crm.dynamics.com` (Dataverse, solutions, canvas source)
3. `ppx env add work --url <environment url> --workspace <workspace> --site <site url> --use`
4. Tier B (below) when SharePoint or Graph is needed

## Tier B: the user's own app for SharePoint and Graph

az cannot mint SharePoint tokens on most tenants (AADSTS65002). The fix is one public-client app the user registers and consents to themselves.

1. Run `ppx auth app-spec` (read-only; you may run it). With az signed in it looks up every permission id, writes the `required-resource-accesses` file, and returns the exact command:
   `az ad app create --display-name "ppx (power-platform-forge)" --public-client-redirect-uris http://localhost --is-fallback-public-client true --required-resource-accesses "@<file>"`
   Without az it returns portal steps (Entra admin center > App registrations > New registration; Authentication: mobile and desktop, `http://localhost`, allow public client flows; API permissions by name).
2. The user runs that command in their own terminal and notes the `appId` it prints.
3. The user runs `ppx auth login --backend m365 --app-id <appId> --tenant <tenantId>` and accepts the consent prompt. ppx remembers the app id and tenant, so later sign-ins need only `ppx auth login --backend m365`.
4. Re-run the doctor with `--audience sharepoint` (or `graph`).

Registration and consent are separate facts. If the registration command fails, the tenant blocks app registration. If registration works but the consent prompt is refused (AADSTS65001 again), user consent is disabled. Either way the next step is an admin request, not a retry.

## Admin requests

Anything only an administrator can change (consent when user consent is off, a Conditional Access exception, device compliance) goes to `docs/ADMIN-REQUESTS.md`. The doctor's `fix` names the entry (`#consent`, `#conditional-access`, `#device-compliance`). Point the user at that entry and the exact text to send IT; never ask for or suggest admin roles, service principals or client secrets.

## AADSTS codes

`references/aadsts-map.md` lists each code, what ppx does and what the user does. Unknown codes exit 1 with the verbatim (scrubbed) message and `https://login.microsoftonline.com/error?code=<code>`; pass both to the user.

## Never

- Print, echo, log or paste a token; never call `az account get-access-token` or `m365 util accesstoken get` yourself.
- Edit `routing.json` or the token cache by hand; `ppx probe` writes routing, `ppx auth logout --backend <x>` clears the cache.
