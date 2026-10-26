"""pbi params set: change a semantic model's Power Query parameters, then refresh through gated().

Facts (Learn, Datasets - Update Parameters In Group, read 2026-10-26): POST .../Default.UpdateParameters
{updateDetails: [{name, newValue}]}; the caller must own the model; at most 100 parameters per request;
names are case-sensitive; types Any and Binary cannot be updated; the model needs a refresh afterwards.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from ppx import apis, http
from ppx.changes import ChangeSet
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, UsageError
from ppx.pbi import common, refresh

MAX_PARAMS = 100
_SHELL = re.compile(r'["$`\\!\r\n]')  # would expand or break inside a double-quoted shell argument


def quoted(text: str) -> str | None:
    """`text` in double quotes when no shell can expand it, else None."""
    return None if _SHELL.search(text) else f'"{text}"'


def owner_error(exc: http.HttpError, ws: dict, model: dict, what: str) -> NeedsUser:
    takeover = common.url(ws, f"datasets/{model['id']}/Default.TakeOver")
    return NeedsUser(f"only the owner of model {model.get('name')} can {what} ({exc.status}); taking it over makes "
                     "you the owner (the previous owner can take it back)",
                     next_actions=[NextAction("take it over", command=f"ppx raw pbi POST {takeover}")],
                     data={"status": exc.status, "error_code": exc.code})


def read(ctx, ws: dict, model: dict, path: str, what: str) -> list[dict]:
    try:
        return [v for v in common.get(ctx, ws, f"datasets/{model['id']}/{path}").get("value") or []
                if isinstance(v, dict)]
    except http.HttpError as exc:
        if exc.status in (401, 403):
            raise NeedsUser(f"reading the {what} of {model.get('name')} needs write permission on the model (its "
                            "owner, or Contributor or higher in the workspace)",
                            data={"status": exc.status, "error_code": exc.code}) from None
        raise


def preflight_refresh(ctx, ws: dict, model: dict, skip: bool) -> None:
    """Refuse before changing anything when the trailing refresh would be refused (in progress, 8 a day)."""
    if not skip and not ctx.dry_run:
        refresh.gated(dataclasses.replace(ctx, dry_run=True, changes=ChangeSet(), warnings=[], next_actions=[]),
                      ws, model)


def refresh_after(ctx, ws: dict, model: dict, skip: bool) -> dict | None:
    if skip:
        ctx.next("refresh the model to load data with the change", command=common.command("refresh", model, ws))
        return None
    return refresh.gated(ctx, ws, model)


def _assignments(value: str) -> dict[str, str]:
    if value.startswith("@"):
        try:
            found = json.loads(Path(value[1:]).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise UsageError(f"cannot read --set file {value[1:]}: {exc}") from exc
        if not isinstance(found, dict) or not found:
            raise UsageError("a --set file holds one JSON object of parameter names and values")
        return {str(k): v if isinstance(v, str) else json.dumps(v) for k, v in found.items()}
    name, eq, new = value.partition("=")
    if not eq or not name.strip():
        raise UsageError("--set takes Name=Value, or @file.json holding {\"Name\": \"Value\", ...}")
    return {name.strip(): new}


def set_(ctx, args) -> dict:
    wanted = _assignments(args.set)
    if len(wanted) > MAX_PARAMS:
        raise UsageError(f"at most {MAX_PARAMS} parameters per call")
    ws, model = common.resolve(ctx, "model", args.model, args.workspace)
    by_name = {p.get("name"): p for p in read(ctx, ws, model, "parameters", "parameters")}
    missing = [n for n in wanted if n not in by_name]
    if missing:
        raise UsageError(f"model {model.get('name')} has no parameter {', '.join(missing)} (names are case-sensitive)",
                         data={"parameters": sorted(str(n) for n in by_name)[:30]})
    fixed = [n for n in wanted if by_name[n].get("type") in ("Any", "Binary")]
    if fixed:
        raise UsageError(f"parameters of type Any or Binary cannot be changed through the API: {', '.join(fixed)}")
    target = f"model {model.get('name')} in workspace {ws['name']}"
    base = f"ppx pbi params set --model {common.shell_arg(model)} --workspace {common.shell_arg(ws)} --set "
    for name, new in wanted.items():
        old = by_name[name].get("currentValue")
        back = quoted(f"{name}={old if old is not None else ''}")
        ctx.changes.add(f"set parameter {name} from {old!r} to {new!r}", target,
                        undo=base + back if back else f"set parameter {name} back to {old!r}")
    preflight_refresh(ctx, ws, model, args.no_refresh)
    out = {"model": model.get("name"), "workspace": ws["name"], "parameters": list(wanted)}
    if ctx.dry_run:
        return {**out, "refresh": refresh_after(ctx, ws, model, args.no_refresh)}
    try:
        http.request("POST", common.url(ws, f"datasets/{model['id']}/Default.UpdateParameters"),
                     audience=apis.PBI_RESOURCE, op="write", timeout=ctx.timeout,
                     json_body={"updateDetails": [{"name": n, "newValue": v} for n, v in wanted.items()]})
    except http.HttpError as exc:
        if exc.status in (401, 403):
            raise owner_error(exc, ws, model, "change its parameters") from None
        raise PpxError(f"updating the parameters failed: {exc.message}", data=exc.data) from None
    return {**out, "refresh": refresh_after(ctx, ws, model, args.no_refresh)}
