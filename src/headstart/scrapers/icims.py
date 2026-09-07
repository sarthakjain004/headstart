"""iCIMS: one sitemap listing, then a JSON-LD detail page per Job.

Everything here rests on measurements taken live on 2026-09-07 against 380 boards; the captures
and the sweep scripts are in `experiment/icims-scraper/`. Three of those measurements shape the
module more than the rest, and each is a trap this code is arranged to make hard to reintroduce.

**One listing surface, not a cascade.** iCIMS boards expose two: `/sitemap.xml`, and the paginated
`/jobs/search?ss=1&in_iframe=1&pr=N` HTML. Only the sitemap is used. On 380 boards, `robots.txt`
predicted sitemap availability perfectly — every board declaring a `Sitemap:` line served one
(273/320 in the larger sweep), every silent board returned 403, and **the set of 403 boards was
exactly the set serving `Disallow: /`**. So the HTML walk exists only to crawl tenants that asked
not to be crawled; declining to build it costs no Board we are entitled to read, and deletes
pagination, per-tenant page size (20/50/1/2/5/6 all observed) and the unreliable "Page N of M"
string in one go. The sitemap is also never the shorter list: across 46 fully-paged boards it
missed nothing the listing had, and on `hmi-esp-harvard` it held 109 where the listing advertised
no pagination at all and looked like 20.

**`datePosted` is fabricated per request.** The same posting fetched twice, three seconds apart,
returned `datePosted` values three seconds apart — it is `now - 2 years`, and `validThrough` is
`now + 1 year`. Both are generated at render time and mean nothing. The sitemap's `<lastmod>` is
the only real date iCIMS publishes, which is why `posted_at` is carried on the *listing* record
and why :data:`_LD_KEEP` has no date key: the fabricated value is not ignored downstream, it never
reaches :meth:`parse` at all. (`lastmod` is a last-*modified* date standing in for a first-posted
one. It is the best available, not the same thing.)

**A detail fetch without `in_iframe=1` returns the branded wrapper**: HTTP 200, ~80 KB, no JSON-LD
whatsoever. It fails as "no data", not as an error, so :func:`_ld_fields` returning None is treated
as a gap and marks the Board truncated (ADR-0053) rather than letting a wrapper page read as a
delisting. :func:`_detail_url` is the only place `in_iframe=1` is added.

Two smaller findings are wired in rather than documented. `baseSalary` puts `minValue`/`maxValue`
**directly on the node**, not under `value` as a QuantitativeValue — a spec-correct parser reads
every one as null, which happened during recon — and `unitText` is never present (0/56) while
values span 14 to 215,800, so the period has to come from magnitude. And iCIMS is UA-agnostic
(bare `headstart/0.1`, curl's default, and no User-Agent all return 200), so unlike successfactors
and zwayam there is no UA constraint to respect beyond repo policy.

There is no JSON API. A browser HAR of a job page shows six XHR calls, all third-party (Google
Analytics, altrulabs, Facebook, Snowplow) and none first-party.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from headstart import http, log
from headstart.models import Job, host_of, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_log = log.get(__name__)

_SITEMAP_CAP = (
    30 * 1024 * 1024
)  # runaway guard; the largest sitemap measured is ~1.5 MB
_DETAIL_WORKERS = 16

#: The JSON-LD keys this scraper is willing to see. An allowlist rather than a blocklist so the
#: fabricated `datePosted`/`validThrough` are absent from the value `parse` receives — putting the
#: fabricated date back requires editing this frozenset, which `test_icims.py` asserts against,
#: rather than merely forgetting a rule. `directApply` is omitted for a different reason: it was
#: True on 207 of 207 sampled postings, so it carries no information.
_LD_KEEP = frozenset(
    {
        "title",
        "description",
        "jobLocation",
        "employmentType",
        "occupationalCategory",
        "baseSalary",
        "jobLocationType",
    }
)

_SITEMAP_URL = re.compile(
    r"<url>\s*<loc>([^<]+)</loc>\s*(?:<lastmod>([^<]+)</lastmod>)?", re.IGNORECASE
)
_JOB_PATH = re.compile(r"/jobs/(\d+)/[^/]*/job\b", re.IGNORECASE)
_LD_BLOCK = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

# Above this, a figure is an annual salary; at or below it, an hourly rate. iCIMS never states the
# period (`unitText` absent on all 56 sampled `baseSalary` nodes) while the values themselves span
# 14 to 215,800 — 35 of 55 hourly, 20 annual — so magnitude is the only signal there is. 1,000 sits
# in the empty band between the two clusters: the largest hourly figure measured is 215 and the
# smallest annual one is 27,040.
_HOURLY_CEILING = 1_000


class ICIMSScraper(BaseScraper):
    """One iCIMS tenant's board, keyed by its host (e.g. ``career-celanese.icims.com``)."""

    ats = "icims"
    has_detail_pass = True  # every indexable field lives on the job page (ADR-0050)
    detail_workers = _DETAIL_WORKERS
    detail_streams = _DETAIL_WORKERS

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """Host only — the same normalisation zwayam, zoho and personio each override for.

        Discovery stores the raw capture for host-shaped ATSes, so a ledger row can carry a job
        deep link rather than the board root. Left to the default, `url()` would append
        `/sitemap.xml` to a path or query and fetch something that is not the sitemap. Personio's
        version of this bug cost 678 ParseErrors over 19 runs before it was found.
        """
        return host_of(url) or tenant.strip().lower()

    def url(self) -> str:
        return f"https://{self.slug}/sitemap.xml"

    def fetch_raw(self) -> Any:
        response = http.fetch(
            "GET", self.url(), headers={"User-Agent": USER_AGENT}, timeout=60
        )
        # 403 is iCIMS enforcing the tenant's own `Disallow: /`, measured identical on 47/47
        # opt-out boards. It is a settled answer, not a transient one, so it raises like any other
        # non-200 and the Board earns a liveness verdict rather than looking empty.
        response.raise_for_status()
        listed = _sitemap_rows(response.text[:_SITEMAP_CAP], self.slug)
        _log.info(f"{self.slug}: sitemap -> {len(listed)} job pages to fetch")
        if not listed:
            return []

        if self.async_fanout_enabled():
            fields = self.fan_out_async(
                listed,
                lambda session, row: self._job_fields_async(session, row[1]),
            )
        else:
            fields = self.fan_out(
                listed,
                lambda row: self._job_fields(row[1]),
                workers=_DETAIL_WORKERS,
            )

        lost = self.report_detail_gaps(fields, "detail fields")
        if lost:
            # Every field but `posted_at` comes from the job page, so `parse` drops a Job whose
            # page did not arrive — which makes this list knowingly short, and an unmarked short
            # list is what `index sync` reads as a delisting (ADR-0053). This is also where a
            # dropped `in_iframe=1` would surface: the wrapper page parses to None, so the whole
            # Board reports 100% gaps instead of silently returning nothing.
            self.mark_truncated(
                f"{lost}/{len(listed)} job pages unreadable — those Jobs are listed but unbuilt"
            )
        return [
            {"id": job_id, "url": url, "posted_at": lastmod, "fields": page_fields}
            for (job_id, url, lastmod), page_fields in zip(listed, fields)
        ]

    def _job_fields(self, url: str) -> dict[str, Any] | None:
        response = http.fetch(
            "GET", _detail_url(url), headers={"User-Agent": USER_AGENT}, timeout=30
        )
        if response.status_code != 200:
            return None
        return _ld_fields(response.text)

    async def _job_fields_async(self, session: Any, url: str) -> dict[str, Any] | None:
        response = await http.fetch_async(
            session,
            "GET",
            _detail_url(url),
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        if response.status_code != 200:
            return None
        return _ld_fields(response.text)

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for item in raw:
            fields = item.get("fields") or {}
            title = (fields.get("title") or "").strip()
            if not title:
                continue  # page unreadable or a wrapper — nothing to keep the Job by
            location = fields.get("location")
            remote = fields.get("remote")
            if remote is None:
                remote = is_remote(location)
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{item['id']}",
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=remote,
                    department=fields.get("department"),
                    url=item["url"],
                    # Sitemap `<lastmod>` only. The JSON-LD's own date is generated per request.
                    posted_at=item.get("posted_at"),
                    scraped_at=scraped_at,
                    description=html_to_text(fields.get("description")),
                    employment_type=fields.get("employment_type"),
                    salary=fields.get("salary"),
                )
            )
        return jobs


