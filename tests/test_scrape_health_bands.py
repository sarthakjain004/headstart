"""The run-level coverage verdict must be able to read healthy, and must separate an ordinary
run from a provider-wide outage.

`degraded` was `not complete or any(failed or partial)` — a zero threshold over ~20,000 Boards and
31 ATSes, so it read DEGRADED on 7 of 7 runs sampled across four days and would have printed the
identical word during the 2026-09-12 Workday outage, where that provider failed 80.8% of its Board
attempts and tech output fell ~87.7%.

Thresholds are set off measurement, not taste. The unusable share (failed + partial over attempted)
measured 0.55-0.79% across the five runs of 2026-09-16 and 0.655% on the live
`data/state/scrape_health.json`. Per-ATS baselines are much noisier — keka 9.95%, oracle 4.90%,
amazon 100% on a single Board — which is why the band is on the *overall* share and why the
per-ATS callout carries a minimum denominator.
"""

from headstart.ingest.observability import ScrapeHealth


def _health(coverage, *, reports=15, expected=15, malformed=0):
    return ScrapeHealth(
        {ats: dict(c) for ats, c in coverage.items()},
        {},
        __import__("collections").Counter(),
        {},
        reports,
        expected,
        malformed,
    )


def _spread(successful, failed=0, partial=0):
    return {"successful": successful, "failed": failed, "partial": partial}


def test_an_ordinary_run_reads_healthy():
    """The measured baseline — 35 failed, 96 partial of 20,000 — must not be an alarm."""
    health = _health({"a": _spread(19965, failed=35, partial=96)})
    assert not health.degraded
    assert "healthy" in health.verdict_line()


def test_a_provider_wide_outage_does_not_read_the_same_as_a_normal_run():
    """The 2026-09-12 shape: one large provider failing most of its attempts."""
    health = _health(
        {
            "workday": _spread(500, failed=2100),
            "greenhouse": _spread(17000, failed=20),
        }
    )
    assert health.degraded
    line = health.verdict_line()
    assert "healthy" not in line
    assert "workday" in line, "the line must name who is driving it"


def test_a_single_board_ats_cannot_drive_the_verdict():
    """`amazon` is one Board and sits at 100% unusable at baseline."""
    health = _health(
        {
            "amazon": _spread(0, partial=1),
            "greenhouse": _spread(19900, failed=30, partial=60),
        }
    )
    assert not health.degraded, "one partial Board must not flip a 20,000-Board run"


def test_incomplete_telemetry_still_degrades():
    """A missing or malformed shard report is a different failure and must survive the banding."""
    assert _health({"a": _spread(19965)}, reports=14, expected=15).degraded
    assert _health({"a": _spread(19965)}, malformed=1).degraded


def test_the_verdict_states_the_share_it_judged():
    """A number with a trend beats a word: the line must show what it measured."""
    line = _health({"a": _spread(19965, failed=35, partial=96)}).verdict_line()
    assert "0.6" in line or "0.7" in line
