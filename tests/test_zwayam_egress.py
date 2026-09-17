"""zwayam's spare-egress opt-in — the one thing standing between a spent quota and a lost Board.

zwayam meters cumulative requests per IP across the single shared API every tenant is probed
through, and refuses with a bare 403. Without the opt-in the request names an egress group but an
empty `egress_on`, so it is routed over the spare egress yet nothing ever *marks* the group walled
— and 47-71% of attempted Boards failed in each of runs 35175065218-35188643520.
"""

from __future__ import annotations

from headstart import http
from headstart.scrapers.zwayam import ZwayamScraper


def test_zwayam_marks_the_wall_on_a_bare_403():
    assert 403 in ZwayamScraper.egress_fallback_on, (
        "zwayam's wall is a bare 403 — a 429-only opt-in never fires"
    )


def test_the_opt_in_is_retryable_or_it_only_helps_the_next_board():
    """`egress_on` outside `retry_on` is marked but never retried, so *this* request settles on
    the wall it just reported. 403 is in the default `TRANSIENT`, so it gets its second attempt."""
    assert ZwayamScraper.egress_fallback_on <= http.TRANSIENT


def test_the_opt_in_actually_reaches_the_request():
    """`egress_fallback_on` is silently inert on a scraper that bypasses the base fetch seam, so
    assert the kwargs rather than the attribute."""
    egress = ZwayamScraper("careers.example.com")._egress()

    assert egress["egress_group"] == "zwayam", (
        "the metering is per origin across every tenant, so the group is the ATS"
    )
    assert 403 in egress["egress_on"]
