"""A count with its noun, as every answer says one: "1 job", "2,334 jobs", "1 company".

One rule for the whole server, so no answer reads "1 jobs", "1 job(s)" or "1 more were left out".
Its verb agrees by the same rule (:func:`verb`), and a list of names reads "a, b and c"
(:func:`listed`).
"""

from __future__ import annotations

from collections.abc import Sequence


def counted(n: int, singular: str, plural: str | None = None) -> str:
    """``n`` with thousands separators and ``singular`` when it is 1, else ``plural`` (the
    singular with an "s" unless given)."""
    return f"{n:,} {singular if n == 1 else plural or singular + 's'}"


def verb(n: int, singular: str, plural: str) -> str:
    """The verb that agrees with ``n`` things: ``singular`` ("is") when it is 1, else
    ``plural`` ("are")."""
    return singular if n == 1 else plural


def listed(items: Sequence[str]) -> str:
    """``items`` as an answer lists them: "a", "a and b", "a, b and c"."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
