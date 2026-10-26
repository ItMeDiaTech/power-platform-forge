"""pbi query: one DAX query through executeQueries, rows back as CSV.

Facts (Learn, Datasets - Execute Queries In Group, read 2026-10-26): one query and one table per
call; at most 100,000 rows or 1,000,000 values and 15 MB per query; 120 requests per minute per
user across all models; needs Build permission and the tenant setting "Dataset Execute Queries
REST API" (now "Semantic Model Execute Queries REST API"). An oversized result comes back as 200
with an error object and partial rows; a DAX error is a 400.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from ppx import apis, exports, http, ratelimit
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, UsageError
from ppx.pbi import common

BUCKET = "executeQueries"
PER_MINUTE = 120
DEFAULT_TOP = 50
HEAD_LINES = 10
DETAIL_CHARS = 300
_TOKENS = re.compile(r'"(?:[^"]|"")*"|\'(?:[^\']|\'\')*\'|\[[^\]]*\]|--[^\n]*|//[^\n]*|/\*.*?\*/', re.S)
_SIMPLE = re.compile(r"^EVALUATE\s+(.+?)(?:\s+ORDER\s+BY\s+(.+))?$", re.I | re.S)
_DIRECTION = re.compile(r"^(.+?)\s+(ASC|DESC)$", re.I | re.S)


def _blank(dax: str, keep_names: bool) -> str:
    """Comments removed; unless keep_names, string literals and quoted names are blanked too."""
    def sub(m: re.Match) -> str:
        tok = m.group()
        if tok.startswith(("--", "//", "/*")):
            return " "
        return tok if keep_names else " "
    return _TOKENS.sub(sub, dax)


def evaluate_count(dax: str) -> int:
    return len(re.findall(r"\bEVALUATE\b", _blank(dax, keep_names=False), re.I))


def _split_top_level(text: str) -> list[str]:
    """Split on commas outside parentheses, brackets and quotes."""
    parts, depth, start = [], 0, 0
    masked = _TOKENS.sub(lambda m: "x" * len(m.group()), text)
    for i, ch in enumerate(masked):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def wrap_top(dax: str, n: int) -> str | None:
    """`EVALUATE TOPN(n, <table>, <order keys>) [ORDER BY ...]` for a plain query, else None.

    Only `EVALUATE <table> [ORDER BY ...]` without DEFINE or START AT is wrapped; TOPN gets the
    ORDER BY keys so the n rows are the first n in that order, not an arbitrary n.
    """
    text = _blank(dax, keep_names=True).strip()
    if re.search(r"\bDEFINE\b|\bSTART\s+AT\b", _blank(dax, keep_names=False), re.I):
        return None
    # Match on a copy with strings and names masked, so an "ORDER BY" inside a string cannot split the query.
    m = _SIMPLE.match(_TOKENS.sub(lambda t: "x" * len(t.group()), text))
    if not m or evaluate_count(text) != 1:
        return None
    table = text[m.start(1):m.end(1)].strip()
    order = text[m.start(2):m.end(2)] if m.group(2) else None
    args = [str(n), table]
    for key in _split_top_level(order) if order else []:
        d = _DIRECTION.match(key)
        args += [d.group(1).strip(), d.group(2).upper()] if d else [key, "ASC"]  # TOPN's own default is DESC
    return f"EVALUATE TOPN({', '.join(args)})" + (f" ORDER BY {order.strip()}" if order else "")


def _read_dax(value: str) -> str:
    if not value.startswith("@"):
        return value
    path = Path(value[1:])
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise UsageError(f"cannot read --dax file {path}: {exc}") from exc


def _denied(exc: http.HttpError, model: dict) -> NeedsUser:
    return NeedsUser(
        f"executeQueries on {model.get('name')} was refused ({exc.status}{' ' + exc.code if exc.code else ''}): "
        "either you lack Build permission on the model, or the tenant setting "
        "'Semantic Model Execute Queries REST API' is off",
        next_actions=[NextAction("ask the model owner for Build permission (Contributor or higher in the workspace "
                                 "also gives it)"),
                      NextAction("if Build permission is in place, ask IT to enable the tenant setting: "
                                 "docs/ADMIN-REQUESTS.md#power-bi-tenant-settings")],
        data={"status": exc.status, "error_code": exc.code})


def _result_error(body: dict) -> dict | None:
    result = (body.get("results") or [{}])[0] if isinstance(body.get("results"), list) else {}
    tables = result.get("tables") or [{}]
    for err in (body.get("error"), result.get("error"), tables[0].get("error") if isinstance(tables[0], dict) else None):
        if isinstance(err, dict) and (err.get("code") or err.get("message") or err.get("details")):
            return err
    return None


def _error_text(err: dict) -> str:
    message = err.get("message")
    if not message:  # Learn: the message may sit in a details object instead, under pbi.error or not
        message = http.detail_text(err) or (json.dumps(err["details"], ensure_ascii=False) if err.get("details")
                                               else "")
        message = message[:DETAIL_CHARS]
    return f"{err.get('code') or ''} {message}".strip()


NEAR_SIZE_LIMIT = 14_500_000  # bytes; executeQueries stops adding rows at 15 MB


def execute(ctx, ws: dict, model: dict, dax: str) -> list[dict]:
    """Run one DAX query and return its rows; every executeQueries call in ppx goes through here."""
    ratelimit.acquire(ctx.need_data_dir(), BUCKET, PER_MINUTE, timeout=ctx.timeout)
    body = {"queries": [{"query": dax}], "serializerSettings": {"includeNulls": True}}
    try:
        raw = http.request("POST", common.url(ws, f"datasets/{model['id']}/executeQueries"),
                           audience=apis.PBI_RESOURCE, json_body=body, timeout=ctx.timeout)
        resp = raw.json() or {}
        # Learn: past 15 MB "the current row will be completed but no additional rows will be written", with no error.
        if len(raw.body) >= NEAR_SIZE_LIMIT:
            ctx.warn(f"the result is {len(raw.body) // 1_000_000} MB, at the 15 MB limit, so rows may be missing "
                     "without an error; aggregate or add TOPN and run it again")
    except http.HttpError as exc:
        if exc.status in (401, 403):
            raise _denied(exc, model) from None
        if exc.status == 429:
            raise PpxError(f"{PER_MINUTE} queries per minute per user reached; retry in a minute",
                           data={"status": 429}) from None
        if exc.status == 400:
            raise PpxError(f"DAX query failed: {exc.message}", data=exc.data) from None
        raise
    err = _result_error(resp)
    if err:
        text = _error_text(err)
        if re.search(r"more than one result table", text, re.I):
            raise PpxError(f"one table per query: {text}")
        # Learn's wording: "More than {allowed number} rows in a query result"; values likewise. Past 15 MB the
        # service sends no error (see execute); the 15 MB pattern stays in case it ever reports one.
        if re.search(r"more than [\d,.]+ (?:rows|values)|\b15 ?MB\b", text, re.I):
            raise NeedsUser(f"the result is over the executeQueries limit (100,000 rows or 1,000,000 values): {text}",
                            next_actions=[NextAction("aggregate (SUMMARIZECOLUMNS, GROUPBY) or add TOPN/ORDER BY "
                                                     "and run it again")])
        raise PpxError(f"DAX query failed: {text}")
    tables = ((resp.get("results") or [{}])[0].get("tables") or [{}])
    return [r for r in tables[0].get("rows") or [] if isinstance(r, dict)]


def build_check(ctx, ws: dict, model: dict) -> dict:
    """{build: True|False|None, why?}: whether this user can query the model (Build and the tenant setting)."""
    try:
        execute(ctx, ws, model, 'EVALUATE ROW("ok", 1)')
    except NeedsUser as exc:
        return {"build": False, "why": exc.message}
    except PpxError as exc:
        return {"build": None, "why": exc.message}
    return {"build": True}


def _csv(rows: list[dict]) -> tuple[list[str], str]:
    columns = list(dict.fromkeys(k for r in rows for k in r))
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows([["" if r.get(c) is None else r.get(c) for c in columns] for r in rows])
    return columns, buf.getvalue()


def run(ctx, args) -> dict:
    dax = _read_dax(args.dax)
    if not dax.strip():
        raise UsageError("--dax is empty")
    if evaluate_count(dax) > 1:
        raise PpxError("one query per call: this DAX has more than one EVALUATE; run them one at a time")
    top = ctx.top if ctx.top is not None else (None if ctx.raw else DEFAULT_TOP)
    wrapped = wrap_top(dax, top) if top else None
    ws, model = common.resolve(ctx, "model", args.model, args.workspace)
    rows = execute(ctx, ws, model, wrapped or dax)
    total = len(rows)
    if top is not None and total > top:
        rows = rows[:top]
        ordered = re.search(r"\bORDER\s+BY\b", _blank(dax, keep_names=False), re.I)
        ctx.warn(f"truncated to the first {top} of {total} rows" if ordered else
                 f"truncated, rows unordered: kept {top} of {total}; add ORDER BY or TOPN to choose which rows")
    columns, text = _csv(rows)
    out = {"model": model.get("name"), "workspace": ws["name"], "rows": len(rows), "columns": columns,
           "topn_wrapped": wrapped is not None}
    if ctx.raw or len(text.encode()) <= exports.INLINE_LIMIT:
        return {**out, "csv": text}
    path = exports.save(ctx.need_data_dir(), f"query-{model.get('name') or model['id']}", text, ".csv")
    return {**out, "saved_to": str(path), "bytes": len(text.encode()), "head": text.splitlines()[:HEAD_LINES]}
