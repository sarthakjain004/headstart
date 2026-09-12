"""Maps an ATS name to its scraper implementation."""

from __future__ import annotations

from collections.abc import Container

from headstart.scrapers.amazon import AmazonScraper
from headstart.scrapers.ashby import AshbyScraper
from headstart.scrapers.base import BaseScraper
from headstart.scrapers.darwinbox import DarwinboxScraper
from headstart.scrapers.eightfold import EightfoldScraper
from headstart.scrapers.freshteam import FreshteamScraper
from headstart.scrapers.greenhouse import GreenhouseScraper
from headstart.scrapers.icims import ICIMSScraper
from headstart.scrapers.jazzhr import JazzHRScraper
from headstart.scrapers.jobvite import JobviteScraper
from headstart.scrapers.join import JoinScraper
from headstart.scrapers.keka import KekaScraper
from headstart.scrapers.lever import LeverScraper
from headstart.scrapers.oracle import OracleScraper
from headstart.scrapers.personio import PersonioScraper
from headstart.scrapers.recruitee import RecruiteeScraper
from headstart.scrapers.ripplehire import RippleHireScraper
from headstart.scrapers.rippling import RipplingScraper
from headstart.scrapers.sensehq import SenseHQScraper
from headstart.scrapers.smartrecruiters import SmartRecruitersScraper
from headstart.scrapers.successfactors import SuccessFactorsScraper
from headstart.scrapers.teamtailor import TeamtailorScraper
from headstart.scrapers.trakstar import TrakstarScraper
from headstart.scrapers.workable import WorkableScraper
from headstart.scrapers.workday import WorkdayScraper
from headstart.scrapers.zoho import ZohoScraper
from headstart.scrapers.zwayam import ZwayamScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    cls.ats: cls
    for cls in (
        GreenhouseScraper,
        LeverScraper,
        AshbyScraper,
        ZohoScraper,
        WorkdayScraper,
        WorkableScraper,
        SmartRecruitersScraper,
        RecruiteeScraper,
        OracleScraper,
        SenseHQScraper,
        KekaScraper,
        TrakstarScraper,
        RippleHireScraper,
        DarwinboxScraper,
        TeamtailorScraper,
        PersonioScraper,
        JoinScraper,
        RipplingScraper,
        FreshteamScraper,
        EightfoldScraper,
        SuccessFactorsScraper,
        ZwayamScraper,
        ICIMSScraper,
        JazzHRScraper,
        JobviteScraper,
        AmazonScraper,
    )
}


# ATSes that are wired up but excluded from the active scrape list. join is ~99.99% non-tech
# (German-SMB boards; ~1 tech job in ~10k), so scraping it is pure noise for a tech-only index —
# disabled 2026-07-07 pending non-English/non-tech expansion. The scraper class and its tests stay
# intact (get_scraper("join", ...) still works); re-enable by removing it from this set.
#
# jazzhr and jobvite are disabled on arrival (2026-09-07), on cost rather than correctness. Both
# are complete, tested, and ship a liveness ledger, so removing them here really is all it takes:
# 3,684 jazzhr and 401 jobvite Hiring Boards are waiting. They stay off on storage, this
# pipeline's binding constraint: jazzhr is 3,684 Boards x 27.5 jobs = ~100k detail fetches at a
# measured 112 KB a page = ~10.7 GB, for 5.1% tech = ~5,100 tech Jobs; jobvite is 23,461 postings
# = ~1.5-2 GB for 7.0% = ~1,640. Neither showed a measurable India presence. The inputs to both
# sums (jobs/board, tech share, page size) are in docs/jazzhr/ and docs/jobvite/.
DISABLED_ATS: frozenset[str] = frozenset({"join", "jazzhr", "jobvite"})


def detail_pass_atses() -> frozenset[str]:
    """ATSes whose ``description`` comes from a per-Job **detail pass**, so it can go missing.

    Lives here because three callers across two packages need the same answer and had drifted into
    computing it three ways — `embed_plan` to decide which vectors were degraded, `update_meta` to
    backfill that flag, and `board_priority` to drain the cheap half of the description gap first.
    """
    return frozenset(
        ats for ats, scraper in SCRAPERS.items() if scraper.has_detail_pass
    )


def get_scraper(
    ats: str,
    slug: str,
    company: str | None = None,
    *,
    have_details: Container[str] | None = None,
) -> BaseScraper:
    try:
        cls = SCRAPERS[ats]
    except KeyError:
        raise ValueError(f"unknown ats {ats!r}; known: {sorted(SCRAPERS)}") from None
    scraper = cls(slug, company)
    # Set after construction, not passed in: five scrapers override ``__init__`` and only one
    # consults this, so widening all their signatures for it would be churn for nothing.
    scraper.have_details = have_details
    return scraper
