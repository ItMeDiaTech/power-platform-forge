"""pbi screenshot: report pages captured from a running Power BI Desktop through the Desktop Bridge CLI.

The bridge (npm @microsoft/powerbi-desktop-bridge-cli 1.0.0, bin `powerbi-desktop`) talks to Desktop over a
named pipe. Its readme documents `status [--pid]`, `screenshot <page-id> --pid <pid> [--scale 1-3]` and
`screenshot-all --pid <pid> --output-dir <dir> [--scale]`, JSON on stdout, but not the JSON fields, so the
instance list is read leniently. Nothing here opens Desktop or a browser: without a connected instance
the verb exits 2 with the bridge status and the report's service URL.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ppx import exports, proc
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, SetupNeeded, UsageError
from ppx.pbi import common, definition
from ppx.redact import scrub_text

BRIDGE = "powerbi-desktop"
INSTALL = "npm install -g @microsoft/powerbi-desktop-bridge-cli"


def _json(p: proc.Proc, what: str):
    text = p.stdout.strip()
    try:
        return json.loads(text[min((i for i in (text.find("["), text.find("{")) if i >= 0), default=0):])
    except ValueError:
        tail = scrub_text((p.stderr.strip() or text)[-300:])
        raise PpxError(f"{BRIDGE} {what} printed no JSON (exit {p.code}): {tail}") from None


def instances(status) -> list[dict]:
    """Bridge instances with a process id, from `status` output in any of its plausible shapes."""
    found = status.get("instances", status.get("value", [status])) if isinstance(status, dict) else status
    out = []
    for i in found if isinstance(found, list) else []:
        pid = i.get("pid", i.get("processId")) if isinstance(i, dict) else None
        if isinstance(pid, int) or (isinstance(pid, str) and pid.isdigit()):
            out.append({"pid": int(pid), **{k: i[k] for k in ("file", "currentFile", "path", "dirty", "unsaved")
                                            if k in i}})
    return out


def run(ctx, args) -> dict:
    ws = item = None
    if args.report:
        ws, item = common.resolve(ctx, "report", args.report, args.workspace)
    service = [NextAction("view the report in the service instead", url=definition.report_url(ws, item["id"]))] if item else []
    try:
        status = _json(proc.run([BRIDGE, "status"], timeout=ctx.timeout, install_hint=INSTALL), "status")
    except SetupNeeded as exc:  # the bridge is not installed: the service URL is still worth handing over
        exc.next_actions[:0] = service
        raise
    found = instances(status)
    if args.pid is not None:
        found = [i for i in found if i["pid"] == args.pid]
    if not found:
        raise NeedsUser("no Power BI Desktop instance is connected to the Desktop Bridge"
                        + (f" with process id {args.pid}" if args.pid is not None else "")
                        + "; open the report's PBIP or PBIX in Desktop (the bridge preview setting must be on)",
                        next_actions=service + [NextAction("open a project in Desktop through the bridge",
                                                           command=f'{BRIDGE} open "<path to .pbip or .pbix>"')],
                        data={"bridge_status": exports.digest(status)})
    if len(found) > 1:
        raise UsageError(f"{len(found)} Desktop instances are connected; pass --pid", data={"instances": found[:5]})
    pid = found[0]["pid"]
    scale = ["--scale", str(args.scale)] if args.scale else []
    if args.page:
        p = proc.run([BRIDGE, "screenshot", args.page, "--pid", str(pid), *scale], timeout=ctx.timeout * 2,
                     install_hint=INSTALL)
        result = _json(p, "screenshot")
        if p.code != 0:
            raise PpxError(f"{BRIDGE} screenshot failed: {json.dumps(result)[:300]}")
        return {"pid": pid, "page": args.page, **exports.inline_or_file(ctx, f"screenshot-{args.page}", result)}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else ctx.need_data_dir() / "exports" / f"screenshots-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    p = proc.run([BRIDGE, "screenshot-all", "--pid", str(pid), "--output-dir", str(out), *scale],
                 timeout=ctx.timeout * 4, install_hint=INSTALL)
    if p.code != 0:
        raise PpxError(f"{BRIDGE} screenshot-all failed: {scrub_text((p.stderr.strip() or p.stdout.strip())[-300:])}")
    images = sorted(f.name for f in out.iterdir() if f.is_file())
    return {"pid": pid, "folder": str(out), "count": len(images), "files": images[:20]}
