# AADSTS codes and what ppx does with them

`ppx.auth` reads the first `AADSTS<n>` in a token tool's error output. A failed source never stops the search: ppx tries every remaining source for that API (az, then Tier B, then pac for the Power Platform API, or the order in `routing.json`) and only then reports the most useful failure. Classified codes exit 2 and name the fix; unknown codes exit 1.

Meanings from Microsoft Entra's error reference (<https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes>) and, for 530036, the Conditional Access authentication-flows page (<https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-authentication-flows>), read 2026-10-26.

| Code | Meaning | What ppx does | What the user does |
|---|---|---|---|
| 65001 | DelegationDoesNotExist: nobody consented to this app for this API | exit 2. For az: fix is `ppx auth app-spec` (use your own app). For Tier B: fix is `ppx auth login --backend m365` to see the consent prompt | Tier B: sign in again and accept consent. If the prompt says an admin must approve, file the `docs/ADMIN-REQUESTS.md#consent` request |
| 65002 | A Microsoft first-party client (az) is not pre-authorised for this API | tries the next source silently; if nothing else works, exit 2 with `ppx auth app-spec` | register the Tier B app (never an admin request) |
| 53000 | DeviceNotCompliant: Conditional Access requires a compliant device | exit 2, API marked `blocked` | use the company-managed work laptop; if it is that laptop, `docs/ADMIN-REQUESTS.md#device-compliance` |
| 53001 | DeviceNotDomainJoined: Conditional Access requires a domain-joined device | same as 53000 | same as 53000 |
| 53003 | BlockedByConditionalAccess: a policy does not allow token issuance | exit 2, API marked `blocked`; tries the other sources, since a policy may target one client only. If another source is merely signed out, its login line (or `ppx auth app-spec` for Tier B without an app) follows the IT entry in `next_actions` | try the other source's sign-in first; otherwise ask IT which policy blocks the client (`docs/ADMIN-REQUESTS.md#conditional-access`) |
| 50076 | UserStrongAuthClientAuthNRequired: MFA needed for this resource | exit 2 with `ppx auth login --backend <x>` | run the login line in their own terminal and complete MFA |
| 50079 | UserStrongAuthEnrollmentRequired: MFA registration needed | exit 2 with the login line | register at <https://mysignins.microsoft.com/security-info>, then run the login line |
| 530036 | Refresh token invalid under an authentication-flows (device code) policy | exit 2 with the login line | `ppx auth logout --backend <x>`, then sign in again with the browser, never a device code |
| 700082 | Refresh token expired after inactivity | exit 2 with the login line | run the login line |
| 70043 | Refresh token expired under the sign-in frequency policy | exit 2 with the login line | run the login line |
| not signed in | az "Please run 'az login'", m365 "Log in to Microsoft 365 first", pac "no profiles" | tries the next source; if nothing works, exit 2 with the login line (Tier B with no saved app: `ppx auth app-spec`) | run the line in their own terminal |
| any other code | not classified | exit 1 with the scrubbed verbatim message and `https://login.microsoftonline.com/error?code=<n>` | read the lookup page; report the code so it can be added here |

Failures without an AADSTS code:

| Case | What ppx does |
|---|---|
| token tool not installed | exit 2 (`SetupNeeded`) with the install command of every missing tool; doctor status `missing` |
| tool did not answer within `--timeout` | exit 1; doctor status `timeout`, never a hang |
| `routing.json` records no source for this API and operation | exit 2, code `no-source`, fix `ppx auth doctor` |
| other non-zero exit | exit 1 with the scrubbed message |

When several sources fail, ppx reports in this order of usefulness: a classified code, an unknown code, not signed in, another error, a timeout, a missing tool, 65002.

Error text is scrubbed before it reaches any output: tokens are masked, e-mail addresses become `<user>`, `*.onmicrosoft.com` tenant domains become `<tenant>`, GUIDs become `<id>`, and trace and correlation ids are cut.

Any classified code (not 65002) also gets the login line of every other source that was only signed out, so a block on one client never hides the self-service route.
