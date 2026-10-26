"""pbi repoint: move a semantic model from one SharePoint list to another with the same columns.

The column sets of both lists are compared first (display names and types, which the 2.0 connector
reads); any missing or retyped column stops the repoint. Then, in this order:
  (a) a Power Query parameter holds the list (and, across sites, another holds the site): Update
      Parameters (owner only, so ppx takes the model over first);
  (b) branch A: pull the TMDL and rewrite only the navigation step that selects the list
      (`{[Id="..."]}` or `{[Title="..."]}`, plus the SharePoint.Tables site literal across sites) and
      push it back with a backup; the partition is never re-rendered from the template;
  (c) branches F and D, across sites, when both lists share a title: Update Datasources through
      `pbi datasources update` (T3.3's preconditions); otherwise exit 2 naming the Desktop route.
Same-site paths end with one gated refresh. Across sites ppx does not refresh: the new site is a new data source
without credentials, so the refresh would fail and spend one of the day's 8; the envelope returns the settings
URL and `pbi datasources bind --transient` instead. Facts: Datasets - Update Parameters In Group and Take Over In
Group, Items - Get/Update Semantic Model Definition (Learn, read 2026-10-26).
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import difflib
import re

from ppx import apis, http
from ppx.changes import ChangeSet
from ppx.errors import NeedsUser, PpxError, UsageError
from ppx.pbi import backup, common, datasources, definition, params
from ppx.sp import fields as fields_mod
from ppx.sp import lists, sites

DIFF_LINES = 40
_GUID = re.compile(r"\{?[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\}?", re.I)


def schema_diff(a: list[dict], b: list[dict]) -> dict[str, list]:
    """Columns of list A missing from B, present in both with another type, and new in B (by display name)."""
    def cols(rows):
        return {(f.get("Title") or f.get("InternalName") or "").lower(): f for f in rows if fields_mod.shown(f)}

    ca, cb = cols(a), cols(b)
    return {"missing": sorted(ca[k].get("Title") or k for k in set(ca) - set(cb)),
            "retyped": sorted(f"{ca[k].get('Title') or k}: {ca[k].get('TypeAsString')} -> {cb[k].get('TypeAsString')}"
                              for k in set(ca) & set(cb) if ca[k].get("TypeAsString") != cb[k].get("TypeAsString")),
            "added": sorted(cb[k].get("Title") or k for k in set(cb) - set(ca))}


def rewrite(text: str, old: dict, new: dict, from_site: str, to_site: str) -> tuple[str, int]:
    """Point the list navigation (and, across sites, the SharePoint.Tables site) of one TMDL file from list
    `old` to `new` ({Id, Title}); returns (text, navigation steps rewritten)."""
    if "SharePoint.Tables" not in text:
        return text, 0
    old_id, new_id = old["Id"].strip("{}"), new["Id"].strip("{}").lower()
    by_id = re.compile(r'(\{\[\s*Id\s*=\s*")\{?' + re.escape(old_id) + r'\}?("\s*\]\})', re.I)
    by_title = re.compile(r'(\{\[\s*Title\s*=\s*")' + re.escape(old["Title"].replace('"', '""')) + r'("\s*\]\})')
    text, hits = by_id.subn(lambda m: m.group(1) + new_id + m.group(2), text)
    text, more = by_title.subn(lambda m: m.group(1) + new["Title"].replace('"', '""') + m.group(2), text)
    if hits + more and from_site.lower() != to_site.lower():
        site = re.compile(r'(SharePoint\.Tables\(\s*")' + re.escape(from_site.rstrip("/")) + r'/?(")', re.I)
        text, moved = site.subn(lambda m: m.group(1) + to_site.rstrip("/") + m.group(2), text)
        if not moved:
            return text, 0  # the site comes from somewhere else (a parameter): only half could be rewritten
    return text, hits + more


def _site_of(ctx, args, sources: list[dict]) -> str:
    if args.site:
        return sites.resolve(ctx, args.site)
    found = sorted({str((s.get("connectionDetails") or {}).get("url") or "").rstrip("/") for s in sources
                    if "sharepoint" in str(s.get("datasourceType") or "").lower()} - {""})
    if len(found) != 1:
        raise UsageError(f"the model has {len(found)} SharePoint data sources; pass --site <the site of "
                         f"{args.from_list}>", data={"sources": found[:5]})
    return found[0]


def _take_over(ctx, ws: dict, model: dict, target: str) -> None:
    if ws["id"] == common.ME:
        return  # Take Over exists only in a group; in My workspace the user owns every model
    # Not reversible from here (the same rule as ppx probe): only the previous owner can take it back, and
    # the model's refresh credentials stay with them, so a shared model can stop refreshing.
    ctx.changes.add("take over the model: you become its owner", target, reversible=False,
                    undo=f"its previous owner ({model.get('configuredBy') or 'unknown'}) can take it over again")


def _do_take_over(ctx, ws: dict, model: dict) -> None:
    if ws["id"] == common.ME:
        return
    try:
        http.request("POST", common.url(ws, f"datasets/{model['id']}/Default.TakeOver"), audience=apis.PBI_RESOURCE,
                     op="write", body=b"", timeout=ctx.timeout)
    except http.HttpError as exc:
        if exc.status in (401, 403):
            raise NeedsUser(f"the service would not make you the owner of {model.get('name')} ({exc.status}); "
                            "taking over needs write permission on the model", data=exc.data) from None
        raise


def _by_parameters(ctx, args, ws, model, updates: dict[str, tuple], target: str, cross: bool) -> dict:
    base = f"ppx pbi params set --model {common.shell_arg(model)} --workspace {common.shell_arg(ws)} --set "
    _take_over(ctx, ws, model, target)
    for name, (old, new) in updates.items():
        back = params.quoted(f"{name}={old}")
        ctx.changes.add(f"set parameter {name} from {old!r} to {new!r}", target,
                        undo=base + back if back else f"set parameter {name} back to {old!r}")
    params.preflight_refresh(ctx, ws, model, args.no_refresh or cross)
    out = {"route": "parameters", "parameters": {n: v[1] for n, v in updates.items()}}
    if ctx.dry_run:
        return out
    ctx.changes.require_yes(ctx.yes)
    _do_take_over(ctx, ws, model)
    try:
        http.request("POST", common.url(ws, f"datasets/{model['id']}/Default.UpdateParameters"),
                     audience=apis.PBI_RESOURCE, op="write", timeout=ctx.timeout,
                     json_body={"updateDetails": [{"name": n, "newValue": v[1]} for n, v in updates.items()]})
    except http.HttpError as exc:
        if exc.status in (401, 403):
            raise params.owner_error(exc, ws, model, "change its parameters") from None
        raise PpxError(f"updating the parameters failed: {exc.message}", data=exc.data) from None
    if cross:
        _credentials_next(ctx, ws, model)
        return {**out, "refresh": None}
    return {**out, "refresh": params.refresh_after(ctx, ws, model, args.no_refresh)}


def _by_definition(ctx, args, ws, model, old: dict, new: dict, from_site: str, to_site: str, target: str) -> dict:
    wid = definition.fabric_workspace_id(ctx, ws)
    try:
        current = definition.get_definition(ctx, wid, "model", model["id"])
    except PpxError as exc:
        raise definition.refused(exc, "read", "model", model, ws) from None
    parts, diff, hits = [], [], 0
    for part in current["parts"]:
        if part["path"].startswith("definition/") and part["path"].endswith(".tmdl"):
            before = base64.b64decode(part.get("payload") or "").decode("utf-8-sig")
            after, n = rewrite(before, old, new, from_site, to_site)
            if n:
                hits += n
                diff += list(difflib.unified_diff(before.splitlines(), after.splitlines(), part["path"], part["path"],
                                                  n=0, lineterm=""))
                part = {**part, "payload": base64.b64encode(after.encode("utf-8")).decode("ascii")}
        parts.append(part)
    if not hits:
        raise NeedsUser(f"no SharePoint.Tables query in model {model.get('name')} selects list {old['Title']} by "
                        f"{{[Id=\"{old['Id'].strip('{}')}\"]}} or {{[Title=\"{old['Title']}\"]}}"
                        + (f" on site {from_site}" if from_site.lower() != to_site.lower() else "")
                        + "; ppx rewrites only that navigation step and never re-renders a partition. Change the query "
                          "in Power BI Desktop (or with /powerbi-authoring:semantic-model-authoring)",
                        next_actions=definition.desktop_actions("model", model, ws)[:1])
    cross = from_site.lower() != to_site.lower()
    stamp, folder = backup.reserve(ctx.need_data_dir(), model["id"])
    undo = f"ppx pbi push model {common.shell_arg(model)} --workspace {common.shell_arg(ws)} --restore {stamp}"
    ctx.changes.add(f"point {hits} SharePoint navigation step(s) from list {old['Title']} to {new['Title']}"
                    + (f" and the site to {to_site}" if cross else ""),
                    target, backup=str(folder), undo=undo)
    params.preflight_refresh(ctx, ws, model, args.no_refresh or cross)
    out = {"route": "definition", "steps": hits, "diff": diff[:DIFF_LINES]}
    if ctx.dry_run:
        return out
    backup.save_definition(folder, kind="model", item=model, workspace=ws, definition=current)
    try:
        definition.update_definition(ctx, wid, "model", model["id"],
                                     {"parts": parts, **({"format": current["format"]} if current.get("format") else {})})
    except PpxError as exc:
        raise definition.refused(exc, "write", "model", model, ws) from None
    ctx.next("undo the repoint", command=undo)
    if cross:
        _credentials_next(ctx, ws, model)
        return {**out, "backup": stamp, "refresh": None}
    return {**out, "backup": stamp, "refresh": params.refresh_after(ctx, ws, model, args.no_refresh)}


def _credentials_next(ctx, ws: dict, model: dict) -> None:
    ctx.warn("the new site is a new data source without credentials, so ppx did not refresh (it would fail and "
             "spend one of the day's refreshes)")
    ctx.next("set the new site's credentials once (settings page), or bind your sign-in for about an hour and "
             "refresh", command=common.command("datasources bind", model, ws, " --transient"),
             url=common.settings_url(ws, model["id"]))


def run(ctx, args) -> dict:
    ws, model = common.resolve(ctx, "model", args.model, args.workspace)
    sources = params.read(ctx, ws, model, "datasources", "data sources")
    from_site = _site_of(ctx, args, sources)
    to_site = sites.resolve(ctx, args.to_site) if args.to_site else from_site
    cross = from_site.lower() != to_site.lower()
    old = lists.fetch(ctx, from_site, args.from_list, "Title,Id")
    new = lists.fetch(ctx, to_site, args.to_list, "Title,Id")
    if not (old.get("Id") and new.get("Id")):
        raise PpxError("m365 returned no list id for one of the lists")
    diff = schema_diff(fields_mod.load(ctx, from_site, args.from_list), fields_mod.load(ctx, to_site, args.to_list))
    if diff["missing"] or diff["retyped"]:
        raise NeedsUser(f"list {new['Title']} does not have the columns of {old['Title']} "
                        f"({len(diff['missing'])} missing, {len(diff['retyped'])} with another type); the model's "
                        "refresh would fail. Add or fix them first (ppx sp fields add)",
                        data={"missing": diff["missing"][:20], "retyped": diff["retyped"][:20]})
    if diff["added"]:
        ctx.warn(f"{new['Title']} has {len(diff['added'])} column(s) {old['Title']} lacks; the model ignores them: "
                 + ", ".join(diff["added"][:5]))
    target = f"model {model.get('name')} in workspace {ws['name']}"
    out = {"model": model.get("name"), "workspace": ws["name"], "from": {"site": from_site, "list": old["Title"]},
           "to": {"site": to_site, "list": new["Title"]}}
    found = params.read(ctx, ws, model, "parameters", "parameters")

    def holding(*values: str):
        wanted = {v.strip("{}").lower().rstrip("/") for v in values}
        return next((p for p in found if str(p.get("currentValue") or "").strip("{}").lower().rstrip("/") in wanted),
                    None)

    list_param, site_param = holding(old["Title"], old["Id"]), holding(from_site) if cross else None
    if list_param and (site_param or not cross):
        by_id = bool(_GUID.fullmatch(str(list_param["currentValue"]).strip()))
        updates = {list_param["name"]: (list_param["currentValue"], new["Id"].strip("{}") if by_id else new["Title"])}
        if site_param:
            updates[site_param["name"]] = (site_param["currentValue"], to_site)
        return {**out, **_by_parameters(ctx, args, ws, model, updates, target, bool(site_param))}
    branch = definition.branch(ctx)
    if branch == "A":
        return {**out, **_by_definition(ctx, args, ws, model, old, new, from_site, to_site, target)}
    if cross and old["Title"] == new["Title"]:
        ctx.warn("Update Datasources changes the site only; this works when the query selects the list by title")
        # Always across sites, so no refresh: the new site has no credentials yet (see _credentials_next).
        ns = argparse.Namespace(model=model["id"], workspace=ws["id"], url=to_site, server=None, database=None,
                                path=None, no_refresh=True)
        _take_over(ctx, ws, model, target)
        if not ctx.dry_run:
            ctx.changes.require_yes(ctx.yes)
            # Every precondition of Update Datasources runs before ownership moves to the user.
            datasources.update(dataclasses.replace(ctx, dry_run=True, changes=ChangeSet(), warnings=[],
                                                   next_actions=[]), ns)
            _do_take_over(ctx, ws, model)
        moved = datasources.update(ctx, ns)
        bare = common.command("refresh", model, ws)  # refresh_after's hint; a refresh before credentials would fail
        ctx.next_actions[:] = [a for a in ctx.next_actions if a.command != bare]
        _credentials_next(ctx, ws, model)
        return {**out, "route": "datasources", "backup": moved.get("backup"), "refresh": None}
    raise NeedsUser(f"on this tenant model definitions cannot be written (routing.json: branch {branch}) and no "
                    "parameter holds the list, so ppx cannot repoint it; change the list in Power BI Desktop and publish",
                    next_actions=definition.desktop_actions("model", model, ws)[:1])
