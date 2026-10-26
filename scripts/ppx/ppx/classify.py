"""Decide what a Bash, PowerShell or MCP tool call would change, for the change-notice hook.

Stdlib only, importing only ppx.verbtable and ppx.redact, because the hook loads it on every
shell call. Kinds, least to most severe:
  read          changes nothing, or is not a Power Platform command; no notice
  local         changes only this computer (profiles, sign-in caches, local files); notice
  change        changes the tenant; notice, or a prompt in confirm mode
  interactive   opens a sign-in; the user should run it in their own terminal
  unknown       a Power Platform command this module does not know; the user is asked
  irreversible  deletes, overwrites, imports or restores; the user is asked
A compound command takes the most severe kind of its parts.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from ppx.redact import MASK, scrub_text
from ppx.verbtable import GLOBAL_ARGS, find

KINDS = ("read", "local", "change", "interactive", "unknown", "irreversible")
_RANK = {kind: i for i, kind in enumerate(KINDS)}

# Identical to the PreToolUse MCP matcher in hooks/hooks.json (a test checks): Power Platform servers only.
# Each term must be a whole token of the server name, so pnpm or fabricator do not match.
MCP_MATCHER = (r"^mcp__(?:(?!__).)*(?<![A-Za-z0-9])"
               r"(?:power-platform-forge_ppx|powerbi|flowagent|dataverse|fabric|m365|microsoft365|pnp)"
               r"(?![A-Za-z0-9])(?:(?!__).)*__")


@dataclass(frozen=True)
class Classification:
    kind: str
    summary: str
    binary: str


READ = Classification("read", "", "")


def classify(tool_name: str, tool_input: dict) -> Classification:
    if tool_name in ("Bash", "PowerShell"):
        return _shell(str(tool_input.get("command") or ""), tool_name == "PowerShell")
    if tool_name.startswith("mcp__"):
        return _mcp(tool_name, tool_input if isinstance(tool_input, dict) else {})
    return READ


# Sign-in secrets on m365, PnP and Power Apps command lines, which ppx.redact's option list does not cover.
_SECRET_OPT = re.compile(r"(?i)(?<![\w-])(-{1,2}(?:(?:client|app)?secret|(?:certificate)?password"
                         r"|certificatebase64encoded))([ =:])\S+")


def _clip(text: str, limit: int = 160) -> str:
    text = _SECRET_OPT.sub(lambda m: m.group(1) + m.group(2) + MASK, scrub_text(text))
    text = " ".join(text.split())  # scrub before cutting, so a token is never half-masked
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _worst(results: list[Classification]) -> Classification:
    hits = sorted((r for r in results if r.kind != "read"), key=lambda r: -_RANK[r.kind])
    if not hits:
        return READ
    if hits[0].kind == "interactive":  # the reason must stay a command the user can paste
        hits = [r for r in hits if r.kind == "interactive"]
    summary = "; ".join(r.summary for r in hits[:3]) + (f"; and {len(hits) - 3} more" if len(hits) > 3 else "")
    return Classification(hits[0].kind, summary, hits[0].binary)


# ---- shell command lines -------------------------------------------------------------------------

_BASH_BREAKS = "();<>|&`\n"
_REDIRECT = set("<>&|")
_PS_BREAKS = ";|\n"
_COMMENT = re.compile(r"(?m)^[ \t]*#.*$")
# Quoted text and escapes are matched first and kept, so only a `#` that starts a word ends the line.
_BASH_COMMENT = re.compile(r"""('[^']*'|"(?:\\.|[^"\\])*"|\\.)|(?<![^\s;&|()<>])#[^\n]*""", re.S)
_PS_BLOCK_COMMENT = re.compile(r"<#.*?#>", re.S)
_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][\w-]*)\1([^\n]*)\n.*?^[ \t]*\2[ \t]*$", re.S | re.M)
_PS_HERESTRING = re.compile(r"@([\"'])[ \t]*\n.*?\n\1@", re.S)
_SUBSTITUTION = re.compile(r"\$\(|[()`]")
_SHELLS = {"bash", "sh", "zsh", "dash", "pwsh", "powershell"}
_KNOWN = re.compile(r"(?i)\b(?:ppx|pac|az|m365|fab|curl|wget|irm|iwr|invoke-(?:restmethod|webrequest)|powershell|pwsh)\b"
                    r"|-(?:admin)?(?:powerapp|flow)|-(?:mg|pnp)[a-z]|-powerbi")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PS_VARIABLE = re.compile(r"^\$[\w:]+$")
