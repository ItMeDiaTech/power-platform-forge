# Making a Power BI report reachable from an app

Pick the first option that fits; every option needs each viewer to hold a Power BI licence and access to the report (workspace role or app audience).

| Option | Where | Who can set it up | Prerequisites | ppx part | Source |
|---|---|---|---|---|---|
| **Power BI embedded system dashboard** (default for model-driven apps) | model-driven app, Dashboards area | System Customizer or System Administrator in the environment | none beyond the licences; it ignores the "Power BI visualization embedding" environment setting | `ppx pbi report show <r>` gives the workspace and report; after adding the page, `ppx mda sitemap <app>` confirms it | learn.microsoft.com/power-apps/maker/model-driven-apps/create-edit-powerbi-embedded-page (2026-08-06) |
| **Sitemap link to the report URL** (fallback, always works) | model-driven app navigation | app maker | none | `ppx pbi report show <r>` for the URL; `ppx mda sitemap <app>` to check | sitemap URL subareas, app designer |
| **Power BI tile control** | canvas app | app maker | a dashboard tile to show (pins come from the report) | `ppx pbi dashboards ls` to find the tile | canvas control reference |
| **Power BI tiles on a personal dashboard** | model-driven app, per user | each user, after an admin turns the setting on | environment setting **Power BI visualization embedding** (off by default; Power Platform admin center > Settings > Product > Features) | none | learn.microsoft.com/power-platform/admin/use-power-bi (2025-11-25) |

## Embedded system dashboard steps (browser; the user does them)

1. make.powerapps.com > Solutions > open the solution > New > Dashboard > Power BI embedded.
2. Display name; type Power BI report; select the workspace and report. Choose **Use environment variable** when the solution will move to other environments: the workspace and report then come from a Power BI environment variable that each target sets (put its value in the deployment settings file).
3. Save (saves and publishes the dashboard).
4. App designer > Add page > Dashboard > Power BI dashboards > select it > Save and Publish.

No ppx verb creates this dashboard; give the user these steps and https://make.powerapps.com. Whether `/model-apps:app-builder` can add the page is not verified; ask it, and fall back to the browser steps.

## Not options

- **Publish to web**: public to anyone with the link and admin-governed; never for internal data.
- **Code apps**: no Power BI integration function; link to the report URL instead.
- **Embed for customers (embed tokens)**: needs a service principal and capacity; out of scope for a maker.
