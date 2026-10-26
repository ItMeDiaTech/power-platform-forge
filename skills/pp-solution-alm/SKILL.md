---
name: pp-solution-alm
description: Move Dataverse solutions between environments and keep them in Git with ppx - list solutions, export unmanaged and managed zips, unpack to source control (packagetype Both), pack, run solution checker, write deployment settings for connection references and environment variables, and import. Use when the user says solution, export a solution, unpack, pack, source control, Git, ALM, deployment settings, settings file, connection reference, environment variable, solution checker, import into test or production, promote, pipeline, or asks why flows are off after an import.
---

# pp-solution-alm

`ppx <verb>` below is short for:

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}/scripts/ppx" ppx --data-dir "${CLAUDE_PLUGIN_DATA}" <verb> ...
```

The MCP tool `run` (`mcp__plugin_power-platform-forge_ppx__run`) takes the same verb and arguments, with `dry_run` and `yes` as fields. Every verb answers with one JSON envelope; read `data`, `warnings` and `next_actions`.

All solution verbs run `pac`. The active profile's `--url` picks the environment (`ppx env ls`; switch with `ppx env use <name>`); `solution import` exits 2 without one. If a verb fails on sign-in, run the `pp-auth-doctor` skill; never run `pac auth create` yourself.

## Which verb answers what

| Question or task | Verb | Notes |
|---|---|---|
| Which solutions are in this environment? | `ppx solution ls` | cached per environment; `--refresh` refetches; `--top`, `--fields name,version` |
| Get a copy for source control | `ppx solution export --name <name> --managed` | unique or display name; writes `<name>.zip` and `<name>_managed.zip` under `<data>/exports/solutions/<time>/`; `--out <path>.zip` to choose; never overwrites |
| Put it in Git | `ppx solution unpack --zip <name>.zip --folder <repo>/solutions/<name>` | always packagetype Both, so both zips must sit side by side |
| Rebuild zips from the folder | `ppx solution pack --folder <repo>/solutions/<name> --zip <out>/<name>.zip` | a folder from `unpack` packs as Both (also writes `<name>_managed.zip`); a Git-integration (YAML) folder as Unmanaged; `--packagetype` overrides |
| Is it healthy? | `ppx solution check --zip <zip>` | counts by severity and the top rules; the SARIF reports stay on disk (`data.results`) |
| What does the target need? | `ppx solution settings --zip <zip> --out <settings.json>` | lists connection references and environment variables; re-running keeps values already filled in |
| What would an import do? | `ppx solution import --zip <zip> --settings <file> --dry-run` | solution, version, managed or not, target, and each reference with `set: true/false`; changes nothing |
| Import | see "Importing" | user-confirmed only |

## The round trip

1. `ppx solution ls`, then `ppx solution export --name <name> --managed` from the development environment. Only unmanaged solutions can be exported; an installed managed solution cannot.
2. `ppx solution unpack` into the repository folder and commit. Layout, what to commit and how to refresh: `references/git-layout.md`.
3. `ppx solution check --zip <name>.zip`. Fix Critical and High findings before promoting; report Medium and below to the user.
4. `ppx solution settings --zip <name>_managed.zip --out <settings>/<target>.json` once per target environment. The user fills each `ConnectionId` (make.powerapps.com > Connections, the id in the URL) and `Value`. Never invent a connection id.
5. Switch the profile to the target (`ppx env use test`), then `ppx solution import ... --dry-run`.
6. Import only as described below, then check flows (`references/import.md`).

## Importing (only when the user asks)

An import changes the target environment and ppx cannot undo it. Before any import, read `references/import.md`. Then:

- Import only when the user explicitly asked to import into a named environment, never as a side step of another task.
- Run `--dry-run` first and show the user: solution, version, managed or unmanaged, target URL, and the connection references and environment variables.
- Add `--yes` only after the user says yes in the conversation. The change notice also asks them before the command runs; that prompt is theirs to answer.
- Use the managed zip for test and production. An unmanaged import overwrites customizations in the target and cannot be undone.
- Add `--async` for large solutions (pac then waits up to 60 minutes).

## Exit codes

| Exit | Envelope says | Do |
|---|---|---|
| 2 | `data.unset` lists connection references or environment variables | `ppx solution settings --zip <zip> --out <file>`, ask the user to fill the empty values, re-run with `--settings <file>` |
| 2 | needs `--yes`, with `data.changes` | show the change to the user and ask; do not add `--yes` on your own |
| 2 | unpack is missing `<name>_managed.zip` | run the `next_actions` command (`export --managed`) |
| 1 | `pac solution ... failed: <last lines>` | read pac's text; sign-in problems go to `pp-auth-doctor`; a missing dependency means the target lacks a solution this one needs |
| 1 | `already exists` | choose another `--out`; ppx never overwrites an export |
| 1 | `not a valid Dataverse unique name` | the zip's solution.xml is malformed or tampered with; do not use it, ask the user where it came from |
| 1 | `Dataverse recorded the import as failed` | pac exited cleanly but the import job failed; show `data.import_job.error` |
| 1 | `no unique-name field` | pac's output changed; ppx was written for pac 2.14.1; tell the user, do not parse pac yourself |
| 1 | `no SARIF results` | the checker produced no report; show `data.pac_output` |
| 1 | `over 95 MB` | Dataverse imports at most 95 MB per solution file; split the solution |

After an import, `data.import_job` holds what Dataverse recorded: `progress`, `completed`, `result` (`success` or `failure`) and `error`. When pac timed out, the import keeps running on the server; re-read it with the `ppx raw dataverse GET ...` command in `next_actions`.

## Limits that matter

- Solution checker uploads the zip to Microsoft's checker service for the tenant and usually takes 1 to 5 minutes.
- An unmanaged import lands in a draft state; add `--publish-changes` or the changes stay invisible.
- `--activate-plugins` activates plug-in steps and classic workflows. Cloud flows turn on by themselves when they were on at export and every connection reference has a connection the importing user can use.
- Canvas apps stay as `.msapp` files. ppx never runs `pac canvas pack|unpack` or `--processCanvasApps`; canvas edits go through `/canvas-apps:canvas-app` with Power Apps Studio open.

## What ppx does not do

- Delete, upgrade, holding imports or `--force-overwrite`: the user does these in make.powerapps.com if ever needed.
- Power Platform pipelines and Dataverse Git integration: both need an administrator (Managed Environments, a pipelines host). Give the user the admin request in `references/import.md#pipelines`; never ask for admin roles.
- Design of what goes into a solution: that is the `pp-solution-architect` skill.
