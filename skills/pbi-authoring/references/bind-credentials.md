# Binding data source credentials

Learn, "Gateways - Update Datasource", Limitations (page updated 2026-05-20, read 2026-10-26,
https://learn.microsoft.com/en-us/rest/api/power-bi/gateways/update-datasource):

> "When setting OAuth credentials, a refresh token isn't incorporated as it is when setting
> credentials through the UI in Power BI service. Therefore, credentials can only be used for one
> hour."

So the durable fix is always the model's settings page, and the API bind is a one-hour test.

| Call | What happens |
|---|---|
| `ppx pbi datasources bind --model M --workspace W` | No API call. Exit 2 with the settings page URL (`https://app.powerbi.com/groups/<ws>/settings/datasets/<id>`): Data source credentials, Edit credentials, sign in. Then `ppx pbi refresh` to check. |
| `... --transient` | Takes over the model (you become its owner), sets each data source to an OAuth2 token of your own sign-in through the API, then one refresh through the 8-a-day gate. Always warns that the credentials expire in about an hour. |
| `... --transient` after a scheduled refresh succeeded in the last 7 days | Exit 2: the model already has working credentials that the bind would replace. `--yes` only if those are broken. |

`--transient` handles SharePoint Online and Azure SQL cloud data sources (it can mint tokens for
those audiences); others exit 2 with the settings page. On-premises sources through a gateway need
the gateway admin and are never bound by ppx.

Taking over changes the owner: the previous owner (shown in the change list) can take it back.
Update Datasources and Update Parameters also require the caller to own the model.

A refresh that succeeds right after a `--transient` bind and fails about an hour later with a
credential error is this limitation, not a new fault: send the user to the settings page.
