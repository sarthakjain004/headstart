"""Base scraper: shared fetching plus the parse contract each ATS implements."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from headstart.models import Job

_USER_AGENT = "headstart/0.1 (job-board reader)"
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


class BaseScraper(ABC):
    """Fetch one company's postings from an ATS and normalize them to Jobs.

    Network and parsing are split on purpose: ``parse`` is pure and is what the
    tests exercise against recorded fixtures, while ``fetch_raw`` is the only
    part that touches the network.
    """

    ats: str  # set by each subclass

    def __init__(self, slug: str, company: str | None = None) -> None:
        self.slug = slug
        self.company = company or slug

    @abstractmethod
    def url(self) -> str:
        """The public JSON endpoint for this company's board."""

    @abstractmethod
    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        """Turn a raw API response into normalized Jobs."""

    def fetch_raw(self) -> Any:
        """GET the board JSON, retrying transient failures (rate limits, 5xx)."""
        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            request = urllib.request.Request(
                self.url(),
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
            except urllib.error.URLError as exc:
                last_error = exc
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
        raise last_error  # pragma: no cover - loop always returns or raises first

    def fetch(self) -> list[Job]:
        scraped_at = datetime.now(timezone.utc).isoformat()
        return self.parse(self.fetch_raw(), scraped_at)
