"""connector scaffold|validate|deploy: custom connector folders over `pac connector init|create|update`.

pac (Learn, connector command group, ms.date 2026-02-25): `init` writes the API properties file
(and, on request, a script and a settings file) into --outputDirectory but no OpenAPI definition, so
scaffold adds a skeleton; `create` and `update` take --api-definition-file, --api-properties-file,
--icon-file, --script-file, --solution-unique-name and --environment; `update` and `download` need
--connector-id, a GUID. pac does not name the files it writes, so the folder's files are recognised by
content: the definition is the JSON with a "swagger" (or "openapi") key, the properties file the JSON
with a "properties" object. An update first downloads the current connector into
<data>/backups/connector/<id>/<UTC time>/, which is what makes it reversible.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from ppx import pac
from ppx.connector import openapi
from ppx.data.dataverse import is_guid
from ppx.envelope import NextAction
from ppx.errors import PpxError, UsageError
from ppx.redact import scrub_text
from ppx.solution.common import env_args

DEFINITION = "apiDefinition.swagger.json"
_GUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


def _read(path: Path):
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError(f"cannot read {path}: {exc}") from exc
    try:
        return json.loads(text), len(text.encode())
    except ValueError as exc:
        raise UsageError(f"{path} is not valid JSON: {exc}") from exc


def _one(paths: list[Path], what: str, folder: Path, preferred: str) -> Path | None:
    if len(paths) > 1:
        named = [p for p in paths if p.name.lower() == preferred.lower()]
        if len(named) == 1:
            return named[0]
        raise UsageError(f"{folder} holds several {what} files ({', '.join(p.name for p in paths)}); keep one")
    return paths[0] if paths else None


def find_files(folder: Path) -> dict:
    """{definition, properties, icon, script} paths in `folder`, or in its one subfolder that holds them
    (a downloaded connector may sit one level down)."""
    if not folder.is_dir():
        raise UsageError(f"no folder {folder}")
    jsons = sorted(folder.glob("*.json")) or sorted(folder.glob("*/*.json"))
    found: dict[str, list[Path]] = {"definition": [], "properties": []}
    for path in jsons:
        try:
            body = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if isinstance(body, dict) and ("swagger" in body or "openapi" in body):
            found["definition"].append(path)
        elif isinstance(body, dict) and isinstance(body.get("properties"), dict):
            found["properties"].append(path)
    home = jsons[0].parent if jsons else folder
    definition = _one(found["definition"], "OpenAPI definition", folder, DEFINITION)
    if definition is None:
        raise UsageError(f"{folder} has no OpenAPI definition (a JSON file with \"swagger\": \"2.0\")",
                         next_actions=[NextAction("start one", command=f'ppx connector scaffold --folder "{folder}" '
                                                                       '--title <title> --host <host>')])
    return {"definition": definition,
            "properties": _one(found["properties"], "API properties", folder, "apiProperties.json"),
            "icon": _one(sorted(home.glob("*.png")), "icon", folder, "icon.png"),
            "script": _one(sorted(home.glob("*.csx")), "script", folder, "script.csx")}


def _check(files: dict) -> dict:
    spec, size = _read(files["definition"])
    result = openapi.check(spec, size)
    if files["properties"] is None:
        result["errors"].append("no API properties file (a JSON file with a \"properties\" object); "
                                "`ppx connector scaffold` or `pac connector init` writes one")
    else:
        props, _ = _read(files["properties"])
        extra = openapi.check_properties(props, result["operation_ids"])
        result["errors"] += extra["errors"]
        result["warnings"] += extra["warnings"]
    info = spec.get("info") if isinstance(spec, dict) else None
    result["title"] = info.get("title") if isinstance(info, dict) else None
    return result


def _summary(folder: Path, files: dict, result: dict) -> dict:
    return {"folder": str(folder), "title": result["title"], "operations": result["operations"],
            "files": {k: v.name for k, v in files.items() if v is not None},
            "errors": result["errors"], "warnings": result["warnings"]}


def validate(ctx, args) -> dict:
    folder = Path(args.folder).expanduser().resolve()
    files = find_files(folder)
    out = _summary(folder, files, _check(files))
    if out["errors"]:
        raise PpxError(f"{len(out['errors'])} errors in the connector in {folder}", data=out)
    return out


def _host(value: str) -> tuple[str, str]:
    """(host[:port], basePath) from a host name or an https:// base URL; both forms get the same checks."""
    text = value.strip()
    try:
        parts = urllib.parse.urlsplit(text if "://" in text else "https://" + text)
    except ValueError as exc:
        raise UsageError(f"--host is not a valid host name or URL: {exc}") from None
    if "@" in parts.netloc:  # checked before anything echoes the value
        raise UsageError("--host must not carry a user name or password; the connection parameters hold credentials")
    try:
        port = parts.port
    except ValueError as exc:
        raise UsageError(f"--host is not a valid host name or URL: {exc}") from None
    if (parts.scheme != "https" or not parts.hostname or not re.fullmatch(r"[a-z0-9_.-]+", parts.hostname)
            or re.search(r"\s", text) or parts.query or parts.fragment):
        raise UsageError(f"--host {value!r}: give a host name or an https:// base URL")
    return parts.hostname + (f":{port}" if port else ""), parts.path.rstrip("/") or "/"


