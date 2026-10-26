"""ppx raw: call any documented endpoint of one API with the user's token."""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

from ppx import apis, exports, http
from ppx.envelope import NextAction
from ppx.errors import UsageError

_REFRESH = re.compile(r"/datasets/[^/]+/refreshes/?$", re.I)


def _body(text: str | None):
    if text is None:
        return None
    if text.startswith("@"):
        path = Path(text[1:])
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise UsageError(f"cannot read --body file {path}: {exc}") from exc
    try:
        return json.loads(text)
    except ValueError as exc:
        raise UsageError(f"--body is not valid JSON: {exc}") from exc


def run(ctx, args) -> dict:
    api = apis.by_name(args.api, ctx.profile, args.site)
    url = api.url(args.path)
    body = _body(args.body)
    method = args.method.upper()
    if api.name == "pbi" and method == "POST" and _REFRESH.search(urllib.parse.urlsplit(url).path):
        # Every refresh goes through one gate that counts today's (8 a day on shared capacity); raw would skip it.
        raise UsageError("start refreshes with ppx pbi refresh, which counts today's refreshes and waits for a running one",
                         next_actions=[NextAction("refresh the model", command="ppx pbi refresh --model <model> "
                                                                               "--workspace <workspace>")])
    if method != "GET":
        ctx.changes.add(f"{method} {args.path}", api.name, reversible=method != "DELETE")
        ctx.changes.require_yes(ctx.yes)
        if ctx.dry_run:
            return {"method": method, "url": url}
    resp = http.request(method, url, audience=api.resource, op="read" if method == "GET" else "write",
                        json_body=body, timeout=ctx.timeout)
    try:
        value = resp.json()
    except ValueError:
        value = resp.body[:2000].decode("utf-8", "replace")
    out = {"status": resp.status}
    if resp.status == 202 and resp.headers.get("location"):
        out["operation"] = resp.headers["location"]
    if value is not None:
        out.update(exports.inline_or_file(ctx, f"raw-{api.name}", value))
    return out
