# Requests for IT

power-platform-forge never needs an administrator role, a service principal or a client secret.
A few things can only be changed by a tenant administrator. When ppx hits one, it exits 2 and
names one of the entries below. Each entry says what is blocked, how ppx shows it, and the text
to send IT. Replace the parts in angle brackets; never paste tokens or ppx output that has not
been through `ppx`'s redactor.

## App registration

**Blocked:** SharePoint lists and files, Graph, and any API `az` cannot get a token for
(AADSTS65002). These need your own Entra app registration (Tier B, `ppx auth app-spec`).

**How ppx shows it:** `m365 entra app add` fails with "You don't have permission to register
applications".

**Ask IT:**

> I need a single-tenant public-client app registration for command-line access to SharePoint
> and Power BI as myself (delegated permissions only, no secret, no certificate). The exact
> specification is the output of `ppx auth app-spec`. Either register it and make me an owner,
> or assign me the Application Developer role so I can register it myself.

The Entra setting behind this is *Users can register applications* (Identity > Users > User
settings).

## Consent

**Blocked:** the first sign-in with your own app, or a token for an API whose permission nobody
has consented to.

**How ppx shows it:** AADSTS65001 (consent missing) or AADSTS90094 (admin approval required)
from `ppx auth doctor` or `ppx auth login --backend m365`.

**Ask IT:**

> Please grant admin consent for the delegated permissions on my app registration
> `<app name>` (client id `<client id>`): Microsoft Graph User.Read, Sites.ReadWrite.All and
> Files.ReadWrite.All, and SharePoint AllSites.Write. They act only as me, on what I can already
> reach.

The Entra setting behind this is *User consent for applications* (Identity > Applications >
Enterprise applications > Consent and permissions). If user consent is allowed, accepting the
prompt during `ppx auth login --backend m365` is enough and no request is needed.

## Conditional Access

**Blocked:** one sign-in tool (az, pac or m365) for one API, while the others may still work.

**How ppx shows it:** AADSTS53003 in `ppx auth doctor`; the API is marked `blocked` and ppx tries
the other sources first.

**Ask IT:**

> A Conditional Access policy blocks `<az | pac | m365>` from getting a token for
> `<Power BI | Dataverse | SharePoint | Graph>` (AADSTS53003, correlation id `<from the sign-in
> error>`). Which policy is it, and can my account use that client from my company-managed
> laptop?

## Device compliance

**Blocked:** every sign-in from a computer the tenant does not manage.

**How ppx shows it:** AADSTS53000 or AADSTS53001.

Run ppx on the company-managed work laptop. If that laptop is the one failing:

> My company-managed laptop `<device name>` reports as not compliant when I sign in
> (AADSTS53000). Can you check its Intune compliance state?

## Power BI tenant settings

These are switched on by a Fabric administrator (Fabric admin portal > Tenant settings) and are
often limited to a security group, so the request is usually "add me to the group".

| Feature | Setting | Shown as |
|---|---|---|
| DAX queries through the REST API (`ppx pbi query`) | *Semantic Model Execute Queries REST API* (Integration settings), plus Build permission on the model | 401/403 naming the setting and Build permission |
| Exporting a report page as an image | *Export reports as image files* (off by default) and a workspace on Premium, Embedded or Fabric capacity | 403, or "capacity required" |
| Creating a workspace | *Create workspaces* (Workspace settings) | 403 |
| Fabric items other than reports and models | *Users can create Fabric items* | 403 |

**Ask IT:**

> Please add me to the security group for the Power BI tenant setting `<setting name>`. I use
> it from the REST API as myself, for workspaces where I am already a member or contributor.

Editing a semantic model over XMLA additionally needs the capacity's XMLA endpoint set to
Read Write, which a capacity administrator controls.

## Power Platform

Environment-level items need an environment or tenant administrator:

- an **application user** (a service principal inside an environment). ppx never needs one;
- a **Managed Environment** or a **developer environment** when the tenant limits who can
  create them;
- reading **data loss prevention (DLP) policies** and their connector groups.

**Ask IT:**

> I build Power Apps and flows in `<environment name>`. Could you tell me which connectors the
> DLP policy for that environment puts in the Business and Non-business groups, so I can
> design within it?
