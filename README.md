# Power Platform Forge

A Claude Code plugin and CLI harness that lets an AI agent design, build, test and ship a complete Microsoft Power Platform solution end to end: a Dataverse schema, Power Apps (model-driven, canvas or code apps), Power Automate flows, a Power BI model and report over Fabric, a Copilot Studio agent and custom connectors, delivered as one managed solution through a pipeline, with production guardrails enforced in code.

> **Status: planning.** No code yet. The first phase scaffolds the plugin manifests, the `ppx` harness and the production guard.

## Why

Microsoft now ships official agent plugins for canvas, model-driven and code apps, Power Pages, Power Automate, Power BI and Fabric. None of them covers the work that spans products: solution ALM, Dataverse schema-as-code, custom connectors, Copilot Studio sync, testing, auth diagnosis and environment safety. This project depends on Microsoft's plugins for authoring and owns the glue.

## What it will ship

- **`ppx`**, a JSON-emitting CLI harness over `pac`, `fab`, the Dataverse Web API, the Power BI and Fabric REST APIs and the Power Platform API. One response envelope, `--dry-run` and `--yes` on every mutating verb, an audit log, throttling and long-running-operation polling built in.
- **Skills:** `pp-auth-doctor`, `pp-solution-alm`, `dataverse-schema`, `custom-connector`, `copilot-studio-agent`, `pp-testing`, `fabric-link-analytics`, `pp-solution-architect`.
- **Agents:** `pp-architect` (plans with read-only tools), `pp-deployer` (the only agent that imports or deploys), `pp-reviewer` (solution checker, best-practice analysis, schema validation).
- **Hooks:** a `PreToolUse` guard that blocks destructive commands against production environments unless explicitly allow-listed, and `PostToolUse` validators for TMDL, PBIR and YAML.
- **Dual manifest** so it installs in both Claude Code and GitHub Copilot CLI.

## Repository conventions

- Secrets never enter git. `scripts/check-secrets.sh` runs as a pre-commit hook. Enable it once per clone:

  ```bash
  git config core.hooksPath .githooks
  ```

- Tokens come from `az`, `pac auth` and `fab auth`. Nothing in this repository stores them.
