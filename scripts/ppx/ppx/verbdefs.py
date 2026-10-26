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

# namedtuple, not dataclass: dataclasses imports inspect, which costs the hook about 45 ms on every call.
from collections import namedtuple

EFFECTS = ("read", "local", "change", "irreversible", "interactive", "by-method")


class Arg(namedtuple("Arg", "flags help kind choices required default", defaults=("str", (), None, None))):
    """flags; help; kind: str | int | flag | choice | multi; choices; required: None means positionals are
    required and options are not; default."""

    __slots__ = ()

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


class Verb(namedtuple("Verb", "path summary effect handler notice args escalate needs_data",
                      defaults=("", (), (), True))):
    """path; summary; effect; handler "module:function"; notice template; args; escalate: (flag or
    flag=value, effect) pairs that raise the effect; needs_data."""

    __slots__ = ()

    @property
    def name(self) -> str:
        return " ".join(self.path)


def a(*flags: str, help: str = "", **kw) -> Arg:
    return Arg(tuple(flags), help, **kw)
