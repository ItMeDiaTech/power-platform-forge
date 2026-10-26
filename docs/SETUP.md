# Setup

The plugin runs on the machine that can reach your work tenant (your work laptop). It is developed elsewhere and installed from GitHub, so updating it is one command.

## Prerequisites

| Tool | Why | Install (user scope where possible) |
|---|---|---|
| Claude Code 2.1.295 or later | Hooks rely on `onFailure: "block"` | `claude update` |
| Git for Windows | Claude Code's Bash tool and hook shell | <https://git-scm.com/download/win> |
| uv, plus a Python it can find | Runs the harness, the change-notice hook and the MCP server | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"`, then `uv python install 3.12` |

The harness itself has no third-party Python dependencies, so after uv has a Python the plugin needs no package index.

The Power Platform command-line tools below are needed for the verbs that use them. `ppx auth doctor` reports which ones are missing and the exact install command.

| Tool | Used for | Install |
|---|---|---|
| Azure CLI | Tokens for Power BI, Fabric and the Dataverse Web API | `winget install -e --id Microsoft.AzureCLI` |
| Power Platform CLI (`pac`) | Dataverse, solutions, canvas app source | needs the .NET 8 SDK or later, then `dotnet tool install --global Microsoft.PowerApps.CLI.Tool` |
| CLI for Microsoft 365 (`m365`) | SharePoint lists and files | needs Node.js 22 or later, then `npm install -g @pnp/cli-microsoft365@11` (ppx is written for version 11; version 12 changes how deletes reach the recycle bin) |
| Power Apps maker PowerShell module | Sharing and restoring canvas apps | in Windows PowerShell 5.1: `Install-Module Microsoft.PowerApps.PowerShell -Scope CurrentUser` |
| Power BI Desktop (optional) | Local edit round trip and screenshots | `winget install -e --id Microsoft.PowerBI`, or the Microsoft Store |

Behind a corporate proxy, set `HTTPS_PROXY` and `HTTP_PROXY` before installing; uv and npm both honour them.

## Optional Microsoft plugins

These author content that this plugin deliberately does not rebuild. Install them only if you use them; each adds MCP tools to every session (only their names load until one is needed).

| Plugin | Adds | Install |
|---|---|---|
| `powerbi-authoring` | 23 tools for semantic models and reports (local Power BI Modeling MCP) | `/plugin marketplace add microsoft/skills-for-fabric`, then `/plugin install powerbi-authoring@fabric-collection` |
| `power-automate` | 65 tools for cloud flows (FlowAgent) plus Microsoft Learn search | `/plugin marketplace add microsoft/power-platform-skills`, then `/plugin install power-automate@power-platform-skills` |
| `canvas-apps` | Canvas app authoring through a live Power Apps Studio session (needs the .NET 10 SDK) | `/plugin install canvas-apps@power-platform-skills` |


## Install

In Claude Code:

```text
/plugin marketplace add ItMeDiaTech/power-platform-forge
/plugin install power-platform-forge@power-platform-forge
```

Or from a terminal:

```bash
claude plugin marketplace add ItMeDiaTech/power-platform-forge
claude plugin install power-platform-forge@power-platform-forge
```

## Update

```bash
claude plugin update power-platform-forge@power-platform-forge
```

Then run `/reload-plugins` in any open session, and `uv tool upgrade ppx` if you installed the command below. `claude plugin marketplace update` on its own only refreshes the listing; it does not install the new version.

## Sign in from your own terminal

Sign-in opens a browser, so it never runs inside Claude Code. Install the same harness as a command for your own terminal once:

```bash
uv tool install --from "git+https://github.com/ItMeDiaTech/power-platform-forge#subdirectory=scripts/ppx" ppx
uv tool update-shell
```

Open a new terminal, then sign in with the tools you use. `ppx auth doctor` lists exactly which lines you still need:

```bash
ppx auth login --backend az
ppx auth login --backend pac --environment https://contoso.crm.dynamics.com
ppx env add work --url https://contoso.crm.dynamics.com --workspace "Sales" --site https://contoso.sharepoint.com/sites/Finance --use
```

SharePoint and Graph need your own app registration first (`ppx auth app-spec` prints it); if your tenant does not let you register or consent, `docs/ADMIN-REQUESTS.md` has the request to send IT. The command finds the plugin's data directory through a pointer file that the plugin writes when a Claude Code session starts, so start Claude Code once before the first sign-in.

### Power Apps sharing, versions and restore: the maker session

The Power Apps maker PowerShell module keeps its sign-in only inside the window that signed in. Once a day, run:

```bash
ppx auth login --backend maker
```

A Windows PowerShell window opens; sign in there and leave it open. `ppx pa share`, `pa versions`, `pa restore` and the canvas app list then run in it until you close it, run `ppx auth logout --backend maker`, or leave it unused for 8 hours. If the module needs you to sign in again meanwhile, it asks in that window. Only your Windows account can talk to it, and not over the network. `ppx auth doctor` shows whether it is running (and whether it is busy). Without it, those verbs give you the PowerShell lines to run yourself.

## Your design preferences

`ppx prefs` shows the rules the agent follows when it builds or changes a report, app or list: colours, fonts, page layout, naming, delegation and list-column rules. Run `ppx prefs --init` once to get your own copy, then edit it in any editor (the envelope prints its path, in the plugin's data folder, so updates never overwrite it). The plugin's MCP server also serves it as `ppx://design-preferences`, so you can @-mention it in Claude Code.

## What the plugin adds to a session

- **Change notices.** Before a Bash, PowerShell or Power Platform MCP call that changes something, a one-line notice says what will change in plain English. Irreversible or costly changes ask first. `ppx env set --notice-mode confirm` makes every change ask; `docs/SAFETY.md` has the details.
- **The `ppx` MCP server.** Three tools (`doctor`, `verbs`, `run`) that run any ppx verb in-process, so tokens and caches stay warm between calls.
- **Your design preferences**, which the authoring skills read before they build anything (see above).
- **Skills** that tell the agent which verb to use for each task, starting with `pp-auth-doctor`, and two agents, `pp-architect` and `pp-reviewer`.
- **A PBIR schema check.** After the agent writes or edits a Power BI report definition file (`*.Report/definition/**/*.json`), it is checked against Microsoft's schema and any errors go back to the agent. The check advises; it never blocks.

## Uninstall

```bash
claude plugin uninstall power-platform-forge@power-platform-forge
```

This also deletes the plugin's data directory (cached inventories, backups, the audit log). Add `--keep-data` to keep it.

## Developing this repository

```bash
git config core.hooksPath .githooks
```

That enables the pre-commit scan, which rejects credentials and tenant identifiers (SharePoint hosts, Dataverse org URLs, tenant domains, email addresses and GUIDs). Use `contoso`-style placeholders in documentation and examples.
