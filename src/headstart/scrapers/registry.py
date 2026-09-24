"""Maps an ATS name to its scraper implementation."""

from __future__ import annotations

from collections.abc import Container

from headstart.scrapers.adp import ADPScraper
from headstart.scrapers.amazon import AmazonScraper
from headstart.scrapers.apple import AppleScraper
from headstart.scrapers.ashby import AshbyScraper
from headstart.scrapers.bamboohr import BambooHRScraper
from headstart.scrapers.base import BaseScraper
from headstart.scrapers.breezy import BreezyScraper
from headstart.scrapers.bytedance import ByteDanceScraper
from headstart.scrapers.clearcompany import ClearCompanyScraper
from headstart.scrapers.cornerstone import CornerstoneScraper
from headstart.scrapers.darwinbox import DarwinboxScraper
from headstart.scrapers.eightfold import EightfoldScraper
from headstart.scrapers.freshteam import FreshteamScraper
from headstart.scrapers.gem import GemScraper
from headstart.scrapers.google import GoogleScraper
from headstart.scrapers.greenhouse import GreenhouseScraper
from headstart.scrapers.icims import ICIMSScraper
from headstart.scrapers.jazzhr import JazzHRScraper
from headstart.scrapers.jibe import JibeScraper
from headstart.scrapers.jobvite import JobviteScraper
from headstart.scrapers.join import JoinScraper
from headstart.scrapers.keka import KekaScraper
from headstart.scrapers.lever import LeverScraper
from headstart.scrapers.meta import MetaScraper
from headstart.scrapers.oracle import OracleScraper
from headstart.scrapers.personio import PersonioScraper
from headstart.scrapers.phenom import PhenomScraper
from headstart.scrapers.pinpoint import PinpointScraper
from headstart.scrapers.pyjamahr import PyjamaHRScraper
from headstart.scrapers.recruitee import RecruiteeScraper
from headstart.scrapers.ripplehire import RippleHireScraper
from headstart.scrapers.rippling import RipplingScraper
from headstart.scrapers.sensehq import SenseHQScraper
from headstart.scrapers.smartrecruiters import SmartRecruitersScraper
from headstart.scrapers.successfactors import SuccessFactorsScraper
from headstart.scrapers.taleo_be import TaleoBEScraper
from headstart.scrapers.taleo_enterprise import TaleoEnterpriseScraper
from headstart.scrapers.teamtailor import TeamtailorScraper
from headstart.scrapers.tesla import TeslaScraper
from headstart.scrapers.tiktok import TikTokScraper
from headstart.scrapers.trakstar import TrakstarScraper
from headstart.scrapers.uber import UberScraper
from headstart.scrapers.workable import WorkableScraper
from headstart.scrapers.workday import WorkdayScraper
from headstart.scrapers.zoho import ZohoScraper
from headstart.scrapers.zwayam import ZwayamScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    cls.ats: cls
    for cls in (
        GoogleScraper,
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
        TaleoEnterpriseScraper,
        PersonioScraper,
        JoinScraper,
        RipplingScraper,
        FreshteamScraper,
        EightfoldScraper,
        SuccessFactorsScraper,
        TaleoBEScraper,
        ZwayamScraper,
        ICIMSScraper,
        BambooHRScraper,
        BreezyScraper,
        ClearCompanyScraper,
        PhenomScraper,
        PinpointScraper,
        PyjamaHRScraper,
        JazzHRScraper,
        JibeScraper,
        JobviteScraper,
        TeslaScraper,
        ADPScraper,
        AmazonScraper,
        ByteDanceScraper,
        CornerstoneScraper,
        AppleScraper,
        UberScraper,
        MetaScraper,
        TikTokScraper,
        GemScraper,
    )
}


# ATSes that are wired up but excluded from the active scrape list. join is ~99.99% non-tech
# (German-SMB boards; ~1 tech job in ~10k), so scraping it is pure noise for a tech-only index —
# disabled 2026-07-07 pending non-English/non-tech expansion. The scraper class and its tests stay
# intact (get_scraper("join", ...) still works); re-enable by removing it from this set.
#
# jazzhr and jobvite were disabled on arrival (2026-09-07) on cost rather than correctness, and are
# re-enabled here (2026-09-16) now that a full sweep — Wayback run to completion, unioned with the
# Common-Crawl candidates that found 96 tenants it missed — has measured what that cost actually is. The 2026-09-07 figures were taken on a pool built from a partial sweep, and both moved:
#
#   jazzhr  4,871 Hiring Boards (was 3,684) x 20.5 jobs/board (was 27.5) = 99,963 detail fetches
#           at a measured 112 KB a page = ~10.7 GB, for 5.1% tech = ~5,098 tech Jobs.
#   jobvite   748 Hiring Boards, 39,573 postings — **1.69x the 23,461 assumed** — so ~2.5-3.5 GB
#           (was ~1.5-2) for 7.0% tech = ~2,770 tech Jobs (was ~1,640). Excludes jvauto, the
#           vendor's own 10,000-posting automation tenant (config.EXCLUDED_BOARDS).
#
# jazzhr lands on its old storage number by coincidence, not by being unchanged: it gained Boards
# and lost jobs-per-Board, and the two cancelled. jobvite simply was not measured at full pool.
# Total accepted: ~13.2-14.2 GB for ~7,868 tech Jobs. Neither shows a measurable India presence.
# Decided in ADR-0158; the sweep, the liveness pass, the storage arithmetic, the gate A/B and the
# per-field audit are all in docs/jazzhr/2026-09-16_full-pool-measurement.md. The older
# docs/jazzhr/ and docs/jobvite/ notes keep the page-size and tech-share method but carry
# partial-pool counts, and say so at the top.
#
# join stays disabled: ~99.99% non-tech (German-SMB boards, ~1 tech job in ~10k).
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
