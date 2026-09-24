"""One process-wide spacing of request starts to a host that meters every tenant at once.

Moved out of ``adp.py`` when a second ATS needed it (zwayam's config call): each keeps its own
instance, at its own measured spacing.
"""

from __future__ import annotations

import asyncio
import threading
import time


class Pacer:
    """Spaces request starts to one host across every thread and event loop in the process.

    `harvest` scrapes many Boards concurrently in one process, so a delay kept per Board or per
    scraper instance multiplies by the Board count. This holds one next-free slot under a lock:
    :meth:`reserve` claims the next slot and says how long to wait for it, which the sync path
    sleeps and the async path awaits, so both paths draw from the same budget.

    A slot claimed before a :meth:`rest` would still fire into the refused window, so a caller
    that wakes while :meth:`resting` claims a fresh slot instead — which a rest has already put
    past the window's end.
    """

    def __init__(self, spacing: float) -> None:
        self.spacing = spacing
        self._lock = threading.Lock()
        self._next = 0.0
        self._rest_until = 0.0

    def reserve(self) -> float:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.spacing
            return start - now

    def rest(self, seconds: float) -> None:
        """Hold every request until ``seconds`` from now — a refused window's remainder."""
        with self._lock:
            until = time.monotonic() + seconds
            self._next = max(self._next, until)
            self._rest_until = max(self._rest_until, until)

    def resting(self) -> bool:
        with self._lock:
            return time.monotonic() < self._rest_until

    def wait(self) -> None:
        """Sleep until this caller's slot, re-claiming one if a rest began meanwhile."""
        while True:
            time.sleep(self.reserve())
            if not self.resting():
                return

    async def wait_async(self) -> None:
        while True:
            await asyncio.sleep(self.reserve())
            if not self.resting():
                return