def _sitemap_rows(xml: str, host: str) -> list[tuple[str, str, str | None]]:
    """``(job_id, public_url, lastmod)`` per posting, deduped, in sitemap order.

    Non-job entries are skipped: all 35 sitemaps sampled carry at least one non-posting URL
    alongside the jobs — `/jobs/intro` on 15 of them, `/jobs/search` on the rest — and neither
    has an id, so `_JOB_PATH` is what separates them rather than a name-based exclusion.
    """
    rows: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()
    for loc, lastmod in _SITEMAP_URL.findall(xml):
        match = _JOB_PATH.search(loc)
        if not match:
            continue
        job_id = match.group(1)
        if job_id in seen:
            continue
        seen.add(job_id)
        rows.append((job_id, _public_url(loc.strip()), (lastmod or "").strip() or None))
    _ = host  # host is the caller's own slug; kept in the signature for symmetry with parse
    return rows


def _public_url(job_url: str) -> str:
    """The user-facing job URL: no query string, so never `in_iframe=1`.

    This is what ships as ``Job.url`` and what a person opens — it renders the tenant's branded
    page carrying the apply button (`?mode=apply&apply=yes&in_iframe=1&hashed=N`, which is
    per-link hashed and cannot be constructed here).
    """
    parts = urlsplit(job_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _detail_url(job_url: str) -> str:
    """The fetchable job URL. The only place `in_iframe=1` is added.

    Without it iCIMS serves the branded wrapper: 200, ~80 KB, and no JSON-LD at all.
    """
    parts = urlsplit(job_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "in_iframe=1", ""))


