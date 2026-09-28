"""Recruitee job-board scraper ({slug}.recruitee.com offers API).

Adapted from jobhive's Recruitee scraper (kalil0321/ats-scrapers, MIT) to this project's
BaseScraper contract:
    https://{slug}.recruitee.com/api/offers/

One request returns every published offer; no pagination or cap has been seen (rebootmonkey,
the largest Board known, answered 4,379 offers with unique ids in one 53 MB body, 2026-09-28).
An unknown slug answers 404 and raises; an empty Board answers ``{"offers": []}``. The host is a
wildcard, so the liveness probe reads a DNS failure as unknown, not dead. Each offer carries
every language the tenant wrote in ``translations``; a real English description is read over
the primary one (``_description``, ADR-0254).
"""

from __future__ import annotations

from typing import Any

from headstart.jobs import salary
from headstart.jobs.job import Job, html_to_text, remote_from_workplace
from headstart.scrapers.base import BaseScraper


def _offer_url(tenant: str, offer: dict) -> str:
    """The job link, built on the tenant's own Recruitee host.

    NOT the API's ``careers_url``: that is whichever vanity domain the customer configured,
    and it frequently is not serving the board at all. Measured 2026-08-12 against the live
    index — 49% of served recruitee rows sat on a custom host, and 9 of 25 sampled hosts were
    dead (``transperfect.com/o/…`` 404s while the same job answers 200 on
    ``transperfect.recruitee.com``). The tenant host is the ATS's own and always resolves:
    200 on 14/14 tenants sampled, including every one whose custom domain worked.

    Falls back to the API's links only when an offer carries no slug to build from.
    """
    slug = offer.get("slug")
    if not slug:
        return offer.get("careers_url") or offer.get("careers_apply_url", "")
    return f"https://{tenant}.recruitee.com/o/{slug}"


def _is_remote_sentinel(location: str | None, city: str | None) -> bool:
    """Is this ``location`` a localized "remote" marker rather than a place?

    Recruitee puts one there instead of a place on remote offers, and it is truthy, so it used
    to win the `or` chain in ``parse`` and discard the structured city/country the same offer
    carries — leaving the row unmatchable by any place filter.

    Detected structurally rather than by listing the strings: at least eight markers across
    seven locales have been observed (``Remote job``, ``Poste a distance``, ``Homeoffice``,
    ``Werken op afstand``, ``Trabajo a distancia``, ``Praca zdalna``, ``Trabalho remoto``,
    ``Lavoro da remoto``) and independent samples keep turning up locales the previous one
    missed, so any list is one locale behind by construction. The rule instead is that a
    ``location`` which does not name the offer's own ``city`` is not a place.

    Measured 2026-08-25 across three independent samples totalling ~19k offers: it fires on
    ~12% of them, every fire carried the offer's ``remote`` flag, and every fire had
    city/country to fall back on. Enumerating only the English string would have caught well
    under half.

    Known limitation, not observed live: a real location spelled differently from its own city
    (``"Bengaluru, India"`` against ``city="Bangalore"``) trips this. The cost is capped at
    swapping one spelling of the place for another, because the fallback is that same city --
    which is why ``parse`` does not let this decide ``remote``.
    """
    return bool(location) and bool(city) and city.lower() not in location.lower()


def _description(offer: dict) -> str | None:
    """Description plus requirements, from the English translation when it is a real one.

    The top-level text is the offer's primary language; ``translations`` holds every language
    the tenant wrote, keyed by code. Search holds non-English text out of its index, so a Dutch
    offer with an English version was lost to it: 35 of 516 offers in a 30-Board sample
    (2026-09-28; voortman's "Lead Software Developer XR"). Where the top-level text is
    already English the ``en`` translation equals it (119 of 119 offers, 40 Boards, 2026-09-28).

    Only a translation at least half the primary text's length is read, and the title stays the
    primary one: an English version can be a template of headings alone, and an English title can
    be a stale copy of another offer's (measurements in ADR-0254).
    """

    def description_and_requirements(texts: dict) -> str | None:
        return html_to_text(
            "\n".join(
                t for t in (texts.get("description"), texts.get("requirements")) if t
            )
        )

    primary = description_and_requirements(offer)
    english = description_and_requirements(
        (offer.get("translations") or {}).get("en") or {}
    )
    if english and len(english) >= len(primary or "") / 2:
        return english
    return primary


