"""Shared constants of ppx probe and the helpers that build, settle and summarise its check records."""

from __future__ import annotations

import hashlib
import json
import re
import time

from ppx import auth, cmd_auth, http, proc, resolve
from ppx.errors import PpxError, SetupNeeded

ORDER = ("az", "tier_b")  # read and write fallback order; pac mints Power Platform API tokens only
SLOW = 3  # definitions, canvas downloads and PowerShell cold starts get this many --timeout spans
CELL_MAX = 32  # matrix cells stay short so the envelope fits in 4 KB
KINDS = {"report": ("reports", "PBIR"), "model": ("semanticModels", "TMDL")}
_YAML_KEY = re.compile(r"""^[\s-]*('[^']*'|"[^"]*"|[A-Za-z_][\w .()-]*?)\s*:(?:\s|$)""")
_BLOCK = re.compile(r"\s*[|>][-+0-9]*\s*(?:#.*)?$")  # a `key: |-` value: the deeper lines are text, not keys
_AADSTS = re.compile(r"AADSTS(\d+)")
_SIGN_IN = re.compile(r"log ?in|sign ?in|auth create|no profiles", re.I)
_GUID = auth._GUID


def _ok(summary: dict, raw=None, value=None) -> dict:
    return {"status": "ok", "summary": summary, "raw": raw, "value": value}


def _run(fn, timeout: float) -> dict:
    start = time.monotonic()
    done = cmd_auth._parallel({"check": fn}, timeout).get("check")
    if done is None:
        out = {"status": "timeout", "code": "timeout", "message": f"no answer within {timeout:g} s"}
    elif isinstance(done, BaseException):
        out = _from_error(done)
    else:
        out = done
    out["elapsed_ms"] = round((time.monotonic() - start) * 1000)
    return out


def _from_error(exc: BaseException) -> dict:
    if isinstance(exc, http.HttpError):
        refused = exc.status in (401, 403) or "read-only" in exc.message.lower()
        return {"status": "blocked" if refused else "error", "code": f"{exc.status} {exc.code or ''}".strip(),
                "message": exc.message}
    if isinstance(exc, proc.ProcTimeout):
        return {"status": "timeout", "code": "timeout", "message": exc.message}
    if isinstance(exc, SetupNeeded):
        return {"status": "missing", "code": "missing", "message": exc.message}
    if isinstance(exc, PpxError):
        return {"status": "error", "code": exc.data.get("code") or ("error" if type(exc) is PpxError
                                                                    else type(exc).__name__),
                "message": exc.message}
    return {"status": "error", "code": type(exc).__name__, "message": auth.clean(str(exc))}


def _failure(f) -> dict:
    """An auth._Failure (one source could not mint a token) as an outcome."""
    return {"status": {"missing": "missing", "timeout": "timeout", "error": "error"}.get(f.code, "blocked"),
            "code": f"AADSTS{f.code}" if f.code.isdigit() else f.code, "message": f.detail, "token": True}


def _cli_failure(p: proc.Proc) -> dict:
    # pac's "Connected as/to..." banner names the user and environment and says nothing about the failure.
    text = "\n".join(line for line in f"{p.stderr}\n{p.stdout}".splitlines()
                     if not line.lstrip().startswith("Connected "))
    found = _AADSTS.search(text)
    code = f"AADSTS{found.group(1)}" if found else ("not-signed-in" if _SIGN_IN.search(text) else f"exit {p.code}")
    return {"status": "error" if code.startswith("exit") else "blocked", "code": code, "message": auth.clean(text),
            "raw": {"exit": p.code, "stdout": p.stdout, "stderr": p.stderr}}


def _settle(attempts: list[dict], summarise) -> dict:
    """One check's outcome from its per-source attempts: ok when any source worked."""
    via = "; ".join(f"{a['source']} {_cell(a)}" for a in attempts)
    ok = [a for a in attempts if a["status"] == "ok"]
    if ok:
        return {"status": "ok", "summary": {**summarise(ok[0]["value"]), "via": via},
                "raw": {"attempts": attempts}, "value": ok[0]["value"]}
    worst = next((a for a in attempts if not a.get("token")), attempts[0])  # an API refusal says more
    return {"status": worst["status"], "code": worst.get("code"), "message": worst.get("message"),
            "summary": {"via": via}, "raw": {"attempts": attempts}}


def _cell(rec: dict) -> str:
    status, code = rec["status"], rec.get("code")
    return (status if status in ("ok", "skipped") or not code or code == status else f"{status}:{code}")[:CELL_MAX]


