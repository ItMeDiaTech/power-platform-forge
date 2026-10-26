"""Structural check of a custom connector's OpenAPI definition, offline and stdlib-only.

Rules and their sources:
- Learn, "Create a custom connector from an OpenAPI definition" (ms.date 2026-06-03): the definition
  must be OpenAPI 2.0 (3.0 is not supported) and under 1 MB; with several security definitions the
  connector uses the first; OAuth client credentials are not supported; header names must be ASCII.
- Learn, "Custom connector FAQ" (ms.date 2025-03-13): at most 256 operations per definition (the
  Logic Apps limit).
- OpenAPI 2.0 specification (spec.openapis.org/oas/v2.0.html): info.title and info.version, paths
  and each operation's responses are required; basePath and path keys start with "/"; operationId is
  unique; every {name} in a path template has a path parameter, and path parameters are required.
- Learn, "Create a custom connector with the CLI" (paconn, ms.date 2025-05-28): the API properties file
  holds a "properties" object; its scriptOperations names operations of the definition.
"""

from __future__ import annotations

import re

MAX_BYTES = 1024 * 1024
MAX_OPERATIONS = 256
METHODS = ("get", "put", "post", "delete", "options", "head", "patch")
_TEMPLATE = re.compile(r"\{([^}/]+)\}")


def _resolve(spec: dict, node, errors: list[str], where: str):
    """Follow a local $ref (#/parameters/x); a ref that does not resolve is an error."""
    ref = node.get("$ref") if isinstance(node, dict) else None
    if not isinstance(ref, str):
        return node
    if not ref.startswith("#/"):
        errors.append(f"{where}: $ref {ref!r} points outside the file; a connector definition is one file")
        return {}
    target = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or part not in target:
            errors.append(f"{where}: $ref {ref!r} does not resolve")
            return {}
        target = target[part]
    return target