def _workplace_type(offer: dict) -> str | None:
    """The workplace type the offer's ``remote``/``hybrid``/``on_site`` flags state.

    Several can be set at once (a 40-Board sample, 2026-09-28: remote with hybrid 34, hybrid with
    on-site 26, of 610 offers); remote wins, then hybrid, as ``remote_from_workplace`` reads them."""
    if offer.get("remote"):
        return "remote"
    if offer.get("hybrid"):
        return "hybrid"
    if offer.get("on_site"):
        return "onsite"
    return None


class RecruiteeScraper(BaseScraper):
    ats = "recruitee"
    # Was host-agnostic (`https://.+/o/[^/]+`) because tenants serve on custom careers
    # domains — which is exactly how it passed 458 rows whose custom host was dead. Both the
    # scraper and the serve-time rewrite now put every link on the tenant's own host, so the
    # shape can assert that host and this check finally bites. Both of them keep a fallback
    # for an offer with no slug to build from, and a row that took it would fail here — which
    # is the point: it has never happened (0 of 772 served rows, 0 of 612 offers inspected),
    # so if it ever does, that is news and not something to wave through.
    url_shape = r"https://[\w-]+\.recruitee\.com/o/[^/]+"

    def url(self) -> str:
        return f"https://{self.slug}.recruitee.com/api/offers/"

    def job_url(self, offer: dict) -> str:
        """Delegates to the module-level :func:`_offer_url`, which does the real construction
        (ADR-0153) — kept a free function since ``tests/test_scrapers.py`` exercises it
        directly against a bare tenant string with no scraper instance in hand."""
        return _offer_url(self.slug, offer)

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        offers = raw.get("offers")
        if offers is None and "offers" not in raw:
            # Only an *absent* container answers here. A ``offers`` that is present but not a
            # list falls through and raises, as it did before this guard existed: a loud
            # Board error keeps the Board out of ADR-0053's eviction scope, where a quiet
            # `[]` would land it in `boards_ok` and evict its rows two runs later — the
            # failure this line exists to report, arriving by the path that reports it.
            # A tenant with nothing open still answers `{"offers": []}`, so a payload carrying no
            # `offers` at all was not *read* — the same zero downstream as an empty board, which
            # is what makes it worth a line (`note_unreadable_board`). Not marked truncated: what
            # a container-less payload means on this API has not been measured, and ADR-0053's
            # exclusion has no drain, so a wrong guess holds this Board's rows in the index
            # indefinitely.
            self.note_unreadable_board(
                "a payload with an `offers` list", "no `offers` key"
            )
            return []
        jobs: list[Job] = []
        for o in offers:
            raw_location = o.get("location")
            is_sentinel = _is_remote_sentinel(raw_location, o.get("city"))
            location = (
                (None if is_sentinel else raw_location)
                or ", ".join(x for x in (o.get("city"), o.get("country")) if x)
                or None
            )
            jobs.append(
                Job(
                    id=self.job_id(o["id"]),
                    ats=self.ats,
                    company=o.get("company_name") or self.company,
                    title=(o.get("title") or "").strip(),
                    location=location,
                    # Deliberately NOT `or is_sentinel`: a detector false positive must be able
                    # to swap one spelling of a place for another and nothing worse. Recruitee's
                    # own flag is the authoritative remote signal and was set on every one of
                    # ~2.3k observed markers, so reading the marker here would buy nothing while
                    # letting a mis-detected city silently mark an on-site Job remote.
                    # Hybrid is None (`remote_from_workplace`).
                    remote=remote_from_workplace(_workplace_type(o), location),
                    department=o.get("department"),
                    url=self.job_url(o),
                    posted_at=o.get("published_at") or o.get("created_at"),
                    scraped_at=scraped_at,
                    # requirements is a separate field — dropping it starves experience
                    # extraction and the embedding of the qualifications text
                    description=_description(o),
                    experience=o.get("experience_code"),
                    employment_type=o.get("employment_type_code"),
                    salary=self._salary_field(o.get("salary")),
                )
            )
        return jobs

    def _salary_field(self, raw: dict | None) -> str | None:
        """Format Recruitee's structured salary, e.g. '50000-70000 EUR per year'. None if blank."""
        raw = raw or {}
        lo, hi = raw.get("min"), raw.get("max")
        if not lo:
            # blank, or a ceiling alone — `salary.extract` reads a lone figure as a floor, so
            # "up to 5339 EUR month" would serve as a 64k/yr minimum (iCIMS refuses the same)
            return None
        return salary.to_field(lo, hi or None, raw.get("currency"), raw.get("period"))
