"""A Single source scraper (ADR-0139) must display its company's name, not its careers host.

The ledger's `tenant` column doubles as slug *and* display name, so a Board discovered by hostname
served that hostname to the UI: `www.amazon.jobs` on all 9,281 of Amazon's served tech rows
(`filter_tech`'s kept figure for run 35595828212; the ledger's 22,539 is *listed* postings,
a different denominator). Renaming the tenant
cannot fix it — `job_id` is `{ats}:{slug}:{native_id}`, so a new tenant renames every id and
`index prune` evicts the Board's whole population as off-Board (ADR-0023). Hence a name declared
in code, which is also why `meta`/`tesla` never needed a `company_name` pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from headstart.boards import scrapable_boards
from headstart.boards.company_ref import CompanyRef
from headstart.scrapers.registry import get_scraper

LEDGERS = Path(__file__).resolve().parents[1] / "data" / "validate" / "liveness"

# Every Single source scraper, with the display name its own module docstring states.
EXPECTED = {
    "amazon": "Amazon",
    "apple": "Apple",
    "bytedance": "ByteDance",
    "google": "Google",
    "meta": "Meta",
    "tesla": "Tesla",
    "tiktok": "TikTok",
    "uber": "Uber",
}


def _single_board(ats: str) -> CompanyRef:
    """This ATS's one Board, via `scrapable_boards.load` rather than the CSV.

    CLAUDE.md is explicit that a liveness ledger is read through that function and never the file:
    it is what applies `board_key()`-grouped dedupe and the parked-Board filter, so a raw CSV read
    can disagree with what the pipeline actually scrapes.
    """
    boards = [c for c in scrapable_boards.load(LEDGERS) if c.ats == ats]
    assert len(boards) == 1, (
        f"{ats} is a Single source scraper with {len(boards)} Boards"
    )
    return boards[0]


@pytest.mark.parametrize("ats,expected", sorted(EXPECTED.items()))
def test_a_single_source_board_displays_its_company_not_its_host(
    ats, expected, monkeypatch
):
    tenant = _single_board(ats).slug
    scraper = get_scraper(ats, tenant, tenant)
    # No network: `board_page()` returning None is `resolve_company`'s documented no-op path, so a
    # scraper that has not declared its company falls through to the slug rather than fetching.
    monkeypatch.setattr(scraper, "board_page", lambda: None)
    scraper.resolve_company()
    assert scraper.company == expected


@pytest.mark.parametrize("ats", sorted(EXPECTED))
def test_naming_the_company_leaves_the_board_identity_alone(ats):
    """The slug is the id's Board segment. If this moves, every row of the Board is evicted."""
    tenant = _single_board(ats).slug
    scraper = get_scraper(ats, tenant, tenant)
    before = (scraper.slug, scraper.board_key(), scraper.job_id("123"))
    scraper.resolve_company()
    assert (scraper.slug, scraper.board_key(), scraper.job_id("123")) == before
    assert scraper.board_key() == f"{ats}:{tenant}"


@pytest.mark.parametrize("ats,expected", sorted(EXPECTED.items()))
def test_the_name_is_declared_on_the_class_not_borrowed_from_the_ledger(ats, expected):
    """The test above passes vacuously wherever the ledger's tenant is already the name.

    `meta.csv` and `tesla.csv` hold `Meta`/`Tesla`, so `self.COMPANY or company or slug` reaches
    the right answer through the *slug* arm whether or not `COMPANY` is set — and the first version
    of this change proved it, declaring `COMPANY = "Tesla"` on `TeslaBrowserUnavailable` instead of
    `TeslaScraper` while all eight arms stayed green. So assert the declaration itself, and drive
    the scraper with a host no ledger spelling can rescue.
    """
    assert (
        type(get_scraper(ats, "nothing.example", "nothing.example")).COMPANY == expected
    )
    assert get_scraper(ats, "nothing.example", "nothing.example").company == expected
