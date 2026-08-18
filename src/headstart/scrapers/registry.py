"""Maps an ATS name to its scraper implementation."""

from __future__ import annotations

from collections.abc import Container

from headstart.scrapers.ashby import AshbyScraper
from headstart.scrapers.base import BaseScraper
from headstart.scrapers.darwinbox import DarwinboxScraper
from headstart.scrapers.eightfold import EightfoldScraper
from headstart.scrapers.freshteam import FreshteamScraper
from headstart.scrapers.greenhouse import GreenhouseScraper
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
    )
}


# ATSes that are wired up but excluded from the active scrape list. join is ~99.99% non-tech
# (German-SMB boards; ~1 tech job in ~10k), so scraping it is pure noise for a tech-only index —
# disabled 2026-07-07 pending non-English/non-tech expansion. The scraper class and its tests stay
# intact (get_scraper("join", ...) still works); re-enable by removing it from this set.
DISABLED_ATS: frozenset[str] = frozenset({"join"})


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
