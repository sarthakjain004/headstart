"""Recruitee job-board scraper ({slug}.recruitee.com offers API).

Adapted from jobhive's Recruitee scraper (kalil0321/ats-scrapers, MIT) to this project's
BaseScraper contract:
    https://{slug}.recruitee.com/api/offers/
"""

from __future__ import annotations

from typing import Any

from headstart.models import Job, html_to_text, is_remote
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


def _salary(sal: dict | None) -> str | None:
    """Format Recruitee's structured salary, e.g. '50000-70000 EUR per year'. None if blank."""
    sal = sal or {}
    lo, hi = sal.get("min"), sal.get("max")
    if not lo and not hi:
        return None
    rng = f"{lo}-{hi}" if lo and hi else str(lo or hi)
    return " ".join(str(x) for x in (rng, sal.get("currency"), sal.get("period")) if x)


class RecruiteeScraper(BaseScraper):
    ats = "recruitee"

    def url(self) -> str:
        return f"https://{self.slug}.recruitee.com/api/offers/"

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for o in raw.get("offers", []):
            raw_location = o.get("location")
            is_sentinel = _is_remote_sentinel(raw_location, o.get("city"))
            location = (
                (None if is_sentinel else raw_location)
                or ", ".join(x for x in (o.get("city"), o.get("country")) if x)
                or None
            )
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{o['id']}",
                    ats=self.ats,
                    company=o.get("company_name") or self.company,
                    title=(o.get("title") or "").strip(),
                    location=location,
                    # Deliberately NOT `or is_sentinel`: a detector false positive must be able
                    # to swap one spelling of a place for another and nothing worse. Recruitee's
                    # own flag is the authoritative remote signal and was set on every one of
                    # ~2.3k observed markers, so reading the marker here would buy nothing while
                    # letting a mis-detected city silently mark an on-site Job remote.
                    remote=bool(o.get("remote")) or is_remote(location),
                    department=o.get("department"),
                    url=_offer_url(self.slug, o),
                    posted_at=o.get("published_at") or o.get("created_at"),
                    scraped_at=scraped_at,
                    # requirements is a separate field — dropping it starves experience
                    # extraction and the embedding of the qualifications text
                    description=html_to_text(
                        "\n".join(
                            s
                            for s in (o.get("description"), o.get("requirements"))
                            if s
                        )
                    ),
                    experience=o.get("experience_code"),
                    employment_type=o.get("employment_type_code"),
                    salary=_salary(o.get("salary")),
                )
            )
        return jobs
