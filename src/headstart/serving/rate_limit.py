"""How often one client may ask the Space's public read routes (ADR-0262).

A sliding window per client: at most ``limit`` requests admitted in any ``window_s`` seconds. The
Space is one process serving requests on threads (``app.run``), so a count held in memory behind
one lock is authoritative, as the résumé-read guard in ``app.py`` is. A refused request is not
counted, so a client that keeps asking while refused is let in again as soon as its oldest admitted
request leaves the window. Who a client is, and which requests are limited, is the app's to say.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable


class RateLimit:
    """At most ``limit`` admitted requests from one client in any ``window_s`` seconds."""

    def __init__(
        self,
        limit: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window_s = window_s
        self._clock = clock
        # Each client's admitted times, oldest first. Clients are kept in the order of their
        # latest admission, so the first is always the one idle longest (`_forget_idle`).
        self._admitted: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def admit(self, client: str) -> int:
        """0 when ``client`` may ask now, and this request is counted; otherwise the whole
        seconds until it may, and nothing is counted."""
        now = self._clock()
        with self._lock:
            self._forget_idle(now)
            times = self._admitted.setdefault(client, deque())
            while times and now - times[0] >= self._window_s:
                times.popleft()
            if len(times) >= self._limit:
                return math.ceil(self._window_s - (now - times[0]))
            times.append(now)
            self._admitted.move_to_end(client)
            return 0

    def _forget_idle(self, now: float) -> None:
        """Drop every client with nothing admitted inside the window, so the map holds the
        clients of the last window rather than every address seen since boot."""
        while self._admitted:
            times = next(iter(self._admitted.values()))
            if times and now - times[-1] < self._window_s:
                return
            self._admitted.popitem(last=False)
