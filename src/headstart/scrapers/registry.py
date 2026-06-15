"""Maps an ATS name to its scraper implementation."""

from __future__ import annotations

from headstart.scrapers.ashby import AshbyScraper
from headstart.scrapers.base import BaseScraper
from headstart.scrapers.greenhouse import GreenhouseScraper
from headstart.scrapers.lever import LeverScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    cls.ats: cls for cls in (GreenhouseScraper, LeverScraper, AshbyScraper)
}


def get_scraper(ats: str, slug: str, company: str | None = None) -> BaseScraper:
    try:
        cls = SCRAPERS[ats]
    except KeyError:
        raise ValueError(f"unknown ats {ats!r}; known: {sorted(SCRAPERS)}") from None
    return cls(slug, company)
