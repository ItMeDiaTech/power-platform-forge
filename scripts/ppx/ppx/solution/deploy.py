"""ppx solution settings and import: deployment settings, then an import that never runs while a
connection reference or environment variable is still empty.

Deployment settings shape (learn.microsoft.com/power-platform/alm/conn-ref-env-variables-build-tools):
{"EnvironmentVariables": [{"SchemaName", "Value"}], "ConnectionReferences": [{"LogicalName",
"ConnectionId", "ConnectorId"}]}. pac waits for the import itself (with --async it polls the async
operation); afterwards ppx reads the import job record (importjobs: progress, startedon,
completedon, data) to report the result Dataverse recorded.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from ppx import apis, http, pac
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, SetupNeeded, UsageError
from ppx.solution import common

MAX_IMPORT_BYTES = 95 * 1024 * 1024  # learn.microsoft.com/power-apps/maker/data-platform/import-update-export-solutions
POLL_SECONDS = 10
JOB_WAIT_SECONDS = 60  # the job record can trail pac's exit by a few seconds
CLOCK_SLACK = timedelta(minutes=5)


def _unset(refs: list[dict], variables: list[dict]) -> dict:
    return {"connection_references": [r["name"] for r in refs if not r["set"]],
            "environment_variables": [v["name"] for v in variables if not v["set"]]}


def settings(ctx, args) -> dict:
    zip_path = common.solution_zip(args.zip)
    name = common.manifest(zip_path)["name"]
    out = Path(args.out).expanduser().resolve()
    old = common.read_settings(out) if out.exists() else None
    found = common.discover(zip_path, ctx.timeout)
    merged = common.merge(found, old)
    refs, variables = common.review(found, merged, common.defaults(zip_path))
    kept = {r.get("LogicalName") for r in found.get("ConnectionReferences") or []} | \
           {e.get("SchemaName") for e in found.get("EnvironmentVariables") or []}
    dropped = sorted({c.get("LogicalName") for c in (old or {}).get("ConnectionReferences") or []} |
                     {e.get("SchemaName") for e in (old or {}).get("EnvironmentVariables") or []} - kept - {None})
    backup = None
    if old is not None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = ctx.need_data_dir() / "backups" / "solution-settings" / name / stamp / out.name
    ctx.changes.add(f"{'update' if old else 'write'} deployment settings for solution {name}", str(out),
                    backup=str(backup) if backup else None)
    if not ctx.dry_run:
        if backup:
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(out.read_bytes())
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    unset = _unset(refs, variables)
    if any(unset.values()):
        ctx.next(f"fill each empty ConnectionId (make.powerapps.com > Connections, the id in the URL) and Value in {out}, "
                 "then preview the import", command=f'ppx solution import --zip "{zip_path}" --settings "{out}" --dry-run')
    return {"solution": name, "settings": str(out), "connection_references": refs, "environment_variables": variables,
            "unset": unset, "dropped": dropped}


def _odata(text: str) -> str:
    return text.replace("'", "''")


def _job_result(xml_text: str | None) -> dict:
    """result and error text from the import job's data XML (//solutionManifest/result/@result)."""
    if not xml_text:
        return {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    for el in root.iter():
        if str(el.tag).lower() == "solutionmanifest":
            res = next((c for c in el if str(c.tag).lower() == "result"), None)
            if res is not None:
                out = {"result": res.get("result")}
                if res.get("errortext"):
                    out["error"] = res.get("errortext")[:500]
                return out
    return {}


def _newest_job_id(ctx, name: str) -> str | None:
    """The id of the newest import job for `name` before this import, so it is never read as this one's."""
    dv = apis.dataverse(ctx.profile.url)
    try:
        body = http.call("GET", dv.url("importjobs"), audience=dv.resource, timeout=ctx.timeout,
                         params={"$select": "importjobid", "$filter": f"solutionname eq '{_odata(name)}'",
                                 "$orderby": "startedon desc", "$top": "1"}) or {}
    except PpxError:
        return None
    return (body.get("value") or [{}])[0].get("importjobid")


def _import_job(ctx, name: str, since: datetime, wait: int, before: str | None = None) -> dict | None:
    """Poll the newest import job for `name` started after `since` (and not `before`, the job that existed
    already) until it completes or `wait` runs out. Its record can appear seconds after pac exits."""
    dv = apis.dataverse(ctx.profile.url)
    not_before = f" and importjobid ne {before}" if before and re.fullmatch(r"[0-9a-fA-F-]{36}", before) else ""
    params = {"$select": "importjobid,solutionname,progress,startedon,completedon",
              "$filter": f"solutionname eq '{_odata(name)}' and startedon ge {since:%Y-%m-%dT%H:%M:%SZ}{not_before}",
              "$orderby": "startedon desc", "$top": "1"}
    job = None
    try:
        for attempt in range(wait // POLL_SECONDS + 1):
            if attempt:
                http.sleep(POLL_SECONDS)
            body = http.call("GET", dv.url("importjobs"), audience=dv.resource, params=params, timeout=ctx.timeout) or {}
            job = (body.get("value") or [None])[0]
            if job and job.get("completedon"):
                break
        if not job:
            ctx.warn(f"no import job for {name} found in the environment since {since:%H:%M} UTC")
            return None
        out = {"id": job.get("importjobid"), "progress": job.get("progress"), "started": job.get("startedon"),
               "completed": job.get("completedon")}
        if job.get("completedon"):
            detail = http.call("GET", dv.url(f"importjobs({job['importjobid']})"), audience=dv.resource,
                               params={"$select": "data"}, timeout=ctx.timeout) or {}
            out.update(_job_result(detail.get("data")))
        return out
    except PpxError as exc:
        ctx.warn(f"import job not read: {exc.message}")
        return None


def import_(ctx, args) -> dict:
    zip_path = common.solution_zip(args.zip)
    if zip_path.stat().st_size > MAX_IMPORT_BYTES:
        raise UsageError(f"{zip_path.name} is over 95 MB, the largest solution file Dataverse imports; "
                         "split the solution")
    url = ctx.need_profile("solution import").url
    if not url:
        raise SetupNeeded("solution import needs the target environment's URL on the active profile, so an import "
                          "never lands in whatever environment pac auth last selected",
                          next_actions=[NextAction("set it", command="ppx env set --url https://<org>.crm.dynamics.com")])
    info = common.manifest(zip_path)
    settings_path = Path(args.settings).expanduser().resolve() if args.settings else None
    given = common.read_settings(settings_path) if settings_path else None
    refs, variables = common.review(common.discover(zip_path, ctx.timeout), given, common.defaults(zip_path))
    kind = "managed" if info["managed"] else "unmanaged"
    ctx.changes.add(f"import {kind} solution {info['name']} {info['version'] or ''}".rstrip(), url, reversible=False)
    data = {"solution": info, "connection_references": refs, "environment_variables": variables}
    unset = _unset(refs, variables)
    fix = NextAction("write or update the deployment settings, fill the empty values, then import with --settings",
                     command=f'ppx solution settings --zip "{zip_path}" --out "{settings_path or zip_path.with_suffix(".settings.json")}"')
    if ctx.dry_run:
        if any(unset.values()):
            ctx.warn(f"{len(unset['connection_references'])} connection reference(s) and "
                     f"{len(unset['environment_variables'])} environment variable(s) have no value; the import will refuse")
            ctx.next_actions.append(fix)
        return data
    if any(unset.values()):
        raise NeedsUser("connection references or environment variables in this solution have no value; "
                        "set them in a deployment settings file first", data={"unset": unset}, next_actions=[fix])
    ctx.changes.require_yes(ctx.yes)
    since = datetime.now(timezone.utc) - CLOCK_SLACK
    before = _newest_job_id(ctx, info["name"])
    argv = ["solution", "import", "--path", str(zip_path), *common.env_args(ctx)]
    argv += ["--settings-file", str(settings_path)] if settings_path else []
    argv += [flag for flag, on in (("--async", getattr(args, "async")), ("--activate-plugins", args.activate_plugins),
                                   ("--publish-changes", args.publish_changes)) if on]
    try:
        pac.run(argv, timeout=common.long_timeout(ctx))
    except PpxError as exc:  # a pac failure or timeout: say what Dataverse recorded so far
        exc.data["import_job"] = _import_job(ctx, info["name"], since, 0, before)
        # Percent-encoded so neither Bash nor PowerShell expands $select or splits on the quotes.
        query = (f"%24select=progress,completedon&%24filter=solutionname%20eq%20%27{quote(_odata(info['name']))}%27"
                 "&%24orderby=startedon%20desc&%24top=1")
        exc.next_actions.append(NextAction("read the import job again later",
                                           command=f'ppx raw dataverse GET "importjobs?{query}"'))
        raise
    data["import_job"] = job = _import_job(ctx, info["name"], since, JOB_WAIT_SECONDS, before)
    if job and job.get("result") == "failure":
        raise PpxError(f"pac finished, but Dataverse recorded the import as failed: {job.get('error') or 'no error text'}",
                       data=data)
    return data
