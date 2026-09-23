"""Pinpoint job-board scraper (``{slug}.pinpointhq.com``).

A Board is one tenant subdomain, and its **lowercased label** is the slug: DNS and the board are
case-insensitive (``CINVEN`` serves ``cinven``), and the seed list spells one tenant ``Cinven``.
There is one host and no regional pod. Everything below was measured 2026-09-23 — a census of
``/postings.json`` on all 555 pool and seed slugs (550 live, 433 hiring, 13,419 postings), 76
posting pages from 40 Boards, ~1,450 rate-limit requests — and is written up in
``docs/pinpoint/2026-09-23_postings-api-measurement.md`` (ADR-0184).

**The listing is the Board, whole, in one call.** ``GET /postings.json`` is the board page's own
XHR: one ``{"data": [...]}`` array, no pagination and no parameter (``?page``/``?per_page`` are
ignored), no stated total to fall short of. It matched the tenant's own sitemap exactly on the
largest Boards (trilongroup 932 of 932). It is not a teaser either: the body arrives as four HTML
sections — ``description`` (100%), ``key_responsibilities`` (100%),
``skills_knowledge_expertise`` (85.7%), ``benefits`` (72.3%), each under a tenant-chosen header
— which are exactly what the page's JSON-LD ``description`` concatenates. No cap (max 20,016
chars); upstream's 25,000-char cap is its own.

**The date exists only on the posting page.** No listing field states one on any of 13,419 rows
(upstream reads ``first_published_at``, which does not exist). The page's JSON-LD ``datePosted``
was present on 76 of 76 and unchanged across two fetches 5 s apart; the sitemap's ``<lastmod>``
disagreed with it on 36 of 76, always later, so it is a modification date and not used. The page
also names the country the listing lacks (``applicantLocationRequirements``, 75 of 76). So this
is a detail-pass ATS for two fields, and it declines ADR-0048's skip of the already-described:
the description comes from the listing, so a store hit says nothing about the date, and skipping
would blank ``posted_at`` on every run after the first. The skip it does take is the tech gate
(ADR-0166), as an **exact** site: ``title`` and ``job.department.name`` are listing fields on
13,419 of 13,419 rows and the page overrides neither. At 12.9% tech that spares ~87% of the page
fetches (~132 KB each).

**Identity.** A posting's UUID (the last segment of its ``url``) is the native id: it is the only
id that addresses a page, ``/en/postings/{numeric id}`` answers 404. 29 of 433 hiring Boards run a
vanity host and the listing's ``url`` names it, but the same path on the vendor host serves the
same page (5 of 5 checked), so every link is built on the vendor host.

**Remote is stated.** ``workplace_type`` is populated on 100% (onsite 9,282 / hybrid 2,738 /
remote 1,399), and 919 of the remote rows name only a city, so it wins over any location guess.
Hybrid is ``None``.

**Dead versus empty lives in the prober, not here.** An unknown slug is a real 404 and raises
through ``_get`` as a Board failure; a live empty Board is 200 ``{"data": []}`` and parses to no
Jobs. A renamed tenant 301s to another label, which ``check_liveness.p_pinpoint`` records dead.

No rate limit was found (to 128 concurrent, zero non-200s) and the host is User-Agent-agnostic.
"""

from __future__ import annotations

import json
import re
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text
from headstart.scrapers.base import BaseScraper

_LD_BLOCK = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

#: The listing's body sections after `description`, in the order the posting page's own JSON-LD
#: `description` lays them out, each under its tenant-chosen `{section}_header`.
_SECTIONS = ("key_responsibilities", "skills_knowledge_expertise", "benefits")


def _description(item: dict) -> str | None:
    """`description` then each present section under its header, as plain text."""
    parts = [item.get("description") or ""]
    for section in _SECTIONS:
        body = item.get(section)
        if body:
            parts.append(f"<h3>{item.get(f'{section}_header') or ''}</h3>{body}")
    return html_to_text("".join(parts))


def _location(item: dict, country: str | None = None) -> str | None:
    """`location.name`, then `city`, `province` and the page's country, each only when what
    precedes it does not already contain it (case-insensitively). `name` leads because it is the
    tenant's own label, often a site name (\"GM Tech\", \"Shipboard\") that the structured
    fields do not repeat; there is one location per posting on every row measured."""
    place = item.get("location") or {}
    parts: list[str] = []
    for value in (place.get("name"), place.get("city"), place.get("province"), country):
        value = (value or "").strip()
        if value and value.lower() not in ", ".join(parts).lower():
            parts.append(value)
    return ", ".join(parts) or None


#: `workplace_type` -> `Job.remote`, on 100% of rows. Hybrid is not remote (ashby's rule).
_REMOTE = {"remote": True, "onsite": False}


#: `compensation_frequency` -> the period spelling `salary._field_generic` reads. The three it
#: reads are year (3,233 rows), hour (2,781) and month (342). week (33), day (16) and two_weeks
#: (10) have no spelling it reads, and its default is annual, so those yield no salary rather
#: than a figure served at the wrong period.
_PERIODS = {"year": "per-year", "hour": "per-hour", "month": "per-month"}


