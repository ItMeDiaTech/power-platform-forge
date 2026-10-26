"""Parse a ppx command line from the verb table, run the verb, and build the envelope.

`dispatch` is the single entry point for both the console script and the MCP server, so the
two can never disagree about what a verb does or prints.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from ppx import audit, datadir, envs
from ppx.changes import ChangeSet
from ppx.envelope import Envelope, NextAction, Result, dumps, fit_budget
from ppx.errors import NeedsConfirmation, PpxError, SetupNeeded, UsageError
from ppx.redact import scrub, scrub_text
from ppx.verbtable import GLOBAL_ARGS, VERBS, Arg, Verb


@dataclass
class Ctx:
    """Everything a verb handler needs. Handlers return their `data` dict and raise PpxError."""

    verb: Verb
    data_dir: Path | None
    state: envs.State
    profile: envs.Profile | None
    dry_run: bool = False
    yes: bool = False
    raw: bool = False
    refresh: bool = False
    fields: list[str] | None = None
    top: int | None = None
    timeout: float = 60
    interactive: bool = True
    changes: ChangeSet = field(default_factory=ChangeSet)
    warnings: list[str] = field(default_factory=list)
    next_actions: list[NextAction] = field(default_factory=list)
    target_env: dict | None = None

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def next(self, action: str, command: str | None = None, url: str | None = None) -> None:
        self.next_actions.append(NextAction(action, command, url))

    def need_data_dir(self) -> Path:
        if self.data_dir is None:
            raise SetupNeeded("this verb needs the ppx data directory",
                              next_actions=[NextAction("pass --data-dir <path>, or start a Claude Code session with the plugin")])
        return self.data_dir

    def need_profile(self, what: str = "this verb") -> envs.Profile:
        if self.profile is None:
            raise SetupNeeded(f"{what} needs a profile",
                              next_actions=[NextAction("create one", command="ppx env add work --url <environment-url> --use")])
        return self.profile


class _Parser(argparse.ArgumentParser):
    """No help exit and no abbreviations: the parser and the change-notice classifier must agree exactly."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("add_help", False)
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str):  # argparse would print and exit; ppx answers with an envelope instead
        raise UsageError(f"{message}; run `ppx <verb> --help` or `ppx verbs` for the arguments")


def _add_arg(parser: argparse.ArgumentParser, arg: Arg) -> None:
    kw: dict = {"help": arg.help}
    if arg.kind == "flag":
        kw["action"] = "store_true"
    elif arg.kind == "int":
        kw["type"] = int
    elif arg.kind in ("choice", "multi") and arg.choices:  # a multi without choices takes any value
        kw["choices"] = arg.choices
    if arg.kind == "multi":
        kw["nargs"] = "+"
    if arg.default is not None:
        kw["default"] = arg.default
    if arg.positional:
        if not arg.is_required:
            kw["nargs"] = "*" if arg.kind == "multi" else "?"
        parser.add_argument(arg.flags[0], **kw)
    else:
        if arg.is_required:
            kw["required"] = True
        parser.add_argument(*arg.flags, dest=arg.dest, **kw)


