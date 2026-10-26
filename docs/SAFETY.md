# Safety

This plugin lets an agent change things on your Microsoft tenant: Power BI models and reports, Power Apps, flows, SharePoint lists, Dataverse solutions. Its safety design is simple: **you are told what every Power Platform command changes, and you are asked before anything that cannot be undone.** It does not sandbox the agent.

## What you see

A Claude Code hook (`hooks/hooks.json`, script `scripts/hooks/change_notice.py`) looks at every Bash and PowerShell command and every call to a Power Platform MCP tool before it runs, and sorts it into one of six kinds:

| Kind | Examples | What happens (default `notify` mode) |
|---|---|---|
| read | `ppx env ls`, `pac solution list`, `az account show`, `Get-AdminPowerApp`, `curl` to any non-Power-Platform host, every non-Power-Platform command | Nothing. No notice, no prompt from this hook. |
| local | `ppx env add`, `pac auth select`, `az account set`, `Export-PowerApp` | A notice line; your Claude Code permission rules then decide as usual. |
| change | `ppx raw pbi POST ...`, `pac solution publish`, `m365 spo listitem add`, `Set-AdminPowerAppOwner`, a Power BI Modeling MCP edit | A notice line; your Claude Code permission rules then decide as usual. |
| irreversible | see the list below | You are asked first, with the reason `Irreversible: <what it does>`. |
| interactive | `ppx auth login`, `az login`, `pac auth create`, `m365 login`, `fab auth login`, `Add-PowerAppsAccount`, `Connect-MgGraph`, `Connect-PnPOnline -Interactive` | You are asked first: `This opens a sign-in. Run it in your own terminal instead: <command>`. Sign-ins belong in your own terminal, never the agent's. |
| unknown | a `pac`/`az`/`m365`/`fab` subcommand or Power Platform cmdlet verb the classifier does not know, an unknown tool on a Power Platform MCP server, a Power Platform command line that cannot be parsed | You are asked first: `Unrecognised command; it may change something: <command>`. |

A notice line looks like this in the transcript:

```text
PreToolUse:Bash says: ppx notice: Publish solution with pac (solution publish)
```

Notices name the target when the command shows it: the workspace, model, site, list, environment, solution file or URL. Tokens and `sig=` values are masked in every notice. A compound command (`cd x && pac solution import ...`) takes the most severe kind of its parts and the notice lists up to three of the parts that change something.

The hook never approves a call. A notice adds a line to the transcript and nothing else, so Claude Code's permission mode and your allow, ask and deny rules still decide whether the command runs, including any unrelated parts of the same command line. The hook can only add a prompt (for irreversible, interactive and unknown commands, and for every change in confirm mode) or block the call when it fails.

## Notify or confirm

```bash
ppx env set --notice-mode confirm   # ask before every change, not only irreversible ones
ppx env set --notice-mode notify    # the default: tell me, ask only for irreversible or unknown
```

The mode lives in `envs.json` in the ppx data directory. A missing `envs.json` means notify.

## What counts as irreversible

You are asked before any of these, in either mode:

