"""A Board that comes back *materially* short must say so in the log.

The observability was inverted: `mark_truncated_unless_negligible` logs INFO on the branch it
*tolerates*, while `mark_truncated` — the branch where the Board leaves ADR-0053's eviction scope
entirely and keeps serving closed postings — logged nothing at all.

Measured over the five runs of 2026-09-16: 74-98 partial Boards per run, of which only Workday's
were named. `amazon:www.amazon.jobs` fell 22,475 -> 18,466 postings across those runs and logged a
line only in the two runs where it was *fine*; `[amazon]` appears twice in all 75 shard logs.

INFO, not WARNING, deliberately: a WARNING is an Actions annotation against ADR-0039's hard
50-per-run quota, and the merge job already emits one aggregate warning naming the scope-excluded
Boards. 74-98 annotations a run would blow that budget on its own.
"""

import logging

from headstart.scrapers.base import MIN_AUTHORITATIVE_SHARE, BaseScraper


class _Scraper(BaseScraper):
    """Minimal concrete scraper, matching `tests/test_base.py`'s stub."""

    ats = "testats"
    url_shape = r"https://example\.invalid/jobs/\w+"

    def url(self):
        return "https://example.invalid/jobs"

    def parse(self, raw, scraped_at):
        return []

    def _salary_field(self, raw):
        return None

    def job_url(self, native_id):
        return f"https://example.invalid/jobs/{native_id}"


LOGGER = "headstart.scrapers.testats"


def _scraper() -> _Scraper:
    return _Scraper("aboard")


def test_mark_truncated_logs_the_board_and_the_reason(caplog):
    scraper = _scraper()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        scraper.mark_truncated("hit the 1000-job widget cap")
    assert scraper.truncated == "hit the 1000-job widget cap"
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert scraper.board_key() in logged, "the line must name the Board"
    assert "hit the 1000-job widget cap" in logged, "the line must carry the reason"


def test_mark_truncated_logs_at_info_not_warning(caplog):
    """ADR-0039: a WARNING is an Actions annotation against a hard 50-per-run quota."""
    scraper = _scraper()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        scraper.mark_truncated("some reason")
    assert caplog.records, "nothing was logged at all"
    assert all(r.levelno < logging.WARNING for r in caplog.records)


def test_only_the_first_reason_logs(caplog):
    """`mark_truncated` keeps the first reason; a second call must not log a contradicting one."""
    scraper = _scraper()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        scraper.mark_truncated("the real cause")
        scraper.mark_truncated("a downstream consequence")
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "the real cause" in logged
    assert "a downstream consequence" not in logged


def test_the_tolerated_branch_still_reads_as_tolerated(caplog):
    """The negligible path must not start claiming the Board is unauthoritative."""
    scraper = _scraper()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        scraper.mark_truncated_unless_negligible(999, 1000, "one short")
    assert scraper.truncated is None
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "within tolerance" in logged


def test_a_material_shortfall_routed_through_the_tolerance_also_logs(caplog):
    """Below the share it delegates to `mark_truncated`, so it must inherit the new line."""
    scraper = _scraper()
    read = int(1000 * MIN_AUTHORITATIVE_SHARE) - 1
    with caplog.at_level(logging.INFO, logger=LOGGER):
        scraper.mark_truncated_unless_negligible(read, 1000, "far short")
    assert scraper.truncated == "far short"
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert scraper.board_key() in logged and "far short" in logged


def test_a_complete_read_through_the_tolerance_says_nothing(caplog):
    """Nothing missing is nothing to tolerate: phenom's walk routes every Board through here, and
    "read N of N (100.000%) — within tolerance … carries the 0 missing id(s)" was ~70 lines a run
    (runs 36200233818-36218633315), google's and amazon's a few more."""
    scraper = _scraper()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        scraper.mark_truncated_unless_negligible(1000, 1000, "all read")
    assert scraper.truncated is None
    assert not [r for r in caplog.records if "within tolerance" in r.getMessage()]
