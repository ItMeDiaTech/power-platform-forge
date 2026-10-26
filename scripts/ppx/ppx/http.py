"""HTTPS calls to Power BI, Fabric, Dataverse, Graph and SharePoint.

One function owns retries (429/503 with Retry-After), paging (@odata.nextLink and Fabric
continuation tokens), long-running operations (202 + Location) and the one retry with the
next recorded token source when a token turns out to be read-only.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

from ppx.envelope import NextAction
from ppx.errors import PpxError
from ppx.redact import scrub_text

MAX_RETRIES = 3
MAX_WAIT_SECONDS = 60
USER_AGENT = "ppx/0.2 (power-platform-forge)"


@dataclass
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def json(self):
        if not self.body.strip():
            return None
        return json.loads(self.body.decode("utf-8-sig"))


class Transport:
    def send(self, method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> Response:
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return Response(resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read())
        except urllib.error.HTTPError as exc:
            return Response(exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, exc.read() or b"")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PpxError(f"{method} {_short(url)} failed: {getattr(exc, 'reason', exc)}",
                           next_actions=[NextAction("check the network or proxy (HTTPS_PROXY)")]) from exc


transport: Transport = Transport()
sleep = time.sleep


class HttpError(PpxError):
    def __init__(self, method: str, url: str, resp: Response):
        code, message = _error_parts(resp)
        self.status, self.code = resp.status, code
        request_id = resp.headers.get("x-ms-request-id") or resp.headers.get("requestid") or resp.headers.get("request-id")
        text = f"{method} {_short(url)} returned {resp.status}" + (f" {code}" if code else "") + (f": {message}" if message else "")
        super().__init__(scrub_text(text), data={"status": resp.status, "error_code": code, "request_id": request_id})


def _short(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.netloc}{parts.path}"


def _error_parts(resp: Response) -> tuple[str | None, str | None]:
    try:
        body = resp.json()
    except ValueError:
        return None, resp.body[:300].decode("utf-8", "replace") or None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            inner = err.get("innererror") or err.get("pbi.error") or {}
            msg = err.get("message") or (inner.get("message") if isinstance(inner, dict) else None)
            return err.get("code"), (msg or "")[:500] or None
        if "errorCode" in body:
            return body.get("errorCode"), (body.get("message") or "")[:500] or None
        if "message" in body:
            return body.get("code"), str(body["message"])[:500]
    return None, None


def _retry_after(resp: Response, attempt: int) -> float:
    value = resp.headers.get("retry-after")
    if value:
        try:
            return min(float(value), MAX_WAIT_SECONDS)
        except ValueError:
            try:
                return min(max(parsedate_to_datetime(value).timestamp() - time.time(), 1), MAX_WAIT_SECONDS)
            except (TypeError, ValueError):
                pass
    return min(2 ** attempt, MAX_WAIT_SECONDS)


def _readonly_denial(resp: Response) -> bool:
    if resp.status not in (401, 403):
        return False
    text = resp.body[:2000].decode("utf-8", "replace").lower()
    return any(s in text for s in ("read-only", "readonly", "insufficientdelegatedpermissions", "insufficient privileges",
                                   "unauthorized", "invalidaudience", "tokenexpired", "powerbinotauthorized"))


def _default_token(audience: str, op: str, exclude: tuple[str, ...] = ()):
    from ppx import auth  # imported lazily: auth spawns az/m365 and is not needed for local verbs

    return auth.token(audience, op, exclude=exclude)


def request(method: str, url: str, *, audience: str, op: str = "read", json_body=None, body: bytes | None = None,
            params: dict | None = None, headers: dict | None = None, timeout: float = 60,
            token_provider=None) -> Response:
    """Send one request and return the final Response (after retries); raise HttpError on 4xx/5xx."""
    provider = token_provider or _default_token
    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, safe="$,'():")
    tok = provider(audience, op)
    tried = (tok.source,)
    payload = json.dumps(json_body).encode() if json_body is not None else body
    attempt = 0
    while True:
        hdrs = {"Authorization": f"Bearer {tok.value}", "Accept": "application/json", "User-Agent": USER_AGENT}
        if json_body is not None:
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        resp = transport.send(method, url, hdrs, payload, timeout)
        if resp.status in (429, 503) and attempt < MAX_RETRIES:
            sleep(_retry_after(resp, attempt))
            attempt += 1
            continue
        if _readonly_denial(resp):
            try:
                tok = provider(audience, op, exclude=tried)
            except PpxError:
                raise HttpError(method, url, resp) from None
            tried += (tok.source,)
            continue
        if resp.status >= 400:
            raise HttpError(method, url, resp)
        return resp


def call(method: str, url: str, **kwargs):
    """Like request(), returning the parsed JSON body (None for an empty body)."""
    return request(method, url, **kwargs).json()


def paged(url: str, *, audience: str, top: int | None = None, params: dict | None = None,
          timeout: float = 60, token_provider=None) -> tuple[list, bool]:
    """GET a collection, following @odata.nextLink and Fabric continuation tokens.

    Returns (items, more) where `more` is True when `top` stopped the walk early.
    """
    items: list = []
    next_url: str | None = url
    next_params = params
    while next_url:
        body = call("GET", next_url, audience=audience, params=next_params, timeout=timeout,
                    token_provider=token_provider) or {}
        page = body.get("value", body.get("data", [])) if isinstance(body, dict) else body
        items.extend(page or [])
        if top is not None and len(items) >= top:
            nxt = _next_link(body, url)
            return items[:top], bool(nxt) or len(items) > top
        next_url, next_params = _next_link(body, url), None
    return items, False


def _next_link(body, base_url: str) -> str | None:
    if not isinstance(body, dict):
        return None
    link = body.get("@odata.nextLink") or body.get("continuationUri")
    if link:
        return link
    token = body.get("continuationToken")
    if token:
        parts = urllib.parse.urlsplit(base_url)
        query = urllib.parse.parse_qsl(parts.query)
        query = [(k, v) for k, v in query if k != "continuationToken"] + [("continuationToken", token)]
        return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))
    return None


def wait_operation(resp: Response, *, audience: str, timeout: float = 600, token_provider=None,
                   fetch_result: bool = False):
    """Follow a 202 long-running operation to its end and return the final body (or its result)."""
    if resp.status != 202:
        return resp.json()
    location = resp.headers.get("location")
    if not location:
        return resp.json()
    deadline = time.monotonic() + timeout
    delay = _retry_after(resp, 0)
    while True:
        if time.monotonic() + delay > deadline:
            raise PpxError(f"operation did not finish within {timeout:g} s",
                           next_actions=[NextAction("check it later", url=location)], data={"operation": location})
        sleep(delay)
        poll = request("GET", location, audience=audience, token_provider=token_provider)
        body = poll.json() or {}
        status = str(body.get("status", "")).lower()
        if status in ("succeeded", "completed"):
            if fetch_result:
                result_url = poll.headers.get("location") or location.rstrip("/") + "/result"
                return call("GET", result_url, audience=audience, token_provider=token_provider)
            return body
        if status in ("failed", "cancelled", "canceled"):
            err = body.get("error") or {}
            raise PpxError(f"operation {status}: {err.get('message') or err.get('errorCode') or 'no detail'}",
                           data={"operation": body})
        delay = _retry_after(poll, 0)
