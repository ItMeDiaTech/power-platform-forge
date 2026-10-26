"""sp fields ls: a list's columns with Power Apps delegation and Power BI connector notes.

Columns come from `m365 spo field list` (https://pnp.github.io/cli-microsoft365/cmd/spo/field/field-list/;
keys InternalName, Title, TypeAsString, Indexed, Required, Hidden, FromBaseType, plus the
SharePoint REST type properties SP.FieldMultiChoice.Choices and SP.FieldLookup.LookupField,
https://learn.microsoft.com/en-us/previous-versions/office/developer/sharepoint-rest-reference/dn600182(v=office.15)).
Delegation: the SharePoint table on
https://learn.microsoft.com/en-us/power-apps/maker/canvas-apps/connections/connection-sharepoint-online
(ms.date 2026-10-07). Join limit: https://learn.microsoft.com/en-us/power-query/connectors/sharepoint-online-list
(SharePoint join limit, 2.0 connector).
"""

from __future__ import annotations

from ppx import exports, m365
from ppx.envelope import BUDGET_BYTES, dumps
from ppx.resolve import MAX_CANDIDATES
from ppx.sp import lists, sites

INLINE_BUDGET = BUDGET_BYTES - 500  # data plus warnings; the rest is the envelope's own keys
LOOKUP_TYPES = {"Lookup", "LookupMulti", "User", "UserMulti", "WorkflowStatus"}  # each costs a join
LOOKUP_LIMIT = 12  # SharePoint list view lookup threshold; the 2.0 connector fails a query past it
# System columns worth showing next to the user's own; the rest of the base columns are plumbing.
CORE = {"ID", "Title", "Created", "Modified", "Author", "Editor", "FileLeafRef"}
COMPLEX = {"Choice", "MultiChoice", "Lookup", "LookupMulti", "User", "UserMulti", "TaxonomyFieldType",
           "TaxonomyFieldTypeMulti", "OutcomeChoice"}
PA_TYPE = {"Text": "Text", "Note": "Text", "Calculated": "Text", "Number": "Number", "Currency": "Number",
           "Integer": "Number", "Counter": "ID", "Boolean": "Boolean", "DateTime": "DateTime", "URL": "Image",
           "Thumbnail": "Image", **{t: "Complex" for t in COMPLEX}}
# Operations that do not delegate to SharePoint, per Power Apps type (Learn delegation table).
NOT_DELEGABLE = {
    "Text": "no <, <=, <>, >, >=, IsBlank or UpdateIf/RemoveIf; = Filter LookUp Sort StartsWith delegate",
    "Boolean": "no <, <=, <>, >, >=; = Filter LookUp Sort delegate",
    "Complex": "no Sort, SortByColumns, IsBlank or UpdateIf/RemoveIf; filter on a subfield (.Value, .Email); "
               "StartsWith does not delegate on Choice or Lookup subfields; a Person column delegates only "
               "Email and DisplayName",
    "ID": "only = delegates: ID shows as Number in Power Apps but is Text in SharePoint",
    "Image": "not in Microsoft's delegation table: treat as non-delegable",
}
NOTES = (
    ("Power Apps: And/Or delegate, Not does not; a non-delegable formula sees only the first 500 rows "
     "(Data row limit, up to 10,000)"),
    ("Power BI: use SharePoint.Tables(site, [Implementation=\"2.0\", ViewMode=\"Default\"]); 2.0 is not "
     "backward compatible with 1.0 models, and 1.0 returns date/times in UTC"),
)


def load(ctx, site: str, lst: str) -> list[dict]:
    return m365.run(["spo", "field", "list", "--webUrl", site, *lists.ref(lst, prefix="list")],
                    timeout=ctx.timeout) or []


def shown(field: dict) -> bool:
    """The columns a person sees: the list's own, plus the core system ones."""
    return not field.get("Hidden") and (not field.get("FromBaseType") or field.get("InternalName") in CORE)


def _choices(field: dict) -> list | None:
    values = field.get("Choices")
    if isinstance(values, dict):  # odata=verbose shape
        values = values.get("results")
    if not values:
        return None
    return list(values[:10]) + (["..."] if len(values) > 10 else [])


def _row(field: dict) -> dict:
    out = {"name": field.get("InternalName"), "type": field.get("TypeAsString")}
    if field.get("Title") and field["Title"] != out["name"]:
        out["title"] = field["Title"]
    if field.get("Indexed"):
        out["indexed"] = True
    if field.get("Required"):
        out["required"] = True
    if field.get("TypeAsString") in ("Choice", "MultiChoice") and _choices(field):
        out["choices"] = _choices(field)
    if PA_TYPE.get(field.get("TypeAsString")) in NOT_DELEGABLE:
        out["pa"] = PA_TYPE[field["TypeAsString"]]
    return out


def ls(ctx, args) -> dict:
    site = sites.resolve(ctx, args.site)
    cols = [f for f in load(ctx, site, args.list) if ctx.raw or shown(f)]
    rows = [_row(f) for f in cols][:ctx.top]
    present = {PA_TYPE.get(f.get("TypeAsString")) for f in cols}
    lookups = [f.get("Title") or f.get("InternalName") for f in cols if f.get("TypeAsString") in LOOKUP_TYPES]
    power_bi = {"lookup_columns": len(lookups), "limit": LOOKUP_LIMIT}
    if len(lookups) > LOOKUP_LIMIT:  # the rows name every one; this is the reminder
        power_bi["names"] = lookups[:MAX_CANDIDATES] + [f"and {len(lookups) - MAX_CANDIDATES} more"]
        ctx.warn(f"{len(lookups)} lookup and person columns: the Power BI 2.0 connector fails a query touching more "
                 f"than {LOOKUP_LIMIT}; give the list a default view with fewer and use ViewMode=\"Default\"")
    head = {"site": site, "list": args.list, "count": len(cols)}
    tail = {"delegation": {t: rule for t, rule in NOT_DELEGABLE.items() if t in present},
            "power_bi": power_bi, "notes": list(NOTES)}
    data = {**head, "items": rows, **tail}
    if ctx.raw or ctx.data_dir is None or len(dumps({**data, "warnings": ctx.warnings}).encode()) <= INLINE_BUDGET:
        return data
    # A wide list: the rows go to a file, the rules and counts stay in the answer.
    saved = exports.save(ctx.data_dir, f"sp-fields-{args.list}", dumps(rows))
    return {**head, "saved_to": str(saved), "digest": exports.digest(rows), **tail}
