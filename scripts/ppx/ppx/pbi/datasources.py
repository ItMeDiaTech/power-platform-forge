"""pbi datasources ls|bind|update: a model's data sources, their credentials, and repointing them.

Durable credentials are a browser step: OAuth2 credentials set through the API carry no refresh token
and last about an hour (Learn, Gateways - Update Datasource, limitations, read 2026-10-26). So `bind`
returns the model's settings page by default, and `--transient` binds through the API only for one
immediate check refresh. Facts (Learn, read 2026-10-26): Gateways - Update Datasource (PATCH
/gateways/{gatewayId}/datasources/{datasourceId}, credentialDetails OAuth2 with
{"credentialData":[{"name":"accessToken","value":...}]}, encryptionAlgorithm None for cloud sources;
for cloud sources the caller must own the model, see Datasets - Take Over In Group); Datasets - Update
Datasources In Group (POST .../Default.UpdateDatasources {updateDetails: [{datasourceSelector,
connectionDetails}]}; caller must own the model; same schema required; data source type cannot change;
connection strings with parameters unsupported; only the first data source of a multi-source query is
updated, the workaround being a parameter and Update Parameters; incremental refresh models may be
updated partially); Datasets - Get Datasources In Group (datasourceType, connectionDetails, gatewayId,
datasourceId).
"""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

from ppx import apis, auth, http
from ppx.envelope import NextAction
from ppx.errors import NeedsConfirmation, NeedsUser, PpxError, UsageError
from ppx.pbi import backup, common, definition, params

EXPIRY = ("API-set OAuth2 credentials expire in about an hour; set them once in the model's settings page for "
          "scheduled refresh")
RECENT_DAYS = 7
DETAIL_KEYS = ("url", "server", "database", "path")


def _shape(src: dict) -> dict:
    details = src.get("connectionDetails") if isinstance(src.get("connectionDetails"), dict) else {}
    return {"type": src.get("datasourceType"), **details,
            **{k: src[k] for k in ("gatewayId", "datasourceId") if src.get(k)}}


def ls(ctx, args) -> dict:
    ws, model = common.resolve(ctx, "model", args.model, args.workspace)
    sources = params.read(ctx, ws, model, "datasources", "data sources")
    return {"model": model.get("name"), "workspace": ws["name"],
            **common.listing(ctx, [_shape(s) for s in sources], default_top=20)}


def _audience(src: dict) -> str | None:
    """The token audience of a cloud data source ppx can bind: SharePoint Online or Azure SQL."""
    details = src.get("connectionDetails") or {}
    kind = str(src.get("datasourceType") or "").lower()
    host = (urllib.parse.urlsplit(str(details.get("url") or details.get("path") or "")).hostname or "").lower()
    if "sharepoint" in kind and host.endswith(".sharepoint.com"):
        return f"https://{host}"
    server = re.sub(r"^tcp:|,\d+$", "", str(details.get("server") or "").lower())
    if kind == "sql" and server.endswith(".database.windows.net"):
        return "https://database.windows.net/"
    return None


def _scheduled_recently(entries: list[dict]) -> str | None:
    """End time of a scheduled refresh that completed in the last 7 days: durable credentials exist."""
    since = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)
    for e in entries:
        ended = common.parse_time(e.get("endTime"))
        if e.get("refreshType") == "Scheduled" and e.get("status") == "Completed" and ended and ended >= since:
            return e.get("endTime")
    return None


