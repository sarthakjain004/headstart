"""Breezy HR job-board scraper (``{slug}.breezy.hr``).

Every tenant's board is its own subdomain of ``breezy.hr``, and that label is this scraper's
``slug``: the board, its JSON listing, its sitemap and every posting's page (``/p/{friendly_id}``)
sit on the one host, and ``company.friendly_id`` equals the label on every hiring Board measured.

Everything below was measured 2026-09-23 over the whole candidate pool — 4,794 tenants, each
fetched once, 38,314 postings — and is written up in
``docs/breezy/2026-09-23_json-api-measurement.md``; the decisions are ADR-0181.

**One request per Board, and no detail pass.** ``GET /json`` lists every posting with no
pagination — the largest Board, 2,760 postings, comes back whole in one 8.1 MB response, equal to
what its own board page links. The undocumented ``?verbose=true`` adds each posting's
``description``, and its text is the detail page's: ``html_to_text`` of the two was equal on 180
of 180 pages carrying a JSON-LD ``JobPosting``, and every line of it was on the other 24. So
upstream's per-posting detail fetch (``kalil0321/ats-scrapers``) buys nothing, and
``has_detail_pass`` stays False. There is no stated total to check a response against, so a
short Board cannot be detected, only a failed one.

**Dead is a 404.** A departed tenant — and an invented label — answers ``/json`` with a
3,265-byte "Career portal not found" page (917 of 4,794); a live Board with nothing open answers
``[]`` (1,703). No tenant redirected, though upstream expects a 302 to the marketing site. The
404 raises here, so a dead Board fails loudly instead of reading as empty; the liveness prober
keeps such Boards out of the scrape list in the first place.

Field mappings, each on the measured distribution:
  - ``company`` is the row's ``company.name``: one value per Board on 2,174 of 2,174 hiring
    Boards, the employer's own spelling. No board-page request is made for it.
  - ``location`` joins every place (:func:`_location`); ``remote`` is the stated flag, hybrid as
    None (:func:`_remote`).
  - ``posted_at`` is ``published_date``, stable across refetches (293 of 293) and the *latest*
    publish — the page's JSON-LD ``datePosted`` is earlier on 112 of 180.
  - ``employment_type`` is keyed on ``type.id`` (:data:`_TYPE_LABELS`), since ``type.name`` is
    localised per tenant.
  - ``salary`` is the templated string re-spelt for the shared parser (:meth:`_salary_field`).
  - No experience field exists: upstream's ``experience``/``category``/``education``/``tags``
    appear on 0 of 38,314 rows.

No rate limit was found: 4,794 tenants at up to 64 concurrent (94 req/s) drew nothing but 200s
and 404s, and one tenant at 128 concurrent served 113 req/s — upstream's cross-tenant 403 at
~14 req/s did not reproduce. The host is User-Agent-agnostic.
"""

from __future__ import annotations

import re
from typing import Any

from headstart import salary
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

#: `type.id` -> label. The five ids observed across 38,314 postings (25,626 / 5,501 / 5,026 /
#: 1,658 / 503). The id, not `type.name`, is the key: the name is localised per tenant
#: ("Vollzeit", "A tiempo completo"), which no employment-type filter reads. An unobserved id
#: passes through as the provider spells it.
_TYPE_LABELS: dict[str, str] = {
    "fullTime": "Full-Time",
    "contract": "Contract",
    "partTime": "Part-Time",
    "other": "Other",
    "temporary": "Temporary",
}


#: The salary template, which every one of the 19,167 stated salaries matches: an optional
#: "Up to ", a currency symbol, a figure, then a floor "+", an exact figure, or " – " and a second
#: figure under the same symbol, then an optional " / {period}". Figures use comma thousands and
#: dot decimals throughout.
_SALARY = re.compile(
    r"^(?P<upto>Up to )?(?P<sym>[^\d]*?)(?P<lo>\d[\d,.]*)"
    r"(?:(?P<plus>\+)| – (?P=sym)(?P<hi>\d[\d,.]*))?(?: / (?P<period>\w+))?$"
)

#: The period word -> the bare unit `salary.from_field` annualises for breezy. No
#: period (90 postings) reads as annual, that parser's default. "biweekly" (161) is left out on
#: purpose: no shared parser reads it, and passing it bare would read a fortnight's pay as a year's.
_PERIODS: dict[str | None, str] = {
    "hour": "HOUR",
    "day": "DAY",
    "week": "WEEK",
    "month": "MONTH",
    "year": "YEAR",
    None: "",
}

#: Salary symbol -> ISO code, each checked against the posting page's JSON-LD
#: `baseSalary.currency` (499 postings, 2026-09-23; every sampled one agreed). A symbol not here
#: — `kr` (SEK or DKK), `Rp` (never checked) or anything unseen — states no currency.
#:
#: Known limit (ADR-0181): `salary.from_field` names only the ISO codes it knows, so
#: the 15 here outside it (PHP, TWD, PKR, ZAR, …; 103 of 19,167 salaries) still reach `extract`
#: as currency None — annualised, but unpriced, like a bare `$` outside the US and
#: Canada. The code is emitted anyway, so widening that shared list is all it would take.
_SYMBOLS: dict[str, str] = {
    "£": "GBP",
    "€": "EUR",
    "₹": "INR",
    "₱": "PHP",
    "NT$": "TWD",
    "₨": "PKR",
    "R": "ZAR",
    "zł": "PLN",
    "CN¥": "CNY",
    "฿": "THB",
    "RD$": "DOP",
    "CHF": "CHF",
    "₫": "VND",
    "₴": "UAH",
    "￥": "JPY",
    "₪": "ILS",
    "R$": "BRL",
    "Ksh": "KES",
    "د.إ.\u200f": "AED",
    "ر.س.\u200f": "SAR",
    "د.ك.\u200f": "KWD",
}

