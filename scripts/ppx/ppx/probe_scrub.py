"""Placeholders for tenant identifiers in the probe report, so the report can leave the work laptop."""

from __future__ import annotations

import re
import urllib.parse

from ppx.probe_records import _GUID

_BANNER = re.compile(r"^[ \t]*Connected to\.*[ \t]*(\S.*?)[ \t\r]*$", re.M)  # pac names the environment it is signed in to


class Scrubber:
    """Stable placeholders for tenant identifiers: <org>, <site-1>, <env-1>, <guid-1>, <user-1>, <name-1>."""

    _EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
    _HOST = re.compile(r"\b(?:[\w-]+\.)+(?:sharepoint\.com|dynamics\.com|onmicrosoft\.com)\b", re.I)
    _MARK = re.compile(r"(<(?:org|site-\d+|env-\d+|guid-\d+|user-\d+|name-\d+)>)")
    _NAME_KEYS = ("title", "configuredby", "createdby", "modifiedby", "owner")
    MIN_NAME = 3  # shorter values identify nothing and would mangle field names such as "id"

    def __init__(self):
        self.seen: dict[tuple[str, str], str] = {}
        self.names: set[str] = set()
        self.orgs: set[str] = set()

    def learn(self, obj, key: str = "") -> None:
        """Collect display names and tenant labels from raw results, to mask them wherever they reappear."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                self.learn(v, str(k))
        elif isinstance(obj, list):
            for v in obj:
                self.learn(v, key)
        elif isinstance(obj, str):
            low = key.lower()
            if low == "tenantname":
                self.orgs.add(obj.strip())
            elif low.endswith("name") or low in self._NAME_KEYS:
                self.names.add(obj.strip())
            self.names.update(_BANNER.findall(obj))
            for host in self._HOST.findall(obj):
                self.orgs.add(re.sub(r"-(my|admin)$", "", host.split(".")[0], flags=re.I))

    def learn_args(self, args, site: str | None) -> None:
        self.names.update(v.strip() for v in (args.workspace, args.model, args.report, args.app, args.list) if v)
        if site:
            self.learn(site)
            segments = urllib.parse.unquote(urllib.parse.urlsplit(site).path).split("/")
            self.names.update(s for s in segments if s and s.lower() not in ("sites", "teams"))

    def _tag(self, kind: str, value: str) -> str:
        key = (kind, value.lower())
        if key not in self.seen:
            self.seen[key] = "<org>" if kind == "org" else \
                f"<{kind}-{sum(k == kind for k, _ in self.seen) + 1}>"
        return self.seen[key]

    def _host(self, host: str) -> str:
        low = host.lower()
        if low.endswith(".onmicrosoft.com"):
            return "<org>"
        return self._tag("site" if low.endswith(".sharepoint.com") else "env", low)

    def text(self, s: str) -> str:
        s = self._EMAIL.sub(lambda m: self._tag("user", m.group()), s)
        s = self._HOST.sub(lambda m: self._host(m.group()), s)
        s = _GUID.sub(lambda m: self._tag("guid", m.group()), s)
        orgs = {o.lower() for o in self.orgs}
        words = sorted({w for w in self.names | self.orgs if len(w) >= self.MIN_NAME}, key=len, reverse=True)
        if not words:
            return s
        pattern = re.compile("|".join(rf"(?<!\w){re.escape(w)}(?!\w)" for w in words), re.I)
        tag = lambda m: self._tag("org" if m.group().lower() in orgs else "name", m.group())  # noqa: E731
        # Placeholders already written are left alone, so a name such as "site" cannot eat "<site-1>".
        return "".join(part if i % 2 else pattern.sub(tag, part) for i, part in enumerate(self._MARK.split(s)))
