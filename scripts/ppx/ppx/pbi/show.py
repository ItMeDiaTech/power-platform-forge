"""pbi report show and pbi model show: one item in detail, degrading per facet when access is partial.

Datasources and refresh history need write permission on the model (Learn, Get Datasources In
Group and Get Refresh History In Group); a viewer still gets everything else.
"""

from __future__ import annotations

from ppx import http
from ppx.envelope import NextAction
from ppx.errors import PpxError
from ppx.pbi import common, query, refresh

PAGE_NAMES = 40
SHOWN_REFRESHES = 3
SHOWN_DETAILS = 10  # datasources and parameters each; the full count is given when more exist


def _why(exc: http.HttpError) -> str:
    if exc.status in (401, 403):
        return "needs write permission on the model (its owner, or Contributor or higher in the workspace)"
    return exc.message


def report(ctx, args) -> dict:
    ws, item = common.resolve(ctx, "report", args.report, args.workspace)
    out = {k: item[k] for k in ("id", "name", "format", "reportType", "isOwnedByMe") if k in item}
    out["workspace"] = ws["name"]
    if item.get("modelId"):
        model = next((i for i in common.cached_items(ctx, ws) if i.get("id") == item["modelId"]), None)
        out["model"] = {"id": item["modelId"], **({"name": model.get("name")} if model else {})}
    try:
        pages = common.get(ctx, ws, f"reports/{item['id']}/pages").get("value") or []
    except http.HttpError as exc:  # paginated (RDL) reports have no pages
        ctx.warn(f"pages not read: {exc.message}")
        return out
    pages.sort(key=lambda p: int(p.get("order") or 0) if str(p.get("order") or 0).isdigit() else 0)
    out["pages"] = len(pages)
    out["page_names"] = [p.get("displayName") or p.get("name") for p in pages][:PAGE_NAMES]
    common.remember(ctx, ws, item["id"], pages=len(pages))
    return out


def _datasource(d: dict) -> dict:
    details = d.get("connectionDetails") if isinstance(d.get("connectionDetails"), dict) else {}
    return {"type": d.get("datasourceType"), **details, "gateway": bool(d.get("gatewayId"))}


def model(ctx, args) -> dict:
    ws, item = common.resolve(ctx, "model", args.model, args.workspace)
    out = {k: item[k] for k in ("id", "name", "configuredBy", "isRefreshable", "targetStorageMode", "createdDate")
           if k in item}
    out["workspace"] = ws["name"]
    out["onCapacity"] = common.on_capacity(ws)
    unavailable = {}

    def facet(name: str, path: str, params: dict | None = None) -> list | None:
        try:
            return common.get(ctx, ws, path, params).get("value") or []
        except http.HttpError as exc:
            if exc.status == 404:  # deleted since the cached inventory was taken: not a permission question
                raise PpxError(f"model {item.get('name')} was not found in {ws['name']}; it may have been deleted",
                               next_actions=[NextAction("refresh the inventory", command=f"ppx pbi ls --workspace "
                                                        f"{common.shell_arg(ws)} --refresh")]) from None
            unavailable[name] = _why(exc)
            return None

    def capped(name: str, values: list) -> None:
        out[name] = values if ctx.raw else values[:SHOWN_DETAILS]
        if len(out[name]) < len(values):
            out[f"{name}_count"] = len(values)

    mid = item["id"]
    sources = facet("datasources", f"datasets/{mid}/datasources")
    if sources is not None:
        capped("datasources", [_datasource(d) for d in sources])
    params = facet("parameters", f"datasets/{mid}/parameters")
    if params is not None:
        capped("parameters", [{k: p.get(k) for k in ("name", "type", "currentValue")} for p in params])
    entries = facet("refreshes", f"datasets/{mid}/refreshes", {"$top": SHOWN_REFRESHES})
    if entries is not None:
        out["refreshes"] = [refresh.parse_refresh_entry(e) for e in entries]
        if out["refreshes"] and out["refreshes"][0].get("status") == "Failed":
            ctx.next_actions.append(refresh.fix_for(out["refreshes"][0], ws, item))
    out.update(query.build_check(ctx, ws, item))
    if unavailable:
        out["unavailable"] = unavailable
        ctx.warn("some details are hidden from this user: " + ", ".join(unavailable))
    return out
