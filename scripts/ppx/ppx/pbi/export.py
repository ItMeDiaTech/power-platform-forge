"""pbi export-png: report pages as PNG through Export To File, which only capacity workspaces allow.

Facts (Learn, read 2026-10-26): Reports - Export To File In Group (POST .../reports/{id}/ExportTo
{format: PNG, powerBIReportConfiguration: {pages: [{pageName}]}}; 202 with an Export {id, status,
resourceFileExtension}; status NotStarted|Running|Succeeded|Failed|Undefined); Get Export To File Status In
Group (GET .../exports/{id}); Get File Of Export To File In Group (GET .../exports/{id}/file). Export Power
BI report to file: Premium, Embedded or Fabric capacity only, not Premium Per User; images also need the
tenant setting "Export reports as image files", off by default.
"""

from __future__ import annotations

import time
from pathlib import Path

from ppx import apis, exports, http
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, UsageError
from ppx.pbi import common
from ppx.resolve import pick

SETTING = "Export reports as image files"
POLL_SECONDS = 5.0


def _needs(reason: str, report: dict, ws: dict) -> NeedsUser:
    return NeedsUser(f"{reason}: PNG export needs the workspace on Premium, Embedded or Fabric capacity (Pro and "
                     f"Premium Per User are not enough) and the tenant setting '{SETTING}', which is off by default",
                     next_actions=[NextAction("capture the pages from Power BI Desktop instead",
                                              command=f"ppx pbi screenshot {common.shell_arg(report)} "
                                                      f"--workspace {common.shell_arg(ws)}"),
                                   NextAction("or ask IT for the setting: docs/ADMIN-REQUESTS.md#power-bi-tenant-settings")])


def run(ctx, args) -> dict:
    ws, report = common.resolve(ctx, "report", args.report, args.workspace)
    if not common.on_capacity(ws):
        raise _needs(f"workspace {ws['name']} is on shared capacity", report, ws)
    out = Path(args.out) if args.out else None
    if out is not None and out.exists():
        raise UsageError(f"{out} already exists; pass another --out")
    body: dict = {"format": "PNG"}
    page = None
    if args.page:
        listed = common.get(ctx, ws, f"reports/{report['id']}/pages").get("value") or []
        page = pick("page", args.page, listed, id_key="name", name_key="displayName", where=f" in {report.get('name')}")
        body["powerBIReportConfiguration"] = {"pages": [{"pageName": page["name"]}]}
    base = f"reports/{report['id']}/exports"
    try:
        job = http.call("POST", common.url(ws, f"reports/{report['id']}/ExportTo"), audience=apis.PBI_RESOURCE,
                        json_body=body, timeout=ctx.timeout) or {}
    except http.HttpError as exc:
        if exc.status in (400, 401, 403):
            raise _needs(f"the service refused the export ({exc.status}{' ' + exc.code if exc.code else ''})",
                         report, ws) from None
        raise
    deadline = time.monotonic() + ctx.timeout
    while job.get("status") not in ("Succeeded", "Failed"):
        if not job.get("id"):
            raise PpxError("the export was accepted without an export id")
        if time.monotonic() >= deadline:
            raise PpxError(f"the export was still {job.get('status')} after {ctx.timeout:g} s; retry with a longer "
                           "--timeout", data={"export": job.get("id")})
        resp = http.request("GET", common.url(ws, f"{base}/{job['id']}"), audience=apis.PBI_RESOURCE,
                            timeout=ctx.timeout)
        job = resp.json() or {}
        if job.get("status") not in ("Succeeded", "Failed"):
            try:
                delay = float(resp.headers.get("retry-after") or POLL_SECONDS)
            except ValueError:
                delay = POLL_SECONDS
            http.sleep(min(max(delay, 1.0), max(deadline - time.monotonic(), 0)))
    if job.get("status") == "Failed":
        raise PpxError(f"the export of {report.get('name')} failed", data={"export": job.get("id")})
    content = http.request("GET", common.url(ws, f"{base}/{job['id']}/file"), audience=apis.PBI_RESOURCE,
                           headers={"Accept": "*/*"}, timeout=ctx.timeout).body
    suffix = "." + str(job.get("resourceFileExtension") or "png").lstrip(".")
    if out is None:
        out = exports.save(ctx.need_data_dir(), f"{report.get('name') or report['id']}", content, suffix)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
    return {"report": report.get("name"), "workspace": ws["name"], "path": str(out), "bytes": len(content),
            **({"page": page.get("displayName") or page["name"]} if page else {})}
