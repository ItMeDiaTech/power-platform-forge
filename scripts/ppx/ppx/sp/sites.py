"""sp site get|search, and turning --site (a URL or a remembered name) into a site URL.

The tenant-wide site list needs the SharePoint admin role, so names come from Graph site search
(GET /sites?search=, https://learn.microsoft.com/en-us/graph/api/site-search) and from `site get`,
remembered in <data>/cache/sp/sites.json.
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path

from ppx import apis, auth, cache, exports, http, m365
from ppx.envelope import NextAction
from ppx.errors import UsageError
from ppx.resolve import Ambiguous, NotFound, pick

SITES_FILE = Path("cache") / "sp" / "sites.json"
SEARCH_TOP = 25


def graph_tokens(data_dir):
    """Token provider for Graph that tries the Tier B app first until a probe has recorded a Graph route.

    Graph site and list reads need Sites.Read.All; the Tier B app is registered with it, while the
    scopes of az's first-party app are not documented and az still mints a token that Graph then refuses.
    """
    recorded = "graph" in (auth.routing(data_dir) or {})

    def provider(audience, op="read", exclude=()):
        return auth.token(audience, op, exclude=tuple(exclude) if recorded else (*exclude, "az"), data_dir=data_dir)

    return provider


def slug(url: str) -> str:
    return urllib.parse.unquote(urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])


def cached(data_dir) -> list[dict]:
    raw = cache.read_json(Path(data_dir) / SITES_FILE, {})
    sites = raw.get("sites") if isinstance(raw, dict) else None
    return [s for s in sites or [] if isinstance(s, dict) and s.get("url")]


def remember(data_dir, found: list[dict]) -> list[dict]:
    """Merge sites into sites.json (keyed by URL) and return every remembered site."""
    known = {s["url"].lower(): s for s in cached(data_dir)}
    for s in found:
        known[s["url"].lower()] = {"name": s.get("name") or slug(s["url"]), "slug": slug(s["url"]), "url": s["url"]}
    sites = list(known.values())
    cache.write_json(Path(data_dir) / SITES_FILE, {"sites": sites})
    return sites


def _search(ctx, query: str, top: int) -> tuple[list[dict], bool]:
    graph = apis.graph()
    rows, more = http.paged(graph.url("sites"), audience=graph.resource, params={"search": query}, top=top,
                            timeout=ctx.timeout, token_provider=graph_tokens(ctx.data_dir))
    found = [{"name": r.get("displayName") or r.get("name"), "slug": slug(r["webUrl"]), "url": r["webUrl"].rstrip("/"),
              "modified": (r.get("lastModifiedDateTime") or "")[:10]} for r in rows if r.get("webUrl")]
    remember(ctx.need_data_dir(), found)
    return found, more


def _match(value: str, sites: list[dict]) -> str:
    try:
        try:
            return pick("site", value, sites, id_key="url", name_key="name", context_keys=("url",))["url"]
        except NotFound:
            return pick("site", value, sites, id_key="url", name_key="slug", context_keys=("url",))["url"]
    except Ambiguous as exc:  # a site's id here is its URL
        raise Ambiguous(exc.message.replace("pass the id instead", "pass the site URL instead"),
                        data=exc.data) from None


def resolve(ctx, value: str | None) -> str:
    """The site URL for --site: a URL as given, else a remembered name; a miss searches Graph once."""
    value = (value or (ctx.profile.site if ctx.profile else None) or "").strip()
    if not value:
        apis.sharepoint(None)  # raises SetupNeeded naming --site and `ppx env set --site`
    if "://" in value:
        if not value.lower().startswith("https://"):
            raise UsageError(f"--site must be an https:// URL or a site name, not {value!r}")
        apis.sharepoint(value)  # refuses anything but a SharePoint Online host
        return value.rstrip("/")
    try:
        return _match(value, cached(ctx.need_data_dir()))
    except NotFound:
        pass
    _search(ctx, value, SEARCH_TOP)
    try:
        return _match(value, cached(ctx.data_dir))
    except NotFound as exc:
        exc.next_actions.append(NextAction("search with other words, or pass the site URL",
                                           command='ppx sp site search "<words>"'))
        raise


def search(ctx, args) -> dict:
    found, more = _search(ctx, args.query, ctx.top or SEARCH_TOP)
    if more:
        ctx.warn("more sites match; narrow the words or raise --top")
    saved = exports.inline_or_file(ctx, "sp-sites", found)
    return {"count": len(found), "more": more, **({"items": saved["body"]} if "body" in saved else saved)}


def get(ctx, args) -> dict:
    url = resolve(ctx, args.site)
    web = m365.run(["spo", "web", "get", "--url", url], timeout=ctx.timeout) or {}
    remember(ctx.need_data_dir(), [{"name": web.get("Title"), "url": (web.get("Url") or url).rstrip("/")}])
    return {"title": web.get("Title"), "url": web.get("Url") or url, "id": web.get("Id"),
            "template": web.get("WebTemplate"), "description": web.get("Description") or None,
            "created": web.get("Created"), "modified": web.get("LastItemModifiedDate")}
