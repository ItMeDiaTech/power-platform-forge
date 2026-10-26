"""What a verb changes, in plain words: the envelope's data.changes and the audit log."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ppx.errors import NeedsConfirmation


@dataclass(frozen=True)
class Change:
    what: str
    target: str
    reversible: bool = True
    costly: bool = False
    backup: str | None = None
    undo: str | None = None

    def to_dict(self) -> dict:
        out = {k: v for k, v in asdict(self).items() if v is not None}
        if not self.costly:
            out.pop("costly")
        return out


class ChangeSet:
    def __init__(self) -> None:
        self.items: list[Change] = []

    def add(self, what: str, target: str, *, reversible: bool = True, costly: bool = False,
            backup: str | None = None, undo: str | None = None) -> Change:
        change = Change(what, target, reversible, costly, backup, undo)
        self.items.append(change)
        return change

    def to_list(self) -> list[dict]:
        return [c.to_dict() for c in self.items]

    def require_yes(self, yes: bool) -> None:
        """Irreversible or costly changes need --yes; reversible ones never do."""
        risky = [c for c in self.items if not c.reversible or c.costly]
        if risky and not yes:
            raise NeedsConfirmation(
                "this makes irreversible or costly changes; re-run with --yes to confirm",
                data={"changes": self.to_list()},
            )
