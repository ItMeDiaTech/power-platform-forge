# Power Platform Forge

A Claude Code plugin for people who work with many Power BI reports, Power Apps and SharePoint lists every day. It gives an AI agent compact command-line tools for that work, so it can inventory, inspect, edit, publish and refresh your assets without driving a browser, which is slow and burns tokens.

It is built for an ordinary user on a work tenant. Nothing requires an admin role or a service principal. Where a feature needs an admin setting, the tool says so and names the request to send to IT ([docs/ADMIN-REQUESTS.md](docs/ADMIN-REQUESTS.md)).

> **Status: early development.** Everything below is built and tested offline against recorded API shapes. It has not yet run against a real work tenant; the first run on a work laptop (`ppx probe`) records which sign-ins and write paths that tenant allows.

## What it ships

- **`ppx`**, a command-line harness that prints one small JSON envelope per command. It calls Power BI REST, Fabric REST, Microsoft Graph, SharePoint REST and the Dataverse Web API with tokens from `az`, `pac` and the CLI for Microsoft 365, pages and summarises large results, and writes big artifacts (report definitions, PBIX files, exports) to disk instead of the conversation.
- **A local MCP server, `ppx`**, with three tools (`doctor`, `verbs`, `run`) that drive the same harness in-process, so tokens and caches stay warm between calls.
- **Change notices.** Before any command changes something in your tenant, you see a plain-English line saying what will change and where. Irreversible changes and unrecognised commands ask first, and `ppx env set --notice-mode confirm` makes every change ask. Changes are recorded in an audit log, with a backup and an undo command where one exists. See [docs/SAFETY.md](docs/SAFETY.md).
- **Skills** that route the agent to the right verb, or to Microsoft's own agent plugins for authoring (Power BI models and reports, canvas apps, flows): `pp-auth-doctor`, `pbi-ops`, `pbi-authoring`, `sharepoint-ops`, `powerapps-ops`, `pp-solution-alm`, `pp-solution-architect`, `pp-testing`.
- **Agents** `pp-architect` (plans a build: SharePoint list or Dataverse, which app type, how Power BI reads the data, which flows and solution) and `pp-reviewer` (runs the matching checks on a change before it ships and ranks the problems).
- **An advisory schema check** of Power BI report (PBIR) files after the agent edits them, against Microsoft's published JSON schemas.

## What the agent can do

`ppx verbs` lists the groups, `ppx verbs <group>` their verbs, and `ppx verbs <group> <verb>` the arguments.

| Group | Verbs |
|---|---|
| `auth` | doctor, login, logout, app-spec |
| `env` | profiles of defaults: add, set, rm, use, ls |
| `pbi` | workspaces, inventory and diff, report and model details, DAX queries, refresh history and gated refresh, pull/push report and model definitions with backups, publish and deploy, data sources and parameters, clone and rebind, dashboards, screenshots and PNG export, sharing, models from SharePoint lists, repoint |
| `sp` | sites, lists, columns, items, files, CSV/JSON export, Power Query snippets; writes: create lists and columns, add, update and delete items (recycle bin, eTag-safe), upload files |
| `pa` | environments, canvas and model-driven apps, which apps use a list, canvas source for inspection, sharing, versions and restore, flows and runs |
| `mda` | model-driven apps: forms, views, sitemap, publish |
| `data` | Dataverse queries and FetchXML |
| `solution` | list, export, unpack, pack, check, deployment settings, import |
| `test`, `connector`, `copilot`, `dlp` | DAX test suites, custom connectors, Copilot Studio publish checks, connector policy checks |
| `raw`, `probe` | any documented endpoint with your token; discover what your account can reach |

## Install

In Claude Code:

```text
/plugin marketplace add ItMeDiaTech/power-platform-forge
/plugin install power-platform-forge@power-platform-forge
```

Then sign in from your own terminal and run the doctor. Prerequisites, sign-in, update and uninstall are in [docs/SETUP.md](docs/SETUP.md).

## Documentation

- [docs/SETUP.md](docs/SETUP.md): prerequisites, install, sign-in, update, uninstall.
- [docs/SAFETY.md](docs/SAFETY.md): what the change notices do and how to make every change ask.
- [docs/ADMIN-REQUESTS.md](docs/ADMIN-REQUESTS.md): the exact requests to send IT when a tenant setting blocks you.
- [docs/PLATFORM-FACTS.md](docs/PLATFORM-FACTS.md): what the plugin assumes about Claude Code and Microsoft's APIs, with dated evidence.

## Repository conventions

- Secrets and tenant identifiers never enter git. `scripts/check-secrets.sh` runs as a pre-commit hook; enable it once per clone with `git config core.hooksPath .githooks`.
- Tokens come from `az`, `pac auth` and the CLI for Microsoft 365. ppx keeps a short-lived copy in its data directory, encrypted for your Windows account (DPAPI); nothing in this repository stores them.
- The PBIR schemas under `templates/pbir-schemas` are Microsoft's, under the MIT license in that folder.
