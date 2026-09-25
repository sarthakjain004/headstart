"""The Fetcher seam (ADR-0153): what a scraper needs from its HTTP client, named once so
``headstart.network.http`` and ``headstart.network.browser_http`` can each sit behind it as a real adapter
instead of a scraper reaching a module global directly.

Three capabilities, not one artificially merged shape:

- ``fetch`` — issue one request, return whatever settles (a status code, a ``.json()``, a
  ``.raise_for_status()`` — the slice of ``curl_cffi``'s ``Response`` every caller in this repo
  already uses) for the caller to classify. This is the whole surface :meth:`BaseScraper._get`/
  :meth:`~BaseScraper._fetch` need, and the one every real adapter is expected to provide.
- ``fetch_async`` — the multiplexed counterpart :meth:`BaseScraper._get_async`/
  :meth:`~BaseScraper._fetch_async` need, over a caller-supplied session object. Declared here
  because the default (HTTP) adapter is what every scraper's async detail pass runs against
  today, but it is honestly a narrower promise than ``fetch``: an adapter with no multiplexed
  path (a browser tab is one session, not many concurrent HTTP/2 streams) can leave it
  unimplemented as long as nothing calls it. Python does not enforce ``Protocol`` conformance at
  runtime and this repo runs no type checker in CI, so that is a contract stated in prose, not
  machinery. ``headstart.network.browser_http.BrowserFetcher`` is exactly that case: it implements
  ``fetch`` only, because darwinbox's browser escalation never runs a multiplexed detail pass
  (see that module's docstring) — forcing a ``fetch_async`` onto one warmed tab would be the
  unnatural shape this protocol is deliberately declining to invent.
- ``clear_cookies`` — forget the cookies this fetcher holds, for one ``domain`` or all of them
  (ADR-0199). A cookie jar is transport state a scraper cannot otherwise reach through the seam,
  and two scrapers need to reset it: Workday answers a stale session cookie with a 400 that only a
  cleared jar cures (ADR-0103), and Cornerstone's career-site page leaves cookies that make its
  tenant host refuse the session header. A domain the jar holds nothing for is not an error —
  there was nothing to forget. ``BrowserFetcher`` leaves this unimplemented too (its docstring
  says why).

A scraper never calls its ``Fetcher`` directly: :class:`BoardFetcher` binds it to the Board, so
every request carries the Board's spare-egress opt-in and attribution (ADR-0204). That binding is
passed as keyword arguments, which is why ``BrowserFetcher`` — whose ``fetch`` takes ``json`` only
and refuses everything else — no longer matches this protocol's ``fetch(**kwargs)``: no
``BoardFetcher`` ever wraps it, and darwinbox calls it directly for the walled path.
"""

from __future__ import annotations

from typing import Any, Protocol

from headstart.network import spare_egress


class Fetcher(Protocol):
    """What :class:`~headstart.scrapers.base.BaseScraper` can be given instead of
    reaching ``headstart.network.http`` directly."""

    def fetch(self, method: str, url: str, **kwargs: Any) -> Any:
        """Issue one request; return whatever settles, for the caller to classify."""
        ...

    async def fetch_async(
        self, session: Any, method: str, url: str, **kwargs: Any
    ) -> Any:
        """The multiplexed counterpart to :meth:`fetch`, over a caller-supplied session."""
        ...

    def clear_cookies(self, domain: str | None = None) -> None:
        """Forget this fetcher's cookies for ``domain``, or every cookie when it is None."""
        ...


class BoardFetcher:
    """A :class:`Fetcher` bound to one Board (ADR-0204): the Board's spare-egress opt-in and its
    attribution travel with the fetcher, so no request made through it can drop them.

    Before this, every request carried them as keyword arguments a scraper had to spread into
    each call (``**self._egress()``), and a call that forgot was silently inert: its wall never
    marked, its retries never attributed. Here the binding happens once, and :meth:`fetch`/
    :meth:`fetch_async` add exactly the keyword arguments ``headstart.network.http.fetch`` has always
    received, so the request on the wire is unchanged.

    ``egress_group`` is None for a scraper that never opted into the spare egress; its requests
    then carry only ``egress_board``, which steers nothing and names the Board in the retry log.
    ``wall_statuses`` are the statuses that mark the group walled (``http.fetch``'s ``egress_on``).
    """

    def __init__(
        self,
        inner_fetcher: Fetcher,
        *,
        board_key: str,
        egress_group: str | None,
        wall_statuses: frozenset[int],
    ) -> None:
        self._inner_fetcher = inner_fetcher
        self._board_key = board_key
        self._egress_group = egress_group
        self._wall_statuses = wall_statuses

    def egress_binding(self, *, marks_wall: bool = True) -> dict[str, Any]:
        """The keyword arguments :meth:`fetch` adds to every request it forwards.

        ``marks_wall=False`` keeps the **routing** and drops only the **marking**: the request
        still rides the spare egress once the group is walled, but its own failures can never be
        what walls it — for a request whose non-200 means something other than "this IP is
        refused" (Eightfold's API-availability probe, ADR-0063). Dropping the routing too would
        send it over the spent IP on exactly the shard the fallback exists to rescue."""
        if self._egress_group is None:
            return {"egress_board": self._board_key}
        return {
            "egress_group": self._egress_group,
            "egress_on": self._wall_statuses if marks_wall else frozenset(),
            "egress_board": self._board_key,
        }

    def fetch(
        self,
        method: str,
        url: str,
        *,
        marks_wall: bool = True,
        direct: bool = False,
        **kwargs: Any,
    ) -> Any:
        """One request through the inner fetcher, carrying this Board's egress binding.

        ``direct=True`` sends it with no binding at all — the shard's own route, no group and no
        attribution: Workday's listing retrying once off a spare egress that handed back a
        non-JSON page, and :meth:`BaseScraper.alias_key`'s redirect probe, which never carried
        one."""
        binding = {} if direct else self.egress_binding(marks_wall=marks_wall)
        return self._inner_fetcher.fetch(method, url, **binding, **kwargs)

    async def fetch_async(
        self,
        session: Any,
        method: str,
        url: str,
        *,
        marks_wall: bool = True,
        direct: bool = False,
        **kwargs: Any,
    ) -> Any:
        """The multiplexed counterpart to :meth:`fetch`, over a caller-supplied session."""
        binding = {} if direct else self.egress_binding(marks_wall=marks_wall)
        return await self._inner_fetcher.fetch_async(
            session, method, url, **binding, **kwargs
        )

    def stream_width(self, ceiling: int) -> int:
        """How wide this Board's fan-out may go now, at most ``ceiling``: narrowed once its
        egress group has walled (:func:`headstart.network.spare_egress.stream_width`, #195)."""
        return spare_egress.stream_width(self._egress_group, ceiling)

    def clear_cookies(self, domain: str | None = None) -> None:
        """Clear the inner fetcher's cookies (:meth:`Fetcher.clear_cookies`)."""
        self._inner_fetcher.clear_cookies(domain)
