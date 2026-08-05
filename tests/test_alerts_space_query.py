"""The Space search client: URL shape and the cold-Space retry budget (ADR-0035)."""

import urllib.parse

import pytest

from headstart.alerts import space_query as sq
from headstart.alerts.store import Subscription

SUB = Subscription(
    id="abc",
    email="ada@example.com",
    query="backend engineer",
    search_filters={"remote": "true", "max_years": "3"},
    watermark="2026-08-02T12:00:00+00:00",
)
AFTER = "2026-08-02T12:00:00+00:00"


def _params(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))


def test_request_url_carries_query_filters_and_the_exact_cutoff():
    params = _params(sq.request_url("https://space.example/", SUB, AFTER))
    assert params["q"] == "backend engineer"
    assert params["first_seen_after"] == AFTER
    assert params["remote"] == "true" and params["max_years"] == "3"
    assert params["k"] == str(sq.K) == "100"  # the Space's _MAX_K


def test_returns_rows_on_the_first_try():
    rows = [{"title": "Engineer"}]
    assert sq.newly_seen("https://s", SUB, AFTER, fetch=lambda url: rows) is rows


def test_retries_a_cold_space_then_succeeds():
    attempts, waited = [], []

    def flaky(url):
        attempts.append(url)
        if len(attempts) < 3:
            raise TimeoutError("cold start")
        return [{"title": "Engineer"}]

    rows = sq.newly_seen("https://s", SUB, AFTER, fetch=flaky, sleep=waited.append)
    assert len(rows) == 1
    assert len(attempts) == 3
    assert waited == [15, 30]  # the budget is sized to a cold start, not a blip


def test_raises_once_the_budget_is_spent():
    waited = []

    def dead(url):
        raise TimeoutError("still down")

    with pytest.raises(sq.SearchUnavailable):
        sq.newly_seen("https://s", SUB, AFTER, fetch=dead, sleep=waited.append)
    assert waited == [15, 30, 60]  # three retries, then give up


def test_non_list_reply_is_an_error_not_rows():
    with pytest.raises(sq.SearchUnavailable):
        sq.newly_seen(
            "https://s", SUB, AFTER, fetch=lambda url: {"error": "invalid filter"}
        )
