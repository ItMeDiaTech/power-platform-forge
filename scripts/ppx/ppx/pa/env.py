"""pa env ls|who: the environments this user can reach, and who they are in the profile's one."""

from __future__ import annotations

from ppx import apis, auth, cache, http, pac
from ppx.data.dataverse import Dataverse, is_guid
from ppx.errors import PpxError

# Global Discovery Service, commercial cloud (learn.microsoft.com/power-apps/developer/data-platform/discovery-service).
GDS = apis.Api("globaldisco", "https://globaldisco.crm.dynamics.com",
               "https://globaldisco.crm.dynamics.com/api/discovery/v2.0", ("globaldisco.crm.dynamics.com",))
_FIELDS = "FriendlyName,UniqueName,Url,EnvironmentId,State,Region"


def pac_environment(ctx, environment: str | None) -> str | None:
    """What pac's --environment takes (a GUID or a Dataverse URL) for a maker-module environment name.

    The maker module names the default environment Default-<tenant id>, which pac does not accept, so
    the name is looked up in the Global Discovery Service (EnvironmentId to Url, cached per day). With
    no environment the profile's URL is used, as before.
    """
    if not environment:
        return ctx.profile.url if ctx.profile and ctx.profile.url else None
    if is_guid(environment) or environment.lower().startswith("https://"):
        return environment
    data_dir = ctx.need_data_dir()
    snap = cache.latest(data_dir, "pa", "environments")
    if snap is None or ctx.refresh or cache.is_stale(snap):
        try:
            items, _ = http.paged(GDS.url("Instances"), audience=GDS.resource,
                                  params={"$select": "EnvironmentId,Url"}, timeout=ctx.timeout)
        except PpxError as exc:
            if snap is None:
                ctx.warn(f"could not look up environment {environment} in the Global Discovery Service "
                         f"({exc.message}); passed its name to pac as is")
                return environment
            ctx.warn(f"could not refresh the environment list ({exc.message}); "
                     f"used the one from {snap.get('fetched_at')}")
        else:
            snap = {"urls": {str(i["EnvironmentId"]).lower(): i["Url"] for i in items
                             if i.get("EnvironmentId") and i.get("Url")}}
            cache.write_snapshot(data_dir, "pa", "environments", snap)
    url = (snap.get("urls") or {}).get(environment.lower())
    if not url:
        ctx.warn(f"environment {environment} has no Dataverse URL among the environments you can reach; "
                 "passed its name to pac as is")
    return url or environment


def ls(ctx, args) -> dict:
    items, more = http.paged(GDS.url("Instances"), audience=GDS.resource, params={"$select": _FIELDS},
                             timeout=ctx.timeout)
    current = (ctx.profile.url or "").rstrip("/").lower() if ctx.profile else ""
    rows = [{"name": i.get("FriendlyName"), "unique_name": i.get("UniqueName"), "url": i.get("Url"),
             "environment_id": i.get("EnvironmentId"), "enabled": i.get("State") == 0, "region": i.get("Region"),
             **({"profile": True} if current and str(i.get("Url", "")).rstrip("/").lower() == current else {})}
            for i in items]
    top = ctx.top or 50
    if current and not any(r.get("profile") for r in rows):
        ctx.warn("the profile's environment URL is not among the environments you can reach")
    return {"count": len(rows), "items": rows[:top], "more": more or len(rows) > top}


def who(ctx, args) -> dict:
    dv = Dataverse(ctx)
    try:
        me = http.call("GET", dv.api.url("WhoAmI"), audience=dv.api.resource, timeout=ctx.timeout,
                       token_provider=dv._token) or {}
    except auth.AuthError as exc:
        ctx.warn(f"no Dataverse token ({exc.data.get('code')}); answered by pac env who, without security roles")
        body = pac.run(["env", "who", "--environment", dv.api.resource.rstrip("/")], json_out=True,
                       timeout=ctx.timeout)
        return {"environment": dv.api.resource.rstrip("/"), "token_source": "pac", "who": body}
    user = me.get("UserId")
    roles = dv.get(f"systemusers({user})/systemuserroles_association", {"$select": "name"}) if user else {}
    names = sorted({r.get("name") for r in (roles or {}).get("value", []) if r.get("name")})
    if not {"System Customizer", "System Administrator"} & set(names):
        ctx.warn("no System Customizer or System Administrator role assigned directly: mda publish will be refused "
                 "unless a team grants it")
    return {"environment": dv.api.resource.rstrip("/"), "token_source": dv.source, "user_id": user,
            "business_unit_id": me.get("BusinessUnitId"), "organization_id": me.get("OrganizationId"),
            "roles_direct": names}
