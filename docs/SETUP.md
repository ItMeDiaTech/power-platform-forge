# Setup

The plugin runs on the machine that can reach your work tenant (your work laptop). It is developed elsewhere and installed from GitHub, so updating it is one command.

## Prerequisites

| Tool | Why | Install (user scope where possible) |
|---|---|---|
| Claude Code 2.1.295 or later | Hooks rely on `onFailure: "block"` | `claude update` |
| Git for Windows | Claude Code's Bash tool and hook shell | <https://git-scm.com/download/win> |
| uv, plus a Python it can find | Runs the harness, the change-notice hook and the MCP server | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"`, then `uv python install 3.12` |

The harness itself has no third-party Python dependencies, so after uv has a Python the plugin needs no package index.

The Power Platform command-line tools below are needed for the verbs that use them. `ppx auth doctor` (added in the next release) reports which ones are missing and the exact install command.

| Tool | Used for | Install |
|---|---|---|
| Azure CLI | Tokens for Power BI, Fabric and the Dataverse Web API | `winget install -e --id Microsoft.AzureCLI` |
| Power Platform CLI (`pac`) | Dataverse, solutions, canvas app source | needs the .NET 8 SDK or later, then `dotnet tool install --global Microsoft.PowerApps.CLI.Tool` |
| CLI for Microsoft 365 (`m365`) | SharePoint lists and files | needs Node.js 22 or later, then `npm install -g @pnp/cli-microsoft365` |
| Power Apps maker PowerShell module | Sharing and restoring canvas apps | in Windows PowerShell 5.1: `Install-Module Microsoft.PowerApps.PowerShell -Scope CurrentUser` |
| Power BI Desktop (optional) | Local edit round trip and screenshots | Microsoft Store or <https://powerbi.microsoft.com/desktop> |

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

Then run `/reload-plugins` in any open session. `claude plugin marketplace update` on its own only refreshes the listing; it does not install the new version.

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
