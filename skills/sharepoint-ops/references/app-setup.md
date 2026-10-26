# Tier B app setup for SharePoint and Graph

SharePoint verbs run CLI for Microsoft 365 (`m365`) signed in with an app the user registers in their own tenant. Since CLI for Microsoft 365 v9 there is no shared PnP app; every user brings their own (<https://pnp.github.io/cli-microsoft365/cmd/login/>). `az` cannot stand in: on most tenants it cannot get SharePoint tokens (AADSTS65002), and its Graph token is not documented to carry the Sites scopes.

## What `ppx auth app-spec` returns

`ppx auth app-spec` is read-only; you may run it. With `az` signed in it looks up each permission's id at run time and writes a `required-resource-accesses` JSON file under the ppx exports folder.

| Field | Meaning |
|---|---|
| `data.method` | `az`: a ready command; `portal`: az could not look the ids up, follow `steps` in the Entra admin center |
| `data.command` | `az ad app create --display-name "ppx (power-platform-forge)" --public-client-redirect-uris http://localhost --is-fallback-public-client true --required-resource-accesses "@<file>"` |
| `data.file` | the permissions file the command reads |
| `data.steps` | the order: run the command, note `appId`, sign in, accept consent |
| `warnings` | an API whose ids az could not find (for example no Dataverse in the tenant); add it in the portal only if needed |
| `next_actions` | the two lines the user runs in their own terminal |

Delegated permissions the app asks for. The Graph ones need no admin consent by default (Graph permissions reference, read 2026-10-25); whether the tenant lets users consent to the others shows at sign-in.

| API | Scopes | Used by |
|---|---|---|
| Microsoft Graph | `User.Read`, `Sites.ReadWrite.All`, `Files.ReadWrite.All` | sign-in, `sp site search`, Graph reads and file writes |
| SharePoint | `AllSites.Write` | every `m365 spo` call behind `sp list`, `sp fields`, `sp items`, `sp files`, `sp export` |
| Power BI Service | `Dataset.ReadWrite.All`, `Report.ReadWrite.All`, `Workspace.Read.All`, `Item.ReadWrite.All` | Power BI verbs when az is refused |
| Dynamics CRM | `user_impersonation` | Dataverse verbs when az is refused |

## The user's steps (their own terminal, never yours)

1. Run the `data.command` line. It prints JSON; the `appId` value is the client id.
2. Run `ppx auth login --backend m365 --app-id <appId> --tenant <tenantId>` and accept the consent prompt in the browser. ppx saves the app id and tenant (`auth.json` in the data folder; never a secret), so later sign-ins are just `ppx auth login --backend m365`.
3. You re-run `ppx auth doctor --audience sharepoint` (it needs a site on the profile: `ppx env set --site <url>`); the row should read `ok` with source `tier_b`. `--audience graph` checks the token `sp site search` uses.

## When it fails

- **Registration refused** ("You don't have permission to register applications"): the tenant turned off *Users can register applications*. Send the `docs/ADMIN-REQUESTS.md#app-registration` text.
- **Consent refused** (AADSTS65001 again after accepting, or AADSTS90094 "admin approval required"): user consent is off. Every `sp` verb then exits 2 with `data.admin_request`, the exact text for IT (`docs/ADMIN-REQUESTS.md#consent`). Registration and consent are separate facts: a working registration with refused consent still needs IT.
- **AADSTS53003 or device codes**: Conditional Access; run the doctor and follow its fix.

Never suggest a client secret, a certificate, `m365 setup` (its full scope set includes admin-only scopes and fails the whole prompt) or device-code sign-in.
