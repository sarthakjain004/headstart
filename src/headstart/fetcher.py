"""The Fetcher seam (ADR-0153): what a scraper needs from its HTTP client, named once so
``headstart.http`` and ``headstart.browser_http`` can each sit behind it as a real adapter
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
  machinery. ``headstart.browser_http.BrowserFetcher`` is exactly that case: it implements
  ``fetch`` only, because darwinbox's browser escalation never runs a multiplexed detail pass
  (see that module's docstring) — forcing a ``fetch_async`` onto one warmed tab would be the
  unnatural shape this protocol is deliberately declining to invent.
- ``clear_cookies`` — forget the cookies this fetcher holds, for one ``domain`` or all of them
  (ADR-0199). A cookie jar is transport state a scraper cannot otherwise reach through the seam,
  and two scrapers need to reset it: Workday answers a stale session cookie with a 400 that only a
  cleared jar cures (ADR-0103), and Cornerstone's career-site page leaves cookies that make its
  tenant host refuse the session header. A domain the jar holds nothing for is not an error —
  there was nothing to forget. ``BrowserFetcher`` leaves this unimplemented too: its cookies are
  the Cloudflare clearance its navigation earned, and nothing ever asks a warmed tab to drop them.
"""

from __future__ import annotations

from typing import Any, Protocol


class Fetcher(Protocol):
    """What :class:`~headstart.scrapers.base.BaseScraper` can be given instead of
    reaching ``headstart.http`` directly."""

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