_PS_SET = re.compile(r"^\$[\w:]+=(.*)$")
_LEADERS = {"&", ".", "!", "{", "}", "if", "then", "else", "elif", "do", "while", "until"}
_WRAPPERS = {"sudo", "env", "time", "nohup", "exec", "command", "builtin", "nice", "xargs", "timeout", "stdbuf"}


def _shell(text: str, powershell: bool, escape: bool = True) -> Classification:
    text = text.replace("\r\n", "\n")
    if powershell:  # before line comments, which could remove a block comment's closing #>
        text = _PS_BLOCK_COMMENT.sub(" ", text)
    text = _COMMENT.sub("", text)
    try:
        if powershell:
            segments = _ps_segments(_PS_HERESTRING.sub("''", text))
        else:
            segments = _bash_segments(_HEREDOC.sub(_heredoc, text), escape)
    except ValueError:
        if _KNOWN.search(text):
            return Classification("unknown", f"a command that could not be parsed: {_clip(text)}", "")
        return READ
    results = []
    for words in segments:
        results.append(_segment(words, text))
        # $(...) and backticks inside quoted words still run
        results += [_shell(_SUBSTITUTION.sub(" ; ", w), powershell) for w in words if "$(" in w or "`" in w]
    return _worst(results)


def _heredoc(m: re.Match) -> str:
    """Drop a heredoc body (it is data), unless a shell on the same line will run it."""
    line = m.string[m.string.rfind("\n", 0, m.start()) + 1:m.start()] + m.group(3)
    return m.group(0) if any(_name(w) in _SHELLS for w in line.split()) else m.group(3)


def _bash_segments(text: str, escape: bool) -> list[list[str]]:
    if escape:  # cmd.exe has no # comments
        text = _BASH_COMMENT.sub(lambda m: m.group(1) or "", text)
    lex = shlex.shlex(text, posix=True, punctuation_chars=_BASH_BREAKS)
    lex.whitespace, lex.commenters, lex.whitespace_split = " \t\r", "", True
    if not escape:  # cmd.exe lines keep their backslashes
        lex.escape = ""
    segments, words, target = [], [], False
    for tok in lex:
        punct = bool(tok) and set(tok) <= set(_BASH_BREAKS)
        if punct and set(tok) <= _REDIRECT and set(tok) & {"<", ">"}:
            # a redirection (2>&1, >file, &>>log) belongs to its command: drop the fd number and the target
            if words and words[-1].isdigit():
                words.pop()
            target = True
        elif punct:
            if words:
                segments.append(words)
            words, target = [], False
        elif target:
            target = False
        else:
            words.append(tok)
    return segments + [words] if words else segments


def _ps_close(text: str, start: int) -> int:
    """The index of the bracket that closes the ( or { at `start`, skipping quoted text.

    shortcut: a bracket inside a # comment within the group is counted; upgrade if real scripts trip on it.
    """
    depth, quote, i = 0, None, start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
            elif ch == "`" and quote == '"':
                i += 1
        elif ch in "'\"":
            quote = ch
        elif ch == "`":
            i += 1
        elif ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("No closing bracket")


def _ps_segments(text: str) -> list[list[str]]:
    """Split PowerShell on ; | && || and newlines, honouring '' and "" quotes and # comments.

    A (...) or {...} group stays one word of its command, so a hashtable or subexpression argument
    keeps the parameters after it in the same command; the group's own commands become extra segments.
    """
    segments, nested, words, word, quote, i = [], [], [], None, None, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "'" == quote and text.startswith("''", i):
                word += "'"
                i += 1
            elif ch == quote:
                quote = None
            elif ch == "`" and quote == '"' and i + 1 < len(text):
                word += text[i + 1]
                i += 1
            else:
                word += ch
        elif ch in "'\"":
            quote, word = ch, word or ""
        elif ch == "`" and i + 1 < len(text):
            if text[i + 1] != "\n":  # backtick-newline only continues the line
                word = (word or "") + text[i + 1]
            i += 1
        elif ch == "#" and word is None:  # a comment runs to the end of the line
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        elif ch in "({":
            end = _ps_close(text, i)
            word = (word or "") + text[i:end + 1]
            nested.append(text[i + 1:end])
            i = end
        elif ch in " \t" or ch in _PS_BREAKS or text.startswith("&&", i):
            if word is not None:
                words.append(word)
                word = None
            if ch not in " \t" and words:
                segments.append(words)
                words = []
            if text.startswith("&&", i):
                i += 1
        else:
            word = (word or "") + ch
        i += 1
    if quote:
        raise ValueError("No closing quotation")
    if word is not None:
        words.append(word)
    if words:
        segments.append(words)
    for inner in nested:
        segments += _ps_segments(inner)
    return segments