def bind(ctx, args) -> dict:
    ws, model = common.resolve(ctx, "model", args.model, args.workspace)
    settings = common.settings_url(ws, model["id"])
    refresh_cmd = common.command("refresh", model, ws)
    if not args.transient:
        raise NeedsUser("set the data source credentials once in the model's settings page (Data source credentials, "
                        "Edit credentials, sign in); that sign-in keeps working for scheduled refresh, while "
                        "credentials set through the API expire in about an hour",
                        next_actions=[NextAction("sign in to each data source there", url=settings),
                                      NextAction("then refresh once to check", command=refresh_cmd)])
    scheduled = _scheduled_recently(params.read(ctx, ws, model, "refreshes", "refresh history"))
    sources = params.read(ctx, ws, model, "datasources", "data sources")
    if not sources:
        raise UsageError(f"model {model.get('name')} has no data sources to bind")
    unsupported = [_shape(s) for s in sources if not (_audience(s) and s.get("gatewayId") and s.get("datasourceId"))]
    if unsupported:
        raise NeedsUser("ppx binds through the API only SharePoint Online and Azure SQL cloud data sources; set the "
                        "others in the model's settings page", data={"unsupported": unsupported[:5]},
                        next_actions=[NextAction("sign in to them there", url=settings)])
    if scheduled and not ctx.yes:
        raise NeedsConfirmation(f"a scheduled refresh completed at {scheduled}, so the model already has working "
                                "credentials; binding through the API replaces them with ones that expire in about an "
                                "hour. Re-run with --yes only if those credentials are broken",
                                data={"last_scheduled_refresh": scheduled},
                                next_actions=[NextAction("or fix them durably in the settings page", url=settings)])
    target = f"model {model.get('name')} in workspace {ws['name']}"
    # Learn documents Take Over only in a group; in My workspace the user already owns every model.
    take_over = ws["id"] != common.ME and not getattr(args, "owned", False)
    if take_over:
        ctx.changes.add("take over the model: you become its owner", target, reversible=False,
                        undo=f"its previous owner ({model.get('configuredBy') or 'unknown'}) can take it over again")
    for s in sources:
        ctx.changes.add(f"set the {s.get('datasourceType')} data source's credentials to your own sign-in "
                        "(OAuth2, valid about an hour)", f"{target}: {_audience(s)}", reversible=not scheduled)
    ctx.warn(EXPIRY)
    ctx.next("set the credentials durably for scheduled refresh", url=settings)
    params.preflight_refresh(ctx, ws, model, args.no_refresh)
    out = {"model": model.get("name"), "workspace": ws["name"], "sources": len(sources)}
    if ctx.dry_run:
        return {**out, "refresh": params.refresh_after(ctx, ws, model, args.no_refresh)}
    ctx.changes.require_yes(ctx.yes)
    try:
        if take_over:
            http.request("POST", common.url(ws, f"datasets/{model['id']}/Default.TakeOver"), audience=apis.PBI_RESOURCE,
                         op="write", body=b"", timeout=ctx.timeout)
    except http.HttpError as exc:
        if exc.status in (401, 403):
            raise NeedsUser(f"the service would not make you the owner of {model.get('name')} ({exc.status}); "
                            "taking over needs write permission on the model", data=exc.data) from None
        raise
    for s in sources:
        tok = auth.token(_audience(s), timeout=ctx.timeout)
        body = {"credentialDetails": {"credentialType": "OAuth2", "encryptedConnection": "Encrypted",
                                      "encryptionAlgorithm": "None", "privacyLevel": "None",
                                      "credentials": json.dumps({"credentialData": [{"name": "accessToken",
                                                                                     "value": tok.value}]})}}
        http.request("PATCH", apis.pbi().url(f"gateways/{s['gatewayId']}/datasources/{s['datasourceId']}"),
                     audience=apis.PBI_RESOURCE, op="write", json_body=body, timeout=ctx.timeout)
    return {**out, "bound": len(sources), "refresh": params.refresh_after(ctx, ws, model, args.no_refresh)}


def _refresh_policy(ctx, ws: dict, model: dict) -> bool | None:
    """Whether the model's definition holds an incremental refresh policy; None when it cannot be read."""
    if definition.branch(ctx) == "D":
        return None
    try:
        parts = definition.get_definition(ctx, definition.fabric_workspace_id(ctx, ws), "model", model["id"])["parts"]
        return any(b"refreshPolicy" in base64.b64decode(p.get("payload") or "") for p in parts)
    except (PpxError, ValueError):
        return None


