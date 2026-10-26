"""Base URLs and token audiences of the APIs ppx calls, and the rule that a token only goes to its own API."""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from ppx.errors import SetupNeeded, UsageError
from ppx.envelope import NextAction

PBI_RESOURCE = "https://analysis.windows.net/powerbi/api"
PBI_BASE = "https://api.powerbi.com/v1.0/myorg"
FABRIC_RESOURCE = "https://api.fabric.microsoft.com"
FABRIC_BASE = "https://api.fabric.microsoft.com/v1"
GRAPH_RESOURCE = "https://graph.microsoft.com"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
POWERPLATFORM_RESOURCE = "https://api.powerplatform.com"
POWERPLATFORM_BASE = "https://api.powerplatform.com"


@dataclass(frozen=True)
class Api:
    name: str
    resource: str  # token audience
    base: str      # URL that relative paths are joined to
    hosts: tuple[str, ...]  # hosts this token may be sent to (a leading "." allows subdomains)

    def url(self, path: str) -> str:
        if path.startswith("https://"):
            self.check(path)
            return path
        if path.startswith("http://"):
            raise UsageError("only https:// URLs are allowed")
        return self.base.rstrip("/") + "/" + path.lstrip("/")

    def check(self, url: str) -> None:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if not any(host == h or (h.startswith(".") and host.endswith(h)) for h in self.hosts):
            raise UsageError(f"{url!r} is not on {self.name}'s host; a {self.name} token is never sent elsewhere")


def _host(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def pbi() -> Api:
    return Api("pbi", PBI_RESOURCE, PBI_BASE, ("api.powerbi.com", ".analysis.windows.net", ".pbidedicated.windows.net"))


def fabric() -> Api:
    return Api("fabric", FABRIC_RESOURCE, FABRIC_BASE, ("api.fabric.microsoft.com",))


def graph() -> Api:
    return Api("graph", GRAPH_RESOURCE, GRAPH_BASE, ("graph.microsoft.com",))


def powerplatform() -> Api:
    return Api("powerplatform", POWERPLATFORM_RESOURCE, POWERPLATFORM_BASE, ("api.powerplatform.com",))


def dataverse(env_url: str | None) -> Api:
    if not env_url:
        raise SetupNeeded("no Dataverse environment URL: set one on the profile",
                          next_actions=[NextAction("set it", command="ppx env set --url https://<org>.crm.dynamics.com")])
    env_url = env_url.rstrip("/")
    return Api("dataverse", env_url + "/", env_url + "/api/data/v9.2", (_host(env_url),))


def sharepoint(site_url: str | None) -> Api:
    if not site_url:
        raise SetupNeeded("no SharePoint site: pass --site or set one on the profile",
                          next_actions=[NextAction("set it", command="ppx env set --site https://<tenant>.sharepoint.com/sites/<site>")])
    site_url = site_url.rstrip("/")
    host = _host(site_url)
    if not host.endswith(".sharepoint.com"):
        raise UsageError(f"{site_url!r} is not a SharePoint Online site URL")
    return Api("spo", f"https://{host}", site_url + "/_api", (host,))


def by_name(name: str, profile=None, site: str | None = None) -> Api:
    if name == "pbi":
        return pbi()
    if name == "fabric":
        return fabric()
    if name == "graph":
        return graph()
    if name == "powerplatform":
        return powerplatform()
    if name == "dataverse":
        return dataverse(profile.url if profile else None)
    if name in ("spo", "sharepoint"):
        return sharepoint(site or (profile.site if profile else None))
    raise UsageError(f"unknown API {name!r}")