def _refs(node, path: str = "#"):
    """Every $ref value in the document, with where it was found."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                yield path, v
            else:
                yield from _refs(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _refs(v, f"{path}/{i}")


def _array(value, where: str, errors: list[str]) -> list:
    """A `parameters` value: an array, or [] with an error for anything else a hand edit can leave."""
    if value is None or isinstance(value, list):
        return value or []
    errors.append(f"{where}: parameters must be an array")
    return []


def _operation(spec: dict, path: str, method: str, op: dict, shared: list, errors: list[str]) -> None:
    where = f"{method.upper()} {path}"
    if not isinstance(op, dict):
        errors.append(f"{where}: an operation must be an object")
        return
    if not isinstance(op.get("responses"), dict) or not op["responses"]:
        errors.append(f"{where}: responses are required (at least one status code)")
    params = {}
    for raw in shared + _array(op.get("parameters"), where, errors):
        p = _resolve(spec, raw, [], where)  # an unresolved $ref is reported once, by the whole-document pass
        if not isinstance(p, dict) or not p.get("name"):
            continue
        if not isinstance(p["name"], str):
            errors.append(f"{where}: parameter name {p['name']!r} must be text")
            continue
        params[(str(p.get("in")), p["name"])] = p  # operation-level parameters override path-level ones
    for (place, name), p in params.items():
        if place == "path" and p.get("required") is not True:
            errors.append(f"{where}: path parameter {name!r} must have required: true")
        if place == "header" and not name.isascii():
            errors.append(f"{where}: header name {name!r} is not ASCII")
    for name in _TEMPLATE.findall(path):
        if ("path", name) not in params:
            errors.append(f"{where}: {{{name}}} in the path has no path parameter named {name!r}")


def check(spec, size: int) -> dict:
    """{errors, warnings, operations}: errors stop a deploy, warnings do not."""
    errors: list[str] = []
    warnings: list[str] = []
    if size >= MAX_BYTES:
        errors.append(f"the definition is {size:,} bytes; custom connectors need it under 1 MB")
    if not isinstance(spec, dict):
        return {"errors": errors + ["the definition must be a JSON object"], "warnings": warnings, "operations": 0,
                "operation_ids": []}
    if "openapi" in spec:
        errors.append(f"OpenAPI {spec.get('openapi')} is not supported; custom connectors take OpenAPI 2.0 (swagger) only")
        return {"errors": errors, "warnings": warnings, "operations": 0, "operation_ids": []}
    if spec.get("swagger") != "2.0":
        errors.append('"swagger": "2.0" is required')
    info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
    for key in ("title", "version"):
        if not str(info.get(key) or "").strip():
            errors.append(f"info.{key} is required")
    if not str(spec.get("host") or "").strip():
        errors.append("host is required: the connector calls host + basePath")
    elif "://" in str(spec["host"]) or "/" in str(spec["host"]):
        errors.append(f"host {spec['host']!r} must be a host name only (put the path in basePath)")
    if "basePath" in spec and not str(spec["basePath"]).startswith("/"):
        errors.append(f"basePath {spec['basePath']!r} must start with /")

    security = spec.get("securityDefinitions") or {}
    if isinstance(security, dict):
        if len(security) > 1:
            warnings.append(f"{len(security)} security definitions: the connector uses only the first "
                            f"({next(iter(security))!r})")
        for name, scheme in security.items():
            if isinstance(scheme, dict) and scheme.get("type") == "oauth2" and scheme.get("flow") == "application":
                errors.append(f"security definition {name!r} uses the OAuth client credentials flow, "
                              "which custom connectors do not support")

    paths = spec.get("paths")
    ids: dict[str, str] = {}
    count = 0
    if not isinstance(paths, dict):
        errors.append("paths is required (an object)")
        paths = {}
    for path, item in paths.items():
        if str(path).startswith("x-"):
            continue  # vendor extensions are allowed in the Paths Object
        if not str(path).startswith("/"):
            errors.append(f"path {path!r} must start with /")
        if not isinstance(item, dict):
            errors.append(f"path {path!r} must be an object")
            continue
        shared = _array(item.get("parameters"), f"path {path}", errors)
        for method in METHODS:
            if method not in item:
                continue
            count += 1
            op = item[method]
            _operation(spec, path, method, op, shared, errors)
            op_id = op.get("operationId") if isinstance(op, dict) else None
            where = f"{method.upper()} {path}"
            if not op_id:
                warnings.append(f"{where} has no operationId; Power Platform names the action by it")
            elif not isinstance(op_id, str):
                errors.append(f"{where}: operationId {op_id!r} must be text")
            elif op_id in ids:
                errors.append(f"operationId {op_id!r} is used by {ids[op_id]} and {where}; it must be unique")
            else:
                ids[op_id] = where
    for where, ref in _refs(spec):
        _resolve(spec, {"$ref": ref}, errors, where)
    if count == 0:
        warnings.append("no operations yet: add at least one path with an operation before deploying")
    if count > MAX_OPERATIONS:
        warnings.append(f"{count} operations; Logic Apps accepts at most {MAX_OPERATIONS} per definition")
    return {"errors": errors, "warnings": warnings, "operations": count, "operation_ids": list(ids)}


def check_properties(props, operation_ids: list[str]) -> dict:
    """The API properties file: a "properties" object, and script operations that exist in the definition."""
    errors: list[str] = []
    warnings: list[str] = []
    inner = props.get("properties") if isinstance(props, dict) else None
    if not isinstance(inner, dict):
        errors.append('the API properties file needs a "properties" object')
        return {"errors": errors, "warnings": warnings}
    if "connectionParameters" in inner and not isinstance(inner["connectionParameters"], dict):
        errors.append("properties.connectionParameters must be an object")
    script_ops = inner.get("scriptOperations") or []
    if not isinstance(script_ops, list):
        errors.append("properties.scriptOperations must be an array of operationIds")
        script_ops = []
    unknown = [o for o in script_ops if o not in operation_ids]
    if unknown:
        warnings.append(f"scriptOperations names operations the definition lacks: {', '.join(map(str, unknown[:10]))}")
    return {"errors": errors, "warnings": warnings}
