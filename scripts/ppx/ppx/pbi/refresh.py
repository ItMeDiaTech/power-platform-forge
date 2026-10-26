"""Refresh history, failure causes, and the one gated function every Power BI refresh goes through.

Facts (Learn, Datasets - Refresh Dataset In Group and Get Refresh History In Group, read
2026-10-26): shared capacity allows eight refreshes a day, scheduled ones included, and only
`notifyOption` in the body; history needs write permission on the model, returns the last 60
entries without `$top`, and `status` is Unknown while a refresh runs. ppx counts the day in UTC,
an assumption Learn does not state.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

from ppx import apis, http
from ppx.envelope import NextAction
from ppx.errors import NeedsConfirmation, NeedsUser, PpxError, UsageError
from ppx.pbi import common

QUOTA = 8           # shared-capacity refreshes per day
CONFIRM_AT = 7      # from the 8th refresh of the day on, ppx needs --yes
RECENT = 20         # history entries read before a refresh: the in-progress check and today's count
POLL_SECONDS = 15.0
MESSAGE_CHARS = 300
LONG_RUN = timedelta(minutes=115)  # shared capacity stops a refresh at two hours
# Longest a refresh can run (Learn, Data refresh in Power BI): two hours shared, five on a capacity.
MAX_RUN = {False: timedelta(hours=2), True: timedelta(hours=5)}
DEFAULT_TOP = 5
clock = time.monotonic

CAUSES = ("credentials", "gateway", "timeout_2h", "quota_8_per_day", "query", "unknown")
_RULES = (
    ("credentials", re.compile(r"credential|oauth|unauthori[sz]ed|forbidden|password|login failed", re.I)),
    ("gateway", re.compile(r"gateway|GWPipeline", re.I)),
    ("timeout_2h", re.compile(r"time-?out|timed out", re.I)),
    ("quota_8_per_day", re.compile(r"too many|refresh(?:es)? (?:limit|quota)|maximum (?:number of )?refreshes", re.I)),
    ("query", re.compile(r"expression\.error|dataformat\.error|datasource\.error|mashup|query|processing|"
                         r"column|table|memory|type mismatch|circular", re.I)),
)


def _exception(raw) -> tuple[str | None, str | None]:
    """(errorCode, full message) from serviceExceptionJson, a JSON string."""
    if not raw:
        return None, None
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return None, str(raw)
    if not isinstance(obj, dict):
        return None, str(raw)
    err = obj.get("error") if isinstance(obj.get("error"), dict) else {}
    code = obj.get("errorCode") or err.get("code")
    message = (obj.get("errorDescription") or obj.get("message") or err.get("message")
               or http.detail_text(err or obj))
    if not message:
        rest = {k: v for k, v in obj.items() if k != "errorCode"}
        message = json.dumps(rest, ensure_ascii=False) if rest else None
    return code, message


def _cause(code: str | None, message: str | None, start, end) -> str:
    text = f"{code or ''} {message or ''}"
    for cause, rule in _RULES[:2]:
        if rule.search(text):
            return cause
    began, ended = common.parse_time(start), common.parse_time(end)
    if began and ended and ended - began >= LONG_RUN:
        return "timeout_2h"
    for cause, rule in _RULES[2:]:
        if rule.search(text):
            return cause
    return "unknown"


def parse_refresh_entry(entry: dict) -> dict:
    """{start, end, status, type, requestId, errorCode, message (300 chars at most), cause} without empty keys.

    `cause` is one of CAUSES and is set only for a failed entry.
    """
    raw = entry.get("serviceExceptionJson") or next(
        (a.get("serviceExceptionJson") for a in reversed(entry.get("refreshAttempts") or [])
         if isinstance(a, dict) and a.get("serviceExceptionJson")), None)
    code, message = _exception(raw)
    out = {"start": entry.get("startTime"), "end": entry.get("endTime"), "status": entry.get("status"),
           "type": entry.get("refreshType"), "requestId": entry.get("requestId"),
           "errorCode": code, "message": message[:MESSAGE_CHARS] if message else None}
    if out["status"] == "Failed":
        out["cause"] = _cause(code, message, out["start"], out["end"])  # on the full text: a cut can drop the cause
    return {k: v for k, v in out.items() if v is not None}


def fix_for(entry: dict, ws: dict, model: dict) -> NextAction:
    """The one next step for a failed refresh entry (parsed)."""
    settings = common.settings_url(ws, model["id"])
    cause = entry.get("cause")
    if cause == "credentials":
        return NextAction("sign in to the data source under Data source credentials in the model's settings "
                          "(an API-set credential expires in about an hour), then refresh again",
                          command=common.command("refresh", model, ws), url=settings)
    if cause == "gateway":
        return NextAction("check the gateway connection in the model's settings: the gateway must be online "
                          "and mapped to every data source", command=common.command("refresh", model, ws), url=settings)
    if cause == "timeout_2h":
        return NextAction("shared capacity stops a refresh after two hours: import less data or use incremental "
                          "refresh, then refresh again")
    if cause == "quota_8_per_day":
        return NextAction("the eight refreshes a day on shared capacity are used up; try again after midnight UTC",
                          command=common.command("history", model, ws))
    if cause == "query":
        return NextAction("a Power Query or data source error: fix the step the message names in Power BI Desktop "
                          "(or with /powerbi-authoring:semantic-model-authoring), publish, then refresh")
    return NextAction("read the message; the model's settings page shows the full error", url=settings)


def _history(ctx, ws: dict, model: dict, top: int | None) -> list[dict]:
    try:
        body = common.get(ctx, ws, f"datasets/{model['id']}/refreshes", {"$top": top} if top else None)
    except http.HttpError as exc:
        if exc.status in (401, 403):
            raise NeedsUser(f"refresh history of {model['name']} needs write permission on the model "
                            "(its owner, or Contributor or higher in the workspace)",
                            next_actions=[NextAction("ask the model owner or a workspace admin for access")],
                            data={"status": exc.status, "error_code": exc.code}) from None
        raise
    return [e for e in body.get("value") or [] if isinstance(e, dict)]


def _running(entry: dict, ws: dict) -> bool:
    """An Unknown entry is in progress only while it could still be running; older ones are orphans."""
    if entry.get("status") != "Unknown":
        return False
    started = common.parse_time(entry.get("startTime"))
    return started is None or datetime.now(timezone.utc) - started < MAX_RUN[common.on_capacity(ws)]


def history(ctx, args) -> dict:
    ws, model = common.resolve(ctx, "model", args.model, args.workspace)
    top = ctx.top if ctx.top is not None else DEFAULT_TOP
    # --failures reads the service's default window (60 entries) so older failures are found.
    entries = _history(ctx, ws, model, None if args.failures else top)
    parsed = [parse_refresh_entry(e) for e in entries]
    failures = [p for p in parsed if p.get("status") == "Failed"]
    items = failures[:top] if args.failures else parsed
    out = {"model": model.get("name"), "workspace": ws["name"], "count": len(items), "items": items}
    if args.failures:
        out["window"] = {"entries": len(parsed), "failed": len(failures)}
    if failures:
        out["last_failure"] = failures[0]
        ctx.next_actions.append(fix_for(failures[0], ws, model))
    if entries and _running(entries[0], ws):
        ctx.warn("a refresh is in progress now")
    return out


def _today_count(entries: list[dict]) -> int:
    today = datetime.now(timezone.utc).date()
    return sum(1 for e in entries if (t := common.parse_time(e.get("startTime"))) and t.astimezone(timezone.utc).date() == today)


def _key(entry: dict) -> tuple:
    return entry.get("requestId"), entry.get("startTime")


def _ours(entries: list[dict], request_id: str | None, posted_at: datetime, before: set) -> dict | None:
    """Our refresh among `entries`; `before` holds the keys of entries listed before the POST."""
    for e in entries:
        if request_id and e.get("requestId") == request_id:
            return e
    # The history may record the refresh under another id: take the newest new entry started near the POST.
    for e in entries:
        started = common.parse_time(e.get("startTime"))
        if _key(e) not in before and started and started >= posted_at - timedelta(minutes=2):
            return e
    return None


def _wait(ctx, ws: dict, model: dict, request_id: str | None, posted_at: datetime, before: set) -> dict | None:
    """Poll the history until our refresh ends or --timeout passes (then None)."""
    deadline = clock() + ctx.timeout
    path = common.url(ws, f"datasets/{model['id']}/refreshes")
    while True:
        resp = http.request("GET", path, audience=apis.PBI_RESOURCE, params={"$top": 5}, timeout=ctx.timeout)
        entry = _ours([e for e in (resp.json() or {}).get("value") or [] if isinstance(e, dict)], request_id,
                      posted_at, before)
        if entry and entry.get("status") not in (None, "Unknown"):
            return entry
        remaining = deadline - clock()
        if remaining <= 0:
            return None
        try:
            delay = float(resp.headers.get("retry-after") or POLL_SECONDS)
        except ValueError:
            delay = POLL_SECONDS
        http.sleep(min(max(delay, 1.0), remaining))


def gated(ctx, ws: dict, model: dict, *, wait: bool = False, body: dict | None = None) -> dict:
    """Start one refresh of `model`: the only path to POST /refreshes in ppx, composite verbs included.

    Refuses (exit 2) while a refresh is in progress, and on shared capacity from the 8th refresh
    of the UTC day on unless --yes. Shared capacity gets only {notifyOption}; `body` (enhanced
    refresh options) is for capacity workspaces. With `wait`, polls until done or --timeout and
    returns the requestId so a retry polls `pbi history` instead of starting another refresh.
    """
    capacity = common.on_capacity(ws)
    if body and not capacity:
        raise UsageError(f"workspace {ws['name']} is on shared capacity, which accepts only notifyOption; "
                         "enhanced refresh options need Premium, PPU, Embedded or Fabric capacity")
    recent = _history(ctx, ws, model, RECENT)
    poll = common.command("history", model, ws)
    if recent and _running(recent[0], ws):
        raise NeedsUser(f"a refresh of {model['name']} is already in progress (started {recent[0].get('startTime')}); "
                        "poll it instead of starting another",
                        next_actions=[NextAction("poll it", command=poll)],
                        data={"requestId": recent[0].get("requestId")})
    if recent and recent[0].get("status") == "Unknown":
        ctx.warn(f"the newest history entry (started {recent[0].get('startTime')}) still reads Unknown, longer than "
                 "any refresh can run; treated as ended")
    used = _today_count(recent)
    costly = not capacity and used >= CONFIRM_AT
    ctx.changes.add("refresh the model's data now", f"model {model['name']} in workspace {ws['name']}",
                    costly=costly)
    if costly and not ctx.yes:
        raise NeedsConfirmation(
            f"{used} of {QUOTA} refreshes allowed per day on shared capacity already ran today (UTC), counting "
            f"scheduled and failed ones; this one would be number {used + 1}. Re-run with --yes to spend it",
            next_actions=[NextAction("see today's refreshes", command=poll)],
            data={"used_today": used, "limit": QUOTA})
    payload = body or {"notifyOption": "NoNotification"}
    out = {"model": model.get("name"), "workspace": ws["name"], "used_today": used,
           "limit": None if capacity else QUOTA}
    if ctx.dry_run:
        return {**out, "body": payload}
    posted_at = datetime.now(timezone.utc)
    try:
        resp = http.request("POST", common.url(ws, f"datasets/{model['id']}/refreshes"), audience=apis.PBI_RESOURCE,
                            op="write", json_body=payload, timeout=ctx.timeout)
    except http.HttpError as exc:
        if exc.status in (401, 403):
            raise NeedsUser(f"refreshing {model['name']} needs write permission on the model (its owner, or "
                            "Contributor or higher in the workspace)",
                            data={"status": exc.status, "error_code": exc.code}) from None
        raise
    location = resp.headers.get("location") or ""
    request_id = resp.headers.get("x-ms-request-id") or resp.headers.get("requestid") or (
        location.rstrip("/").rsplit("/", 1)[-1] if "/refreshes/" in location else None)
    out.update(requestId=request_id, status="Unknown", used_today=used + 1)
    if not wait:
        ctx.next("check on it", command=poll)
        return out
    entry = _wait(ctx, ws, model, request_id, posted_at, {_key(e) for e in recent})
    if entry is None:
        ctx.warn(f"still running after {ctx.timeout:g} s; poll with pbi history and do not start another refresh")
        ctx.next("poll it (do not refresh again)", command=poll)
        return out
    parsed = parse_refresh_entry(entry)
    out.update(status=parsed.get("status"), result=parsed)
    if parsed.get("status") == "Failed":
        error = NeedsUser if parsed.get("cause") in ("credentials", "gateway") else PpxError
        raise error(f"refresh of {model['name']} failed ({parsed.get('cause')}): "
                    f"{parsed.get('errorCode') or ''} {parsed.get('message') or ''}".strip(),
                    next_actions=[fix_for(parsed, ws, model)], data=out)
    return out


def run(ctx, args) -> dict:
    ws, model = common.resolve(ctx, "model", args.model, args.workspace)
    return gated(ctx, ws, model, wait=args.wait)
