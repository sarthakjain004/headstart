"""Base scraper: shared fetching plus the parse contract each ATS implements."""

from __future__ import annotations

import json
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from headstart.models import Job

_USER_AGENT = "headstart/0.1 (job-board reader)"


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
        request = urllib.request.Request(
            self.url(),
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def fetch(self) -> list[Job]:
        scraped_at = datetime.now(timezone.utc).isoformat()
        return self.parse(self.fetch_raw(), scraped_at)