- `ppx` verbs whose effect is `irreversible` (each one also refuses to run without `--yes`), and `ppx raw <api> DELETE ...`. `ppx verbs` lists every verb with its effect. Anything run with `--dry-run` is a read.
- `pac solution import`, `solution delete`, `solution upgrade`, `pipeline deploy`, `package deploy` (it imports the package's solutions), `admin delete`, `admin reset`, `admin restore`, `admin copy`, and any `pac ... delete`, `reset`, `restore` or `clear` (except `pac auth clear`, which only removes local sign-in profiles).
- `az ... delete`, `az rest --method delete`, `m365 request --method delete`, `fab api -X delete`.
- `m365 ... remove` without `--recycle` (with `--recycle` the item goes to the recycle bin, so it is a change).
- `fab rm`.
- `curl`, `wget`, `Invoke-RestMethod` or `Invoke-WebRequest` with `DELETE` to a Power Platform host (`*.powerbi.com`, `api.fabric.microsoft.com`, `*.dynamics.com`, `api.powerplatform.com`, `*.sharepoint.com`, `graph.microsoft.com`, `*.flow.microsoft.com`, `*.logic.azure.com`, `*.powerapps.com`, `api.bap.microsoft.com`).
- PowerShell cmdlets from the Power Apps, Graph, PnP and Power BI modules with the verbs `Remove`, `Restore`, `Clear` or `Reset` (for example `Restore-PowerAppVersion`, `Remove-AdminFlow`), and `Invoke-MgGraphRequest` or `Invoke-PowerBIRestMethod` with `-Method Delete`.
- Power BI Modeling MCP `ExportToTmdlFolder` and `ExportToBimFile`, which have been reported to delete existing definition files in the target folder.
- FlowAgent `delete_flow`, `delete_connection` and `cancel_all_runs`, and any FlowAgent change made with `forceManaged: true`.

## Backups and undo

Every mutating `ppx` verb returns `data.changes` in its JSON envelope: one entry per change with `what`, `target`, `reversible`, and where it applies `backup` (the path of the copy taken before an overwrite) and `undo` (the command that reverses it). `--dry-run` returns the same list without changing anything. Irreversible or costly verbs refuse to run without `--yes` and exit 2 with the change list, so the agent has to show you what it is about to do.

Other tools keep their own safety nets: FlowAgent writes backups under `.flowagent/backups` in its working directory, and `pac solution export` before an import gives you a copy to go back to.

## Audit log

Every `ppx` call that changed something, or tried to, is appended to `audit.jsonl` in the ppx data directory (`~/.claude/plugins/data/power-platform-forge-power-platform-forge/` for a marketplace install; the file `~/.claude/power-platform-forge/datadir` names the directory in use). Each line holds the time, the verb, its arguments (tokens masked), the profile, the change list and the outcome. Commands run without `ppx` (`pac`, `az`, `m365`, `fab`, cmdlets, MCP tools) appear only in the notices, not in this log.

## When the hook itself fails, the call is blocked

The hook fails closed. Each handler in `hooks/hooks.json` sets `"onFailure": "block"`, and the script exits 2 on any error of its own, so a hook that cannot start, crashes, times out (10 seconds) or cannot read a corrupt `envs.json` blocks the call instead of letting it through unseen. The reason appears in the transcript.

The hook runs through `uv` with Python 3.12 or later and no network (`uv run --no-project --offline --python >=3.12`). **If `uv` or a Python 3.12 is missing, every Bash and PowerShell command and every Power Platform MCP call is blocked,** including the commands you would use to fix it, so fix it from your own terminal:

```bash
claude plugin disable power-platform-forge@power-platform-forge
uv python install 3.12          # or install uv first: https://docs.astral.sh/uv/getting-started/installation/
claude plugin enable power-platform-forge@power-platform-forge
```

Then run `/reload-plugins` in any open session.

## Threat model

The notices make the agent's actions on your tenant **visible**; they do not make the agent safe. This is not a sandbox and not a permission system:

- The hook reads the command line, not what a program does. A script file (`python script.py`, `pwsh -File x.ps1`), inline code (`python -c`), a heredoc fed to a non-shell program, a downloaded tool, an alias or a renamed binary can change the tenant without the classifier seeing it. Encoded PowerShell (`-EncodedCommand`) is asked about as unknown.
- Only Power Platform commands are judged. `curl` to any other host, `git`, file edits and every other command are treated as reads by this hook and go through Claude Code's normal permission rules.
- MCP servers are matched by name. A Power Platform server registered under a name the hook does not know is not covered.
- The agent runs with your sign-in and your rights. Anything you can do in the tenant, it can try. Use confirm mode, Claude Code permission rules and your own review for anything that matters.

## Windows: deny rules and the PowerShell tool

On Windows with Git Bash installed, a `Bash(...)` deny rule in your Claude Code settings also disables the PowerShell tool. If you want to deny specific commands, write scoped `PowerShell(...)` rules next to the Bash ones instead of relying on a Bash rule alone.

## GitHub Copilot CLI

The repository root holds a `hooks.json` in Copilot CLI format with no hooks. Copilot reads its own root `hooks.json`, and the exec-form hooks in `hooks/hooks.json` are Claude Code's format, which would fail under Copilot. Change notices therefore run in Claude Code only for now; under Copilot nothing is classified or announced.
