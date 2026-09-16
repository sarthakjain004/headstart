"""Freshteam's 1,000-job widget cap must mark the Board truncated.

`base.mark_truncated`'s own contract names this exact shape: a **hard cap** "calls
`mark_truncated` directly however close to complete the read looks", because the unread remainder
is genuinely unreachable rather than noise, and unreachable identically on every run.

`freshteam.parse` detected the cap and logged it but did not mark, so `freshteam partial` read 0
in all five runs of 2026-09-16 while three Boards (`abnhire`, `simera-talent`, `kalam`) sat on the
cap. Untreated, a posting past the cap is absent from every snapshot, goes Unconfirmed, and is
evicted on the guaranteed second miss (ADR-0083) — the pipeline deletes live jobs.

Zoho's superficially identical ceiling is deliberately *not* marked and must stay that way: its
~750 is approximate, so landing on it is evidence rather than proof, and ADR-0053 exclusion has no
drain. Freshteam's 1,000 is exact.
"""

from headstart.scrapers.freshteam import _WIDGET_CAP, FreshteamScraper


def _raw(n: int) -> dict:
    return {
        "branches": [],
        "job_roles": [],
        "jobs": [
            {"id": i, "title": f"Engineer {i}", "status": "published"} for i in range(n)
        ],
    }


def test_a_board_on_the_cap_is_marked_truncated():
    scraper = FreshteamScraper("abnhire")
    scraper.parse(_raw(_WIDGET_CAP), "2026-09-16T00:00:00Z")
    assert scraper.truncated is not None
    assert str(_WIDGET_CAP) in scraper.truncated


def test_a_board_under_the_cap_is_not_marked():
    scraper = FreshteamScraper("smallboard")
    scraper.parse(_raw(_WIDGET_CAP - 1), "2026-09-16T00:00:00Z")
    assert scraper.truncated is None


def test_the_cap_does_not_route_through_the_adr_0121_tolerance():
    """A hard cap marks unconditionally; the tolerance would let a near-complete read off.

    Asserting `truncated is not None` alone cannot tell the two paths apart, so this pins the
    *reason*: `mark_truncated_unless_negligible` rewrites nothing but would have left this None at
    1000/1000, whereas the hard-cap call always records the widget-cap wording.
    """
    scraper = FreshteamScraper("abnhire")
    scraper.parse(_raw(_WIDGET_CAP), "2026-09-16T00:00:00Z")
    assert "widget cap" in (scraper.truncated or "")
    assert "within tolerance" not in (scraper.truncated or "")
