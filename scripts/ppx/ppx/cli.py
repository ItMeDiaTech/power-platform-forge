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
from dataclasses import dataclass, field
from pathlib import Path

from ppx import audit, datadir, envs
from ppx.changes import ChangeSet
from ppx.envelope import Envelope, NextAction, Result, dumps, fit_budget
from ppx.errors import PpxError, SetupNeeded, UsageError
from ppx.redact import scrub
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
    def error(self, message: str):  # argparse would print and exit; ppx answers with an envelope instead
        raise UsageError(f"{message}; usage: {self.format_usage().strip()}")


def _add_arg(parser: argparse.ArgumentParser, arg: Arg) -> None:
    kw: dict = {"help": arg.help}
    if arg.kind == "flag":
        kw["action"] = "store_true"
    elif arg.kind == "int":
        kw["type"] = int
    elif arg.kind in ("choice", "multi"):
        kw["choices"] = arg.choices
    if arg.kind == "multi":
        kw["nargs"] = "+"
    if arg.default is not None:
        kw["default"] = arg.default
    if arg.positional:
        if not arg.is_required:
            kw["nargs"] = "?"
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


def _project(data: dict, fields: list[str] | None) -> dict:
    if not fields or not isinstance(data.get("items"), list):
        return data
    data["items"] = [{k: v for k, v in item.items() if k in fields} if isinstance(item, dict) else item
                     for item in data["items"]]
    return data


def dispatch(argv: list[str], *, interactive: bool = True, environ: dict | None = None) -> Result:
    """Run one ppx command line and return its envelope. Never prints to stdout."""
    operation = "ppx"
    ctx: Ctx | None = None
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
        ctx = Ctx(verb=verb, data_dir=data_dir, state=state, profile=profile, dry_run=args.dry_run, yes=args.yes,
                  raw=args.raw, refresh=args.refresh, top=args.top, timeout=float(args.timeout),
                  fields=[f.strip() for f in args.fields.split(",") if f.strip()] if args.fields else None,
                  interactive=interactive)
        if profile is not None:
            ctx.target_env = {"name": profile.name, "url": profile.url}
        module_name, func_name = verb.handler.split(":")
        handler = getattr(importlib.import_module(module_name), func_name)
        with contextlib.redirect_stdout(sys.stderr):  # nothing but the envelope may reach stdout
            data = handler(ctx, args) or {}
        if ctx.changes.items:
            data.setdefault("changes", ctx.changes.to_list())
            if ctx.dry_run:
                data["dry_run"] = True
        env = Envelope(True, operation, _project(data, ctx.fields), ctx.warnings, ctx.next_actions, ctx.target_env)
        result = Result(env.to_dict(), 0)
    except PpxError as exc:
        warnings = ctx.warnings if ctx else []
        nxt = (ctx.next_actions if ctx else []) + exc.next_actions
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


def main(argv: list[str] | None = None) -> int:
    result = dispatch(sys.argv[1:] if argv is None else argv,
                      interactive=sys.stdin.isatty() or os.environ.get("PPX_INTERACTIVE") == "1")
    sys.stdout.write(dumps(result.envelope) + "\n")
    sys.stdout.flush()
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
