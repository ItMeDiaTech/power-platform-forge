"""Errors that end a verb. Each carries the exit code and the next actions that fix it."""

from __future__ import annotations

from ppx.envelope import NextAction


class PpxError(Exception):
    """A verb failed. Exit 1 by default; exit 2 means the user or an admin must act first."""

    exit_code = 1

    def __init__(self, message: str, *, next_actions: list[NextAction] | None = None, data: dict | None = None):
        super().__init__(message)
        self.message = message
        self.next_actions = list(next_actions or [])
        self.data = dict(data or {})


class NeedsUser(PpxError):
    """The verb cannot continue until a person does something (sign in, confirm, ask IT)."""

    exit_code = 2


class NeedsConfirmation(NeedsUser):
    """The change is irreversible or costly and `--yes` was not given."""


class SetupNeeded(NeedsUser):
    """The data directory or a prerequisite is missing."""


class UsageError(PpxError):
    """The command line does not parse."""
