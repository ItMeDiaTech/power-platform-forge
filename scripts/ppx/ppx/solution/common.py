"""Pieces the solution verbs share: the target environment, a solution zip's manifest, and the
connection references and environment variables pac finds in a zip."""

from __future__ import annotations

import json
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from ppx import pac
from ppx.errors import PpxError, UsageError

LONG_SECONDS = 3900  # pac waits up to 60 minutes (its --max-async-wait-time default) for exports and imports
MAX_MANIFEST_BYTES = 10_000_000
UNIQUE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,99}")


def env_args(ctx) -> list[str]:
    """Target the profile's environment, not whichever environment pac auth selected last."""
    url = ctx.profile.url if ctx.profile else None
    return ["--environment", url] if url else []


def snapshot(folder: Path) -> dict[Path, int]:
    """Each file under `folder` with its modification time, to tell what a pac run wrote."""
    return {f: f.stat().st_mtime_ns for f in folder.rglob("*") if f.is_file()} if folder.is_dir() else {}


def long_timeout(ctx) -> float:
    return max(ctx.timeout, LONG_SECONDS)


def solution_zip(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise UsageError(f"no file {path}")
    if not zipfile.is_zipfile(path):
        raise UsageError(f"{path} is not a zip file")
    return path


def managed_sibling(path: Path) -> Path:
    """SolutionPackager pairs <name>.zip with <name>_managed.zip in the same folder."""
    return path.with_name(f"{path.stem}_managed{path.suffix}")


def manifest(path: Path) -> dict:
    """Unique name, version and type from solution.xml, which Dataverse requires at the zip root."""
    with zipfile.ZipFile(path) as z:
        member = next((i for i in z.infolist() if i.filename.lower() == "solution.xml"), None)
        if member is None:
            raise UsageError(f"{path.name} is not a Dataverse solution zip: it has no solution.xml at its root")
        if member.file_size > MAX_MANIFEST_BYTES:
            raise UsageError(f"{path.name}: solution.xml is {member.file_size} bytes, too large to be a manifest")
        try:
            root = ET.fromstring(z.read(member))
        except ET.ParseError as exc:
            raise UsageError(f"{path.name}: solution.xml is not valid XML ({exc})") from exc
    sm = root.find("SolutionManifest")

    def text(tag: str) -> str | None:
        return ((sm.findtext(tag) if sm is not None else None) or "").strip() or None

    stem = path.stem[:-len("_managed")] if path.stem.endswith("_managed") else path.stem
    name = text("UniqueName") or stem
    # The zip may come from anywhere and the name reaches suggested shell commands and backup paths.
    if not UNIQUE_NAME.fullmatch(name):
        raise UsageError(f"{path.name}: solution.xml UniqueName {name[:60]!r} is not a valid Dataverse unique name "
                         "(letters, digits and underscores, not starting with a digit)")
    return {"name": name, "version": text("Version"), "managed": text("Managed") == "1"}


def defaults(path: Path) -> set[str]:
    """Schema names of the environment variables whose definition in the zip carries a default value.
    Dataverse uses the default when no current value exists, so these need no value at import."""
    out = set()
    with zipfile.ZipFile(path) as z:
        for member in z.infolist():
            if not member.filename.lower().endswith("environmentvariabledefinition.xml") or \
                    member.file_size > MAX_MANIFEST_BYTES:
                continue
            try:
                root = ET.fromstring(z.read(member))
            except ET.ParseError:
                continue
            if (root.findtext("defaultvalue") or "").strip() and root.get("schemaname"):
                out.add(root.get("schemaname"))
    return out


def read_settings(path: Path) -> dict:
    try:
        body = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise UsageError(f"no settings file {path}") from None
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise UsageError(f"{path} is not valid JSON: {exc}") from exc
    keys = ("ConnectionReferences", "EnvironmentVariables")
    if not isinstance(body, dict) or not all(isinstance(body.get(k, []), list)
                                             and all(isinstance(x, dict) for x in body.get(k, [])) for k in keys):
        raise UsageError(f"{path} is not a deployment settings file (expected ConnectionReferences and "
                         "EnvironmentVariables lists)")
    return body


def discover(path: Path, timeout: float) -> dict:
    """The deployment settings pac derives from the zip: every connection reference and environment
    variable, with empty values. Runs locally; nothing is sent to the tenant."""
    with tempfile.TemporaryDirectory(prefix="ppx-settings-") as tmp:
        out = Path(tmp) / "settings.json"
        pac.run(["solution", "create-settings", "--solution-zip", str(path), "--settings-file", str(out)],
                timeout=timeout)
        if not out.is_file():
            raise PpxError("pac solution create-settings wrote no settings file")
        return read_settings(out)


def _filled(value) -> bool:
    return value not in (None, "")


def _variable(name, value, defaulted: set[str]) -> dict:
    if _filled(value):
        return {"name": name, "set": True}
    return {"name": name, "set": True, "default": True} if name in defaulted else {"name": name, "set": False}


def review(found: dict, given: dict | None, defaulted: set[str] = frozenset()) -> tuple[list[dict], list[dict]]:
    """Each connection reference and environment variable in the zip, and whether `given` sets it; a
    variable in `defaulted` counts as set by its default value."""
    given = given or {}
    conns = {c.get("LogicalName"): c for c in given.get("ConnectionReferences") or [] if isinstance(c, dict)}
    envs = {e.get("SchemaName"): e for e in given.get("EnvironmentVariables") or [] if isinstance(e, dict)}
    refs = [{"name": c.get("LogicalName"), "connector": str(c.get("ConnectorId") or "").rsplit("/", 1)[-1],
             "set": _filled((conns.get(c.get("LogicalName")) or {}).get("ConnectionId"))}
            for c in found.get("ConnectionReferences") or []]
    variables = [_variable(e.get("SchemaName"), (envs.get(e.get("SchemaName")) or {}).get("Value"), defaulted)
                 for e in found.get("EnvironmentVariables") or []]
    return refs, variables


def merge(found: dict, old: dict | None) -> dict:
    """pac's fresh settings with the values from `old` carried over, so a re-run never loses filled-in values."""
    old = old or {}
    conns = {c.get("LogicalName"): c for c in old.get("ConnectionReferences") or [] if isinstance(c, dict)}
    envs = {e.get("SchemaName"): e for e in old.get("EnvironmentVariables") or [] if isinstance(e, dict)}
    out = {"EnvironmentVariables": [], "ConnectionReferences": []}
    for e in found.get("EnvironmentVariables") or []:
        prior = envs.get(e.get("SchemaName")) or {}
        out["EnvironmentVariables"].append({**e, "Value": prior["Value"]} if _filled(prior.get("Value")) else e)
    for c in found.get("ConnectionReferences") or []:
        prior = conns.get(c.get("LogicalName")) or {}
        out["ConnectionReferences"].append({**c, "ConnectionId": prior["ConnectionId"]}
                                           if _filled(prior.get("ConnectionId")) else c)
    return out
