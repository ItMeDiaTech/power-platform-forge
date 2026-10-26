"""ppx raw: call any documented endpoint of one API with the user's token."""

from __future__ import annotations

import json
from pathlib import Path

from ppx import apis, exports, http
from ppx.errors import UsageError


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
