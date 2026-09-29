"""A count with its noun, as every answer says one: "1 job", "2,334 jobs", "1 company".

One rule for the whole server, so no answer reads "1 jobs", "1 job(s)" or "1 more were left out".
"""

from __future__ import annotations


def counted(n: int, singular: str, plural: str | None = None) -> str:
    """``n`` with thousands separators and ``singular`` when it is 1, else ``plural`` (the
    singular with an "s" unless given)."""
    return f"{n:,} {singular if n == 1 else plural or singular + 's'}"
