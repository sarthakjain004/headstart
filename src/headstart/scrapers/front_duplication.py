"""**Front duplication** (CONTEXT.md): how many of a **Career front**'s postings apply on a
Scrapable Board, so the index serves them twice.

Every career-front scraper (Radancy ADR-0246, Happydance ADR-0264) measures it the same way: each
posting's apply URL is resolved to the **Backing Board** it hands off to (:func:`backing_board`),
matched against the Scrapable Boards the committed ledger holds (:func:`scrapable_boards`), and
the share is logged once per Board and recorded in its telemetry (:func:`report`). Measured every
run and never acted on, by the owner's decision of 2026-09-26.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

if TYPE_CHECKING:
    from headstart.scrapers.base import BaseScraper

#: The career-front vendors whose Boards are keyed by host. A front is never another front's
#: Backing Board — its Apply button can name its own host (a Greenhouse embed on the front) —
#: so their identities are left out of the host index.
_FRONT_ATSES = ("radancy", "happydance")


class ScrapableBoardIndex:
    """The Scrapable Boards, as what an apply URL is matched against: every lowercased identity,
    and those whose slug is a bare host indexed by that host."""

    def __init__(self, identities: frozenset[str]) -> None:
        self.identities = identities
        self.by_host = {
            identity.split(":", 1)[1]: identity
            for identity in identities
            if _is_host(identity.split(":", 1)[1])
            and identity.split(":", 1)[0] not in _FRONT_ATSES
        }


def _is_host(slug: str) -> bool:
    return "." in slug and "/" not in slug and ":" not in slug


@cache
def scrapable_boards() -> ScrapableBoardIndex:
    """Every Scrapable Board, read once per process from the committed ledger the way
    ``scrapable_boards.load`` reads it (Jibe's `_scraped_icims_tenants` is the precedent)."""
    # Imported here: `scrapable_boards` reaches the scraper registry, which imports the scrapers
    # that import this module.
    from headstart.boards import liveness_ledger
    from headstart.boards import scrapable_boards as ledger_boards

    ledger = liveness_ledger.dir_for(Path(__file__).resolve().parents[3])
    return ScrapableBoardIndex(
        frozenset(
            board.lowercase_identity for board in ledger_boards.load(ledger, min_jobs=0)
        )
    )


def report(scraper: BaseScraper, apply_urls: Iterable[str | None]) -> None:
    """Log and record the share of one front's read postings whose Backing Board is a Scrapable
    Board — one apply URL per posting read. One INFO line per Board: a WARNING is an Actions
    annotation against a quota of ten per step (ADR-0039)."""
    held = scrapable_boards()
    if not held.identities:
        # No committed ledger beside this checkout: say it was not measured, never "0%".
        scraper._log.info(
            f"{scraper.board_key()}: Front duplication not measured (no ledger)"
        )
        return
    urls = list(apply_urls)
    backing = Counter(
        board for board in (backing_board(url, held) for url in urls) if board
    )
    duplicated = sum(backing.values())
    scraper.telemetry["front_postings"] = len(urls)
    scraper.telemetry["front_duplicated"] = duplicated
    boards = ", ".join(f"{board} {n}" for board, n in backing.most_common(3))
    scraper._log.info(
        f"{scraper.board_key()}: Front duplication at least {duplicated}/{len(urls)} postings "
        f"apply on a Scrapable Board" + (f" ({boards})" if boards else "")
    )


_WORKDAY_HOST = re.compile(r"^[^.]+\.wd\d+\.myworkdayjobs\.com$")
#: Workday's shared-host spelling of the same site: ``wd5.myworkdaysite.com/[{locale}/]recruiting/
#: {tenant}/{site}/…`` is ``{tenant}.wd5.myworkdayjobs.com/{site}`` (Baird's and Prisma Health's
#: Happydance fronts apply there, 43 of 43 sampled pages).
_WORKDAY_SITE_HOST = re.compile(r"^(wd\d+)\.myworkdaysite\.com$")
_LOCALE = re.compile(r"^[a-z]{2}-[a-z]{2}$", re.IGNORECASE)


def backing_board(apply_url: str | None, held: ScrapableBoardIndex) -> str | None:
    """The Scrapable Board an apply URL hands off to, as its lowercased identity, or None.

    Built the way the ledger spells each ATS's row and read through that ATS's own ``slug_from``
    (``registry.company_from_row``), so the identity is the one ``scrapable_boards`` computes. The
    apply URLs of 6,816 sampled Radancy postings named Workday on 69 of 177 fronts, then iCIMS,
    Oracle, Avature, Taleo, SmartRecruiters and SuccessFactors. A host-keyed Board (iCIMS,
    SuccessFactors RMK, Eightfold, Phenom, Oracle) matches on the apply URL's host; Avature's is
    its tenant label. SuccessFactors' own apply form (``career2.successfactors.eu/…?company=cargill``)
    names a company id, not the RMK host its Board is keyed by, so it resolves to nothing: an
    undercount, stated as such.
    """
    parts = urlsplit(apply_url or "")
    host = (parts.hostname or "").lower()
    if not host:
        return None
    if host in held.by_host:
        return held.by_host[host]
    segments = [s for s in parts.path.split("/") if s]
    row: tuple[str, str, str] | None = None
    workday_site = _WORKDAY_SITE_HOST.match(host)
    if _WORKDAY_HOST.match(host):
        sites = [s for s in segments if not _LOCALE.match(s)]
        if sites:
            row = ("workday", "", f"https://{host}/{sites[0]}")
    elif workday_site:
        path = [s for s in segments if not _LOCALE.match(s)]
        if len(path) > 2 and path[0] == "recruiting":
            pod = workday_site.group(1)
            row = (
                "workday",
                "",
                f"https://{path[1]}.{pod}.myworkdayjobs.com/{path[2]}",
            )
    elif host.endswith(".taleo.net") and segments[:1] == ["careersection"]:
        if len(segments) > 1:
            row = (
                "taleo_enterprise",
                "",
                f"https://{host}/careersection/{segments[1]}",
            )
    elif host in {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"}:
        if segments:
            row = ("smartrecruiters", segments[0], apply_url or "")
    elif host.endswith("greenhouse.io") and segments:
        # An embedded board names its slug in `for=` (`/embed/job_app?for=acme`).
        embedded = parse_qs(parts.query).get("for", [""])[0]
        slug = embedded if segments[0] == "embed" else segments[0]
        if slug:
            row = ("greenhouse", slug, apply_url or "")
    elif host in {"jobs.lever.co", "jobs.eu.lever.co"} and segments:
        row = ("lever", segments[0], apply_url or "")
    elif host.endswith(".avature.net"):
        row = ("avature", host.removesuffix(".avature.net"), apply_url or "")
    if row is None:
        return None
    # Imported here for the same cycle `scrapable_boards` avoids.
    from headstart.boards.board_identity import board_identity, lower_key
    from headstart.scrapers.registry import company_from_row

    identity = lower_key(board_identity(company_from_row(*row)))
    return identity if identity in held.identities else None
