"""Base scraper: shared fetching plus the parse contract each ATS implements."""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, TypeVar

from headstart import http, log
from headstart.models import Job

_USER_AGENT = "headstart/0.1 (job-board reader)"
_T = TypeVar("_T")
_R = TypeVar("_R")

# Default HTTP/2 multiplexing width (concurrent streams per host) for fan_out_async — 100 is around
# the common server MAX_CONCURRENT_STREAMS. Override per-call, via HEADSTART_H2_STREAMS, or
# run_scrapers --streams N. Read at call time (below) so a CLI flag can set the env before the scrape.
_DEFAULT_H2_STREAMS = 100


class BaseScraper(ABC):
    """Fetch one company's postings from an ATS and normalize them to Jobs.

    Network and parsing are split on purpose: ``parse`` is pure and is what the
    tests exercise against recorded fixtures, while ``_get`` is the only part that
    touches the network. JSON boards use the default ``fetch_raw`` (decode + parse);
    HTML boards (Zoho) override ``fetch_raw`` to keep the raw text.
    """

    ats: str  # set by each subclass

    def __init__(self, slug: str, company: str | None = None) -> None:
        self.slug = slug
        self.company = company or slug

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """Derive this scraper's ``slug`` from a discovered (tenant, url) row.

        Most ATSes are keyed by the bare tenant label, which is the default. Override only
        where the scraper interprets its slug differently (zoho wants the careers host,
        workday the full careers URL) — the inverse of what ``url()`` does with the slug.
        """
        return tenant

    def board_key(self) -> str:
        """This board's ``{ats}:{slug}`` key — the ``board_of`` prefix its job ids carry.

        Override where the id's Board segment isn't the bare slug (Workday derives ``{company}/{site}``
        from its careers-URL slug). Lets index maintenance (eviction / dead-Board prune, ADR-0023)
        map a ledger entry to the exact key its rows use."""
        return f"{self.ats}:{self.slug}"

    @abstractmethod
    def url(self) -> str:
        """The public endpoint for this company's board."""

    @abstractmethod
    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        """Turn a raw API/page response into normalized Jobs."""

    def _get(self, url: str | None = None) -> str:
        """GET a board URL as text via the reliable-fetch seam (retry lives there). Defaults to
        ``self.url()``; pass an explicit ``url`` to fetch a secondary endpoint (e.g. Keka's careers
        page for the tenant id). Raises on a definitive HTTP error so a dead board surfaces as a
        per-company failure."""
        response = http.fetch(
            "GET",
            url or self.url(),
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/json, text/html",
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.text

    def fetch_raw(self) -> Any:
        return json.loads(self._get())

    def fetch(self) -> list[Job]:
        scraped_at = datetime.now(timezone.utc).isoformat()
        return self.parse(self.fetch_raw(), scraped_at)

    @staticmethod
    def fan_out(
        items: Sequence[_T],
        fn: Callable[[_T], _R],
        *,
        workers: int = 8,
        default: _R | None = None,
    ) -> list[_R | None]:
        """Apply ``fn`` to each item across a bounded thread pool, isolating per-item failures.

        Returns results aligned to ``items`` — input order, not completion order — where each
        entry is ``fn(item)`` or ``default`` if that call raised. One item's failure never sinks
        the batch: the detail passes are network-bound, so a single 404 or timeout must not drop
        the rest of the Board's Jobs. ``workers`` bounds the pool; a scraper hammering one
        rate-limited host passes a smaller value (trakstar uses 4 under DataDome).
        """
        results: list[_R | None] = [default] * len(items)
        if not items:
            return results
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fn, item): i for i, item in enumerate(items)}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception:  # noqa: BLE001 - one item's failure must not sink the batch
                    results[index] = default
        return results

    @staticmethod
    def fan_out_async(
        items: Sequence[_T],
        fn: Callable[[Any, _T], Awaitable[_R]],
        *,
        concurrency: int | None = None,
        default: _R | None = None,
    ) -> list[_R | None]:
        """HTTP/2-multiplexed counterpart to :meth:`fan_out` (ADR-0015).

        ``fn(session, item)`` returns an awaitable. One ``curl_cffi`` ``AsyncSession`` is shared across
        every item, so same-host requests ride as concurrent **streams over one HTTP/2 connection**
        instead of one connection per thread. ``concurrency`` bounds the in-flight streams (the
        multiplexing width); when None it resolves to ``HEADSTART_H2_STREAMS`` or
        :data:`_DEFAULT_H2_STREAMS` (100) at call time. Results are input-aligned and a raising item
        becomes ``default`` — same contract as :meth:`fan_out`. Runs its own event loop, so a sync
        thread-pool caller (one board per company thread) can invoke it directly.
        """
        if not items:
            return [default] * len(items)
        if concurrency is None:
            concurrency = int(
                os.environ.get("HEADSTART_H2_STREAMS") or _DEFAULT_H2_STREAMS
            )
        return asyncio.run(BaseScraper._gather_async(items, fn, concurrency, default))

    @staticmethod
    async def _gather_async(
        items: Sequence[_T],
        fn: Callable[[Any, _T], Awaitable[_R]],
        concurrency: int,
        default: _R | None,
    ) -> list[_R | None]:
        from curl_cffi.requests import AsyncSession

        sem = asyncio.Semaphore(concurrency)
        results: list[_R | None] = [default] * len(items)
        async with AsyncSession(impersonate="chrome") as session:

            async def one(index: int, item: _T) -> None:
                async with sem:
                    try:
                        results[index] = await fn(session, item)
                    except Exception:  # noqa: BLE001 - one item's failure must not sink the batch
                        results[index] = default

            await asyncio.gather(*(one(i, item) for i, item in enumerate(items)))
        return results

    def report_detail_gaps(self, results: Sequence[Any], what: str = "details") -> None:
        """Log how many of a detail pass's results came back empty (None) — the gaps behind
        ADR-0021's null fields. One INFO line per Board, only when something is missing; the
        failure was isolated per item (the fan_out contract), so this line is usually the
        only trace the gap leaves."""
        missing = sum(1 for r in results if r is None)
        if missing:
            log.get(f"headstart.scrapers.{self.ats}").info(
                f"{self.board_key()}: {missing}/{len(results)} {what} missing"
            )

    @staticmethod
    def async_fanout_enabled() -> bool:
        """Whether the detail pass uses the multiplexed async path (ADR-0015, default per ADR-0016).

        On by default; set ``HEADSTART_ASYNC_FANOUT=0`` to fall back to the sync thread-pool path.
        Centralised here so every detail-fetch scraper shares one policy.
        """
        return os.environ.get("HEADSTART_ASYNC_FANOUT", "1") != "0"
