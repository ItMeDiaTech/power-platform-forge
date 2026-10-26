"""pa flows ls|runs: solution-aware cloud flows (Dataverse workflow, category 5 Modern Flow) and their
runs (flowrun, an elastic table kept 28 days by default whose ingestion is not lossless, and whose
records belong to the flow's primary owner, so co-owners see none;
learn.microsoft.com/power-automate/dataverse/cloud-flow-run-metadata). That page calls the run
duration seconds, but the generated table reference names the `duration` column DurationInMs,
"Duration of the run in milliseconds" (learn.microsoft.com/power-apps/developer/data-platform/
reference/entities/flowrun); the metadata wins."""

from __future__ import annotations

from ppx import resolve
from ppx.data.dataverse import Dataverse

CAVEAT = ("solution-aware flows only; run records belong to each flow's primary owner, so a co-owner may see none; "
          "run history is kept 28 days by default and can have gaps")
STATE = {"0": "off (draft)", "1": "on", "2": "suspended"}  # workflow statecode: Draft, Activated, Suspended
RUN_STATUS = {"success": "succeeded", "succeeded": "succeeded", "failed": "failed", "cancelled": "cancelled",
              "canceled": "cancelled", "running": "running", "waiting": "running"}
_FLOW = ("workflowid", "name", "statecode", "modifiedon")
_RUN = ("name", "status", "starttime", "endtime", "duration", "errorcode", "errormessage", "triggertype")


def run_status(value) -> str:
    text = str(value or "").strip().lower()
    return RUN_STATUS.get(text, text or "unknown")


def _handoff(ctx) -> None:
    ctx.warn(CAVEAT)
    ctx.next("for flows outside solutions (My flows), use FlowAgent's read tools from the power-automate plugin")


def _flows(ctx, dv: Dataverse) -> list[dict]:
    rows = dv.rows("workflows", "workflow", _FLOW, where=[("category", 5)], order=("name", False))
    return [{"id": r.get("workflowid"), "name": r.get("name"), "state": STATE.get(str(r.get("statecode")),
                                                                                   str(r.get("statecode"))),
             "modified": r.get("modifiedon")} for r in rows]


def ls(ctx, args) -> dict:
    dv = Dataverse(ctx)
    items = _flows(ctx, dv)
    _handoff(ctx)
    top = ctx.top or 50
    return {"count": len(items), "items": items[:top], "more": len(items) > top, "token_source": dv.source}


def runs(ctx, args) -> dict:
    dv = Dataverse(ctx)
    flow = resolve.pick("flow", args.flow, _flows(ctx, dv))
    top = ctx.top or 10
    rows = dv.rows("flowruns", "flowrun", _RUN, where=[("_workflow_value", flow["id"])], top=top,
                   order=("starttime", True))
    items = [{"run": r.get("name"), "status": run_status(r.get("status")), "start": r.get("starttime"),
              "end": r.get("endtime"), "duration_ms": r.get("duration"), "trigger": r.get("triggertype"),
              **({"error": r.get("errorcode"), "message": str(r.get("errormessage") or "")[:200]}
                 if r.get("errorcode") or r.get("errormessage") else {})} for r in rows]
    counts: dict[str, int] = {}
    for i in items:
        counts[i["status"]] = counts.get(i["status"], 0) + 1
    _handoff(ctx)
    return {"flow": {"id": flow["id"], "name": flow["name"], "state": flow["state"]}, "count": len(items),
            "by_status": counts, "items": items, "more": dv.more, "token_source": dv.source}
