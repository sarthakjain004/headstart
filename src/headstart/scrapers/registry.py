"""Maps an ATS name to its scraper implementation."""

from __future__ import annotations

from headstart.scrapers.ashby import AshbyScraper
from headstart.scrapers.base import BaseScraper
from headstart.scrapers.darwinbox import DarwinboxScraper
from headstart.scrapers.greenhouse import GreenhouseScraper
from headstart.scrapers.join import JoinScraper
from headstart.scrapers.keka import KekaScraper
from headstart.scrapers.lever import LeverScraper
from headstart.scrapers.oracle import OracleScraper
from headstart.scrapers.personio import PersonioScraper
from headstart.scrapers.recruitee import RecruiteeScraper
from headstart.scrapers.ripplehire import RippleHireScraper
from headstart.scrapers.sensehq import SenseHQScraper
from headstart.scrapers.smartrecruiters import SmartRecruitersScraper
from headstart.scrapers.teamtailor import TeamtailorScraper
from headstart.scrapers.trakstar import TrakstarScraper
from headstart.scrapers.workable import WorkableScraper
from headstart.scrapers.workday import WorkdayScraper
from headstart.scrapers.zoho import ZohoScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    cls.ats: cls
    for cls in (GreenhouseScraper, LeverScraper, AshbyScraper, ZohoScraper, WorkdayScraper,
                WorkableScraper, SmartRecruitersScraper, RecruiteeScraper, OracleScraper,
                SenseHQScraper, KekaScraper, TrakstarScraper, RippleHireScraper,
                DarwinboxScraper, TeamtailorScraper, PersonioScraper, JoinScraper)
}


def get_scraper(ats: str, slug: str, company: str | None = None) -> BaseScraper:
    try:
        cls = SCRAPERS[ats]
    except KeyError:
        raise ValueError(f"unknown ats {ats!r}; known: {sorted(SCRAPERS)}") from None
    return cls(slug, company)
