# Attended canvas edits

Canvas apps have no headless write path a maker can use: `pac canvas pack|unpack` are deprecated and
`.pa.yaml` is read-only. Edits go through Microsoft's canvas-apps plugin and its Canvas Authoring MCP
server, which works on an app the user has open in Power Apps Studio
(learn.microsoft.com/power-apps/maker/canvas-apps/create-canvas-external-tools).

## Prerequisites (the user's machine)

- `/plugin marketplace add microsoft/power-platform-skills` then `/plugin install canvas-apps@power-platform-skills`.
- .NET 10 SDK (`dotnet --list-sdks`); the MCP server runs through `dnx`.
- The app is saved at least once, and coauthoring is on (Studio: Settings > Updates > Coauthoring).
- The user can edit the app (owner or co-owner; `ppx pa share <app> --user <upn> --role CanEdit` makes a co-owner).

## The loop

1. Read first: `ppx pa get <app>` (source and screens) and `ppx pa inspect <app>` (delegation findings).
   For a SharePoint-backed app also `ppx sp fields ls --site <s> --list <list>` for column names and types.
2. Ask the user to open the app in Studio (make.powerapps.com > Apps > the app > Edit) and keep the
   tab open for the whole session. `ppx pa get` returns that URL as a `next_actions` entry.
3. Run `/canvas-apps:canvas-app` (first time: `/configure-canvas-mcp` with the Studio edit URL the
   user copies from the browser). Make the change through its tools; it compiles and syncs into the
   open Studio session.
4. The user saves (and publishes, if it should go live) in Studio.
5. Confirm: `ppx pa get <app> --out <new dir>` and compare with the first download, or
   `ppx pa inspect <app> --refresh` to see findings gone (`--refresh` refetches the inventory, so the
   changed app is downloaded again; without it inspect reuses the source indexed before the edit).

## Rules

- Never edit `.pa.yaml` and expect it to load; never run `pac canvas pack` or `unpack`.
- If the Studio tab closes, the MCP session ends: ask the user to reopen it rather than retrying.
- New screens or controls on a SharePoint list: delegable formulas only (see
  `delegation-sharepoint.md`); prefer `Filter` with `=` and `StartsWith` on text columns.
- Creating a new app from a list has no terminal route: the user starts it in Microsoft Lists
  (Integrate > Power Apps > Create an app) or Studio, then the loop above applies.
