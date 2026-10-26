"""data query|fx: Dataverse rows through the Web API, and Power Fx files through `pac power-fx run`.

`data query` builds $select/$filter/$top and pages; without a Dataverse token it runs the same
read as Power Fx (FirstN, ShowColumns) through pac, which cannot take an OData filter. `data fx`
reads the file first: Remove, RemoveIf, Clear, ClearCollect, Patch, Update and UpdateIf delete or
overwrite rows and need --yes; Collect adds rows.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from ppx import auth, exports, http, pac
from ppx.data.dataverse import Dataverse, odata
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, UsageError

DEFAULT_TOP = 50
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OVERWRITE = re.compile(r"\b(?:Remove|RemoveIf|Clear|ClearCollect|Patch|Update|UpdateIf|Relate|Unrelate)\s*\(")
_ADD = re.compile(r"\bCollect\s*\(")
_PAC_ERROR = re.compile(r"^Error \d+-\d+:")


def _env(ctx) -> list[str]:
    return ["--environment", ctx.profile.url] if ctx.profile and ctx.profile.url else []


def _output(stdout: str) -> list[str]:
    """pac's lines without its "Connected as <user>" banner."""
    return [line.rstrip() for line in stdout.splitlines() if line.strip() and not line.lstrip().startswith("Connected ")]


def _power_fx(ctx, formula: str) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="ppx-fx-", ignore_cleanup_errors=True) as tmp:
        path = Path(tmp) / "query.fx"
        path.write_text(formula + "\n", encoding="utf-8")
        return _output(pac.run(["power-fx", "run", "--file", str(path), *_env(ctx)], timeout=ctx.timeout * 3).stdout)


def query(ctx, args) -> dict:
    if not _IDENT.match(args.table):
        raise UsageError(f"--table takes an entity set name such as accounts, not {args.table!r}")
    fields = ctx.fields or []
    if any(not _IDENT.match(f) for f in fields):
        raise UsageError("--fields takes column logical names separated by commas")
    if not fields:
        ctx.warn("no --fields given, so every column is returned; name the columns you need")
    top = ctx.top or DEFAULT_TOP
    dv = Dataverse(ctx)
    try:
        items, more = http.paged(dv.api.url(args.table), audience=dv.api.resource, top=top, timeout=ctx.timeout,
                                 params=odata(fields, top=top, raw_filter=args.filter), token_provider=dv._token)
    except auth.AuthError as exc:
        return _query_fx(ctx, args, fields, top, exc)
    items = [{k: v for k, v in i.items() if not k.startswith("@odata")} for i in items]
    return {"table": args.table, "count": len(items), "more": more, **_rows(ctx, args.table, items),
            "token_source": dv.source}


def _rows(ctx, table: str, items: list[dict]) -> dict:
    """Rows inline when they fit the envelope; otherwise saved to a file, with its path and a digest inline."""
    out = exports.inline_or_file(ctx, f"data-{table}", items)
    if "body" in out:
        return {"items": out["body"]}
    ctx.warn("the rows are too large to return inline and were saved to data.saved_to; name fewer --fields or "
             "a smaller --top to get them inline")
    return out


def _query_fx(ctx, args, fields: list[str], top: int, exc: auth.AuthError) -> dict:
    if args.filter:
        raise NeedsUser(f"no Dataverse token ({exc.data.get('code')}), and an OData filter cannot run through pac",
                        next_actions=exc.next_actions + [NextAction(
                            "or write the query as Power Fx, e.g. FirstN(Filter(<table>, <condition>), 50), "
                            "in a file and run it", command="ppx data fx --file <file>")])
    ctx.warn(f"no Dataverse token ({exc.data.get('code')}); read through pac power-fx run, which names tables by "
             "their display collection name (Contacts, not contacts)")
    formula = f"FirstN({args.table}, {top})"
    if fields:
        formula = f"ShowColumns({formula}, {', '.join(fields)})"
    lines = _power_fx(ctx, formula)
    errors = [line for line in lines if _PAC_ERROR.match(line)]
    if errors:
        raise PpxError(f"pac power-fx run: {errors[0]}", data={"errors": errors[:5]})
    if fields:
        items = pac.parse_table("\n".join(lines), fields, command="power-fx run")
        return {"table": args.table, "count": len(items), **_rows(ctx, args.table, items), "token_source": "pac"}
    return {"table": args.table, "token_source": "pac", **exports.inline_or_file(ctx, "data-query", lines)}


def fx(ctx, args) -> dict:
    path = Path(args.file)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc}") from exc
    target = f"Dataverse environment {ctx.profile.url if ctx.profile and ctx.profile.url else '(pac default)'}"
    if _OVERWRITE.search(text):
        ctx.changes.add("run Power Fx that deletes or overwrites rows (Remove, Clear, Patch, Update)", target,
                        reversible=False)
    elif _ADD.search(text):
        ctx.changes.add("run Power Fx that adds rows (Collect)", target, undo="remove the added rows")
    ctx.changes.require_yes(ctx.yes)
    effect = "irreversible" if _OVERWRITE.search(text) else "change" if _ADD.search(text) else "read"
    if ctx.dry_run:
        return {"file": str(path), "effect": effect}
    lines = _output(pac.run(["power-fx", "run", "--file", str(path), *_env(ctx)], timeout=ctx.timeout * 3).stdout)
    errors = [line for line in lines if _PAC_ERROR.match(line)]
    if errors:
        raise PpxError(f"Power Fx errors in {path.name}: {errors[0]}", data={"errors": errors[:10]})
    return {"file": str(path), "effect": effect, **exports.inline_or_file(ctx, "power-fx", lines)}
