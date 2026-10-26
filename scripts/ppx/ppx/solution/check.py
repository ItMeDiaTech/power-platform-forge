"""ppx solution check: run solution checker through pac and summarise its SARIF results.

The checker service returns a zip holding SARIF v2 JSON reports (learn.microsoft.com/power-platform/
alm/checker-api/overview); pac saves it in --outputDirectory. Issues are grouped by rule and
severity so a large result fits the envelope; the full reports stay on disk.
"""

from __future__ import annotations

import json
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ppx import pac
from ppx.errors import PpxError, UsageError
from ppx.solution import common

SEVERITY_ORDER = ("critical", "high", "medium", "low", "informational", "error", "warning", "note", "none", "unknown")
MAX_SARIF_BYTES = 200_000_000
TOP_RULES = 10


def _issues(sarif: dict) -> list[dict]:
    out = []
    for run in sarif.get("runs") or []:
        for r in run.get("results") or []:
            props = r.get("properties") or {}
            loc = ((r.get("locations") or [{}])[0] or {}).get("physicalLocation") or {}
            artifact = loc.get("artifactLocation") or loc.get("fileLocation") or {}  # SARIF 2.1 and 2.0 names
            out.append({"rule": r.get("ruleId") or "unknown",
                        # shortcut: the checker's own Critical..Informational label is read from
                        # result.properties.severity; when absent the standard SARIF level is used.
                        "severity": str(props.get("severity") or r.get("level") or "unknown"),
                        "message": ((r.get("message") or {}).get("text") or "")[:200],
                        "file": artifact.get("uri")})
    return out


def _load(data: bytes, where: str) -> list[dict]:
    try:
        return _issues(json.loads(data.decode("utf-8-sig")))
    except (ValueError, AttributeError) as exc:
        raise PpxError(f"{where} is not valid SARIF JSON: {exc}") from exc


def _collect(folder: Path, before: dict[Path, int]) -> tuple[list[dict], list[str], list[str]]:
    """Issues from every SARIF file this run wrote to `folder`, loose or inside the result zips, plus the
    reports skipped for being over MAX_SARIF_BYTES."""
    issues, sources, skipped = [], [], []
    for f, mtime in sorted(common.snapshot(folder).items()):
        if before.get(f) == mtime:
            continue
        if f.suffix.lower() == ".sarif":
            if f.stat().st_size > MAX_SARIF_BYTES:
                skipped.append(str(f))
                continue
            issues += _load(f.read_bytes(), str(f))
            sources.append(str(f))
        elif zipfile.is_zipfile(f):
            with zipfile.ZipFile(f) as z:
                for member in z.infolist():
                    if not member.filename.lower().endswith(".sarif"):
                        continue
                    if member.file_size > MAX_SARIF_BYTES:
                        skipped.append(f"{f}:{member.filename}")
                        continue
                    issues += _load(z.read(member), f"{f.name}:{member.filename}")
                    sources.append(f"{f}:{member.filename}")
    return issues, sources, skipped


def _rank(severity: str) -> int:
    s = severity.lower()
    return SEVERITY_ORDER.index(s) if s in SEVERITY_ORDER else len(SEVERITY_ORDER)


def run(ctx, args) -> dict:
    zip_path = common.solution_zip(args.zip)
    out = Path(args.out).expanduser().resolve() if args.out else ctx.need_data_dir() / "exports" / "solution-check"
    if args.out and out == zip_path.parent:
        raise UsageError("--out must be a separate folder: the checker's result zip could replace the solution zip")
    ctx.changes.add(f"upload {zip_path.name} to Microsoft's solution checker service and save the results", str(out))
    if ctx.dry_run:
        return {"zip": str(zip_path), "results": str(out)}
    out.mkdir(parents=True, exist_ok=True)
    if not args.out:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = Path(tempfile.mkdtemp(prefix=f"{stamp}-{zip_path.stem}-", dir=out))
    before = common.snapshot(out)
    p = pac.run(["solution", "check", "--path", str(zip_path), "--outputDirectory", str(out), *common.env_args(ctx)],
                timeout=common.long_timeout(ctx))
    issues, sources, skipped = _collect(out, before)
    if skipped:
        ctx.warn(f"{len(skipped)} SARIF report(s) over {MAX_SARIF_BYTES // 1_000_000} MB not summarised "
                 f"(open them directly): {', '.join(skipped[:3])}")
    if not sources:
        raise PpxError(f"pac solution check finished but left no SARIF results ppx could summarise in {out}",
                       data={"pac_output": [line[:160] for line in p.stdout.strip().splitlines()[-5:]]})
    by_severity = Counter(i["severity"] for i in issues)
    groups: dict[tuple[str, str], dict] = {}
    for i in issues:
        g = groups.setdefault((i["rule"], i["severity"]), {"rule": i["rule"], "severity": i["severity"], "count": 0,
                                                          "example": i["message"], "file": i["file"]})
        g["count"] += 1
    rules = sorted(groups.values(), key=lambda g: (_rank(g["severity"]), -g["count"], g["rule"]))
    return {"zip": str(zip_path), "results": str(out), "total": len(issues),
            "by_severity": {s: by_severity[s] for s in sorted(by_severity, key=_rank)},
            "count": len(rules), "items": rules[:ctx.top or TOP_RULES]}
