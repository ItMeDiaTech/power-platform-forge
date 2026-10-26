"""pa inspect: SharePoint delegation findings in canvas app source (Src/*.pa.yaml); findings, never source.

The rules follow Learn's SharePoint delegation table and notes
(learn.microsoft.com/power-apps/maker/canvas-apps/connections/connection-sharepoint-online, 2026-10-07):
StartsWith on Choice or Lookup subfields, the `in` operator, relational operators on ID (text
underneath), Person subfields other than Email and DisplayName, IsBlank, Not, SharePoint system
fields, and functions missing from the table (Search, CountIf). Formulas are the `=`-prefixed
property values of the pa.yaml v3.0 schema.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from ppx.errors import UsageError

MAX_FINDINGS = 40
DATA_CALLS = ("Filter", "LookUp", "Search", "Sort", "SortByColumns", "CountIf", "RemoveIf", "UpdateIf")
_SYSTEM = ("Identifier|IsFolder|Thumbnail|Link|Name|FilenameWithExtension|Path|FullPath|ModerationStatus"
           "|ModerationComment|ContentType|IsCheckedOut|VersionNumber")
RULES = {  # rule: (pattern over the predicate, hint); `bare` patterns see quoted names blanked out
    "startswith-choice-lookup": (r"\bStartsWith\s*\(\s*(?:'[^']*'|[A-Za-z_]\w*)\s*\.\s*(?:Value|Id)\b",
                                 "StartsWith on a Choice or Lookup subfield does not delegate; filter on a text column"),
    "in-operator": (r"\b(?:in|exactin)\b", "the in operator does not delegate to SharePoint; use = or StartsWith"),
    "id-relational": (r"\bID\s*(?:<>|<=|>=|<|>)|(?:<>|<=|>=|<|>)\s*(?:ThisRecord\.)?ID\b",
                      "SharePoint ID is text underneath and delegates only with =; use a number column"),
    "person-subfield": (r"\.\s*(?:Claims|Department|JobTitle|Picture)\b",
                        "only Email and DisplayName of a Person column delegate"),
    "isblank": (r"\bIsBlank\s*\(", "IsBlank does not delegate; compare with = Blank() instead"),
    "not": (r"\bNot\s*\(|!\s*[A-Za-z_('(]", "Not does not delegate to SharePoint"),
    "system-field": (r"'\{(?:" + _SYSTEM + r")\}'", "SharePoint system fields do not delegate"),
}
_BARE = {"in-operator", "id-relational", "isblank", "not"}
_FUNCTIONS = {"Search": "search", "CountIf": "countif"}  # data calls missing from the delegable table
HINTS = {**{rule: hint for rule, (_, hint) in RULES.items()},
         "search": "Search does not delegate to SharePoint; use Filter with StartsWith",
         "countif": "CountIf does not delegate to SharePoint; counts stop at the data row limit"}
_CALL = re.compile(r"\b(" + "|".join(DATA_CALLS) + r")\s*\(")
_KEY = re.compile(r"""^(\s*)(-\s+)?('(?:[^']|'')*'|"[^"]*"|[^\s:#'"][^:#]*?)\s*:(?:\s+(.*?))?\s*$""")
_BLOCK = re.compile(r"^[|>][-+0-9]*$")
_STRUCTURE = {"Properties", "Children", "Screens", "App", "ComponentDefinitions", "CustomProperties", "DataSources",
              "Parameters", "Layout", "EditorState", "Groups"}
_SP_URL = re.compile(r"https://[a-z0-9-]+\.sharepoint\.com(?:/[^\s'\"<>,;)}\]]*)?", re.I)


def source_root(target: Path) -> Path:
    if not target.is_dir():
        raise UsageError(f"{target} is not a folder")
    if not any(target.rglob("*.pa.yaml")):
        raise UsageError(f"{target} holds no .pa.yaml files; download the app with `ppx pa get <app>` first")
    return target


def _unquote(key: str) -> str:
    return key[1:-1].replace("''", "'") if key[:1] in "'\"" and key[-1:] == key[:1] else key


def formulas(text: str):
    """Yield (line, control, property, formula without the leading =) for every formula in one .pa.yaml file."""
    lines = text.splitlines()
    stack: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        m = _KEY.match(lines[i])
        if not m or lines[i].lstrip().startswith("#"):
            i += 1
            continue
        indent = len(m.group(1)) + len(m.group(2) or "")
        key, value = _unquote(m.group(3).strip()), (m.group(4) or "")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        control = next((k for _, k in reversed(stack) if k not in _STRUCTURE), "")
        start = i + 1
        if _BLOCK.match(value):
            body = []
            i += 1
            while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
                body.append(lines[i].strip())
                i += 1
            joined = "\n".join(body).strip()
            if joined.startswith("="):
                yield start + 1, control, key, joined[1:]
            continue
        value = _unquote(value)
        if value.startswith("="):
            yield start, control, key, value[1:]
        elif not value:
            stack.append((indent, key))
        i += 1


def _blank_strings(text: str) -> str:
    """Power Fx "strings" become spaces of the same length, so offsets and line numbers still hold."""
    return re.sub(r'"(?:[^"]|"")*"', lambda m: '"' + " " * (len(m.group()) - 2) + '"', text)