def _pick(kind: str, wanted: str | None, items: list | None) -> dict | None:
    """The item `wanted` names (an id or a name), the first item when nothing was asked for, else None."""
    if not wanted:
        return items[0] if items else None
    if _GUID.fullmatch(wanted.strip()):
        return {"id": wanted.strip()}
    try:
        return resolve.pick(kind, wanted, items or [])
    except PpxError:
        return None


def _fields(body) -> dict:
    if isinstance(body, dict) and isinstance(body.get("value"), list):
        body = body["value"]
    if isinstance(body, list):
        first = body[0] if body and isinstance(body[0], dict) else {}
        out = {"count": len(body), "fields": sorted(first)}
        types = sorted({str(i["type"]) for i in body if isinstance(i, dict) and "type" in i})
        return {**out, "types": types} if types else out
    return {"fields": sorted(body)} if isinstance(body, dict) else {"type": type(body).__name__}


def _tool(row: dict) -> str:
    if "reachable" in row:
        return f"{row['name']} {'reachable' if row['reachable'] else 'unreachable'}"
    return f"{row['name']} {row.get('version', '')}".strip() if row.get("found") else f"{row['name']} missing"


def _dax_summary(body) -> dict:
    result = ((body or {}).get("results") or [{}])[0]
    table = (result.get("tables") or [{}])[0]
    error = (body or {}).get("error") or result.get("error") or table.get("error")
    return {"rows": len(table.get("rows") or []), "error": error.get("code") if isinstance(error, dict) else None}


def _definition_summary(pulled: dict) -> dict:
    definition = pulled["definition"]
    paths = [str(p.get("path", "")) for p in definition.get("parts") or []]
    return {"format": definition.get("format") or _format_of(paths), "parts": len(paths), "lro": pulled["lro"],
            "roots": sorted({p.split("/")[0] + ("/" if "/" in p else "") for p in paths})}


def _format_of(paths: list[str]) -> str:
    if any(p.endswith(".tmdl") for p in paths):
        return "TMDL"
    if "model.bim" in paths:
        return "TMSL"
    if "report.json" in paths:
        return "PBIR-Legacy"
    return "PBIR" if any(p.startswith("definition/") for p in paths) else "unknown"


def _digest(definition: dict) -> str:
    h = hashlib.sha256()
    for part in sorted(definition.get("parts") or [], key=lambda p: str(p.get("path"))):
        h.update(f"{part.get('path')}\0{part.get('payload')}\0".encode())
    return h.hexdigest()


def _json(text: str):
    """The first JSON value in a CLI's output, skipping banner lines before it."""
    decoder = json.JSONDecoder()
    for m in re.finditer(r"[\[{]", text):
        try:
            return decoder.raw_decode(text, m.start())[0]
        except ValueError:
            continue
    raise PpxError("printed no JSON", data={"code": "no-json"})


def _key_paths(obj, depth: int, prefix: str = "") -> list[str]:
    if depth == 0 or not isinstance(obj, dict):
        return []
    out = []
    for k, v in obj.items():
        out += [prefix + k] + _key_paths(v, depth - 1, f"{prefix}{k}.")
    return out


def _yaml_hits(text: str, needles: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(key path, what) for each line of a .pa.yaml file that holds one of the needles."""
    # shortcut: key paths come from indentation, not a YAML parser (stdlib has none); enough to say where
    # a value sits, upgrade if real apps put hits under the wrong key.
    stack: list[tuple[int, str]] = []
    hits = set()
    block = None  # indent of the key whose block scalar (a multi-line formula) is being read
    for line in text.splitlines():
        if block is not None and (not line.strip() or len(line) - len(line.lstrip(" ")) > block):
            m = None
        else:
            block, m = None, _YAML_KEY.match(line)
        if m:
            indent = len(line) - len(line.lstrip(" -"))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            stack.append((indent, m.group(1).strip("'\"")))
            if _BLOCK.match(line[m.end():]):
                block = indent
        low = line.lower()
        hits.update(("/".join(k for _, k in stack), what) for needle, what in needles if needle in low)
    return sorted(hits)


def _shape(message: str) -> str:
    return re.sub(r"'[^']*'|\"[^\"]*\"", "'<q>'", auth.clean(str(message)))[:200]


def _detail(rec: dict) -> str:
    bits = []
    for k, v in (rec.get("summary") or {}).items():
        if v in (None, "", [], {}):
            continue
        if isinstance(v, (list, tuple)):
            v = ", ".join(json.dumps(x, separators=(",", ":")) if isinstance(x, dict) else str(x) for x in v)
        elif isinstance(v, dict):
            v = json.dumps(v, separators=(",", ":"))
        bits.append(f"{k}: {v}")
    if rec.get("message") and rec["status"] != "ok":
        bits.append(f"message: {_shape(rec['message'])}")
    return "; ".join(bits)


def _md(value) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")
