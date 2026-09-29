"""How many requests a route answers at once, in all and from one caller (ADR-0276).

:mod:`rate_limit` bounds how often a caller asks; this bounds how much it holds. A request to a
slow route holds its place until it is answered, so a cap on places in all is not enough: one
caller asking four slow things at once would hold every place, and everyone else would wait.
:class:`ConcurrencyLimit` keeps both caps behind one lock. The Space is one process answering on
threads, so the counts it holds are authoritative. Who a caller is, and which route is limited, is
the app's to say.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Mapping
from enum import StrEnum


class Refused(StrEnum):
    """Which cap a request met when no place came free in time."""

    CALLER = "caller"  # the caller already holds its share
    TOTAL = "total"  # every place is held


class ConcurrencyLimit:
    """At most ``total`` places held at once, and at most ``each`` by one caller, or the share
    ``shares`` names for it: a caller that stands for many people, such as Anthropic's range
    for every claude.ai user, may hold more than one person's (ADR-0334)."""

    def __init__(
        self, total: int, each: int, shares: Mapping[str, int] | None = None
    ) -> None:
        self._total = total
        self._each = each
        self._shares = dict(shares or {})
        self._held: Counter[str] = Counter()
        self._changed = threading.Condition()

    def share(self, caller: str) -> int:
        """The most places ``caller`` may hold at once."""
        return self._shares.get(caller, self._each)

    def take(self, caller: str, wait_s: float) -> Refused | None:
        """None once ``caller`` holds a place, waiting up to ``wait_s`` seconds for one; else
        which cap it met. A place taken is given back with :meth:`give_back`."""

        def free() -> bool:
            return self._held.total() < self._total and self._held[caller] < self.share(
                caller
            )

        with self._changed:
            if not self._changed.wait_for(free, timeout=wait_s):
                return (
                    Refused.CALLER
                    if self._held[caller] >= self.share(caller)
                    else Refused.TOTAL
                )
            self._held[caller] += 1
            return None

    def give_back(self, caller: str) -> None:
        with self._changed:
            self._held[caller] -= 1
            if not self._held[caller]:
                del self._held[caller]
            self._changed.notify_all()
