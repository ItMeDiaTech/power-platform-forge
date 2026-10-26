"""Dataverse Web API v9.2 for the pa, mda and data verbs, with the pac fallback.

When no token source can mint a Dataverse token (routing.json says none, or every source fails),
list reads run the same query as FetchXML through `pac env fetch`, whose table output is
header-checked; writes exit 2 naming the consent request. Each call records which token source
answered, so envelopes can say how they were served.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from ppx import apis, auth, http, pac
from ppx.envelope import NextAction
from ppx.errors import NeedsUser

CONSENT = [NextAction("Dataverse writes need a Dataverse token; register your own app with the Dynamics CRM "
                      "permission and sign in with it", command="ppx auth app-spec"),
           NextAction("if consent is blocked, send IT the request in docs/ADMIN-REQUESTS.md#consent")]
CUSTOMIZER = ("Please give me the System Customizer security role in the Dataverse environment {url}, so I can edit "
              "and publish model-driven app components (forms, views, sitemaps) there.")
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def is_guid(value: str) -> bool:
    return bool(_GUID.match(str(value).strip()))


def literal(value) -> str:
    """An OData literal: numbers, booleans and GUIDs bare, text quoted with '' escaping."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int) or is_guid(str(value)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _condition(attr: str, value) -> str:
    if isinstance(value, (list, tuple)):  # the In query function takes the values as strings
        values = ",".join("'" + str(v).replace("'", "''") + "'" for v in value)
        return f"Microsoft.Dynamics.CRM.In(PropertyName='{attr}',PropertyValues=[{values}])"
    return f"{attr} eq {literal(value)}"


def odata(select=(), where=(), top=None, order=None, raw_filter=None) -> dict:
    """Query options for a collection GET. `where` is (attribute, value or list of values) pairs joined with `and`."""
    filters = [_condition(attr, value) for attr, value in where] + ([f"({raw_filter})"] if raw_filter else [])
    params = {"$select": ",".join(select) or None, "$filter": " and ".join(filters) or None,
              "$top": int(top) if top else None,
              "$orderby": f"{order[0]} {'desc' if order[1] else 'asc'}" if order else None}
    return {k: v for k, v in params.items() if v is not None}


def _logical(attr: str) -> str:
    """FetchXML names a lookup by its logical name: _workflow_value is workflow."""
    return attr[1:-6] if attr.startswith("_") and attr.endswith("_value") else attr


def _q(value) -> str:
    return escape(str(value), {"'": "&apos;"})


def _fetch_condition(attr: str, value) -> str:
    if isinstance(value, (list, tuple)):
        return (f"<condition attribute='{_q(_logical(attr))}' operator='in'>"
                + "".join(f"<value>{_q(v)}</value>" for v in value) + "</condition>")
    return f"<condition attribute='{_q(_logical(attr))}' operator='eq' value='{_q(value)}'/>"


def fetchxml(entity: str, select=(), where=(), top=None, order=None) -> str:
    attrs = "".join(f"<attribute name='{_q(_logical(a))}'/>" for a in select)
    conds = "".join(_fetch_condition(a, v) for a, v in where)
    sort = f"<order attribute='{_q(_logical(order[0]))}' descending='{str(bool(order[1])).lower()}'/>" if order else ""
    top_attr = f" top='{int(top)}'" if top else ""
    return (f"<fetch{top_attr}><entity name='{_q(entity)}'>{attrs}{sort}"
            f"{f'<filter>{conds}</filter>' if conds else ''}</entity></fetch>")


class Dataverse:
    def __init__(self, ctx):
        self.ctx = ctx
        self.api = apis.dataverse(ctx.profile.url if ctx.profile else None)
        self.source: str | None = None
        self.more = False

    def _token(self, audience, op="read", exclude=()):
        tok = http._default_token(audience, op, exclude)
        self.source = tok.source
        return tok

    def rows(self, entity_set: str, entity: str, select, where=(), top=None, order=None) -> list[dict]:
        """A collection read; FetchXML through pac when no Dataverse token can be had."""
        try:
            items, self.more = http.paged(self.api.url(entity_set), audience=self.api.resource, top=top,
                                          params=odata(select, where, top, order),
                                          timeout=self.ctx.timeout, token_provider=self._token)
            return [{k: v for k, v in i.items() if not k.startswith("@odata")} for i in items]
        except auth.AuthError as exc:
            return self._fetch(entity, list(select), where, top, order, exc)

    def _fetch(self, entity, select, where, top, order, why: auth.AuthError) -> list[dict]:
        note = (f"no Dataverse token ({why.data.get('code')}); read through pac env fetch instead, "
                "which returns text columns only")
        if note not in self.ctx.warnings:
            self.ctx.warn(note)
        env = ["--environment", self.ctx.profile.url] if self.ctx.profile and self.ctx.profile.url else []
        with tempfile.TemporaryDirectory(prefix="ppx-fetch-", ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "query.xml"
            path.write_text(fetchxml(entity, select, where, top, order), encoding="utf-8")
            p = pac.run(["env", "fetch", "--xmlFile", str(path), *env], timeout=self.ctx.timeout)
        names = [_logical(a) for a in select]
        self.source, self.more = "pac", False
        rows = pac.parse_table(p.stdout, names, command="env fetch")
        return [{a: row[n] for a, n in zip(select, names)} for row in rows]

    def get(self, path: str, params: dict | None = None):
        """One record or function call; needs a Dataverse token (pac env fetch cannot return XML bodies)."""
        try:
            return http.call("GET", self.api.url(path), audience=self.api.resource, params=params,
                             timeout=self.ctx.timeout, token_provider=self._token)
        except auth.AuthError as exc:
            raise NeedsUser(f"this read needs a Dataverse token ({exc.message})",
                            next_actions=exc.next_actions + CONSENT) from exc

    def write(self, method: str, path: str, body=None, headers: dict | None = None):
        try:
            return http.request(method, self.api.url(path), audience=self.api.resource, op="write", json_body=body,
                                headers=headers, timeout=self.ctx.timeout, token_provider=self._token)
        except auth.AuthError as exc:
            raise NeedsUser(f"no Dataverse token for writes ({exc.message})", next_actions=CONSENT,
                            data={"code": exc.code}) from exc
        except http.HttpError as exc:
            if exc.status == 403:
                raise NeedsUser(f"{exc.message}; editing model-driven components needs the System Customizer role",
                                next_actions=[NextAction("send the environment admin the request in data.admin_request")],
                                data={"admin_request": CUSTOMIZER.format(url=self.api.base.split("/api/")[0])}) from exc
            raise