def scaffold(ctx, args) -> dict:
    folder = Path(args.folder).expanduser().resolve()
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise UsageError(f"{folder} exists and is not an empty folder; ppx never writes over connector files")
    host, base_path = _host(args.host)
    ctx.changes.add(f"write the API properties file ({args.auth} connection) with pac connector init, and {DEFINITION} "
                    f"for {host}{'' if base_path == '/' else base_path}", str(folder),
                    undo=f"delete the folder {folder}")
    if ctx.dry_run:
        return {"folder": str(folder), "host": host, "basePath": base_path}
    folder.mkdir(parents=True, exist_ok=True)
    pac.run(["connector", "init", "--outputDirectory", str(folder), "--connection-template", args.auth,
             *(["--generate-script-file"] if args.script else [])], timeout=ctx.timeout)
    skeleton = {"swagger": "2.0", "info": {"title": args.title, "description": "", "version": "1.0"},
                "host": host, "basePath": base_path, "schemes": ["https"], "consumes": ["application/json"],
                "produces": ["application/json"], "paths": {}, "definitions": {}, "parameters": {}, "responses": {}}
    (folder / DEFINITION).write_text(json.dumps(skeleton, indent=2) + "\n", encoding="utf-8")
    ctx.next("add the API's operations to the definition, then check the folder",
             command=f'ppx connector validate --folder "{folder}"')
    return {"folder": str(folder), "host": host, "basePath": base_path,
            "files": sorted(p.name for p in folder.iterdir() if p.is_file())}


def _tail(text: str) -> list[str]:
    return [scrub_text(line)[:200] for line in text.strip().splitlines()[-3:]]


def deploy(ctx, args) -> dict:
    folder = Path(args.folder).expanduser().resolve()
    files = find_files(folder)
    result = _check(files)
    if result["errors"]:
        raise PpxError(f"not deployed: {len(result['errors'])} errors in the connector in {folder}",
                       data=_summary(folder, files, result),
                       next_actions=[NextAction("fix them and check again",
                                                command=f'ppx connector validate --folder "{folder}"')])
    if args.connector_id and not is_guid(args.connector_id):
        raise UsageError(f"--connector-id {args.connector_id!r} is not a GUID (pac needs the connector's id)")
    env = env_args(ctx)
    target = f"the Dataverse environment {env[1] if env else '(the one pac auth selected)'}"
    title = result["title"] or folder.name
    flags = ["--api-definition-file", str(files["definition"]), "--api-properties-file", str(files["properties"]),
             *(["--icon-file", str(files["icon"])] if files["icon"] else []),
             *(["--script-file", str(files["script"])] if files["script"] else []),
             *(["--solution-unique-name", args.solution] if args.solution else []), *env]
    for warning in result["warnings"]:
        ctx.warn(warning)
    if args.connector_id:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = ctx.need_data_dir() / "backups" / "connector" / args.connector_id.lower() / stamp
        # pac connector download fetches only the definition and the API properties, so a new icon or
        # script replaces the live one with no copy to restore.
        unsaved = [k for k in ("icon", "script") if files[k]]
        ctx.changes.add(f"update custom connector {args.connector_id} ({title}) from {folder}"
                        + (f"; its current {' and '.join(unsaved)} {'are' if len(unsaved) > 1 else 'is'} replaced "
                           "without a backup" if unsaved else ""),
                        target, reversible=not unsaved, backup=str(backup),
                        undo=f'ppx connector deploy --folder "{backup}" --connector-id {args.connector_id}')
        if ctx.dry_run:
            return {"action": "update", "connector_id": args.connector_id, "title": title}
        ctx.changes.require_yes(ctx.yes)
        backup.mkdir(parents=True, exist_ok=True)
        pac.run(["connector", "download", "--connector-id", args.connector_id, "--outputDirectory", str(backup),
                 *env], timeout=ctx.timeout)
        if not any(backup.rglob("*.json")):
            raise PpxError(f"pac connector download wrote no definition to {backup}; the connector was not updated")
        p = pac.run(["connector", "update", "--connector-id", args.connector_id, *flags], timeout=ctx.timeout)
        return {"action": "updated", "connector_id": args.connector_id, "title": title, "backup": str(backup),
                "output": _tail(p.stdout)}
    ctx.changes.add(f"create custom connector {title} from {folder}"
                    + (f" in solution {args.solution}" if args.solution else ""), target,
                    undo="delete it in make.powerapps.com > Custom connectors (or from its solution)")
    if ctx.dry_run:
        return {"action": "create", "title": title}
    p = pac.run(["connector", "create", *flags], timeout=ctx.timeout)
    ids = set(_GUID.findall(p.stdout))
    out = {"action": "created", "title": title, "output": _tail(p.stdout)}
    if len(ids) == 1:
        out["connector_id"] = ids.pop()
        ctx.next("later changes update this connector", command=f'ppx connector deploy --folder "{folder}" '
                                                                 f'--connector-id {out["connector_id"]}')
    return out