def _digits(value: float) -> str:
    """A float bound as digits, never `:g`'s scientific notation (1.3e+07)."""
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _uuid(item: dict) -> str:
    """The posting UUID, the last segment of the listing's `url` (13,419 of 13,419 rows)."""
    return (item.get("url") or "").rstrip("/").rsplit("/", 1)[-1]


def _page_fields(page: str) -> dict[str, Any] | None:
    """`posted_at` and `country` from a posting page's JSON-LD `JobPosting`, or None when the
    page carries none (a counted detail gap, not an error)."""
    for match in _LD_BLOCK.finditer(page):
        try:
            node = json.loads(match.group(1))
        except ValueError:
            continue
        if not isinstance(node, dict) or node.get("@type") != "JobPosting":
            continue
        where = node.get("applicantLocationRequirements")
        country = where.get("name") if isinstance(where, dict) else None
        return {"posted_at": node.get("datePosted") or None, "country": country}
    return None


#: Concurrent page fetches. Posting pages of one Board ran clean to 64 (106.7 req/s, zero
#: non-200s) and slowed at 128; `harvest` scrapes Boards concurrently, so peak in-flight is the
#: product, and 16 is what icims, zwayam, oracle and pyjamahr run.
_DETAIL_WORKERS = 16


class PinpointScraper(BaseScraper):
    ats = "pinpoint"
    url_shape = r"https://[a-z0-9-]+\.pinpointhq\.com/en/postings/[0-9a-f-]{36}"
    has_detail_pass = (
        True  # per-Job page fetch fills `posted_at` and the country (ADR-0050)
    )
    detail_workers = _DETAIL_WORKERS

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        return tenant.lower()

    def board_page(self) -> str:
        """The board page, titled `Jobs at {Name} | {Name} Careers` on 40 of 40 sampled Boards
        (`company_name.PATTERNS["pinpoint"]`, ADR-0114)."""
        return f"https://{self.slug}.pinpointhq.com/"

    def url(self) -> str:
        return f"https://{self.slug}.pinpointhq.com/postings.json"

    def job_url(self, uuid: str) -> str:
        return f"https://{self.slug}.pinpointhq.com/en/postings/{uuid}"

    def fetch_raw(self) -> Any:
        listed = json.loads(self._get()).get("data") or []
        wanted = self.tech_detail_wanted(
            listed,
            lambda i: i.get("title"),
            lambda i: ((i.get("job") or {}).get("department") or {}).get("name"),
        )
        uuids = [_uuid(i) for i in wanted]
        details: dict[str, dict] = {}
        if uuids:
            if self.async_fanout_enabled():
                fetched = self.fan_out_async(uuids, self._page_fields_async)
            else:
                fetched = self.fan_out(
                    uuids, self._page_fields, workers=self.detail_workers
                )
            self.report_detail_gaps(fetched, "posting pages")
            details = {u: d for u, d in zip(uuids, fetched) if d}
        return {"data": listed, "details": details}

    def _page_fields(self, uuid: str) -> dict[str, Any] | None:
        try:
            page = self._get(self.job_url(uuid))
        except http.RequestsError as exc:
            self.note_detail_exception(exc)
            return None
        return self._fields_or_loss(page)

    async def _page_fields_async(
        self, session: Any, uuid: str
    ) -> dict[str, Any] | None:
        try:
            page = await self._get_async(session, self.job_url(uuid))
        except http.RequestsError as exc:
            self.note_detail_exception(exc)
            return None
        return self._fields_or_loss(page)

    def _fields_or_loss(self, page: str) -> dict[str, Any] | None:
        fields = _page_fields(page)
        if fields is None:
            self.note_detail_loss("no JSON-LD on a 200")
        return fields

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        details = raw.get("details") or {}
        jobs: list[Job] = []
        for item in raw.get("data") or []:
            uuid = _uuid(item)
            page = details.get(uuid) or {}
            jobs.append(
                Job(
                    id=self.job_id(uuid),
                    ats=self.ats,
                    company=self.company,
                    title=item["title"].strip(),
                    location=_location(item, page.get("country")),
                    remote=_REMOTE.get(item.get("workplace_type") or ""),
                    department=item["job"]["department"]["name"].strip(),
                    url=self.job_url(uuid),
                    posted_at=page.get("posted_at"),
                    scraped_at=scraped_at,
                    description=_description(item),
                    employment_type=item.get("employment_type_text") or None,
                    salary=self._salary_field(item),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        """``Job.salary`` from a listing row, only when the tenant marks it visible.

        Of 7,001 visible rows, 6,414 state min, max, an ISO currency and a frequency, emitted as
        "31.75-39.50 USD per-hour"; 466 state only the display string, which is prose
        ("£26,123 pro rata per annum") and passes through verbatim for the shared parser to read
        or decline; 119 state nothing. Hidden rows carried no figure on any of 6,418.
        """
        if not raw.get("compensation_visible"):
            return None
        lo, hi = raw.get("compensation_minimum"), raw.get("compensation_maximum")
        if lo is None or hi is None:
            return (raw.get("compensation") or "").strip() or None
        period = _PERIODS.get(raw.get("compensation_frequency") or "")
        if period is None:
            return None
        currency = (raw.get("compensation_currency") or "").strip()
        return " ".join(
            p for p in (f"{_digits(lo)}-{_digits(hi)}", currency, period) if p
        )
