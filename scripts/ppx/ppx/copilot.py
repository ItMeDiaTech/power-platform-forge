"""copilot publish-check: was a Copilot Studio agent published after a given time?

Reads the agent's row in the Dataverse `bots` table (Learn, Agent (bot) table reference, ms.date
2026-09-18: primary key botid, name, schemaname, publishedon "Date and time when the agent was last
published", statuscode). `pac copilot publish` can exit 1 although the publish went through, or exit 0
in some locales without publishing (powerplatform-build-tools issues #1307 and #1282), so publishedon
is the check to trust.
"""

from __future__ import annotations

from datetime import timezone

from ppx.data.dataverse import CONSENT, Dataverse
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, UsageError
from ppx.pbi.common import parse_time
from ppx.resolve import NotFound, pick

STUDIO = "https://copilotstudio.microsoft.com"
STATUS = {1: "Provisioned", 2: "Deprovisioned", 3: "Provisioning", 4: "ProvisionFailed", 5: "MissingLicense"}
_SELECT = ("botid", "name", "schemaname", "publishedon", "statuscode")


def _iso(value) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def publish_check(ctx, args) -> dict:
    since = parse_time(args.since)
    if since is None:
        raise UsageError(f"--since {args.since!r} is not an ISO 8601 time such as 2026-10-26T09:30:00Z")
    dv = Dataverse(ctx)
    bots = [{"id": b.get("botid"), "name": b.get("name"), "schemaname": b.get("schemaname"),
             "publishedon": b.get("publishedon"), "statuscode": b.get("statuscode")}
            for b in dv.rows("bots", "bot", _SELECT)]
    try:
        bot = pick("agent", args.bot, bots, context_keys=("schemaname",))
    except NotFound as exc:
        try:
            bot = pick("agent", args.bot, bots, name_key="schemaname")
        except NotFound:
            raise exc from None
    status = bot.get("statuscode")
    out = {"bot": bot["name"], "schema_name": bot["schemaname"], "id": bot["id"],
           "status": STATUS.get(int(status), str(status)) if str(status).isdigit() else status,
           "published_on": bot["publishedon"], "since": _iso(since), "token_source": dv.source}
    if not bot["publishedon"]:
        raise PpxError(f"agent {bot['name']} has never been published",
                       next_actions=[NextAction("publish it in Copilot Studio", url=STUDIO)],
                       data={**out, "published_since": False})
    published = parse_time(bot["publishedon"])
    if published is None and dv.source == "pac":
        raise NeedsUser(f"pac env fetch returned publishedon as locale text ({bot['publishedon']!r}), which ppx "
                        "cannot compare reliably; sign in with a Dataverse token and check again",
                        next_actions=CONSENT, data=out)
    if published is None:
        raise PpxError(f"could not read publishedon {bot['publishedon']!r} as a time", data=out)
    out["published_on"] = _iso(published)
    if published < since:
        raise PpxError(f"agent {bot['name']} was last published at {out['published_on']}, before {out['since']}",
                       next_actions=[NextAction("publish it in Copilot Studio, then run this check again",
                                                url=STUDIO)],
                       data={**out, "published_since": False})
    return {**out, "published_since": True}
