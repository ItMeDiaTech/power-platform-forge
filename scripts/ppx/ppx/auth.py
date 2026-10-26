"""Silent access tokens per API, a DPAPI memo, and sign-in errors that name the fix.

Sources: `az` (Azure CLI), `tier_b` (CLI for Microsoft 365 signed in with the user's own app)
and `pac` (Power Platform API only). The order per API and operation comes from
`<data>/routing.json`, written by `ppx probe`; without it az goes first, then Tier B. Nothing
here opens a browser or prompts: only `ppx auth login` does.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from ppx import apis, datadir, dpapi, pac, proc
from ppx.envelope import NextAction
from ppx.errors import NeedsUser, PpxError, SetupNeeded
from ppx.redact import scrub_text

REFRESH_MARGIN = 300  # a memoised token is reused until five minutes before it expires
ROUTING_FILE = "routing.json"
MEMO_FILE = Path("cache") / "tokens.bin"
SETTINGS_FILE = "auth.json"
LOOKUP_URL = "https://login.microsoftonline.com/error?code={}"
SOURCES = ("az", "tier_b", "pac")
BACKEND = {"az": "az", "tier_b": "m365", "pac": "pac"}
INSTALL = {
    "az": "winget install -e --id Microsoft.AzureCLI",
    "m365": "npm install -g @pnp/cli-microsoft365",
    "pac": pac.INSTALL,
    "fab": "uv tool install ms-fabric-cli",
    "powershell.exe": "Windows PowerShell 5.1 ships with Windows 10 and 11",
}
MEANING = {
    "65001": "consent missing for this app and API",
    "65002": "this sign-in's client is not pre-authorised for this API",
    "53000": "Conditional Access requires a compliant device",
    "53001": "Conditional Access requires a domain-joined device",
    "53003": "blocked by Conditional Access",
    "50076": "multifactor sign-in required",
    "50079": "multifactor registration required",
    "530036": "session blocked by the device-code flow policy",
    "700082": "sign-in expired after inactivity",
    "70043": "sign-in expired under the sign-in frequency policy",
    "not-signed-in": "not signed in",
}
_CLASSIFIED = set(MEANING) - {"65002", "not-signed-in"}
_AADSTS = re.compile(r"AADSTS(\d+)")
_NOT_SIGNED_IN = re.compile(r"az login|log in to microsoft 365 first|not logged in|no profiles were found|pac auth create", re.I)
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_TENANT = re.compile(r"[\w-]+\.onmicrosoft\.com", re.I)
_GUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_CLAIMS = ("scp", "aud", "exp", "tid")

_LOCK = threading.Lock()
_MEMO: dict[tuple[str, str], "Token"] = {}


@dataclass
class Token:
    value: str = field(repr=False)
    source: str
    expires_on: float  # POSIX seconds


class AuthError(NeedsUser):
    """No source could mint a token and the cause is one the user or IT can fix (exit 2)."""

    def __init__(self, message: str, *, code: str, audience: str, source: str | None, fix: NextAction):
        super().__init__(message, next_actions=[fix], data={"code": code, "audience": audience, "source": source})
        self.code, self.audience, self.source, self.fix = code, audience, source, fix


@dataclass
class _Failure:
    source: str
    code: str  # AADSTS digits, not-signed-in, missing, timeout or error
    detail: str
    install: NextAction | None = None


def audience_key(resource: str) -> str:
    """The routing key of a resource URL: pbi, fabric, graph, powerplatform, dataverse or sharepoint."""
    known = {apis.PBI_RESOURCE: "pbi", apis.FABRIC_RESOURCE: "fabric", apis.GRAPH_RESOURCE: "graph",
             apis.POWERPLATFORM_RESOURCE: "powerplatform"}
    if resource.rstrip("/") in known:
        return known[resource.rstrip("/")]
    host = (urllib.parse.urlsplit(resource).hostname or "").lower()
    if host.endswith(".dynamics.com"):
        return "dataverse"
    if host.endswith(".sharepoint.com"):
        return "sharepoint"
    return host or resource


def decode_claims(jwt: str) -> dict:
    """The non-identifying claims of a JWT (scp, aud, exp, tid); no signature check, never the token."""
    try:
        payload = jwt.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}
    return {k: claims[k] for k in _CLAIMS if k in claims} if isinstance(claims, dict) else {}


def routing(data_dir: Path | None) -> dict | None:
    """routing.json as {key: {op: [sources]}} with "none" dropped; None when no probe has run."""
    raw = _read_json(Path(data_dir) / ROUTING_FILE) if data_dir else None
    if not isinstance(raw, dict):
        return None
    out = {}
    for key, entry in raw.items():
        if isinstance(entry, dict):
            # A malformed value is ignored, so that operation falls back to the default order.
            out[key] = {op: [s for s in ([v] if isinstance(v, str) else v) if isinstance(s, str) and s != "none"]
                        for op, v in entry.items() if op in ("read", "write") and isinstance(v, (str, list))}
    return out


def settings(data_dir: Path | None) -> dict:
    """Saved sign-in settings (the Tier B app id and tenant), never credentials."""
    raw = _read_json(Path(data_dir) / SETTINGS_FILE) if data_dir else None
    return raw if isinstance(raw, dict) else {}


def save_settings(data_dir: Path, backend: str, values: dict) -> None:
    current = settings(data_dir)
    current[backend] = {**current.get(backend, {}), **{k: v for k, v in values.items() if v}}
    path = Path(data_dir) / SETTINGS_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def token(audience: str, op: str = "read", *, exclude: tuple = (), data_dir=None, timeout: float = 60) -> Token:
    """Return a token for `audience` (a resource URL) silently, or raise an error naming the fix."""
    data_dir = _resolve(data_dir)
    key = audience_key(audience)
    order = [s for s in _sources(audience, key, op, data_dir) if s not in exclude]
    now = time.time()
    with _LOCK:
        memo = _memo(data_dir)
        for source in order:
            tok = memo.get((audience, source))
            if tok and tok.expires_on - REFRESH_MARGIN > now:
                return tok
    failures = []
    for source in order:
        result = _attempt(source, audience, timeout)
        if isinstance(result, Token):
            _remember(audience, result, data_dir)
            return result
        failures.append(result)
    raise _best_error(key, op, failures, data_dir)


def clear(source: str | None = None, data_dir=None) -> None:
    """Forget memoised tokens of one source (or all), in this process and on disk."""
    data_dir = _resolve(data_dir)
    with _LOCK:
        for k in [k for k in _MEMO if source is None or k[1] == source]:
            del _MEMO[k]
        if _on_disk(data_dir) and (Path(data_dir) / MEMO_FILE).exists():
            _write_disk(data_dir, [] if source is None else [e for e in _disk(data_dir) if e.get("source") != source])


def _resolve(data_dir) -> Path | None:
    if data_dir is not None:
        return Path(data_dir)
    try:
        return datadir.resolve()
    except datadir.DataDirMissing:
        return None


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sources(resource: str, key: str, op: str, data_dir: Path | None) -> list[str]:
    recorded = (routing(data_dir) or {}).get(key, {})
    order = recorded[op] if op in recorded else ["az", "tier_b", "pac"]
    # pac only mints Power Platform API tokens; sending one elsewhere would be the wrong audience.
    return [s for s in order if s in SOURCES and (s != "pac" or resource.rstrip("/") == apis.POWERPLATFORM_RESOURCE)]


def _attempt(source: str, resource: str, timeout: float) -> Token | _Failure:
    argv = {
        "az": ["az", "account", "get-access-token", "--resource", resource, "--output", "json"],
        "tier_b": ["m365", "util", "accesstoken", "get", "--resource", resource, "--output", "text"],
        "pac": ["pac", "auth", "token"],
    }[source]
    try:
        p = proc.run(argv, timeout=timeout, install_hint=INSTALL[argv[0]])
    except SetupNeeded as exc:
        return _Failure(source, "missing", exc.message, exc.next_actions[0] if exc.next_actions else None)
    except proc.ProcTimeout as exc:
        return _Failure(source, "timeout", exc.message)
    except PpxError as exc:
        return _Failure(source, "error", clean(exc.message))
    if p.code == 0:
        tok = _parse(source, p.stdout)
        return tok or _Failure(source, "error", f"{argv[0]} printed no token")
    text = f"{p.stderr}\n{p.stdout}"
    found = _AADSTS.search(text)
    code = found.group(1) if found else ("not-signed-in" if _NOT_SIGNED_IN.search(text) else "error")
    return _Failure(source, code, clean(text))


def _parse(source: str, stdout: str) -> Token | None:
    if source == "az":
        try:
            body = json.loads(stdout)
            value = body["accessToken"]
            return Token(value, source, float(body.get("expires_on") or decode_claims(value).get("exp", 0)))
        except (ValueError, KeyError, TypeError):
            return None
    found = _JWT.search(stdout)
    if not found:
        return None
    return Token(found.group(0), source, float(decode_claims(found.group(0)).get("exp", 0)))


def clean(text: str) -> str:
    """CLI error text without tokens, e-mail addresses, tenant domains, GUIDs or the trailing trace ids."""
    text = _EMAIL.sub("<user>", scrub_text(text.split("Trace ID")[0]))
    text = _GUID.sub("<id>", _TENANT.sub("<tenant>", text))
    return " ".join(text.split())[:300]


def _rank(f: _Failure) -> int:
    if f.code in _CLASSIFIED:
        return 0
    if f.code.isdigit() and f.code != "65002":
        return 1
    return {"not-signed-in": 2, "error": 3, "timeout": 4, "missing": 5, "65002": 6}[f.code]


def _fix(code: str, source: str, data_dir: Path | None) -> NextAction:
    backend = BACKEND[source]
    login = f"ppx auth login --backend {backend}"
    if source == "tier_b" and code in ("not-signed-in", "65001") and not settings(data_dir).get("m365", {}).get("app_id"):
        return NextAction("register your own app first, then sign in with it", command="ppx auth app-spec")
    if code in ("53000", "53001"):
        return NextAction("run this on your compliant, company-managed work computer; if this is that computer, "
                          "ask IT (docs/ADMIN-REQUESTS.md#device-compliance)")
    if code == "53003":
        return NextAction(f"Conditional Access blocks {backend} for this API: ask IT "
                          "(docs/ADMIN-REQUESTS.md#conditional-access)")
    if code == "65002":
        return NextAction("az cannot get tokens for this API; register your own app", command="ppx auth app-spec")
    if code == "65001" and source == "tier_b":
        return NextAction("sign in again in your own terminal and accept the consent prompt; if consent is blocked, "
                          "see docs/ADMIN-REQUESTS.md#consent", command=login)
    if code == "65001":
        return NextAction("consent is missing: use your own app, or ask IT (docs/ADMIN-REQUESTS.md#consent)",
                          command="ppx auth app-spec")
    if code == "50079":
        return NextAction("register multifactor sign-in at https://mysignins.microsoft.com/security-info, "
                          "then sign in again in your own terminal", command=login)
    if code == "530036":
        return NextAction("sign out, then sign in again in your own terminal with the browser, not a device code",
                          command=login)
    return NextAction("sign in again in your own terminal", command=login)


def _best_error(key: str, op: str, failures: list[_Failure], data_dir: Path | None) -> PpxError:
    if not failures:
        return AuthError(f"no token source left for {key} ({op})", code="no-source", audience=key, source=None,
                         fix=NextAction("see which sign-ins work", command="ppx auth doctor"))
    f = min(failures, key=_rank)
    tried = ", ".join(x.source for x in failures)
    data = {"code": f.code, "audience": key, "source": f.source}
    if f.code in MEANING:
        label = MEANING[f.code] if f.code == "not-signed-in" else f"AADSTS{f.code} {MEANING[f.code]}"
        err = AuthError(f"no {key} token: {f.source}: {label} (tried {tried})", code=f.code, audience=key,
                        source=f.source, fix=_fix(f.code, f.source, data_dir))
        # A policy or consent block may target one client only; signing in to another source is the self-service route.
        if f.code in _CLASSIFIED:
            for alt in (_fix("not-signed-in", x.source, data_dir) for x in failures if x.code == "not-signed-in"):
                if alt not in err.next_actions:
                    err.next_actions.append(alt)
        return err
    if f.code.isdigit():
        return PpxError(f"no {key} token: {f.source}: {f.detail}", data=data,
                        next_actions=[NextAction(f"look up AADSTS{f.code}", url=LOOKUP_URL.format(f.code))])
    if f.code == "missing":
        return SetupNeeded(f"no {key} token: {f.detail} (tried {tried})", data=data,
                           next_actions=[x.install for x in failures if x.install])
    if f.code == "timeout":
        return PpxError(f"no {key} token: {f.detail}", data=data,
                        next_actions=[NextAction("retry with a longer --timeout, or check the network or proxy")])
    return PpxError(f"no {key} token: {f.source}: {f.detail}", data=data)


def _disk(data_dir: Path) -> list[dict]:
    try:
        entries = json.loads(dpapi.unprotect((Path(data_dir) / MEMO_FILE).read_bytes()))
    except (OSError, ValueError):
        return []
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _write_disk(data_dir: Path, entries: list[dict]) -> None:
    path = Path(data_dir) / MEMO_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(dpapi.protect(json.dumps(entries).encode()))
        os.replace(tmp, path)
    except OSError:
        pass  # the memo is only a cache; the next call mints a fresh token


def _on_disk(data_dir: Path | None) -> bool:
    return data_dir is not None and dpapi.available()


def _memo(data_dir: Path | None) -> dict[tuple[str, str], Token]:
    """tokens.bin when it can be protected, else this process's dict.

    The file is the only memo when it exists, so a logout in another process (the user's terminal)
    also ends the long-lived MCP server's use of those tokens.
    """
    if not _on_disk(data_dir):
        return _MEMO
    out = {}
    for e in _disk(data_dir):
        try:
            out[(str(e["resource"]), str(e["source"]))] = Token(str(e["value"]), str(e["source"]),
                                                                float(e["expires_on"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _remember(resource: str, tok: Token, data_dir: Path | None) -> None:
    with _LOCK:
        memo = _memo(data_dir)
        memo[(resource, tok.source)] = tok
        if _on_disk(data_dir):
            now = time.time()
            _write_disk(data_dir, [{"resource": r, "source": s, "value": t.value, "expires_on": t.expires_on}
                                   for (r, s), t in memo.items() if t.expires_on > now])