def _name(word: str) -> str:
    base = re.split(r"[\\/]", word)[-1].lower()
    return base[:-4] if base.endswith((".exe", ".cmd", ".bat")) else base


def _strip(words: list[str]) -> list[str]:
    """Drop what runs before the real command: VAR=value, sudo/env/time wrappers, `&`, `$x =`, keywords."""
    i, wrapped = 0, False
    while i < len(words):
        w = words[i]
        if _ASSIGN.match(w) or w in _LEADERS or (wrapped and (w.startswith("-") or w[:1].isdigit())):
            i += 1
        elif _name(w) in _WRAPPERS:
            i, wrapped = i + 1, True
        elif _PS_VARIABLE.match(w) and words[i + 1:i + 2] == ["="]:
            i += 2
        elif m := _PS_SET.match(w):  # $x=cmd or $x= cmd
            words = words[:i] + ([m.group(1)] if m.group(1) else []) + words[i + 1:]
        else:
            break
    return words[i:]


def _segment(words: list[str], line: str = "") -> Classification:
    words = _strip(words)
    if not words or "--help" in words:
        return READ
    name = _name(words[0])
    if re.fullmatch(r"python[\d.]*|py", name):
        name = "python"
    handler = _HANDLERS.get(name) or (_cmdlet if _CMDLET.match(name) else None)
    if handler is None:
        return READ
    result = _http(words, line) if handler is _http else handler(words)
    if result.kind == "interactive":  # the user runs exactly this line in their own terminal
        return Classification("interactive", _clip(" ".join(words), 300), result.binary)
    return result


def _path(tokens: list[str]) -> list[str]:
    """The command words before the first option or redirection, e.g. ['solution', 'import']."""
    out = []
    for tok in tokens:
        if tok.startswith("-") or re.match(r"[\d*]*[<>]", tok):
            break
        out.append(tok)
    return out


def _opt(words: list[str], names: tuple[str, ...], ci: bool = False) -> str | None:
    """The value of the first option in `names`: `--x v`, `--x=v`, or PowerShell `-X:v` when `ci`."""
    for i, w in enumerate(words):
        key = w.lower() if ci else w
        for n in names:
            if key == n:
                return words[i + 1] if i + 1 < len(words) else ""
            if key.startswith(n + "=") or (ci and key.startswith(n + ":")):
                return w[len(n) + 1:]
    return None


def _by_method(method: str | None) -> str:
    if not method:
        return "unknown"
    m = method.upper()
    return "read" if m in ("GET", "HEAD", "OPTIONS") else "irreversible" if m == "DELETE" else "change"


def _rest(binary: str, method: str, url: str | None) -> Classification:
    kind = _by_method(method)
    if kind == "read":
        return READ
    return Classification(kind, _clip(f"{method.upper()} {url or '(no URL)'} with {binary}"), binary.split()[0])


def _cli(binary: str, words: list[str], path: list[str], kind: str, phrase: str | None = None) -> Classification:
    if kind == "read":
        return READ
    if phrase is None:
        verb, nouns = (path[-1], path[:-1]) if path else ("run", [])
        phrase = f"{verb.replace('-', ' ').capitalize()} {' '.join(nouns)}".rstrip()
    return Classification(kind, f"{phrase} with {binary} ({_clip(' '.join(words[1:]))})", binary)


# ---- ppx -----------------------------------------------------------------------------------------

_GLOBAL_VALUE = {f for a in GLOBAL_ARGS if a.kind != "flag" for f in a.flags}
_GLOBAL_FLAG = {f for a in GLOBAL_ARGS if a.kind == "flag" for f in a.flags}


class _Default(dict):
    def __missing__(self, key):
        return "(not given)"


