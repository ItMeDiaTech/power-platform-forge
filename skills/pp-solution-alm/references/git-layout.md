# Git layout for unpacked solutions

`ppx solution unpack` runs `pac solution unpack --packagetype Both`, which reads `<name>.zip` and `<name>_managed.zip` from the same folder and writes one folder that can rebuild either package (SolutionPackager, learn.microsoft.com/power-platform/alm/solution-packager-tool).

## Repository shape

```
<repo>/
  solutions/
    <SolutionUniqueName>/          # ppx solution unpack --folder points here
      Other/
        Solution.xml               # name, version, publisher, root components
        Customizations.xml
        Relationships/ ...
      Entities/ ...                # tables, forms, views
      Workflows/ ...               # cloud flows (JSON) and classic workflows
      CanvasApps/                  # <app>_DocumentUri.msapp plus metadata XML, kept binary
      AppModules/ ...  AppModuleSiteMaps/ ...
      environmentvariabledefinitions/ ...
  settings/                        # deployment settings, one file per target environment
    test.json
    prod.json
  README.md                        # which environment is dev, test, prod; who imports
```

The folder names inside a solution come from SolutionPackager and depend on which component types the solution holds; commit whatever unpack writes.

## Rules

- **Commit the unpacked folder, not the zips.** Zips are build output: `ppx solution pack` rebuilds both from the folder.
- **Canvas apps are never unpacked to source.** They stay as the `.msapp` the solution carries. Do not run `pac canvas unpack` or `--processCanvasApps` (both deprecated); canvas source (`*.pa.yaml`) from `ppx pa get` is read-only and for diffs only. Canvas edits go through `/canvas-apps:canvas-app` with Power Apps Studio open, then a fresh export.
- **Refresh by replacing, not merging.** To capture a new version: export again, delete the contents of `solutions/<name>/` (Git keeps the history), unpack, then review `git diff`. Unpacking over the old folder leaves files of removed components behind, and a later pack would carry them back.
- **One solution per folder.** A repository may hold several, each under its own `solutions/<name>/`.
- **Version before you export.** Ask the user to bump the solution version in the development environment before each export, so the target can tell an update from a re-import.
- **Settings files hold tenant identifiers.** `ConnectionId` values and environment-variable values (site URLs, list names) identify the tenant. Commit them only to a private repository; in a public one keep `settings/*.json` out of Git (`.gitignore`) and store them with the team. `templates/deployment-settings/example.json` in this plugin shows the shape with placeholders.

## Git-integration (YAML) folders

Folders committed by Dataverse Git integration use a different layout (`solutions/<name>/solution.yml`, `publishers/`, `canvasapps/`, `modernflows/`). `ppx solution pack` detects it and packs it as Unmanaged (pac's default, which Microsoft's YAML example uses) unless `--packagetype` says otherwise. Setting up Git integration needs Managed Environments and a System Administrator, so it is an admin request, not a ppx task.

## Review checklist for a diff

1. `Other/Solution.xml`: version changed as intended; no unexpected root components or missing dependencies.
2. Connection references and environment variables added or removed: re-run `ppx solution settings` for every target file.
3. Security roles and table permissions: call them out to the user; they change who sees data.
4. `ppx solution check` on the rebuilt zip before any import.
