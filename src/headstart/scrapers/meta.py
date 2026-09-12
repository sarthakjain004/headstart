"""Meta (metacareers.com) — a Single Source scraper (ADR-0139): one company, one Board, never a
second tenant. ``slug`` is fixed to ``www.metacareers.com``, never discovered.

Meta runs its public listing UI as a client-side React app whose search is fed by GraphQL queries
requiring browser-issued tokens (``fb_dtsg`` and friends) — confirmed 2026-09-11 by inspecting the
`/jobsearch/` page's own server-rendered HTML: it carries no embedded job data (no
``__NEXT_DATA__``, no ``job_search_with_featured_jobs`` payload, zero ``"__typename":"Job"``
matches), so the listing is genuinely unreachable without a real browser session. This scraper does
not attempt one — it uses two public, unauthenticated surfaces instead, found via ``robots.txt``'s
own ``Sitemap:`` lines and confirmed live:

**Listing — `/jobsearch/sitemap.xml`.** Returns a flat ``<urlset>`` (no index, no further
pagination: `/jobsearch/sitemap-1.xml` etc all 404, and `?page=2` is silently ignored — it answers
200 with the *same* member set, just reordered) of every open posting's canonical URL,
``https://www.metacareers.com/profile/job_details/{id}/``. Measured 2026-09-11: 952 postings, no
User-Agent gate (curl default, `python-requests` default and a bare `headstart/0.1` all 200), and
a `<lastmod>` per entry. `robots.txt` also names a second, general sitemap
(`sitemap/www_metacareers_com_sitemap.xml.gz`) — that one 403s and is not used.

**Detail — the job page's own schema.org `JobPosting` JSON-LD.** Every field this scraper emits
except `id` comes from here; the sitemap carries no title, location, or anything else, so — like
icims and oracle — a Job whose detail fetch fails cannot be built at all, and skipping an
already-held Job's detail fetch (ADR-0048) would blank fields that only this pass supplies. No
special headers or query params are needed (unlike icims's `in_iframe=1`): a stale sitemap entry
whose posting has since closed answers 200 with **no JSON-LD block at all**, which reads as a
detail gap like any other. Measured over 80 randomly and sequentially sampled ids (30 + 50, two
bursts at concurrency 10 and 20): 80/80 HTTP 200, 80/80 carried parseable JSON-LD, zero rate
limiting (up to 8.6 req/s, flat latency).

Two JSON-LD dates disagree on how trustworthy they are. `datePosted` is **real and stable** —
the same posting fetched twice 4 seconds apart returned an identical value — so it is used
directly, with the sitemap's `<lastmod>` as a fallback for the (unobserved in 80 samples, but
possible) case it is absent. `validThrough` **is fabricated**: the same two fetches returned
values 5 seconds apart, moving by roughly the elapsed time — so, as with every other ATS that
exhibits this, it is not read at all.

Two fields this scraper cannot supply, both measured absent rather than assumed: **no `department`
or team anywhere** (zero of 80 sampled JSON-LD payloads carry `occupationalCategory`, `industry`,
`department` or `team` — the sibling `kalil0321/ats-scrapers` project reads `teams`/`sub_teams`
from the same GraphQL listing this scraper cannot reach without a browser), and **no salary**
(zero of 80 carry `baseSalary`, unlike icims/oracle where it is merely inconsistently present).

`jobLocation` is a list — often a long one (mean 2.24 across the 50-id random sample, 54% single-
location, one posting listing 14 sites) — and only the first is used, the same policy icims and
eightfold's sitemap fallback already apply to a multi-location posting, for the same reason: there
is no ranking signal to prefer one site over another, and Job.location is one string.
`jobLocationType == "TELECOMMUTE"` is Meta's own remote signal when present (2 of 50 sampled);
`is_remote(location)` is the fallback, matching every other ATS here.
"""

from __future__ import annotations

import json
import re
from typing import Any

from headstart import http
from headstart.models import Job, host_of, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_DETAIL_WORKERS = (
    16  # measured clean at conc 20 (80 reqs, zero non-200s); 16 matches icims/oracle
)

_SITEMAP_URL = re.compile(
    r"<url>\s*<loc>([^<]+)</loc>\s*(?:<lastmod>([^<]+)</lastmod>)?", re.IGNORECASE
)
_JOB_ID = re.compile(r"/profile/job_details/(\d+)/")
_LD_BLOCK = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


