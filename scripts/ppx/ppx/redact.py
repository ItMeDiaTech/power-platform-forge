"""One redactor for every channel ppx writes: stdout, stderr, audit and dry-run output."""

from __future__ import annotations

import re

MASK = "<redacted>"
_SECRET_KEY = re.compile(r"^(token|sig|authorization|credentials|credentialdetails|.*(access|refresh|id)_?token|.*password|.*secret)$", re.I)
_PATTERNS = [
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]{16,}=*"),
    # A purely numeric code= is an AADSTS error-lookup URL, not an OAuth code, so it is kept.
    re.compile(r"(?i)([?&](?:sig|token|code)=)(?!\d{1,10}(?:[&\s\"']|$))[^&\s\"']+"),
    re.compile(r"(?i)((?:--clientSecret|-cs|--certificatePassword|-cp|--password|-p)[ =])\S+"),
    # The password in URL userinfo (https://user:password@host), e.g. a connector host typed with credentials.
    re.compile(r"(?i)(://[^/@\s:]+:)[^@/\s]+(?=@)"),
]


def scrub(value):
    """Return a copy with token-like values masked. Dicts, lists and strings are walked."""
    if isinstance(value, dict):
        return {k: (MASK if isinstance(k, str) and _SECRET_KEY.search(k) and v not in (None, "", {}, []) else scrub(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, tuple):
        return tuple(scrub(v) for v in value)
    if isinstance(value, str):
        return scrub_text(value)
    return value


def scrub_text(text: str) -> str:
    text = _PATTERNS[0].sub(MASK, text)
    for pat in _PATTERNS[1:]:
        text = pat.sub(lambda m: m.group(1) + MASK, text)
    return text
