"""PostToolUse hook (advisory): check an edited PBIR file against its vendored JSON schema.

Runs after Write/Edit, filtered by the handler's `if: Edit(//**/*.Report/definition/**/*.json)` (Edit rules cover
every file-editing tool; `//**` is any drive); the path check below is the backstop. Only files whose `$schema`
URL is vendored under templates/pbir-schemas are checked. A violation exits 2 so the schema errors reach Claude;
the edit has already happened, so this is advice, not a gate. Stdlib only.

The vendored schemas are JSON Schema draft-07 and use only the keywords this validator implements:
$ref (relative files and #/ pointers), type, properties, required, additionalProperties, items, enum,
const, anyOf, oneOf, pattern, minLength and maxLength. Other keywords are ignored, so a refreshed schema
that adds one is checked structurally only; the local tests assert the vendored set stays within it.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PREFIX = "https://developer.microsoft.com/json-schemas/"
MAX_ERRORS = 5
_PBIR = re.compile(r"\.Report[\\/]definition[\\/].+\.json$", re.I)
SUPPORTED = {"$ref", "$schema", "$id", "definitions", "type", "properties", "required", "additionalProperties",
             "items", "enum", "const", "anyOf", "oneOf", "pattern", "minLength", "maxLength", "title", "description",
             "default", "examples", "markdownDescription", "deprecated", "$comment"}


def schemas_root() -> Path:
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    return Path(root if root else Path(__file__).resolve().parents[2]) / "templates" / "pbir-schemas"


class Validator:
    """Draft-07 subset over the vendored files; refs resolve as paths relative to the schema file."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.files: dict[Path, dict] = {}

    def load(self, path: Path) -> dict:
        path = path.resolve()
        if self.root not in path.parents:
            raise ValueError(f"schema reference outside {self.root}: {path}")
        if path not in self.files:
            self.files[path] = json.loads(path.read_text(encoding="utf-8-sig"))
        return self.files[path]

    def resolve(self, ref: str, base: Path) -> tuple[dict, Path]:
        target, _, pointer = ref.partition("#")
        path = (base.parent / target).resolve() if target else base
        node = self.load(path)
        for token in [t for t in pointer.split("/") if t]:
            node = node[token.replace("~1", "/").replace("~0", "~")]
        return node, path

    def errors(self, doc, schema_path: Path) -> list[str]:
        found: list[str] = []
        self.check(doc, self.load(schema_path), schema_path, "", found, MAX_ERRORS)
        return found[:MAX_ERRORS]

    def check(self, value, schema, base: Path, where: str, out: list[str], limit: int) -> None:
        if not isinstance(schema, dict) or len(out) >= limit:
            return
        if "$ref" in schema:  # draft-07: keywords beside $ref are ignored
            node, path = self.resolve(schema["$ref"], base)
            self.check(value, node, path, where, out, limit)
            return
        at = where or "/"
        types = schema.get("type")
        if types is not None and not any(_is(value, t) for t in ([types] if isinstance(types, str) else types)):
            out.append(f"{at}: expected {types}, found {_kind(value)}")
            return
        if "const" in schema and value != schema["const"]:
            out.append(f"{at}: must be {json.dumps(schema['const'])}")
        if "enum" in schema and value not in schema["enum"]:
            out.append(f"{at}: {json.dumps(value)[:60]} is not one of {json.dumps(schema['enum'])[:200]}")
        for key, need in (("anyOf", 1), ("oneOf", 1)):
            if key in schema:
                ok = sum(1 for sub in schema[key] if not self._sub(value, sub, base, where))
                if ok < need or (key == "oneOf" and ok > 1):
                    out.append(f"{at}: matches {ok} of the {len(schema[key])} allowed shapes ({key})")
        if isinstance(value, str):
            if "pattern" in schema and not re.search(schema["pattern"], value):
                out.append(f"{at}: {value[:60]!r} does not match {schema['pattern']}")
            if len(value) > schema.get("maxLength", len(value)) or len(value) < schema.get("minLength", 0):
                out.append(f"{at}: length {len(value)} is outside the allowed range")
        if isinstance(value, dict):
            props = schema.get("properties") or {}
            for name in schema.get("required") or []:
                if name not in value:
                    out.append(f"{at}: missing required property {name!r}")
            extra = schema.get("additionalProperties", True)
            for name, item in value.items():
                if name in props:
                    self.check(item, props[name], base, f"{where}/{name}", out, limit)
                elif extra is False:
                    out.append(f"{at}: unexpected property {name!r}")
                elif isinstance(extra, dict):
                    self.check(item, extra, base, f"{where}/{name}", out, limit)
        if isinstance(value, list) and isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                self.check(item, schema["items"], base, f"{where}/{i}", out, limit)

    def _sub(self, value, schema, base: Path, where: str) -> list[str]:
        found: list[str] = []
        self.check(value, schema, base, where, found, 1)  # one error rules an alternative out
        return found


def _is(value, kind: str) -> bool:
    return {"object": isinstance(value, dict), "array": isinstance(value, list), "string": isinstance(value, str),
            "boolean": isinstance(value, bool), "null": value is None,
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool)}.get(kind, True)


def _kind(value) -> str:
    return next(k for k in ("null", "boolean", "integer", "number", "string", "array", "object") if _is(value, k))


def check_file(path: Path, root: Path | None = None) -> tuple[Path | None, list[str]]:
    """(schema file, errors) for one PBIR file; (None, []) when it names no vendored schema."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        return None, [f"not valid JSON: {exc}"]
    url = doc.get("$schema") if isinstance(doc, dict) else None
    if not isinstance(url, str) or not url.startswith(PREFIX):
        return None, []
    root = root or schemas_root()
    schema = (root / url[len(PREFIX):]).resolve()
    if root.resolve() not in schema.parents or not schema.is_file():
        return None, []
    return schema, Validator(root).errors(doc, schema)


def main() -> int:
    try:
        event = json.loads(sys.stdin.buffer.read() or b"{}")
        path = str((event.get("tool_input") or {}).get("file_path") or "")
        if not _PBIR.search(path) or not Path(path).is_file():
            return 0
        schema, errors = check_file(Path(path))
    except Exception as exc:  # advisory: report the fault, never pretend the file was checked
        print(f"pbir_validate could not check the file: {type(exc).__name__}: {' '.join(str(exc).split())}",
              file=sys.stderr)
        return 1
    if not errors:
        return 0
    against = f" against {schema}" if schema else ""
    print(f"PBIR check of {path}{against} found problems (the edit is saved; fix them before pbi push):\n"
          + "\n".join(f"- {e}" for e in errors), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
