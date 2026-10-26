# Reading canvas app source (.pa.yaml)

Sources: "Source code files for canvas apps (pa.yaml)" (learn.microsoft.com/power-apps/maker/canvas-apps/power-apps-yaml)
and the v3.0 schema (raw.githubusercontent.com/microsoft/PowerApps-Tooling/refs/heads/master/schemas/pa-yaml/v3.0/pa.schema.yaml).

## Where it comes from

`ppx pa get <app> [--out <dir>]` runs `pac canvas download --name <app id> --extract-to-directory`
and leaves `app.msapp` plus the extracted tree. Only `Src/*.pa.yaml` is source:

- `Src/App.pa.yaml`: the App object (OnStart, StartScreen, other app properties).
- `Src/<Screen>.pa.yaml`: one file per screen.
- `Src/Component/<Component>.pa.yaml`: one file per component.

The JSON files elsewhere in the .msapp are not stable between saves; do not reason from them.
The files are **read-only**: Studio ignores edits to them, and only Power Platform Git integration
(Managed Environments and System Administrator, out of reach for a maker) round-trips them.

## Shape

Top-level keys are limited to `App`, `Screens`, `ComponentDefinitions`, `DataSources` and `EditorState`.

```yaml
Screens:
  HomeScreen:
    Properties:
      OnVisible: =Set(varUser, User())
    Children:
      - TaskGallery:
          Control: Gallery@2.15.0
          Variant: Vertical
          Properties:
            Items: =Filter(Tasks, Status.Value = "Open")
```

- `Children` is a list of one-key maps: the key is the control name.
- `Control: <type>@<version>` and `Variant` name the control; they are not formulas.
- Every value under `Properties` is a Power Fx formula starting with `=`; long ones use a `|-` block.
- `DataSources` maps a data source name to `Type` (`Table` or `Actions`), `Parameters`
  (`TableLogicalName` for tables) and, when present, `ConnectorId` (for SharePoint, `shared_sharepointonline`).

## What ppx extracts

`ppx pa index` and `ppx pa inspect` keep, per app: screens (keys under `Screens`), SharePoint data
sources (`DataSources` entries that mention SharePoint, plus the connection references' `dataSources`),
SharePoint site URLs that appear anywhere in the source, and delegation findings. Where the site URL
and list identity of a SharePoint data source appear in the source is still a probe fact
(`ppx probe --area pa --site <url> --list <title> --app <name>` reports the keys); until then a list
is matched by name and `site_checked` says whether a site was confirmed.

## The data row limit

The setting is not part of `.pa.yaml`. `pa inspect` reports it only if the downloaded .msapp's app
settings carry it, else `data_row_limit.value: null` with the default (500, up to 10,000).

## Reading tips

- Find where a list is used: search the source folder for the data source name followed by `,` or `)`.
- A finding's `line` is the line of the `=` formula (or of the call inside a `|-` block).
- To compare two versions, `pa get` each into its own folder and diff the `Src` trees.
