"""Change-notice rules for command-line tools: ppx, python, pac, az, m365, fab, curl and the Power
Platform PowerShell cmdlets. Stdlib only."""

from __future__ import annotations

import re

from ppx.classify_common import (_RANK, _URL, READ, Classification, _by_method, _cli, _clip, _name, _opt, _path,
                                 _pp_host, _rest)
from ppx.verbtable import GLOBAL_ARGS, find

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