def update(ctx, args) -> dict:
    new = {k: getattr(args, k) for k in DETAIL_KEYS if getattr(args, k)}
    if not new:
        raise UsageError("pass the new connection details: --url, --server, --database or --path")
    ws, model = common.resolve(ctx, "model", args.model, args.workspace)
    sources = params.read(ctx, ws, model, "datasources", "data sources")
    found = params.read(ctx, ws, model, "parameters", "parameters")
    workaround = ("Learn's workaround: define the data source as a Power Query parameter (in Desktop, then publish) "
                  "and change it with Update Parameters (`ppx pbi params set`)")
    old_values = {str(v) for s in sources for v in (s.get("connectionDetails") or {}).values() if v}
    hit = next((p for p in found if p.get("currentValue") and str(p["currentValue"]) in old_values), None)
    if hit:
        detail = next((k for s in sources for k, v in (s.get("connectionDetails") or {}).items()
                       if str(v) == str(hit["currentValue"])), None)
        assignment = params.quoted(f"{hit['name']}={new.get(detail) or '<new value>'}")
        fix = (NextAction("set the parameter", command=f"ppx pbi params set --model {common.shell_arg(model)} "
                                                       f"--workspace {common.shell_arg(ws)} --set {assignment}")
               if assignment else NextAction(f"set parameter {hit['name']} with `ppx pbi params set`"))
        raise NeedsUser(f"the data source takes its {detail or 'connection'} from parameter {hit['name']}, and Update "
                        "Datasources does not support parameters in the connection; set the parameter instead",
                        next_actions=[fix])
    if len(sources) != 1:
        raise NeedsUser(f"model {model.get('name')} has {len(sources)} data sources and Update Datasources changes "
                        f"only the first one of a query; {workaround}", data={"datasources": [_shape(s) for s in sources[:5]]})
    src = sources[0]
    details = dict(src.get("connectionDetails") or {})
    if set(new) - set(details):
        raise NeedsUser(f"the {src.get('datasourceType')} data source has no {', '.join(sorted(set(new) - set(details)))}; "
                        f"changing a data source's type is not supported; {workaround}", data={"current": details})
    policy = _refresh_policy(ctx, ws, model)
    if policy:
        raise NeedsUser(f"model {model.get('name')} has an incremental refresh policy, and Update Datasources may then "
                        f"update it only partly; {workaround}")
    if policy is None:
        ctx.warn("could not read the model's definition to check for an incremental refresh policy; if it has one, "
                 "the update may be partial")
    after = {**details, **new}
    reverse = {"updateDetails": [{"datasourceSelector": {"datasourceType": src.get("datasourceType"),
                                                         "connectionDetails": after}, "connectionDetails": details}]}
    stamp, folder = backup.reserve(ctx.need_data_dir(), model["id"])
    path = common.url(ws, f"datasets/{model['id']}/Default.UpdateDatasources")
    ctx.changes.add(f"repoint the {src.get('datasourceType')} data source from {json.dumps(details)} to "
                    f"{json.dumps(after)}", f"model {model.get('name')} in workspace {ws['name']}",
                    backup=str(folder / "datasources.json"),
                    undo=f"ppx raw pbi POST {path} --body \"@{folder / 'reverse.json'}\"")
    params.preflight_refresh(ctx, ws, model, args.no_refresh)
    out = {"model": model.get("name"), "workspace": ws["name"], "from": details, "to": after}
    if ctx.dry_run:
        return {**out, "refresh": params.refresh_after(ctx, ws, model, args.no_refresh)}
    backup.save_bytes(folder, "datasources.json", json.dumps({"datasources": sources}, indent=1).encode())
    backup.save_bytes(folder, "reverse.json", json.dumps(reverse).encode())
    body = {"updateDetails": [{"datasourceSelector": {"datasourceType": src.get("datasourceType"),
                                                      "connectionDetails": details}, "connectionDetails": after}]}
    try:
        http.request("POST", path, audience=apis.PBI_RESOURCE, op="write", json_body=body, timeout=ctx.timeout)
    except http.HttpError as exc:
        if exc.status in (401, 403):
            raise params.owner_error(exc, ws, model, "change its data sources") from None
        raise PpxError(f"updating the data source failed: {exc.message}; {workaround}", data=exc.data) from None
    ctx.next("undo the repoint", command=f"ppx raw pbi POST {path} --body \"@{folder / 'reverse.json'}\"")
    return {**out, "backup": stamp, "refresh": params.refresh_after(ctx, ws, model, args.no_refresh)}
