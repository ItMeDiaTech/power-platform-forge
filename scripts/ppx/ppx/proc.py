"""Run external CLIs (az, pac, m365, fab, powershell) without a console window or a shell.

On Windows, az and m365 are .cmd batch shims that cmd.exe re-parses. Arguments to them are
quoted here and refused if they hold characters cmd.exe would still interpret, so text the
agent passes (a SharePoint filter, a DAX query) can never become a second command.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

from ppx.envelope import NextAction
from ppx.errors import PpxError, SetupNeeded

IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Proc:
    argv: list[str]
    code: int
    stdout: str
    stderr: str


class ProcTimeout(PpxError):
    pass


def which(name: str) -> str | None:
    return shutil.which(name)


def require(name: str, install: str) -> str:
    exe = which(name)
    if not exe:
        raise SetupNeeded(f"{name} is not installed or not on PATH",
                          next_actions=[NextAction(f"install {name}", command=install)])
    return exe


def cmd_quote(arg: str) -> str:
    """Quote one argument for a .cmd/.bat target, refusing what cmd.exe would still expand."""
    if any(ch in arg for ch in '"%\r\n\x00'):
        raise PpxError(f"argument {arg[:40]!r} holds a character that cannot be passed safely to a batch file "
                       "(a double quote, %, or a line break)")
    # Backslashes before the closing quote would escape it for the program's own argv parser.
    trailing = len(arg) - len(arg.rstrip("\\"))
    return '"' + arg + "\\" * trailing + '"'


def run(argv: list[str], *, timeout: float, input_text: str | None = None,
        env: dict | None = None, install_hint: str | None = None) -> Proc:
    exe = require(argv[0], install_hint or f"install {argv[0]}")
    full_env = None if env is None else {**os.environ, **env}
    if IS_WINDOWS and exe.lower().endswith((".cmd", ".bat")):
        command: list[str] | str = " ".join([cmd_quote(exe)] + [cmd_quote(a) for a in argv[1:]])
    else:
        command = [exe, *argv[1:]]
    try:
        cp = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            input=input_text, stdin=None if input_text is not None else subprocess.DEVNULL,
            env=full_env, creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProcTimeout(f"{argv[0]} did not finish within {timeout:g} s") from exc
    return Proc(list(argv), cp.returncode, cp.stdout or "", cp.stderr or "")


def run_interactive(argv: list[str], *, install_hint: str | None = None) -> int:
    """Run with the user's terminal attached (sign-in flows). The MCP server never calls this."""
    exe = require(argv[0], install_hint or f"install {argv[0]}")
    return subprocess.call([exe, *argv[1:]], stdout=sys.stderr)
