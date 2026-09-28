"""Meta (metacareers.com) — a Single source scraper (ADR-0139): one company, one Board, never a
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

The JSON-LD carries no department and no pay (zero of 80 sampled payloads carry
`occupationalCategory`, `industry`, `department`, `team` or `baseSalary`). **The same page's relay
data does**, measured 2026-09-28: a page fetched with a Chrome TLS fingerprint, which this repo's
transport sends, carries a server-streamed ``xcp_requisition_job_description`` object whose
``departments`` list and ``public_compensation`` range are what the page renders. A plain curl
gets a page without that script, which is why the 2026-09-11 measurement missed it. On 40 random
postings, 40/40 carried ``departments`` and 32/40 a one-entry ``public_compensation``, every one
``$…/year`` with ``country_code`` US; the other 8 carried ``[]``. So ``department`` is the
``departments`` list, ", "-joined, and ``salary`` is the US range; any other currency or period
is left unset until one is seen.

`jobLocation` is a list — often a long one (mean 2.24 across the 50-id random sample, 54% single-
location, one posting listing 14 sites) — and every site is kept, "; "-joined, as icims and
eightfold's sitemap fallback keep theirs (`job_location_text`): there is no ranking signal to prefer
one site over another, and the location filter is a substring match that should find each.
`jobLocationType == "TELECOMMUTE"` is Meta's own remote signal when present (2 of 50 sampled);
`is_remote(location)` is the fallback, matching every other ATS here.
"""

from __future__ import annotations

import json
import re
from typing import Any

from headstart.jobs import salary
from headstart.jobs.job import Job, host_of, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper, DetailLost, DetailRequest
from headstart.scrapers.job_posting_jsonld import find_job_posting, job_posting_fields

_DETAIL_WORKERS = 16  # measured clean at conc 20 (80 reqs); matches icims/oracle

_SITEMAP_URL = re.compile(
    r"<url>\s*<loc>([^<]+)</loc>\s*(?:<lastmod>([^<]+)</lastmod>)?", re.IGNORECASE
)
_JOB_ID = re.compile(r"/profile/job_details/(\d+)/")
#: One ``public_compensation`` amount: ``"$183,997/year"`` (module docstring).
_US_YEARLY_PAY = re.compile(r"^\$([\d,]+)/year$")
_JSON = json.JSONDecoder()


class MetaScraper(BaseScraper):
    """metacareers.com — a Single source scraper (ADR-0139); ``slug`` is the fixed careers host."""

    COMPANY = "Meta"

    ats = "meta"
    # scraper passes through the sitemap's own <loc>: the canonical
    # https://www.metacareers.com/profile/job_details/{id}/ page. `meta` is a Single source
    # scraper (ADR-0139), so the host is fixed rather than derived. Verified live 2026-09-11:
    # 80/80 randomly sampled ids 200 with parseable JobPosting JSON-LD.
    url_shape = r"https://www\.metacareers\.com/profile/job_details/\d+/?"
    has_detail_pass = True  # every field but `id` comes from the JSON-LD (ADR-0050)
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

    def job_url(self, url: str) -> str:
        """The sitemap's own ``<loc>`` is already this Job's canonical link; nothing to build,
        so this simply names that as the declared source (ADR-0153)."""
        return url

    def alias_key(self) -> str | None:
        """Meta's own slug: a Single source scraper's Board has no sibling tenant to alias
        against, and the base class's redirect-follow default would be answering a question that
        cannot arise here (ADR-0139). Confirmed 2026-09-11: :meth:`url` does not redirect."""
        return self.slug

    def fetch_raw(self) -> Any:
        xml = self._get()
        listed = _sitemap_rows(xml)
        if not listed:
            self.note_unreadable_board(
                "job <loc>s in the sitemap urlset", f"{len(xml)} bytes: {xml[:40]!r}"
            )
            return []
        pages = self.run_detail_pass(
            listed, key_of=lambda row: row[0], what="detail fields"
        )
        lost = pages.missing
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
            {
                "id": job_id,
                "url": url,
                "lastmod": lastmod,
                "fields": pages.get(job_id),
            }
            for job_id, url, lastmod in listed
        ]

    def detail_request(self, row: tuple[str, str, str | None]) -> DetailRequest:
        return DetailRequest(row[1], headers={"User-Agent": USER_AGENT})

    def read_detail(
        self, row: tuple[str, str, str | None], response: Any
    ) -> dict[str, Any]:
        fields = _ld_fields(response.text)
        if fields is None:
            # A stale sitemap entry (the posting closed since the sitemap was generated) answers
            # 200 with no JSON-LD block at all — measured directly against a bumped, unlisted id.
            raise DetailLost("no JSON-LD on a 200")
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
                    id=self.job_id(item["id"]),
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=remote,
                    department=fields.get("department"),
                    url=self.job_url(item["url"]),
                    # The board's own `datePosted` — measured stable, not fabricated — falling
                    # back to the sitemap's `<lastmod>` only if a detail page omits it.
                    posted_at=fields.get("posted_at") or item.get("lastmod"),
                    scraped_at=scraped_at,
                    description=html_to_text(fields.get("description")),
                    employment_type=fields.get("employment_type"),
                    salary=self._salary_field(fields.get("public_compensation")),
                )
            )
        self.note_unread_rows(
            len(raw) - len(jobs),
            len(raw),
            "without a readable detail page (no title from it)",
        )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        """The page's first US ``public_compensation`` range, as ``"LOW-HIGH USD"`` (annual),
        or None. Only the ``$…/year`` / US shape has been seen (module docstring)."""
        for entry in raw if isinstance(raw, list) else []:
            if entry.get("country_code") != "US":
                continue
            low = _US_YEARLY_PAY.match(entry.get("compensation_amount_minimum") or "")
            high = _US_YEARLY_PAY.match(entry.get("compensation_amount_maximum") or "")
            if low and high:
                return salary.to_field(
                    low.group(1).replace(",", ""), high.group(1).replace(",", ""), "USD"
                )
        return None


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
    node = find_job_posting(page)
    if node is None:
        return None
    # `posted_at` is the page's `datePosted`, measured real and stable, not fabricated. The
    # location is every site a posting names, "; "-joined (module docstring: mean 2.24, one
    # posting listing 14).
    departments = _relay_value(page, "departments")
    return {
        **job_posting_fields(node),
        "description": _full_description(node),
        "department": ", ".join(departments) if isinstance(departments, list) else None,
        "public_compensation": _relay_value(page, "public_compensation"),
    }


def _relay_value(page: str, key: str) -> Any:
    """One key's JSON value from the page's ``xcp_requisition_job_description`` relay data, or
    None when the page carries no such object (module docstring)."""
    start = page.find('"xcp_requisition_job_description":')
    if start == -1:
        return None
    at = page.find(f'"{key}":', start)
    if at == -1:
        return None
    try:
        return _JSON.raw_decode(page, at + len(key) + 3)[0]
    except ValueError:
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
