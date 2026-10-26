"""Classification kinds and the small helpers every change-notice rule shares. Stdlib only."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ppx.redact import MASK, scrub_text

KINDS = ("read", "local", "change", "interactive", "unknown", "irreversible")
_RANK = {kind: i for i, kind in enumerate(KINDS)}


@dataclass(frozen=True)
class Classification:
    kind: str
    summary: str
    binary: str


READ = Classification("read", "", "")


# Sign-in secrets on m365, PnP and Power Apps command lines, which ppx.redact's option list does not cover.
_SECRET_OPT = re.compile(r"(?i)(?<![\w-])(-{1,2}(?:(?:client|app)?secret|(?:certificate)?password"
                         r"|certificatebase64encoded))([ =:])\S+")


def _clip(text: str, limit: int = 160) -> str:
    text = _SECRET_OPT.sub(lambda m: m.group(1) + m.group(2) + MASK, scrub_text(text))
    text = " ".join(text.split())  # scrub before cutting, so a token is never half-masked
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _worst(results: list[Classification]) -> Classification:
    hits = sorted((r for r in results if r.kind != "read"), key=lambda r: -_RANK[r.kind])
    if not hits:
        return READ
    if hits[0].kind == "interactive":  # the reason must stay a command the user can paste
        hits = [r for r in hits if r.kind == "interactive"]
    summary = "; ".join(r.summary for r in hits[:3]) + (f"; and {len(hits) - 3} more" if len(hits) > 3 else "")
    return Classification(hits[0].kind, summary, hits[0].binary)


def _path(tokens: list[str]) -> list[str]:
    """The command words before the first option or redirection, e.g. ['solution', 'import']."""
    out = []
    for tok in tokens:
        if tok.startswith("-") or re.match(r"[\d*]*[<>]", tok):
            break
        out.append(tok)
    return out


def _opt(words: list[str], names: tuple[str, ...], ci: bool = False) -> str | None:
    """The value of the first option in `names`: `--x v`, `--x=v`, or PowerShell `-X:v` when `ci`."""
    for i, w in enumerate(words):
        key = w.lower() if ci else w
        for n in names:
            if key == n:
                return words[i + 1] if i + 1 < len(words) else ""
            if key.startswith(n + "=") or (ci and key.startswith(n + ":")):
                return w[len(n) + 1:]
    return None


def _by_method(method: str | None) -> str:
    if not method:
        return "unknown"
    m = method.upper()
    return "read" if m in ("GET", "HEAD", "OPTIONS") else "irreversible" if m == "DELETE" else "change"


def _rest(binary: str, method: str, url: str | None) -> Classification:
    kind = _by_method(method)
    if kind == "read":
        return READ
    return Classification(kind, _clip(f"{method.upper()} {url or '(no URL)'} with {binary}"), binary.split()[0])


def _cli(binary: str, words: list[str], path: list[str], kind: str, phrase: str | None = None) -> Classification:
    if kind == "read":
        return READ
    if phrase is None:
        verb, nouns = (path[-1], path[:-1]) if path else ("run", [])
        phrase = f"{verb.replace('-', ' ').capitalize()} {' '.join(nouns)}".rstrip()
    return Classification(kind, f"{phrase} with {binary} ({_clip(' '.join(words[1:]))})", binary)


_PP_HOSTS = ("powerbi.com", "dynamics.com", "sharepoint.com", "flow.microsoft.com", "logic.azure.com", "powerapps.com",
             "api.powerplatform.com", "api.fabric.microsoft.com", "graph.microsoft.com", "api.bap.microsoft.com")
_URL = re.compile(r"https?://([^/:?#\s\"']+)[^\s\"']*", re.I)


def _pp_host(url: re.Match | None) -> bool:
    host = url.group(1).lower() if url else ""
    return any(host == h or host.endswith("." + h) for h in _PP_HOSTS)


def _name(word: str) -> str:
    base = re.split(r"[\\/]", word)[-1].lower()
    return base[:-4] if base.endswith((".exe", ".cmd", ".bat")) else base