def _ppx(tokens: list[str]) -> Classification:
    rest, i = [], 0
    while i < len(tokens):
        tok = tokens[i]
        name = tok.split("=", 1)[0]
        if name == "--dry-run":
            return READ
        if name in _GLOBAL_VALUE:
            i += 1 if "=" in tok else 2
            continue
        if tok not in _GLOBAL_FLAG:
            rest.append(tok)
        i += 1
    if not rest:
        return READ
    verb, n = find(rest)
    if verb is None:
        return Classification("unknown", _clip(f"ppx {' '.join(rest)} (a verb this version of ppx does not know)"), "ppx")
    by_flag = {f: a for a in verb.args if not a.positional for f in a.flags}
    positionals = iter([a for a in verb.args if a.positional])
    values, args, j = {}, rest[n:], 0
    while j < len(args):
        key, eq, val = args[j].partition("=")
        arg = by_flag.get(key)
        if arg is None:
            pos = None if args[j].startswith("-") else next(positionals, None)
            if pos is not None:
                values[pos.dest] = args[j]
        elif arg.kind == "flag":
            values[arg.dest] = True
        elif eq:
            values[arg.dest] = val
        elif j + 1 < len(args):
            values[arg.dest] = args[j + 1]
            j += 1
        j += 1
    kind = _by_method(values.get("method")) if verb.effect == "by-method" else verb.effect
    for flag, effect in verb.escalate:
        key, _, want = flag.partition("=")
        got = values.get(by_flag[key].dest if key in by_flag else key.lstrip("-").replace("-", "_"))
        if (str(got).lower() == want.lower() if want else got) and _RANK[effect] > _RANK[kind]:
            kind = effect
    return Classification(kind, _clip((verb.notice or verb.summary).format_map(_Default(values))), "ppx")


def _ppx_cli(words: list[str]) -> Classification:
    return _ppx(words[1:])


_UV_VALUE = {"--project", "--with", "--with-editable", "--with-requirements", "--python", "-p", "--directory",
             "--group", "--extra", "--package", "--env-file", "--index", "--default-index", "--only-group",
             "--no-group", "--cache-dir", "--config-file", "-w"}


def _skip_options(tokens: list[str], takes_value: set[str]) -> list[str]:
    i = 0
    while i < len(tokens) and tokens[i].startswith("-"):
        i += 2 if tokens[i] in takes_value else 1
    return tokens[i:]


def _uv(words: list[str]) -> Classification:
    rest = _skip_options(words[1:], _UV_VALUE)
    if rest[:1] != ["run"]:
        return READ
    rest = _skip_options(rest[1:], _UV_VALUE)
    return _segment(rest) if rest else READ


def _python(words: list[str]) -> Classification:
    i = 1
    while i < len(words):
        w = words[i]
        if w in ("-X", "-W"):
            i += 2
            continue
        if w == "-m":
            return _ppx(words[i + 2:]) if words[i + 1:i + 2] in (["ppx"], ["ppx.cli"]) else READ
        if w == "-c":
            # Inline code is visible: if it reaches a Power Platform API or ppx, ask rather than guess.
            code = words[i + 1] if i + 1 < len(words) else ""
            if any(_pp_host(m) for m in _URL.finditer(code)) or re.search(r"\bppx\b", code):
                return Classification("unknown", _clip(f"inline Python that calls a Power Platform API or ppx: "
                                                       f"{' '.join(code.split())}"), "python")
            return READ
        if not w.startswith("-"):
            return READ  # shortcut: a script file's effect is not visible here; read it if this ever matters
        i += 1
    return READ


# ---- Power Platform command-line tools -----------------------------------------------------------

