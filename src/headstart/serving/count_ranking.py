"""The order a served list of counts takes: most first, ties by name, so equal counts read the same
on every call. `/companies/locations` (ADR-0275) and `/requirements` (ADR-0331) both list theirs so.
"""

from __future__ import annotations

from collections import Counter


def most_first(counted: Counter[str]) -> list[tuple[str, int]]:
    """``counted``'s items, largest count first, ties by name."""
    return sorted(counted.items(), key=lambda item: (-item[1], item[0]))
