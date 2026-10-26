"""test dax: DAX query/expect pairs, each sent through pbi query's executeQueries path.

Every query goes through `ppx.pbi.query.execute`, so it shares the 120-per-minute executeQueries
budget with `pbi query` and gets the same error mapping. The suite is a JSON file:

    {"model": "Sales Model", "workspace": "Finance",
     "tests": [{"name": "row count", "dax": "EVALUATE ROW(\\"n\\", COUNTROWS('Sales'))", "expect": 1200},
               {"name": "regions", "dax": "EVALUATE VALUES('Geo'[Region]) ORDER BY 'Geo'[Region]",
                "expect": [{"Geo[Region]": "East"}, {"Geo[Region]": "West"}]},
               {"name": "total", "dax": "EVALUATE ROW(\\"t\\", [Total Sales])", "expect": 1234.5, "tolerance": 0.01}]}

A scalar `expect` needs exactly one row with one column. A list is compared row by row, in order,
with the column names executeQueries returns ("Table[Column]", "[name]" for ROW).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from ppx import auth, http
from ppx.errors import NeedsUser, PpxError, UsageError
from ppx.pbi import common, query

SHOWN_ROWS = 3
SHOWN_COLUMNS = 10
GOT_BYTES = 600  # per failing test, so a few failures still fit the 4 KB envelope
WHY_CHARS = 300
SCALAR = (str, int, float, bool, type(None))


def _load(path_text: str) -> dict:
    path = Path(path_text).expanduser()
    try:
        suite = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise UsageError(f"cannot read suite {path}: {exc}") from exc
    except ValueError as exc:
        raise UsageError(f"suite {path} is not valid JSON: {exc}") from exc
    tests = suite.get("tests") if isinstance(suite, dict) else None
    if not isinstance(tests, list) or not tests:
        raise UsageError(f'suite {path} needs a non-empty "tests" list')
    for key in ("model", "workspace"):
        if not isinstance(suite.get(key), (str, type(None))):
            raise UsageError(f'suite {path}: "{key}" must be text (a name or an id)')
    for i, t in enumerate(tests):
        if not (isinstance(t, dict) and isinstance(t.get("dax"), str) and "expect" in t):
            raise UsageError(f'test {i + 1} in {path} needs "dax" (text) and "expect"')
        if not isinstance(t.get("name"), (str, type(None))):
            raise UsageError(f'test {i + 1} in {path}: "name" must be text')
        if not isinstance(t["expect"], (list, *SCALAR)) or (isinstance(t["expect"], list)
                                                              and not all(isinstance(r, dict) for r in t["expect"])):
            raise UsageError(f'test {i + 1} in {path}: "expect" must be one value or a list of row objects')
        tolerance = t.get("tolerance", 0)
        if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool) or tolerance < 0:
            raise UsageError(f'test {i + 1} in {path}: "tolerance" must be a non-negative number')
    return suite


def same(expected, actual, tolerance: float = 0) -> bool:
    """Equal values; numbers within `tolerance`, and always within float rounding. A boolean equals only a boolean."""
    if isinstance(expected, bool) != isinstance(actual, bool):
        return False
    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (expected, actual))
    if numeric:
        return math.isclose(expected, actual, rel_tol=1e-9, abs_tol=tolerance)
    return expected == actual


def compare(expect, rows: list[dict], tolerance: float = 0) -> str | None:
    """None when the rows match `expect`, else the first difference in words."""
    if not isinstance(expect, list):
        if len(rows) != 1 or len(rows[0]) != 1:
            width = len(rows[0]) if rows else 0
            return f"expected one value, got {len(rows)} rows with {width} columns"
        (got,) = rows[0].values()
        return None if same(expect, got, tolerance) else f"expected {expect!r}, got {got!r}"
    if len(rows) != len(expect):
        return f"expected {len(expect)} rows, got {len(rows)}"
    for n, (want, got) in enumerate(zip(expect, rows), start=1):
        if set(want) != set(got):
            return f"row {n}: expected columns {sorted(want)}, got {sorted(got)}"
        for col, value in want.items():
            if not same(value, got[col], tolerance):
                return f"row {n} column {col}: expected {value!r}, got {got[col]!r}"
    return None


def _one(ctx, ws: dict, model: dict, test: dict) -> dict:
    name = test.get("name") or test["dax"][:60]
    if query.evaluate_count(test["dax"]) != 1:
        return {"name": name, "ok": False, "why": "each test needs exactly one EVALUATE (executeQueries runs one query)"}
    try:
        rows = query.execute(ctx, ws, model, test["dax"])
    except NeedsUser as exc:
        if isinstance(exc, auth.AuthError) or exc.data.get("status") in (401, 403):
            raise  # not signed in, no Build permission or the tenant setting is off: every test would fail alike
        return {"name": name, "ok": False, "why": exc.message}  # only the result-too-large error is per query
    except http.HttpError:
        raise  # execute already mapped 400; a 404 (stale model id) or a 5xx is about the service, not this DAX
    except PpxError as exc:
        if exc.data.get("bucket") or exc.data.get("status") == 429:
            raise  # the per-minute budget is spent here or at the service; the rest would only be throttled
        return {"name": name, "ok": False, "why": exc.message}
    why = compare(test["expect"], rows, float(test.get("tolerance", 0)))
    if why is None:
        return {"name": name, "ok": True}
    shown = rows[:SHOWN_ROWS]
    got = ({"got": shown} if len(json.dumps(shown, default=str)) <= GOT_BYTES
           else {"got_rows": len(rows), "got_columns": list(rows[0])[:SHOWN_COLUMNS]})  # wide rows would fill 4 KB
    return {"name": name, "ok": False, "why": why[:WHY_CHARS], **got}


def run(ctx, args) -> dict:
    suite = _load(args.suite)
    wanted = args.model or suite.get("model")
    if not wanted:
        raise UsageError('name the model with --model or a "model" key in the suite')
    ws, model = common.resolve(ctx, "model", str(wanted), args.workspace or suite.get("workspace"))
    results = [_one(ctx, ws, model, t) for t in suite["tests"]]
    failed = [r for r in results if not r["ok"]]
    out = {"model": model.get("name"), "workspace": ws["name"], "suite": str(Path(args.suite).expanduser()),
           "count": len(results), "passed": len(results) - len(failed), "failed": len(failed),
           "items": failed + [r for r in results if r["ok"]]}  # failures first, so a cut keeps them
    if failed:
        raise PpxError(f"{len(failed)} of {len(results)} DAX tests failed", data=out)
    return out