def build_parser() -> argparse.ArgumentParser:
    root = _Parser(prog="ppx", description="Power BI, Power Apps and SharePoint from the terminal; one JSON envelope per command.")
    for arg in GLOBAL_ARGS:
        _add_arg(root, arg)
    groups: dict[tuple[str, ...], argparse._SubParsersAction] = {}

    def subparsers_for(prefix: tuple[str, ...], parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
        if prefix not in groups:
            groups[prefix] = parser.add_subparsers(dest=f"_cmd{len(prefix)}", metavar="verb", parser_class=_Parser)
        return groups[prefix]

    parsers: dict[tuple[str, ...], argparse.ArgumentParser] = {(): root}
    for verb in VERBS:
        for i in range(1, len(verb.path) + 1):
            prefix = verb.path[:i]
            if prefix in parsers:
                continue
            parent = parsers[prefix[:-1]]
            is_leaf = i == len(verb.path)
            sub = subparsers_for(prefix[:-1], parent).add_parser(prefix[-1], help=verb.summary if is_leaf else None)
            parsers[prefix] = sub
            if is_leaf:
                sub.set_defaults(_verb=verb)
                for arg in verb.args:
                    _add_arg(sub, arg)
    return root


def _split_globals(argv: list[str]) -> list[str]:
    """Allow global flags anywhere on the line by moving them to the front before parsing."""
    takes_value = {f for a in GLOBAL_ARGS if a.kind != "flag" for f in a.flags}
    flags = {f for a in GLOBAL_ARGS if a.kind == "flag" for f in a.flags}
    front, rest, i = [], [], 0
    while i < len(argv):
        tok = argv[i]
        name = tok.split("=", 1)[0]
        if name in takes_value:
            front.append(tok)
            if "=" not in tok and i + 1 < len(argv):
                front.append(argv[i + 1])
                i += 1
        elif tok in flags:
            front.append(tok)
        else:
            rest.append(tok)
        i += 1
    return front + rest


def _project(data: dict, fields: list[str] | None, top: int | None) -> dict:
    """Apply --top and --fields to data.items for every verb, including those that ignore them."""
    if not isinstance(data.get("items"), list):
        return data
    if top is not None and len(data["items"]) > top:
        data["items"], data["more"] = data["items"][:top], True
    if fields:
        data["items"] = [{k: v for k, v in item.items() if k in fields} if isinstance(item, dict) else item
                         for item in data["items"]]
    return data


def _help(argv: list[str]) -> Result:
    from ppx.cmd_verbs import listing
    from ppx.verbtable import find

    words = [w for w in argv if not w.startswith("-")]
    verb, _ = find(words)
    if verb is not None:
        words = list(verb.path)
    elif not (words and any(v.path[0] == words[0] for v in VERBS)):
        words = []
    else:
        words = words[:1]
    return Result(fit_budget(Envelope(True, "help", listing(words)).to_dict()), 0)


@contextlib.contextmanager
def _data_dir_env(data_dir: Path | None):
    """Token lookups deep in ppx.http find the same data directory the command line chose."""
    if data_dir is None:
        yield
        return
    previous = os.environ.get("PPX_DATA_DIR")
    os.environ["PPX_DATA_DIR"] = str(data_dir)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("PPX_DATA_DIR", None)
        else:
            os.environ["PPX_DATA_DIR"] = previous


def dispatch(argv: list[str], *, interactive: bool = True, environ: dict | None = None) -> Result:
    """Run one ppx command line and return its envelope. Never prints to stdout."""
    operation = "ppx"
    ctx: Ctx | None = None
    if "-h" in argv or "--help" in argv:
        return _help(list(argv))
    try:
        args = build_parser().parse_args(_split_globals(list(argv)))
        verb: Verb | None = getattr(args, "_verb", None)
        if verb is None:
            raise UsageError("no verb given; run `ppx verbs` to list them")
        operation = ".".join(verb.path)
        try:
            data_dir = datadir.resolve(args.data_dir, environ)
        except datadir.DataDirMissing as exc:
            if verb.needs_data:
                raise SetupNeeded(str(exc)) from exc
            data_dir = None
        state = envs.load(data_dir) if data_dir else envs.State()
        profile = state.profiles.get(args.profile) if args.profile else state.active()
        if args.profile and profile is None:
            raise UsageError(f"no profile named {args.profile!r}; `ppx env ls` lists them")
        if args.top is not None and args.top < 1:
            raise UsageError(f"--top must be 1 or more, not {args.top}")
        ctx = Ctx(verb=verb, data_dir=data_dir, state=state, profile=profile, dry_run=args.dry_run, yes=args.yes,
                  raw=args.raw, refresh=args.refresh, top=args.top, timeout=float(args.timeout),
                  fields=[f.strip() for f in args.fields.split(",") if f.strip()] if args.fields else None,
                  interactive=interactive)
        if profile is not None:
            ctx.target_env = {"name": profile.name, "url": profile.url}
        module_name, func_name = verb.handler.split(":")
        handler = getattr(importlib.import_module(module_name), func_name)
        with contextlib.redirect_stdout(sys.stderr), _data_dir_env(data_dir):  # only the envelope reaches stdout
            data = handler(ctx, args) or {}
        if ctx.changes.items:
            data.setdefault("changes", ctx.changes.to_list())
            if ctx.dry_run:
                data["dry_run"] = True
        env = Envelope(True, operation, _project(data, ctx.fields, ctx.top), ctx.warnings, ctx.next_actions,
                       ctx.target_env)
        result = Result(env.to_dict(), 0)
    except Exception as caught:
        exc = caught if isinstance(caught, PpxError) else _unexpected(caught)
        warnings = ctx.warnings if ctx else []
        nxt = (ctx.next_actions if ctx else []) + exc.next_actions
        if isinstance(exc, NeedsConfirmation) and ctx and ctx.dry_run:
            # A dry run answers what the real run would change; that the real run needs --yes is part of it.
            data = {"changes": ctx.changes.to_list(), "dry_run": True, "needs_yes": exc.message}
            result = Result(Envelope(True, operation, data, warnings, nxt, ctx.target_env).to_dict(), 0)
        else:
            data = {"error": exc.message, **exc.data}
            if ctx and ctx.changes.items:
                data.setdefault("changes", ctx.changes.to_list())
            env = Envelope(False, operation, data, warnings, nxt, ctx.target_env if ctx else None)
            result = Result(env.to_dict(), exc.exit_code)
    _audit(ctx, argv, result)
    out = scrub(result.envelope)
    if not (ctx and ctx.raw):
        out = fit_budget(out)
    return Result(out, result.exit_code)


def _unexpected(exc: Exception) -> PpxError:
    """A bug or an unexpected reply (say, a proxy's HTML page) still ends in one envelope and an audit line."""
    print(scrub_text(traceback.format_exc()), file=sys.stderr)
    return PpxError(f"unexpected {type(exc).__name__}: {scrub_text(' '.join(str(exc).split()))[:300]}",
                    next_actions=[NextAction("an unexpected reply (such as a proxy's sign-in page) or a ppx bug; "
                                             "the full error is on stderr, so read it before retrying")])


def _audit(ctx: Ctx | None, argv: list[str], result: Result) -> None:
    if ctx is None or ctx.data_dir is None or ctx.dry_run:
        return
    if not ctx.changes.items and ctx.verb.effect in ("read", "interactive", "by-method"):
        return
    try:
        audit.record(ctx.data_dir, operation=".".join(ctx.verb.path), argv=list(argv),
                     profile=ctx.profile.name if ctx.profile else None, changes=ctx.changes.to_list(),
                     ok=result.envelope["ok"], exit_code=result.exit_code, dry_run=ctx.dry_run)
    except OSError as exc:
        result.envelope.setdefault("warnings", []).append(f"audit log not written: {exc}")


def _console_attached() -> bool:
    """True only for a real terminal. On Windows isatty() is also True for NUL, which is what an
    agent's PowerShell tool and `< /dev/null` give, so ask the console API instead."""
    if not sys.stdin.isatty():
        return False
    if sys.platform != "win32":
        return True
    import ctypes
    import msvcrt
    from ctypes import wintypes

    mode = wintypes.DWORD()
    try:
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    except OSError:
        return False
    return bool(ctypes.windll.kernel32.GetConsoleMode(wintypes.HANDLE(handle), ctypes.byref(mode)))


def main(argv: list[str] | None = None) -> int:
    result = dispatch(sys.argv[1:] if argv is None else argv,
                      interactive=_console_attached() or os.environ.get("PPX_INTERACTIVE") == "1")
    sys.stdout.write(dumps(result.envelope) + "\n")
    sys.stdout.flush()
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
