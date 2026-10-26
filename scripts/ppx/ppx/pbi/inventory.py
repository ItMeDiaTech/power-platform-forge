"""pbi ws ls and pbi ls: what exists, from the daily inventory cache."""

from __future__ import annotations

from collections import Counter

from ppx.pbi import common

DEFAULT_TOP = 15  # keeps the default answer well under 4 KB with ids and long names
BRIEF = {"report": ("model",), "model": ("lastRefresh", "lastStatus"), "dashboard": (), "dataflow": ()}


def ws_ls(ctx, args) -> dict:
    items, _ = common.workspaces(ctx)
    if not (ctx.fields or ctx.raw):
        items = [{k: w[k] for k in ("id", "name", "onCapacity") if k in w} for w in items]
    return common.listing(ctx, items, default_top=30)


def ls(ctx, args) -> dict:
    ws = common.workspace(ctx, args.workspace)
    snap, _ = common.inventory(ctx, ws)
    every = snap["items"]
    model_names = {i["id"]: i.get("name") for i in every if i.get("type") == "model"}
    items = [i for i in every if not args.type or i.get("type") == common.TYPES[args.type]]
    # Reports name their model, so "which report uses which model" needs no second call.
    items = [{**i, "model": model_names.get(i["modelId"], i["modelId"])} if i.get("modelId") else i for i in items]
    if not (ctx.fields or ctx.raw):
        items = [{k: i[k] for k in ("id", "name", "type", *BRIEF.get(i.get("type"), ())) if k in i} for i in items]
    out = {"workspace": ws["name"], "onCapacity": common.on_capacity(ws),
           "by_type": dict(Counter(i.get("type") for i in every)), "cached_at": snap.get("fetched_at")}
    if snap.get("source") == "fabric":
        out["source"] = "fabric"
    return {**out, **common.listing(ctx, items, default_top=DEFAULT_TOP)}
