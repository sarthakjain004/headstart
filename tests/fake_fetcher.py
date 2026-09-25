"""The shared test double for the Fetcher seam (ADR-0153, ADR-0199) — what tests inject instead of
writing another. The older per-file fakes (test_fetcher, test_bamboohr, test_jibe, test_cornerstone)
now build on it too.

A Scraper built with ``fetcher=FakeFetcher(route)`` sends every request — listing, detail, sync or
multiplexed — through ``route(method, url, kwargs)``, which returns a :class:`FakeResponse` or an
exception to raise. Tests then assert on observable outcomes (Jobs, truncation, loss labels) and
on :attr:`FakeFetcher.requests`, never on a Scraper's private methods.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from headstart.network import http


class FakeResponse:
    """The slice of ``curl_cffi``'s ``Response`` the Scrapers read."""

    def __init__(
        self,
        status_code: int = 200,
        text: str = "",
        *,
        url: str = "",
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = headers or {}
        self.content = text.encode() if content is None else content

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise http.RequestsError(f"HTTP {self.status_code}", response=self)

    def close(self) -> None:
        pass


@dataclass(frozen=True)
class FakeRequest:
    method: str
    url: str
    kwargs: dict[str, Any]


Route = Callable[[str, str, dict[str, Any]], "FakeResponse | Exception"]


class FakeFetcher:
    """A :class:`headstart.network.fetcher.Fetcher` whose answers come from ``route``."""

    def __init__(self, route: Route) -> None:
        self.route = route
        self.requests: list[FakeRequest] = []
        #: One entry per ``clear_cookies`` call: the domain asked for, or None for the whole jar.
        #: A route that answers differently once the jar is cleared (Workday's stale-cookie 400,
        #: ADR-0103) reads this.
        self.cookie_clears: list[str | None] = []

    def fetch(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append(FakeRequest(method, url, kwargs))
        outcome = self.route(method, url, kwargs)
        if isinstance(outcome, Exception):
            raise outcome
        outcome.url = outcome.url or url
        return outcome

    async def fetch_async(
        self, session: Any, method: str, url: str, **kwargs: Any
    ) -> FakeResponse:
        return self.fetch(method, url, **kwargs)

    def clear_cookies(self, domain: str | None = None) -> None:
        self.cookie_clears.append(domain)

    def urls(self) -> list[str]:
        return [request.url for request in self.requests]