#: A bare `$` by the posting's country (ADR-0181). Measured against the page's JSON-LD: USD on
#: 141 of 141 US postings, CAD on 179 of 188 Canadian ones (the other 9 are paid in USD — a known
#: error this accepts), and USD on only 41 of 47 elsewhere (MXN, SGD, COP, unstated), so every
#: other country states none. A scoped exception, chosen by the user, to `salary.from_field`'s
#: rule that a bare `$` names no currency.
_DOLLAR_BY_COUNTRY: dict[str, str] = {"US": "USD", "CA": "CAD"}


def _currency(symbol: str, country: str | None) -> str | None:
    """The ISO code a salary symbol names, a bare `$` by the posting's country code."""
    if symbol == "$":
        return _DOLLAR_BY_COUNTRY.get(country or "")
    return _SYMBOLS.get(symbol)


def _location(row: dict) -> str | None:
    """The primary place, then every other place in `locations`, "; "-joined without repeats —
    the multi-place form workday and pyjamahr use, so the substring location filter matches each
    of them. `locations` names 2-5 places on 2,017 of 38,314 postings; the primary is not among
    them on 828 of the 35,344 rows that list any, and `locations` is empty on 2,970 (one of which
    has no primary either)."""
    places: list[str] = []
    for place in [row.get("location"), *(row.get("locations") or [])]:
        name = ((place or {}).get("name") or "").strip()
        if name and name not in places:
            places.append(name)
    return "; ".join(places) or None


def _remote(row: dict, location: str | None) -> bool | None:
    """The primary location's `is_remote` flag, with hybrid as None (ashby's rule).

    The flag is what the posting page publishes: true → JSON-LD `TELECOMMUTE` on 46 of 46 pages,
    false or absent → none on 57 of 57. `remote_details` is not trusted on its own — it still
    says remote, remote-location or hybrid on 307 rows whose flag is false. The flag is absent on
    2,194 rows, which fall back to the location text — it names "remote" on none of them, so the
    fallback answers False there, as the 12 of those checked against their pages do (among the 57
    above); it only speaks if a flagless row ever says "Remote". Within one posting the
    per-place flags never disagree (0 of 38,314), so the primary speaks for all of them.
    """
    primary = row.get("location") or {}
    flag = primary.get("is_remote")
    if flag is None:
        return is_remote(location)
    if flag and (primary.get("remote_details") or {}).get("value") == "hybrid":
        return None
    return bool(flag)


def _employment_type(row: dict) -> str | None:
    type_id = (row.get("type") or {}).get("id")
    return _TYPE_LABELS.get(type_id, type_id) if type_id else None


class BreezyScraper(BaseScraper):
    ats = "breezy"
    # scraper: the row's own `url`, `https://{slug}.breezy.hr/p/{friendly_id}` on 38,314 of
    # 38,314 postings. `friendly_id` is `[a-z0-9_-]` (3 carry an underscore).
    url_shape = r"https://[a-z0-9-]+\.breezy\.hr/p/[\w-]+"

    def url(self) -> str:
        return f"https://{self.slug}.breezy.hr/json?verbose=true"

    def job_url(self, friendly_id: str) -> str:
        return f"https://{self.slug}.breezy.hr/p/{friendly_id}"

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for row in raw:
            location = _location(row)
            jobs.append(
                Job(
                    id=self.job_id(row["id"]),
                    ats=self.ats,
                    company=(row.get("company") or {}).get("name") or self.company,
                    title=(row.get("name") or "").strip(),
                    location=location,
                    remote=_remote(row, location),
                    department=row.get("department") or None,
                    posted_at=row.get("published_date"),
                    url=self.job_url(row["friendly_id"]),
                    scraped_at=scraped_at,
                    description=html_to_text(row.get("description")),
                    employment_type=_employment_type(row),
                    salary=self._salary_field(row),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        """``Job.salary`` from the row's templated `salary` string ("$25 – $30 / hour"), re-spelt
        as RANGE CODE UNIT ("25-30 USD HOUR") through `salary.to_field` — the shape
        `salary.from_field` reads for breezy, where the generic reader read none of the hourly,
        weekly or monthly figures (neither "/ hour" nor "/ week" is one of its phrase markers).

        A floor ("$20+") keeps no ceiling and an exact figure ("$18") becomes coinciding bounds.
        A lone ceiling ("Up to $60,000", 37 postings) and a biweekly period (161) yield None: the
        parser would read the first as a floor and the second as a year.
        """
        text = ((raw or {}).get("salary") or "").strip()
        m = _SALARY.match(text)
        if not m or m.group("upto") or m.group("period") not in _PERIODS:
            return None
        lo = m.group("lo").replace(",", "")
        if m.group("hi"):
            hi = m.group("hi").replace(",", "")
        elif m.group("plus"):
            hi = None
        else:
            hi = lo
        country = ((raw.get("location") or {}).get("country") or {}).get("id")
        return salary.to_field(
            lo, hi, _currency(m.group("sym"), country), _PERIODS[m.group("period")]
        )