# pac: exact "group command" entries first, then the command word, then a command-word prefix.
_PAC = {
    "auth create": ("interactive", ""),
    "auth clear": ("local", "Remove every pac sign-in profile"),
    "auth delete": ("local", "Remove a pac sign-in profile"),
    "auth select": ("local", "Switch the active pac sign-in profile"),
    "auth name": ("local", "Rename a pac sign-in profile"),
    "auth update": ("local", "Change a pac sign-in profile"),
    "env select": ("local", "Switch the default environment"),
    "env delete": ("irreversible", "Delete an environment"),
    "solution import": ("irreversible", "Import a solution"),
    "solution delete": ("irreversible", "Delete a solution"),
    "solution upgrade": ("irreversible", "Apply a solution upgrade"),
    "solution online-version": ("change", "Set the version of a solution in Dataverse"),
    "solution version": ("local", "Change a local solution project's version"),
    "solution create-settings": ("local", "Write a deployment settings file"),
    "solution add-reference": ("local", "Add a reference to a local solution project"),
    "admin delete": ("irreversible", "Delete an environment"),
    "admin reset": ("irreversible", "Reset an environment"),
    "admin restore": ("irreversible", "Restore an environment from a backup"),
    "admin copy": ("irreversible", "Copy one environment over another"),
    "admin backup": ("change", "Back up an environment"),
    "pipeline deploy": ("irreversible", "Deploy a solution through a pipeline"),
    "package deploy": ("irreversible", "Deploy a package (imports its solutions)"),
    "power-fx run": ("change", "Run Power Fx against Dataverse"),
    "power-fx repl": ("interactive", ""),
}
_PAC_VERB = {
    **dict.fromkeys(("help", "list", "who", "show", "download", "export", "check", "fetch", "status", "token",
                     "pack", "unpack"), "read"),
    **dict.fromkeys(("init", "sync", "clone", "pull"), "local"),
    **dict.fromkeys(("create", "update", "push", "publish", "import", "deploy", "install", "upload"), "change"),
    **dict.fromkeys(("delete", "reset", "restore", "clear"), "irreversible"),
}
_PAC_PREFIX = (("list-", "read"), ("add-", "change"), ("set-", "change"), ("assign-", "change"),
               ("update-", "change"), ("create-", "change"))


def _pac(words: list[str]) -> Classification:
    path = [p.lower() for p in _path(words[1:])]
    entry = _PAC.get(" ".join(path[:2]))
    if entry:
        return _cli("pac", words, path, entry[0], entry[1])
    verb = path[-1] if path else "help"
    kind = _PAC_VERB.get(verb) or next((k for p, k in _PAC_PREFIX if verb.startswith(p)), "unknown")
    return _cli("pac", words, path, kind)


_AZ = {"account get-access-token": "read", "account show": "read", "account list": "read", "account set": "local",
       "login": "interactive", "logout": "local"}
_AZ_VERB = {**dict.fromkeys(("list", "show", "get", "check"), "read"), "delete": "irreversible",
            **dict.fromkeys(("create", "update", "set", "add", "remove"), "change")}


def _az(words: list[str]) -> Classification:
    path = [p.lower() for p in _path(words[1:])]
    if path[:1] == ["rest"]:
        return _rest("az rest", _opt(words, ("--method", "-m")) or "GET", _opt(words, ("--url", "--uri", "-u")))
    kind = _AZ.get(" ".join(path[:2])) or _AZ.get(" ".join(path[:1])) or (_AZ_VERB.get(path[-1], "unknown") if path else "read")
    return _cli("az", words, path, kind)


_M365 = {"login": "interactive", "logout": "local", "status": "read", "setup": "local", "version": "read"}
_M365_VERB = {"get": "read", "list": "read", "add": "change", "set": "change", "remove": "irreversible"}


def _m365(words: list[str]) -> Classification:
    path = [p.lower() for p in _path(words[1:])]
    if not path:
        return READ
    if path[0] == "request":
        return _rest("m365 request", _opt(words, ("--method", "-m")) or "GET", _opt(words, ("--url", "-u")))
    kind = _M365.get(path[0]) or _M365_VERB.get(path[-1], "unknown")
    if kind == "irreversible" and "--recycle" in words:  # goes to the recycle bin, so it can be restored
        kind = "change"
    return _cli("m365", words, path, kind)


_FAB_GROUPS = {"auth", "config", "jobs", "job", "table", "acl", "acls", "label"}
_FAB = {
    **dict.fromkeys(("ls", "dir", "get", "exists", "find", "pwd", "cd", "export", "bulk-export", "open", "status"), "read"),
    **dict.fromkeys(("cp", "copy", "mv", "move", "import", "set", "mkdir", "create", "deploy", "start", "stop",
                     "assign", "unassign", "ln", "mklink"), "change"),
    "rm": "irreversible", "del": "irreversible", "login": "interactive", "logout": "local",
}
_FAB_SAY = {"rm": "Delete", "del": "Delete", "cp": "Copy", "mv": "Move", "mkdir": "Create", "ln": "Create a shortcut",
            "mklink": "Create a shortcut"}