def _close(text: str, start: int) -> int:
    depth = 0
    for j in range(start, len(text)):
        depth += {"(": 1, ")": -1}.get(text[j], 0)
        if depth == 0:
            return j
    return len(text)


def _split_first(args: str) -> tuple[str, str]:
    depth = 0
    for j, ch in enumerate(args):
        depth += {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            return args[:j], args[j + 1:]
    return args, ""


def _calls(formula: str, sp_sources: set[str]):
    """Yield (offset, function, predicate) of each data call on a known SharePoint list (on any, if none is known)."""
    text = _blank_strings(formula)
    for m in _CALL.finditer(text):
        end = _close(text, m.end() - 1)
        source, rest = _split_first(text[m.end():end])
        name = _unquote(source.strip()).lower()
        if sp_sources and name not in sp_sources:
            continue
        yield m.start(), m.group(1), rest


def findings(root: Path, sp_lists=()) -> tuple[list[dict], dict]:
    outline_ = outline(root)
    known = {s.lower() for s in sp_lists} | {n.lower() for n, sp in outline_["data_sources"].items() if sp}
    found: list[dict] = []
    for path in sorted(root.rglob("*.pa.yaml")):
        rel = path.relative_to(root).as_posix()
        for line, control, prop, formula in formulas(path.read_text(encoding="utf-8", errors="replace")):
            for offset, func, predicate in _calls(formula, known):
                at = line + formula[:offset].count("\n")
                hit = {"file": rel, "line": at, "control": control, "property": prop, "function": func}
                if func in _FUNCTIONS:
                    found.append({**hit, "rule": _FUNCTIONS[func]})
                bare = re.sub(r"'(?:[^']|'')*'", lambda q: " " * len(q.group()), predicate)
                found += [{**hit, "rule": rule} for rule, (pattern, _) in RULES.items()
                          if re.search(pattern, bare if rule in _BARE else predicate)]
    return found, {**outline_, "sources_known": bool(known)}


def outline(root: Path) -> dict:
    """Screens, data sources (name: is SharePoint) and SharePoint site URLs named in the source."""
    screens, sources, sites = [], {}, set()
    for path in sorted(Path(root).rglob("*.pa.yaml")):
        text = path.read_text(encoding="utf-8", errors="replace")
        sites.update(u.rstrip("/") for u in _SP_URL.findall(text))
        section, current = None, None
        for line in text.splitlines():
            m = _KEY.match(line)
            if not m or m.group(2):
                continue
            indent, key = len(m.group(1)), _unquote(m.group(3).strip())
            if indent == 0:
                section, current = key, None
            elif indent == 2 and section == "Screens":
                screens.append(key)
            elif indent == 2 and section == "DataSources":
                current = key
                sources.setdefault(key, False)
            if section == "DataSources" and current and "sharepoint" in line.lower():
                sources[current] = True
    return {"screens": screens, "data_sources": sources, "sites": sorted(sites)}


def row_limit(msapp: Path | None) -> dict:
    """The data row limit, if the .msapp's app settings carry it; the .pa.yaml source does not."""
    if msapp and msapp.is_file():
        try:
            with zipfile.ZipFile(msapp) as z:
                for name in (n for n in z.namelist() if n.endswith(".json") and "/" not in n):
                    hit = re.search(r'"(\w*(?:Delegation|DataRow)\w*Limit\w*)"\s*:\s*"?(\d+)',
                                    z.read(name).decode("utf-8", "replace"))
                    if hit:
                        return {"value": int(hit.group(2)), "key": hit.group(1), "file": name}
        except (OSError, zipfile.BadZipFile):
            pass
    return {"value": None, "note": "not in the source: check Settings > General > Data row limit "
                                   "(500 by default, up to 10,000)"}


def run(ctx, args) -> dict:
    from ppx.pa import index  # index downloads through this module's parser

    target = Path(args.target)
    if target.is_dir():
        msapp = next(iter(sorted([*target.glob("*.msapp"), *target.parent.glob("*.msapp")])), None)
        root, lists, app = source_root(target), [], None
    else:
        app, entry = index.ensure(ctx, args.target, args.environment)
        if entry is None:
            return {"app": {"id": app["id"], "name": app.get("name")}}
        root, msapp, lists = Path(entry["src"]), Path(entry["msapp"]), entry.get("lists") or []
    found, info = findings(root, lists)
    counts: dict[str, int] = {}
    for f in found:
        counts[f["rule"]] = counts.get(f["rule"], 0) + 1
    if not info["sources_known"]:
        ctx.warn("no SharePoint data source could be identified, so every Filter, LookUp and Search was checked")
    return {"app": {"id": app["id"], "name": app.get("name")} if app else None, "root": str(root),
            "findings_count": len(found), "by_rule": counts, "hints": {r: HINTS[r] for r in counts},
            "findings": found[:MAX_FINDINGS],
            "data_row_limit": row_limit(msapp), "screens": info["screens"][:30],
            "sharepoint_sources": sorted(n for n, sp in info["data_sources"].items() if sp) or sorted(lists)}
