"""Turn a name or id the agent typed into exactly one item, or explain why not."""

from __future__ import annotations

from ppx.errors import PpxError

MAX_CANDIDATES = 5


class NotFound(PpxError):
    """No item matched; callers may refresh their inventory once and try again."""


class Ambiguous(PpxError):
    """Several items share the name; the envelope lists up to five with their ids."""


def pick(kind: str, wanted: str, items: list[dict], *, id_key: str = "id", name_key: str = "name",
         where: str = "", context_keys: tuple[str, ...] = ()) -> dict:
    """Exact id match first, then a case-insensitive exact name match."""
    w = wanted.strip().lower()
    by_id = [i for i in items if str(i.get(id_key, "")).lower() == w]
    if by_id:
        return by_id[0]
    by_name = [i for i in items if str(i.get(name_key, "")).strip().lower() == w]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        cands = [{k: i.get(k) for k in (id_key, name_key, *context_keys) if i.get(k) is not None}
                 for i in by_name[:MAX_CANDIDATES]]
        raise Ambiguous(f"{len(by_name)} {kind}s are named {wanted!r}{where}; pass the id instead",
                        data={"candidates": cands})
    near = [i for i in items if w in str(i.get(name_key, "")).lower()][:MAX_CANDIDATES]
    raise NotFound(f"no {kind} named {wanted!r}{where}",
                   data={"similar": [{id_key: i.get(id_key), name_key: i.get(name_key)} for i in near]} if near else {})
