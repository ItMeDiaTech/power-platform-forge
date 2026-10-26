# Model-driven apps through the Dataverse Web API

Sources: "Create, manage, and publish model-driven apps using code"
(learn.microsoft.com/power-apps/developer/model-driven-apps/create-manage-model-driven-apps-using-code),
the appmodulecomponent table reference, and the publish request schema
(learn.microsoft.com/power-apps/developer/model-driven-apps/publish-request-schema).

## What ppx reads

| Verb | Web API |
|---|---|
| `mda ls` | `GET appmodules?$select=appmoduleid,appmoduleidunique,name,uniquename,clienttype,modifiedon` |
| `mda show` | `GET appmodulecomponents?$filter=_appmoduleidunique_value eq <id>` (componenttype, objectid) |
| `mda forms` | `GET systemforms?$filter=Microsoft.Dynamics.CRM.In(PropertyName='formid',...)`; `--form`: `formxml` |
| `mda views` | `GET savedqueries?...`; `--view`: `fetchxml` and `layoutxml` |
| `mda sitemap` | `GET sitemaps(<id>)?$select=sitemapname,sitemapxml` |

Component types: 1 tables, 26 views, 29 business process flows, 48 ribbons, 59 charts, 60 forms,
62 sitemap. A table with no forms or views listed shows all of its forms or views in the app.

## What `mda publish` writes

1. Files in `--from` named `sitemap-<id>.xml`, `form-<id>.xml`, `view-<id>.fetchxml.xml`,
   `view-<id>.layoutxml.xml` (as the read verbs save them) are checked to be well-formed XML before
   anything is sent.
2. Each is compared with the live value; unchanged files are skipped.
3. Every value about to be replaced is saved to `<data>/backups/mda/<app id>/<UTC time>/` under the
   same file name. `ppx mda publish <app> --from <that folder>` puts it back (the change's `undo`).
4. `PATCH sitemaps(<id>)` `{sitemapxml}`, `PATCH systemforms(<id>)` `{formxml}`,
   `PATCH savedqueries(<id>)` `{fetchxml|layoutxml}`, each with `If-Match: *` so a wrong id fails
   instead of creating a row.
5. `--add view:<id>,form:<id>`: one `POST AddAppComponents` with `savedquery`/`systemform` entries.
6. One `POST PublishXml` with `<importexportxml>`: `<entities>` for the tables whose forms or views
   changed, `<sitemaps>` when the sitemap changed, and `<appmodules>` for the app.

Learn documents form edits through solution export and import or the form designer; a direct
`formxml` update is supported by the table but not shown as the recommended path. Keep form edits
small (labels, field order, visibility) and check the form in the app after publishing.

## Roles and limits

- Editing and publishing need System Customizer or System Administrator in the environment
  (Environment Maker is not enough). `ppx pa env who` lists directly assigned roles; roles through a
  team are not shown. A 403 returns `data.admin_request` for the environment admin.
- Security-role access to an app (`appmoduleroles_association`) needs System Administrator: not a
  ppx verb; the user asks the admin.
- Service protection per user: 6,000 requests, 1,200 seconds of execution, 52 concurrent requests
  in a 5-minute window.

## Without a Dataverse token

Lists (`mda ls`, `show`, `forms`, `views`) run as FetchXML through `pac env fetch` (text columns,
header-checked). XML bodies and every write need a token: they exit 2 with the consent steps.

## Building new

New model-driven apps, generative pages and sitemaps from scratch: Microsoft's `/model-apps:app-builder`
(and `/model-apps:genpage`), which need `pac` and `az` signed in as the same user. Use `ppx mda`
afterwards to inspect and adjust.
