# Power Platform Forge

A Claude Code plugin for people who work with many Power BI reports, Power Apps and SharePoint lists every day. It gives an AI agent compact command-line tools for that work, so it can inventory, inspect, edit, publish and refresh your assets without driving a browser, which is slow and burns tokens.

It is built for an ordinary user on a work tenant. Nothing requires an admin role or a service principal. Where a feature needs an admin setting, the tool says so and names the request to send to IT.

> **Status: early development.** This release ships the plugin manifests and the install and update path. The `ppx` harness, change notices, skills and the MCP server arrive in the next releases.

## What it ships

- **`ppx`**, a command-line harness that prints one small JSON envelope per command. It reads Power BI REST, Fabric REST, the Dataverse Web API and SharePoint through `az`, `pac` and the CLI for Microsoft 365, pages and summarises large results, and writes big artifacts (report definitions, exports) to disk instead of the conversation.
- **A local MCP server, `ppx`**, with three tools (`doctor`, `verbs`, `run`) that drive the same harness for clients without a shell.
- **Change notices.** Before any command changes something in your tenant, you see a plain-English line saying what will change and where. Irreversible changes and unrecognised commands ask first. Every change is recorded with a backup and an undo command where one exists.
- **Skills** that route the agent to the right `ppx` verb or to Microsoft's own agent plugins for authoring (Power BI models and reports, canvas apps, flows).

## Install

In Claude Code:

```text
/plugin install power-platform-forge --marketplace ItMeDiaTech/power-platform-forge
```

Prerequisites, the update command and uninstall are in [docs/SETUP.md](docs/SETUP.md).

## Documentation

- [docs/SETUP.md](docs/SETUP.md): prerequisites, install, update, uninstall.
- [docs/PLATFORM-FACTS.md](docs/PLATFORM-FACTS.md): what the plugin assumes about Claude Code and Microsoft's APIs, with dated evidence.

## Repository conventions

- Secrets and tenant identifiers never enter git. `scripts/check-secrets.sh` runs as a pre-commit hook; enable it once per clone with `git config core.hooksPath .githooks`.
- Tokens come from `az`, `pac auth` and the CLI for Microsoft 365. Nothing in this repository stores them.
