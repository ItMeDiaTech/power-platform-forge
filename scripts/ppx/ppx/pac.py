"""Power Platform CLI calls. Structured output where pac documents --json; a header-checked
fixed-width parser where it does not, so a pac upgrade that changes a table fails loudly."""

from __future__ import annotations

import json
import re

from ppx import proc
from ppx.errors import PpxError
from ppx.redact import scrub_text

PINNED = "2.14.1"
INSTALL = "dotnet tool install --global Microsoft.PowerApps.CLI.Tool"


def run(args: list[str], *, json_out: bool = False, timeout: float = 120):
    """Run pac; return parsed JSON when json_out, else the Proc. Non-zero exit raises."""
    p = proc.run(["pac", *args, *(["--json"] if json_out else [])], timeout=timeout, install_hint=INSTALL)
    if p.code != 0:
        tail = (p.stderr.strip() or p.stdout.strip()).splitlines()[-3:]
        raise PpxError(f"pac {' '.join(args[:2])} failed: {scrub_text(' '.join(tail))[:400]}",
                       data={"exit_code": p.code})
    if not json_out:
        return p
    text = p.stdout.strip()
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if start < 0:
        raise PpxError(f"pac {' '.join(args[:2])} --json printed no JSON")
    try:
        return json.loads(text[start:])
    except ValueError as exc:
        raise PpxError(f"pac {' '.join(args[:2])} --json printed invalid JSON: {exc}") from exc


def parse_table(text: str, expected: list[str], *, command: str) -> list[dict]:
    """Parse a space-aligned table whose header holds the `expected` columns (in order, among others).

    Header columns are separated by two or more spaces; every row is sliced at the header's
    column starts, and only the expected columns are returned.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        cols = [(m.group(), m.start()) for m in re.finditer(r"\S+(?: \S+)*", line)]
        names = [c for c, _ in cols]
        it = iter(names)
        if not all(col in it for col in expected):  # expected must appear in order
            continue
        starts = [s for _, s in cols]
        rows = []
        for row in lines[i + 1:]:
            if not row.strip():
                if rows:
                    break
                continue
            if set(row.strip()) <= set("-= "):
                continue
            cells = {name: row[s:e].strip() for name, s, e in zip(names, starts, starts[1:] + [None])}
            rows.append({col: cells[col] for col in expected})
        return rows
    raise PpxError(f"pac {command} output has no header with columns {', '.join(expected)}; "
                   f"ppx was written for pac {PINNED}, so the output format probably changed",
                   data={"first_lines": [scrub_text(l)[:120] for l in lines[:3]]})
