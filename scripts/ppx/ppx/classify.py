"""Decide what a Bash, PowerShell or MCP tool call would change, for the change-notice hook.

Stdlib only, importing only ppx.verbtable, ppx.redact and the classify_* rule modules, because
the hook loads it on every shell call. Kinds, least to most severe:
  read          changes nothing, or is not a Power Platform command; no notice
  local         changes only this computer (profiles, sign-in caches, local files); notice
  change        changes the tenant; notice, or a prompt in confirm mode
  interactive   opens a sign-in; the user should run it in their own terminal
  unknown       a Power Platform command this module does not know; the user is asked
  irreversible  deletes, overwrites, imports or restores; the user is asked
A compound command takes the most severe kind of its parts.

The rules live in classify_common (kinds, helpers), classify_cli (per-tool rules) and classify_mcp;
this module parses shell lines into commands and dispatches each to its rule.
"""

from __future__ import annotations

import re
import shlex

from ppx.classify_cli import _CMDLET, _az, _cmdlet, _fab, _http, _m365, _pac, _ppx_cli, _python
from ppx.classify_common import KINDS, READ, Classification, _clip, _name, _worst
from ppx.classify_mcp import MCP_MATCHER, _mcp

__all__ = ["KINDS", "MCP_MATCHER", "Classification", "classify"]

def classify(tool_name: str, tool_input: dict) -> Classification:
    if tool_name in ("Bash", "PowerShell"):
        return _shell(str(tool_input.get("command") or ""), tool_name == "PowerShell")
    if tool_name.startswith("mcp__"):
        return _mcp(tool_name, tool_input if isinstance(tool_input, dict) else {})
    return READ


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
