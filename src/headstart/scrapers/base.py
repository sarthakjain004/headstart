"""Base scraper: shared fetching plus the parse contract each ATS implements."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, TypeVar

from headstart import http
from headstart.models import Job

_USER_AGENT = "headstart/0.1 (job-board reader)"
_T = TypeVar("_T")
_R = TypeVar("_R")


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

    @abstractmethod
    def url(self) -> str:
        """The public endpoint for this company's board."""

    @abstractmethod
    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        """Turn a raw API/page response into normalized Jobs."""

    def _get(self) -> str:
        """GET the board URL as text via the reliable-fetch seam (retry lives there). Raises
        on a definitive HTTP error so a dead board surfaces as a per-company failure."""
        response = http.fetch(
            "GET",
            self.url(),
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
