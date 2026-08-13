"""The Space search client: URL shape and the cold-Space retry budget (ADR-0035)."""

import urllib.error
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


def test_auth_headers_carry_the_service_token_only_when_one_is_set(monkeypatch):
    # The Space's wall gates /search (ADR-0042); this run has no Google identity, so the
    # service token is its credential. Unset must send nothing rather than an empty
    # bearer, which would read as a malformed credential rather than as "anonymous".
    monkeypatch.delenv("ALERTS_TOKEN", raising=False)
    assert sq.auth_headers() == {}
    monkeypatch.setenv("ALERTS_TOKEN", "  service-token  ")
    assert sq.auth_headers() == {"Authorization": "Bearer service-token"}


def test_a_permanent_auth_failure_is_not_retried():
    # A 401 cannot become a 200 by waiting. The ladder is sized to a Space cold start,
    # so retrying an unauthorised call spends 105s per Subscription to fail anyway —
    # which is how one broken credential turned into a run-long stall.
    waited = []

    def unauthorised(url):
        raise urllib.error.HTTPError(url, 401, "UNAUTHORIZED", {}, None)

    with pytest.raises(sq.SearchUnavailable):
        sq.newly_seen("https://s", SUB, AFTER, fetch=unauthorised, sleep=waited.append)
    assert waited == []


def test_a_cold_or_throttled_space_still_gets_the_full_budget():
    # The converse: 429 and 408 are transient by definition, and 5xx is what a cold Space
    # returns while it reloads. Narrowing the no-retry rule to permanent 4xx keeps these.
    for code in (408, 429, 500, 503):
        waited = []

        def flaky(url, code=code):
            raise urllib.error.HTTPError(url, code, "later", {}, None)

        with pytest.raises(sq.SearchUnavailable):
            sq.newly_seen("https://s", SUB, AFTER, fetch=flaky, sleep=waited.append)
        assert waited == [15, 30, 60], f"HTTP {code} lost its retry budget"


def test_non_list_reply_is_an_error_not_rows():
    with pytest.raises(sq.SearchUnavailable):
        sq.newly_seen(
            "https://s", SUB, AFTER, fetch=lambda url: {"error": "invalid filter"}
        )
