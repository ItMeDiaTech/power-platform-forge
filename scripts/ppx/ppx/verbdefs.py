"""Building blocks of the verb table. Data only, stdlib only: the change-notice hook imports it.

Effects:
  read          changes nothing anywhere; no notice
  local         changes only this computer (profiles, sign-in caches); notice
  change        changes the tenant reversibly (backups or undo exist); notice
  irreversible  overwrites, deletes or costs something; the hook asks first and ppx needs --yes
  interactive   opens a sign-in or prompt; the user should run it in their own terminal
  by-method     ppx raw: GET reads, DELETE is irreversible, other methods change
"""

from __future__ import annotations

from dataclasses import dataclass

EFFECTS = ("read", "local", "change", "irreversible", "interactive", "by-method")


@dataclass(frozen=True)
class Arg:
    flags: tuple[str, ...]
    help: str
    kind: str = "str"  # str | int | flag | choice | multi
    choices: tuple[str, ...] = ()
    required: bool | None = None  # None: positionals are required, options are not
    default: object = None

    @property
    def dest(self) -> str:
        name = self.flags[-1] if self.flags[0].startswith("-") else self.flags[0]
        return name.lstrip("-").replace("-", "_")

    @property
    def positional(self) -> bool:
        return not self.flags[0].startswith("-")

    @property
    def is_required(self) -> bool:
        return self.positional if self.required is None else self.required


@dataclass(frozen=True)
class Verb:
    path: tuple[str, ...]
    summary: str
    effect: str
    handler: str
    notice: str = ""
    args: tuple[Arg, ...] = ()
    escalate: tuple[tuple[str, str], ...] = ()  # (flag or flag=value, effect) that raise the effect
    needs_data: bool = True

    @property
    def name(self) -> str:
        return " ".join(self.path)


def a(*flags: str, help: str = "", **kw) -> Arg:
    return Arg(tuple(flags), help, **kw)