def _fab(words: list[str]) -> Classification:
    path = _path(words[1:])
    if not path:
        return READ
    if path[0].lower() == "api":
        return _rest("fab api", _opt(words, ("-X", "--method")) or "GET", path[1] if len(path) > 1 else None)
    verb = (path[1] if path[0].lower() in _FAB_GROUPS and len(path) > 1 else path[0]).lower()
    kind = _FAB.get(verb, "unknown")
    return _cli("fab", words, path, kind, _FAB_SAY.get(verb, verb.capitalize()))


_PP_HOSTS = ("powerbi.com", "dynamics.com", "sharepoint.com", "flow.microsoft.com", "logic.azure.com", "powerapps.com",
             "api.powerplatform.com", "api.fabric.microsoft.com", "graph.microsoft.com", "api.bap.microsoft.com")
_URL = re.compile(r"https?://([^/:?#\s\"']+)[^\s\"']*", re.I)


def _pp_host(url: re.Match | None) -> bool:
    host = url.group(1).lower() if url else ""
    return any(host == h or host.endswith("." + h) for h in _PP_HOSTS)


# curl short options that take a value (curl.se/docs/manpage.html); in a cluster such as -sX DELETE the
# value-taking letter ends the cluster and its value is the rest of the word or the next word.
_CURL_VALUE = set("AbcCdDeEFHKmoPQrtTuUwxXyYz")


def _curl_short(words: list[str]) -> tuple[str | None, bool]:
    """The method (-X) and whether a body is sent (-d, -F, -T), read from curl's short-option clusters."""
    method, data = None, False
    for i, w in enumerate(words[1:], 1):
        if not re.match(r"-[A-Za-z]", w):
            continue
        for j, ch in enumerate(w[1:], 2):
            data = data or ch in "dFT"
            if ch == "X":
                method = w[j:] or (words[i + 1] if i + 1 < len(words) else None)
            if ch in _CURL_VALUE:
                break
    return method, data


def _http(words: list[str], line: str = "") -> Classification:
    """curl, wget, Invoke-RestMethod/WebRequest: only calls to Power Platform hosts are judged."""
    url = next((m for w in words[1:] if (m := _URL.search(w))), None)
    if url is None or "$" in url.group(1):  # the URL is in a variable: judge it by a host set elsewhere on the line
        url = next((m for m in _URL.finditer(line) if _pp_host(m)), url)
    if not _pp_host(url):
        return READ
    method = _opt(words, ("--request", "--method")) or _opt(words, ("-method",), ci=True)
    data = any(w.startswith(("--data", "--form", "--json", "--upload-file", "--post-", "--body-"))
               or w.lower().split(":")[0] in ("-body", "-infile") for w in words[1:])
    if _name(words[0]) == "curl":
        short_method, short_data = _curl_short(words)
        method, data = method or short_method, data or short_data
    if not method:
        method = "POST" if data else "HEAD" if any(w in ("-I", "--head") for w in words) else "GET"
    return _rest(_name(words[0]), method, url.group(0).rstrip(";"))  # a URL found on the line can end at `;`


# ---- PowerShell and other shells -----------------------------------------------------------------

_CMDLET = re.compile(r"^([a-z]+)-((?:admin)?powerapp\w*|(?:admin)?flow\w*|mg\w+|pnp\w+|powerbi\w*)$")
_PS_VERB = {
    **dict.fromkeys(("get", "find", "test", "search", "resolve", "select", "show", "read", "measure"), "read"),
    **dict.fromkeys(("export", "connect", "disconnect"), "local"),
    **dict.fromkeys(("remove", "restore", "clear", "reset"), "irreversible"),
    **dict.fromkeys(("set", "new", "add", "update", "publish", "enable", "disable", "grant", "revoke", "invoke",
                     "start", "stop", "copy", "move", "rename", "import"), "change"),
}
# Connect-PowerBIServiceAccount's aliases are from its [Alias] attribute in microsoft/powerbi-powershell.
_PS_SIGN_IN = {"add-powerappsaccount", "connect-mggraph", "connect-powerbiserviceaccount", "login-powerbi",
               "login-powerbiserviceaccount", "add-powerbiserviceaccount", "set-powerbiserviceaccount"}


def _cmdlet(words: list[str]) -> Classification:
    name = _name(words[0])
    if name in _PS_SIGN_IN or (name == "connect-pnponline" and any(w.lower() == "-interactive" for w in words)):
        return Classification("interactive", "", words[0])
    if name in ("invoke-mggraphrequest", "invoke-powerbirestmethod"):
        return _rest(words[0], _opt(words, ("-method",), ci=True) or "GET", _opt(words, ("-uri", "-url"), ci=True))
    kind = _PS_VERB.get(_CMDLET.match(name).group(1), "unknown")
    return READ if kind == "read" else Classification(kind, _clip(" ".join(words)), words[0])


