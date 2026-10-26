"""pbi diff: what changed in a workspace between today's inventory and one at least N days old.

Power BI REST keeps no change log a non-admin can read, so ppx compares its own dated snapshots:
added, removed and renamed items, and items whose latest refresh status, owner (configuredBy) or
page count changed. Page counts are known for reports someone ran `pbi report show` on; each
snapshot carries the last count seen, so a page change shows once report show has seen it.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from ppx import cache
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, UsageError
from ppx.pbi import common

TRACKED = ("lastStatus", "configuredBy", "pages")
SHOWN = 15  # entries per list by default; the counts always cover everything


def _days(since: str) -> int:
    m = re.fullmatch(r"\s*(\d+)\s*d?\s*", since or "")
    if not m or int(m.group(1)) < 1:
        raise UsageError(f"--since takes a number of days such as 7d, not {since!r}")
    return int(m.group(1))


def _brief(item: dict) -> dict:
    return {k: item[k] for k in ("id", "name", "type") if k in item}


def compare(old: list[dict], new: list[dict]) -> dict:
    before, after = {i["id"]: i for i in old}, {i["id"]: i for i in new}
    both = [k for k in after if k in before]
    return {
        "added": [_brief(after[k]) for k in after if k not in before],
        "removed": [_brief(before[k]) for k in before if k not in after],
        "renamed": [{"id": k, "type": after[k].get("type"), "from": before[k].get("name"), "to": after[k].get("name")}
                    for k in both if before[k].get("name") != after[k].get("name")],
        "changed": [{"id": k, "name": after[k].get("name"), "field": f, "from": before[k][f], "to": after[k][f]}
                    for k in both for f in TRACKED
                    if f in before[k] and f in after[k] and before[k][f] != after[k][f]],
    }


def run(ctx, args) -> dict:
    days = _days(args.since)
    ws = common.workspace(ctx, args.workspace)
    data_dir = ctx.need_data_dir()
    today = datetime.now(timezone.utc).date()
    newest = cache.at_least_days_old(data_dir, common.AREA, ws["id"], 0)
    if ctx.refresh or newest is None or newest[0] != today or not isinstance(newest[1], dict):
        current, _ = common.inventory(ctx, ws, fresh=True)
    else:
        current = newest[1]
    old = cache.at_least_days_old(data_dir, common.AREA, ws["id"], days)
    if old is None or not isinstance(old[1], dict):
        raise NeedsUser(f"no snapshot of {ws['name']} older than {days} days; run `ppx pbi ls --refresh` daily "
                        "so there is one to compare with",
                        next_actions=[NextAction("take today's snapshot (and repeat daily)",
                                                 command=f"ppx pbi ls --workspace {common.shell_arg(ws)} --refresh")])
    changes = compare(old[1].get("items", []), current.get("items", []))
    out = {"workspace": ws["name"], "from": old[0].isoformat(), "to": today.isoformat(),
           "counts": {k: len(v) for k, v in changes.items()}}
    for key, entries in changes.items():
        out[key] = entries if ctx.raw else entries[:SHOWN]
    if not ctx.raw and any(len(v) > SHOWN for v in changes.values()):
        ctx.next(f"lists show {SHOWN} entries each; pass --raw for all of them")
    return out