def _ld_fields(page: str) -> dict[str, Any] | None:
    """The JobPosting fields from a job page's JSON-LD, or None if it carries none.

    None is the wrapper-page signature as well as a genuine JSON-LD outage (3% of pages measured),
    and both are counted as detail gaps by the caller.
    """
    for match in _LD_BLOCK.finditer(page):
        try:
            data = json.loads(match.group(1))
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type")
            if node_type != "JobPosting" and not (
                isinstance(node_type, list) and "JobPosting" in node_type
            ):
                continue
            kept = {k: v for k, v in node.items() if k in _LD_KEEP}
            employment = kept.get("employmentType")
            if isinstance(employment, list):
                employment = ", ".join(str(e) for e in employment) or None
            return {
                "title": kept.get("title"),
                "description": kept.get("description"),
                "location": _ld_location(kept),
                "department": kept.get("occupationalCategory"),
                "employment_type": employment,
                "salary": _salary(kept.get("baseSalary")),
                "remote": True
                if kept.get("jobLocationType") == "TELECOMMUTE"
                else None,
            }
    return None


def _ld_location(node: dict[str, Any]) -> str | None:
    """`jobLocation` flattened to one string, or None.

    Only the first of a multi-location posting is used (4 of 207 sampled carry more than one), and
    the literal `UNAVAILABLE` iCIMS writes into unset address parts is dropped rather than shown.
    """
    place = node.get("jobLocation")
    if isinstance(place, list):
        place = place[0] if place else None
    if not isinstance(place, dict):
        return None
    address = place.get("address")
    if not isinstance(address, dict):
        return None
    country = address.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    parts = [address.get("addressLocality"), address.get("addressRegion"), country]
    joined = ", ".join(
        str(p).strip()
        for p in parts
        if p and str(p).strip() and str(p).strip() != "UNAVAILABLE"
    )
    return joined or None


def _salary(node: Any) -> str | None:
    """``baseSalary`` as a string ``headstart.salary`` can actually read, or None.

    Two measured quirks, both handled here and nowhere else. iCIMS puts `minValue`/`maxValue`
    **directly on the node** rather than under `value` as schema.org specifies, so both shapes are
    read — a spec-correct parser sees null on every real iCIMS posting. And `unitText` is never
    present, so the period comes from magnitude (:data:`_HOURLY_CEILING`).

    The spelling matters and is not free choice: `salary.extract()` reads `"USD 30.00 hourly"` and
    `"USD 95000-125000 yearly"`, but returns None for `"USD 30.00/hour"`. Since 35 of the 55
    measured figures are hourly, the slash spelling would null the majority case while annual
    salaries landed — so the period word is appended, never a slash. There is one return site and
    the period is always part of it, so a bare number cannot be emitted.
    """
    if not isinstance(node, dict):
        return None
    value = node.get("value")
    source = value if isinstance(value, dict) else node
    lo = _number(source.get("minValue"))
    hi = _number(source.get("maxValue"))
    if lo is None and hi is None:
        lo = _number(source.get("value"))
    figures = [n for n in (lo, hi) if n is not None]
    if not figures:
        return None
    period = "hourly" if max(figures) <= _HOURLY_CEILING else "yearly"
    currency = node.get("currency") or node.get("salaryCurrency")
    amount = (
        f"{lo:g}-{hi:g}"
        if lo is not None and hi is not None and lo != hi
        else f"{figures[0]:g}"
    )
    return f"{currency} {amount} {period}" if currency else f"{amount} {period}"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    return parsed if parsed > 0 else None