_PS_VALUE = {"ep", "ex", "w", "wd", "o", "of", "in", "if", "v"}
_PS_VALUE_NAMES = ("executionpolicy", "windowstyle", "workingdirectory", "configurationname", "outputformat",
                   "inputformat", "settingsfile", "version", "psconsolefile", "custompipename")


def _pwsh(words: list[str]) -> Classification:
    """powershell/pwsh: classify the -Command script (or Windows PowerShell's positional command)."""
    i = 1
    while i < len(words) and words[i].startswith(("-", "/")):
        f = words[i].lower().lstrip("-/")
        if f == "c" or (len(f) >= 3 and "command".startswith(f)):
            i += 1
            break
        if f in ("e", "ec") or (len(f) >= 3 and "encodedcommand".startswith(f)):
            return Classification("unknown", _clip(f"an encoded PowerShell script: {' '.join(words)}"), "powershell")
        if f == "f" or (len(f) >= 2 and "file".startswith(f)):
            return READ  # a script file: its effect is not visible here
        i += 2 if f in _PS_VALUE or (len(f) >= 3 and any(n.startswith(f) for n in _PS_VALUE_NAMES)) else 1
    inner = " ".join(words[i:])
    return _shell(inner, True) if inner else READ


def _cmd(words: list[str]) -> Classification:
    for i, w in enumerate(words[1:], 1):
        if w.lower() in ("/c", "/k", "/r"):
            return _shell(" ".join(words[i + 1:]), False, escape=False)
    return READ


def _sh(words: list[str]) -> Classification:
    for i, w in enumerate(words[1:], 1):
        if not w.startswith("-"):
            return READ  # a script file
        if not w.startswith("--") and "c" in w:
            return _shell(words[i + 1], False) if i + 1 < len(words) else READ
    return READ


def _iex(words: list[str]) -> Classification:
    return _shell(" ".join(w for w in words[1:] if w.lower() != "-command"), True)


_HANDLERS = {
    "ppx": _ppx_cli, "uv": _uv, "python": _python, "pac": _pac, "az": _az, "m365": _m365, "microsoft365": _m365,
    "fab": _fab, "curl": _http, "wget": _http, "invoke-restmethod": _http, "invoke-webrequest": _http, "irm": _http,
    "iwr": _http, "powershell": _pwsh, "pwsh": _pwsh, "cmd": _cmd, "bash": _sh, "sh": _sh, "zsh": _sh,
    "eval": lambda words: _shell(" ".join(words[1:]), False), "invoke-expression": _iex, "iex": _iex,
}


# ---- MCP tools -----------------------------------------------------------------------------------

_MCP_NAME = re.compile(r"^mcp__((?:(?!__).)*)__(.+)$")
_OUR_SERVER = "plugin_power-platform-forge_ppx"

# Power BI Modeling MCP 1.0.0 (docs/PLATFORM-FACTS.md fact 17): tools multiplexed by `operation`.
_PBI_READ = re.compile(r"(?i)^(help|get.*|list.*|find|test|checkstatusofrefreshwithapi|exporttmdl|exporttmsl|execute"
                       r"|validate|report|getstatus|listactive|importfromtmdlfolder|importfrombimfile)$")
_PBI_FOLDER_WRITE = {"exporttotmdlfolder", "exporttobimfile"}

# FlowAgent in power-automate 3.2.1 (fact 18): 17 tenant changes, 5 local-state tools, 40 reads.
_FLOW_IRREVERSIBLE = {"delete_flow", "delete_connection", "cancel_all_runs"}
_FLOW_CHANGE = {"create_flow", "update_flow", "edit_flow", "copy_flow", "publish_flow", "disable_flow", "run_flow",
                "cancel_run", "resubmit_run", "pick_or_create_connection", "fix_connection", "smoke_test",
                "run_desktop_flow", "restore_backup"}