class MetaScraper(BaseScraper):
    """metacareers.com — a Single Source scraper (ADR-0139); ``slug`` is the fixed careers host."""

    ats = "meta"
    has_detail_pass = (
        True  # every field but `id` comes from the per-Job JSON-LD (ADR-0050)
    )
    detail_workers = _DETAIL_WORKERS
    detail_streams = _DETAIL_WORKERS

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """The careers host, not the ledger's display name (oracle's pattern). The one ledger
        row holds ``tenant="Meta"`` — a real name, not slug-shaped, so :meth:`resolve_company`
        leaves it alone rather than needing a `board_page`/`company_name` pattern that would be
        pure overhead for a single, fixed company — and the host in ``url``. ADR-0139 fixes the
        Board to ``www.metacareers.com`` regardless of what the ledger says, but deriving it from
        the row rather than hardcoding it keeps this scraper's only ledger dependency in one
        place."""
        return host_of(url) or tenant.strip().lower()

    def url(self) -> str:
        return f"https://{self.slug}/jobsearch/sitemap.xml"

    def alias_key(self) -> str | None:
        """Meta's own slug: a Single Source Board has no sibling tenant to alias against, and the
        base class's redirect-follow default would be answering a question that cannot arise here
        (ADR-0139). Confirmed 2026-09-11: :meth:`url` does not redirect."""
        return self.slug

    def fetch_raw(self) -> Any:
        listed = _sitemap_rows(self._get())
        if not listed:
            return []
        if self.async_fanout_enabled():
            fields = self.fan_out_async(
                listed, lambda session, row: self._job_fields_async(session, row[1])
            )
        else:
            fields = self.fan_out(
                listed,
                lambda row: self._job_fields(row[1]),
                workers=self.detail_workers,
            )
        lost = self.report_detail_gaps(fields, "detail fields")
        if lost:
            # Every field but `id` lives on the detail page, so a lost fetch is a Job `parse`
            # cannot build at all — the list is knowingly short (ADR-0053), and the sitemap gives
            # a real total to measure the shortfall against, so a negligible loss is left to
            # ADR-0083's grace period rather than costing the whole Board its eviction scope
            # (ADR-0121) — the same call oracle and eightfold's sitemap fallback make.
            self.mark_truncated_unless_negligible(
                len(listed) - lost,
                len(listed),
                f"{lost}/{len(listed)} job pages unreadable — those Jobs are listed but unbuilt",
            )
        return [
            {"id": job_id, "url": url, "lastmod": lastmod, "fields": page_fields}
            for (job_id, url, lastmod), page_fields in zip(listed, fields)
        ]

    def _job_fields(self, url: str) -> dict[str, Any] | None:
        try:
            response = http.fetch(
                "GET", url, headers={"User-Agent": USER_AGENT}, timeout=30
            )
        except http.RequestsError as exc:
            self.note_detail_loss(type(exc).__name__)
            return None
        return self._fields_of(response)

    async def _job_fields_async(self, session: Any, url: str) -> dict[str, Any] | None:
        try:
            response = await http.fetch_async(
                session, "GET", url, headers={"User-Agent": USER_AGENT}, timeout=30
            )
        except http.RequestsError as exc:
            self.note_detail_loss(type(exc).__name__)
            return None
        return self._fields_of(response)

    def _fields_of(self, response: Any) -> dict[str, Any] | None:
        if response.status_code != 200:
            self.note_detail_loss(f"HTTP {response.status_code}")
            return None
        fields = _ld_fields(response.text)
        if fields is None:
            # A stale sitemap entry (the posting closed since the sitemap was generated) answers
            # 200 with no JSON-LD block at all — measured directly against a bumped, unlisted id.
            self.note_detail_loss("no JSON-LD on a 200")
        return fields

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for item in raw:
            fields = item.get("fields") or {}
            title = (fields.get("title") or "").strip()
            if not title:
                continue  # detail fetch failed or carried no JobPosting — nothing to build
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
                    department=None,  # not exposed by this surface (module docstring)
                    url=item["url"],
                    # The board's own `datePosted` — measured stable, not fabricated — falling
                    # back to the sitemap's `<lastmod>` only if a detail page omits it.
                    posted_at=fields.get("posted_at") or item.get("lastmod"),
                    scraped_at=scraped_at,
                    description=html_to_text(fields.get("description")),
                    employment_type=fields.get("employment_type"),
                )
            )
        return jobs


def _sitemap_rows(xml: str) -> list[tuple[str, str, str | None]]:
    """``(job_id, public_url, lastmod)`` per posting, deduped, in sitemap order."""
    rows: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()
    for loc, lastmod in _SITEMAP_URL.findall(xml):
        match = _JOB_ID.search(loc)
        if not match:
            continue
        job_id = match.group(1)
        if job_id in seen:
            continue
        seen.add(job_id)
        rows.append((job_id, loc.strip(), (lastmod or "").strip() or None))
    return rows


def _ld_fields(page: str) -> dict[str, Any] | None:
    """One job page's JobPosting fields, or None if it carries no JSON-LD JobPosting block."""
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
            employment = node.get("employmentType")
            if isinstance(employment, list):
                employment = ", ".join(str(e) for e in employment) or None
            return {
                "title": node.get("title"),
                "description": _full_description(node),
                "location": _first_location(node.get("jobLocation")),
                "employment_type": employment,
                "posted_at": node.get(
                    "datePosted"
                ),  # measured real and stable, not fabricated
                "remote": True
                if node.get("jobLocationType") == "TELECOMMUTE"
                else None,
            }
    return None


def _full_description(node: dict[str, Any]) -> str | None:
    """``description`` plus the ``responsibilities``/``qualifications`` sections Meta ships as
    separate JSON-LD keys instead of folding them into ``description`` itself — measured present
    (under those exact keys) on every one of 80 sampled postings. Labelled so the concatenation
    reads as the sections a candidate sees on the page, not one undifferentiated block."""
    parts = [node.get("description")]
    if node.get("responsibilities"):
        parts.append("Responsibilities: " + node["responsibilities"])
    if node.get("qualifications"):
        parts.append("Minimum Qualifications: " + node["qualifications"])
    return "\n\n".join(p for p in parts if p)


def _first_location(job_location: Any) -> str | None:
    """First ``Place``'s "City, Region, Country" from a JobPosting ``jobLocation`` list — Meta
    postings often name many alternative sites (module docstring: mean 2.24, one posting listing
    14) and there is no signal to prefer one, so only the first is read, matching icims/eightfold.
    """
    if isinstance(job_location, list):
        job_location = job_location[0] if job_location else None
    if not isinstance(job_location, dict):
        return None
    address = job_location.get("address")
    if not isinstance(address, dict):
        return None
    country = address.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    parts = [address.get("addressLocality"), address.get("addressRegion"), country]
    joined = ", ".join(str(p).strip() for p in parts if p and str(p).strip())
    return joined or None