_FLOW_LOCAL = {"reconnect", "switch_account", "set_current_env", "set_current_flow", "clear_current_flow"}
_FLOW_READ = {
    "whoami", "doctor", "list_accounts", "list_environments", "resolve_environment", "get_current_env", "list_flows",
    "get_flow", "get_flow_context", "preview_update", "get_run_history", "get_run_details", "get_run_actions",
    "get_run_action_content", "get_run_action_repetitions", "diagnose_run", "get_expression_help", "get_current_flow",
    "get_past_trigger_inputs", "preflight_flow", "list_trigger_emulators", "list_connections", "resolve_refs",
    "resolve_params", "resolve_entity", "test_connection", "list_connectors", "get_connector", "search_operations",
    "get_operation_details", "invoke_operation", "list_datasets", "list_tables", "list_templates", "scaffold_flow",
    "validate_flow", "list_desktop_flows", "list_machine_groups", "list_backups", "get_backup",
}


def _mcp(tool_name: str, args: dict) -> Classification:
    m = _MCP_NAME.match(tool_name)
    if m is None or not re.match(MCP_MATCHER, tool_name):
        return READ
    server, tool = m.groups()
    if server == _OUR_SERVER:
        return _ppx_mcp(tool, args)
    if "powerbi-modeling" in server:
        return _pbi_modeling(tool, args)
    if "flowagent" in server:
        return _flowagent(tool, args)
    return Classification("unknown", _clip(f"{tool} on MCP server {server}"), server)


def _targets(args: dict, suffixes: tuple[str, ...]) -> str:
    """Up to three name-like argument values (one level of nesting), to say what a call touches."""
    pairs = list(args.items()) + [kv for v in args.values() if isinstance(v, dict) for kv in v.items()]
    found = [v for k, v in pairs if isinstance(v, str) and v and k.lower() != "operation" and k.lower().endswith(suffixes)]
    return _clip(", ".join(found[:3]), 120)


def _ppx_mcp(tool: str, args: dict) -> Classification:
    if tool in ("doctor", "verbs"):
        return READ
    verb, extra = args.get("verb"), args.get("args") or []
    if tool != "run" or not isinstance(verb, str) or not isinstance(extra, list) or not all(isinstance(a, str) for a in extra):
        return Classification("unknown", _clip(f"ppx MCP tool {tool} with arguments it does not take"), "ppx")
    tokens = verb.split() + extra + (["--dry-run"] if args.get("dry_run") else []) + (["--yes"] if args.get("yes") else [])
    result = _ppx(tokens)
    if result.kind == "interactive":
        return Classification("interactive", _clip("ppx " + " ".join(tokens), 300), "ppx")
    return result


def _pbi_modeling(tool: str, args: dict) -> Classification:
    binary = "powerbi-modeling-mcp"
    if tool == "accept_eula":
        return Classification("local", "Accept the Power BI Modeling MCP licence terms on this computer", binary)
    request = args.get("request") if isinstance(args.get("request"), dict) else args
    op = next((v for k, v in request.items() if k.lower() == "operation" and isinstance(v, str)), None)
    if op is None:
        return Classification("unknown", f"Power BI Modeling MCP {tool} without an operation", binary)
    if tool == "connection_operations" or _PBI_READ.match(op):
        return READ
    targets = _targets(request, ("name", "path"))
    if op.lower() in _PBI_FOLDER_WRITE:  # powerbi-modeling-mcp issue #96: existing definition files were deleted
        return Classification("irreversible", f"{op} writes the model to {targets or 'a local folder'} and can delete "
                                              "definition files already there", binary)
    where = "a Fabric workspace" if op.lower() == "deploytofabric" else "the connected Power BI model"
    noun = tool.removesuffix("_operations").replace("_", " ")
    return Classification("change", f"{noun} {op} on {where}" + (f" ({targets})" if targets else ""), binary)


def _flowagent(tool: str, args: dict) -> Classification:
    if tool in _FLOW_READ or (tool == "edit_flow" and args.get("dryRun") is True):
        return READ
    kind = ("irreversible" if tool in _FLOW_IRREVERSIBLE else "change" if tool in _FLOW_CHANGE
            else "local" if tool in _FLOW_LOCAL else "unknown")
    forced = args.get("forceManaged") is True and kind in ("change", "local")
    if forced:
        kind = "irreversible"
    targets = _targets(args, ("name", "id"))
    summary = f"{tool.replace('_', ' ').capitalize()} with FlowAgent" + (f" ({targets})" if targets else "")
    return Classification(kind, summary + ("; forceManaged overrides the managed-solution guard" if forced else ""),
                          "flowagent")
