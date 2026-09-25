"""SuccessFactors RMK scraper (career sites on customer vanity domains: jobs.sap.com,
careers.wipro.com, jobsearch.alstom.com, ...).

RMK ("Recruiting Marketing") is the crawlable SEO surface most SuccessFactors customers put in
front of the modern CSB job search, whose DWR POST-RPC we deliberately don't touch — CSB-only
tenants (Ericsson-class) are a known gap. A tenant's ``slug`` is its vanity host. Three listing
surfaces exist, tried cheapest-first (all probed live 2026-07-21;
experiment/ats-provider-expansion/artifacts/research_successfactors.md + 2026-07-21_rmk_board_probe*.csv):

1. ``/sitemap.xml`` as a **urlset** of ``/job/{slug}/{id}/`` URLs — most tenants; one compact
   GET enumerates the whole board.
2. ``/sitemap.xml`` as the **Google-jobs RSS feed** — a minority (SAP, Alstom, Voith, ...). The
   feed carries full descriptions but its generator trickles at ~30 KB/s, so it is never read
   whole up-front; these tenants list via the server-rendered ``/search/?startrow=N`` pages
   instead (page size varies per tenant — 10 to 100 rows, measured across sampled boards.
   Pagination steps by the size of the page it got and stops when a page adds no new ids, which
   also guards against offset wrap-around; it then checks what it read against the total the
   board advertises, because running off the end and stopping two-thirds of the way through
   otherwise look identical).
3. The patient full RSS stream — last resort for RSS tenants whose ``/search/`` is CSB-rendered
   and lists nothing (Voith, Tetra Pak). Read with a long timeout, keeping whatever arrived if
   the tenant's own generator aborts mid-feed (Voith's dies ~2 MB in): partial beats none.

The list surfaces otherwise carry no indexable fields — the one exception is surface 3's own
``g:job_function`` (:func:`_job_functions_from`), read for free since that surface's whole body
is already being paid for; a bounded detail pass fetches every job page and extracts every other
field from its markup: schema.org microdata (``itemprop="title"`` / ``"description"``),
``og:title``, a ``<title>`` of the form "{Job Title} Job Details | {Co}", and per-tenant
``joblayouttoken`` label/value spans (City / State/Province / Posting Start Date) — each field
falls back independently, since tenants mix the shapes. The reader tries a JSON-LD ``JobPosting``
first, and this docstring used to say classic RMK pages embed one; no page measured does today:
none of 50 pages from the 10 largest Boards nor the probe pages of 195 more (ADR-0196), and none of
113 pages from 60 more Boards sampled 2026-09-24 (two carried an ``ld+json`` block, neither a
``JobPosting``). The branch stays because it costs nothing where absent. No detail markup sampled
carries a department field at all, which is why ``department`` was hardcoded ``None`` until this
RSS-feed field was found — see :func:`_job_functions_from`'s docstring. A page that yields no
title drops that job for the run (there is nothing to keep it by); it returns next scrape.

One title-less page is not a failure: RMK's unavailable shell, a ``<p class="jobErrMsg">`` reading
"You can't view this job because it's not available at this time.", served with a 200 for an id
the sitemap and ``/sitemal.xml`` both still list. That posting is closed, so it is dropped as a
closure — neither counted as a loss nor filled from the feed below — and ADR-0083 evicts it like
any delisting. Measured 2026-09-25: 15 of 60 sampled ``careers.hcltech.com`` pages, none of the
45 that parsed carrying it; the same shell on closed ids of ``careers.wipro.com``,
``lockheed.jobs.hr.cloud.sap`` and ``jobs.danfoss.com``, 13 of 13.

**A fourth surface backs up that detail pass: ``/sitemal.xml``** (that typo, not ``sitemap`` —
the same undocumented-but-stable path on every tenant that has it). It is the full Google-jobs
RSS: one GET carries ``title``/``description``/``g:location`` inline for (usually) the whole board,
keyed by the same numeric id as ``/sitemap.xml``'s trailing path segment — measured live
2026-09-22, exact id parity on ``basf.jobs`` (789/789) and ``ace1950.jobs2web.com`` (60/60), 97.9%
on ``jobsearch.alstom.com`` (2,234/2,283). Not every tenant has it (``jobs.thyssenkrupp.com``
404s).

**It is a fallback, not a shortcut: its fields fill a Job only where that id's job page yielded
none**, and its ``location`` alone fills a page that read but stated no place. It is fetched only
when some page yielded nothing or stated no place. The page stays the authority because it states
a posting date and the feed never does (no item of 3,083 over three tenants, nor of 961 on
``jobs.sap.com``), while the page does on 8 of 9 tenants sampled 2026-09-22 — ``datePosted``
microdata or a "Posting Start Date:" label (:func:`_csb_posted_at`); ``basf.jobs`` is the
exception. Serving the feed *instead* of readable
pages (as #564 did) leaves ``posted_at=None`` on nearly every such Job, and ``update_meta`` then
copies that over the indexed row's stored date. The listing surface above stays the sole authority
on which ids exist; this only ever fills fields.

The feed also appends the location to the title in parens (``"... (Ludwigshafen am Rhein, DE)"``,
matching the item's own ``g:location`` value exactly) — a job page's own title never carries that
suffix, so :func:`_sitemal_items` strips it back off wherever the parenthesized tail matches the
location field verbatim, rather than serving a title shaped differently depending on which surface
happened to answer for it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from html import unescape
from types import MappingProxyType
from typing import Any
from urllib.parse import unquote

from headstart import company_name, log
from headstart.country_codes import ISO_ALPHA2_NAMES
from headstart.jobs.job import Job, html_to_text, is_remote, requisition_of
from headstart.network import http
from headstart.network.fetcher import Fetcher
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    DetailLost,
    DetailRequest,
    classify_exception,
)
from headstart.scrapers.job_posting_jsonld import find_job_posting, job_posting_fields

_log = log.get(__name__)


_CLASSIFY_BYTES = 64 * 1024  # enough sitemap head to tell urlset from RSS
_SITEMAP_CAP = 30 * 1024 * 1024  # runaway guard; largest observed urlset is ~3 MB
# The same guard for the two full-description RSS reads, which the urlset figure does not fit: of
# 144 live RSS feeds read whole 2026-09-23 the largest was 46 MB (jobs.scotiabank.com), and
# jobs.crh.com's 32.8 MB feed — its only surface — was being cut 86 postings short every run.
_RSS_CAP = 128 * 1024 * 1024
_RSS_TIMEOUT = 300  # the RSS generator trickles (~30 KB/s); a full feed is minutes
_MAX_SEARCH_PAGES = 400  # loop bound; 4,000 rows at the smallest measured page (10)
_DETAIL_WORKERS = 6  # sync-path detail fetches; bounded since they hit one host

# ``/job/{slug}/{id}/`` — the one URL shape all three listing surfaces share. The slug part may
# span %-escapes and XML entities; the trailing numeric segment is the stable posting id.
_JOB_PATH = re.compile(r"(/job/[^\s\"'<>?#]+/(\d+)/)")

# The RSS/Google-jobs-feed `<item>` shape `_job_functions_from` reads a department out of —
# see that function's docstring for the field and the junk-token guard.
_RSS_ITEM = re.compile(r"<item>(.*?)</item>", re.DOTALL)
_RSS_ID = re.compile(r"<g:id>\s*(\d+)\s*</g:id>")
_RSS_JOB_FUNCTION = re.compile(r"<g:job_function>(.*?)</g:job_function>", re.DOTALL)
_ATS_TOKEN = re.compile(r"^ATS_[A-Z0-9_]+$")

_ITEMPROP_TITLE = re.compile(r'<[^>]*itemprop="title"[^>]*>([^<]*)')
_OG_TITLE = re.compile(r'property="og:title"\s+content="([^"]*)"')
#: The requisition every RMK job page states, then its locale: `"internalId":"41525-en_US"`. Only
#: the id is kept, so a requisition's pages in two locales carry one id (ADR-0210). It rides the
#: page the detail pass already fetches, so it costs no request. Measured 2026-09-24: 484 of 510
#: pages across 27 Boards, classic and CSB-rendered alike; all 26 misses were one tenant's
#: (careers.bsp.gov.ph), and one careers.dolby.com page omitted it for about a minute.
_INTERNAL_ID = re.compile(r'"internalId"\s*:\s*"([^"-]+)')
_TITLE_TAG = re.compile(r"<title>([^<|]*)", re.IGNORECASE)
_DESC_OPEN = re.compile(
    r'<(span|div)\b[^>]*itemprop="description"[^>]*>', re.IGNORECASE
)
# The element RMK's "You can't view this job because it's not available at this time." shell
# renders its message in (module docstring). Matched on the class, not the sentence: every shell
# measured was English, even requested in de_DE/fr_FR, but the class is the template's own.
_UNAVAILABLE_SHELL = re.compile(r'<[^>]*\bclass="jobErrMsg"')
# What `read_detail` returns for that shell: a closed posting, neither fields nor a loss. An
# empty read-only mapping rather than a bare `object()`, so a caller outside `fetch_raw` that
# reads it as fields (`fetch_detail(...) or {}` in scripts/enrich/salary_sample.py) sees an
# empty page and `parse` skips it, instead of failing on `.get`.
_CLOSED_POSTING: Mapping[str, Any] = MappingProxyType({})

# /sitemal.xml — the Google-jobs RSS field surface (module docstring). Matched with simple,
# non-nesting patterns rather than a general XML parser: every field it carries is a leaf element
# with no nested tags of the same name, unlike the department blocks bamboohr.py has to walk.
_SITEMAL_ITEM = re.compile(r"<item>(.*?)</item>", re.DOTALL)
_SITEMAL_TITLE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_SITEMAL_DESCRIPTION = re.compile(r"<description>(.*?)</description>", re.DOTALL)
_SITEMAL_LOCATION = re.compile(r"<g:location>(.*?)</g:location>", re.DOTALL)
_SITEMAL_LINK = re.compile(r"<link>(.*?)</link>", re.DOTALL)
_CDATA = re.compile(r"\A\s*<!\[CDATA\[(.*)\]\]>\s*\Z", re.DOTALL)

# The company a job page names (:func:`_page_company`), and the shapes it refuses there. Each is a
# value live sites served on 2026-09-24 (census of 1,469 Boards): a SuccessFactors instance id —
# two or more digits ("L3HHCM20", "erstegro01P2"), a PROD/PRD suffix ("PMIProd", "ASTARPRD") or
# RMK's truncated-name-plus-P/T form ("zffriedricP2", "shyamsteelT1") — and filler text.
_HIRING_ORG = re.compile(
    r'<meta\b(?=[^>]*\bitemprop="hiringOrganization")[^>]*\bcontent="([^"]*)"'
)
_INSTANCE_ID = re.compile(
    r"[A-Za-z0-9]*\d[A-Za-z0-9]*\d[A-Za-z0-9]*|\S*(?i:prod|prd)|[a-z]\S*[a-z][PT]\d*"
)
_FILLER_NAMES = frozenset({"our company", "apply now!", "group"})

# Vanity-host labels that are the board, not the company: jobs.sap.com -> "sap".
_BOARD_HOST_LABELS = {"jobs", "careers", "career", "jobsearch", "jobdetails"}


class SuccessFactorsScraper(BaseScraper):
    """SuccessFactors RMK scraper — ``slug`` is the board's vanity host."""

    ats = "successfactors"
    #: Why the last :meth:`_sitemal_fields` came back empty, for the rescue line in
    #: :meth:`fetch_raw`.
    _sitemal_failure: str | None = None
    # scraper passes through RMK sitemap URLs: /job/{slug}/{id}/ on per-tenant vanity hosts
    # (jobs.bt.com, careers.capgemini.com, jobs.turbo.co.th — no common host to anchor on)
    url_shape = r"https://[^/]+/job/.+/\d+/?"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)
    # Where SAP parks a decommissioned RMK tenant (ADR-0111). Both spellings observed live.
    alias_vendor_hosts = frozenset({"www.sap.com", "sap.com"})

    def __init__(
        self, slug: str, company: str | None = None, fetcher: Fetcher | None = None
    ) -> None:
        super().__init__(slug, company, fetcher)
        # The ledger only knows the host, so a missing display name derives from it.
        if self.company == self.slug:
            labels = self.slug.split(".")
            if labels[0] in _BOARD_HOST_LABELS and len(labels) > 2:
                self.company = labels[1]
            else:
                self.company = labels[0]

    def url(self) -> str:
        return f"https://{self.slug}/sitemap.xml"

    def job_url(self, path: str) -> str:
        """This Board's vanity host plus a job path already extracted from a sitemap/RSS/search
        link — the same ``https://{host}{path}`` formula :func:`_job_urls_from` applies inline
        for each match it finds (ADR-0153). Not called from that function directly: it batch-
        extracts *every* job on a page in one regex pass with no scraper instance in hand (it is
        tested that way too), so it keeps building full URLs itself; this method exists as the
        declared, single-job-shaped statement of the same formula for :attr:`url_shape` and
        anything that needs one URL at a time."""
        return f"https://{self.slug}{path}"

    def _fetch_sitemap(self) -> tuple[str, str, str | None]:
        """GET ``/sitemap.xml`` streamed. Returns ``(kind, text, cut_short)`` with kind "urlset" |
        "rss" | "other". A urlset is read to the end (compact); an RSS feed is abandoned right
        after classification so the trickling generator never stalls the fetch. A torn urlset read
        raises — a silent partial board would drop jobs.

        ``cut_short`` is why the read stopped early, or None when the document arrived whole: a
        urlset cut at ``_SITEMAP_CAP`` lists only the jobs that fit. Reported, not recorded, for
        the same reason :meth:`_search_job_urls` reports — ``fetch_raw`` decides which surface is
        the Board's answer (ADR-0053)."""
        # Through the retry seam, not the raw session: a 429/5xx here used to settle on the
        # first try, and `_fetch_sitemap` maps a non-200 to an empty text — so a throttled
        # fetch read as an empty Board and `index sync` evicted its rows (ADR-0047, ADR-0053).
        response = self._fetch(
            "GET",
            self.url(),
            headers={"User-Agent": USER_AGENT},
            timeout=30,
            stream=True,
        )
        chunks: list[bytes] = []
        size = 0
        kind = ""
        capped = False
        try:
            for chunk in response.iter_content():
                chunks.append(chunk)
                size += len(chunk)
                if size >= _SITEMAP_CAP:
                    capped = True
                    break
                if not kind and size >= _CLASSIFY_BYTES:
                    kind = _sitemap_kind(b"".join(chunks).decode("utf-8", "replace"))
                    if kind != "urlset":
                        break
        finally:
            response.close()
        if response.status_code != 200:
            # Neither urlset nor rss, so it lists nothing; the status is the kind so that
            # `fetch_raw`'s surface line can say why.
            return f"HTTP {response.status_code}", "", None
        text = b"".join(chunks).decode("utf-8", "replace")
        return (
            kind or _sitemap_kind(text),
            text,
            _cap_reason("sitemap", _SITEMAP_CAP) if capped else None,
        )

    def _search_job_urls(
        self,
    ) -> tuple[list[tuple[str, str]], str | None, int | None]:
        """Enumerate the board via the server-rendered ``/search/`` pages.

        Returns the pairs found; when the walk was cut short rather than reaching the end, why;
        and, when that shortfall is measured against the board's own stated total, the total —
        the one shape ADR-0121 tolerates. Reported rather than recorded, because whether it matters is the caller's to decide:
        this surface is only the Board's answer when it returns something, and a truncation on a
        surface that lost the fallback race must not be attached to the list that won it
        (ADR-0053)."""
        seen: dict[str, str] = {}  # id -> url, insertion-ordered
        page_size: int | None = (
            None  # both from the board's own pagination label, when it
        )
        advertised_total: int | None = (
            None  # renders one; None leaves the walk as it was
        )
        startrow = 0
        for page_index in range(_MAX_SEARCH_PAGES):
            response = self._fetch(
                "GET",
                f"https://{self.slug}/search/?startrow={startrow}",
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
            if response.status_code != 200:
                # Unlike the empty-page exit below, this is the walk being cut short rather than
                # reaching the end: whatever sits past this offset is unread, not absent
                # (ADR-0053). No total to compare against here, so report the offset instead.
                return (
                    [(u, i) for i, u in seen.items()],
                    (
                        f"HTTP {response.status_code} at startrow {startrow} — "
                        f"{len(seen)} postings read before the walk stopped"
                    ),
                    None,
                )
            if page_index == 0:
                paging = _advertised_paging(response.text)
                if paging:
                    page_size, advertised_total = paging
            found = _job_urls_from(response.text, self.slug)
            fresh = [(u, i) for u, i in found if i not in seen]
            if not fresh:
                break
            seen.update({i: u for u, i in fresh})
            # Step by the board's own stated page size, falling back to the links we counted.
            # A fixed 25-row floor here skipped the difference on every tenant serving fewer
            # rows than that, silently, for months (docs/pipeline/2026-08-23_false-board-
            # eviction-root-cause.md §4.2). The link count is the better guess but still only a
            # guess: not every /job/ link on a page is one of that page's results, and
            # `jobs.kaufland.com` renders 19 for a stated 15, so stepping by what we counted
            # would skip 4 rows of every window — the same bug in a new disguise. An empty page
            # cannot loop here: `fresh` is then empty and the walk breaks above, which is what
            # the floor was really guarding against.
            startrow += page_size or len(found)
        else:
            # Ran out of pages rather than reaching the end. Eightfold and Workday both mark
            # their equivalent ceilings; this one returned None and the short list read as the
            # whole Board (ADR-0053).
            return (
                [(u, i) for i, u in seen.items()],
                (
                    f"hit the {_MAX_SEARCH_PAGES}-page search ceiling at startrow {startrow} — "
                    f"{len(seen)} postings read, the rest unread"
                ),
                None,
            )
        # Reaching the natural end is not proof the walk read everything — the stride bug above
        # exited by exactly this path for months. The board states its own total on the search
        # page, so compare against it and report a shortfall rather than presenting a short list
        # as the whole Board (ADR-0053). Only when the label parses, which is far from universal:
        # 17 of 30 sampled tenants render one, and the rest must walk exactly as before and claim
        # nothing. So this is a second line of defence over roughly half the estate, not the fix
        # — the stride above is the fix, and it applies to every board.
        #
        # `len(seen)` counts links, so a board rendering extras beyond its results (kaufland
        # again) compares slightly high and can mask a shortfall smaller than the extras. That is
        # the safe direction and deliberately left alone: under-reporting costs one run's
        # eviction, while over-reporting marks the Board unauthoritative and serves its closed
        # postings indefinitely.
        if advertised_total is not None and len(seen) < advertised_total:
            return (
                [(u, i) for i, u in seen.items()],
                (
                    f"read {len(seen)} of the {advertised_total} postings the board "
                    "advertises — the rest were not listed"
                ),
                advertised_total,
            )
        return [(u, i) for i, u in seen.items()], None, None

    def _rss_job_urls(
        self,
    ) -> tuple[list[tuple[str, str]], dict[str, str], str | None]:
        """Enumerate the board from the full RSS feed, patiently; keeps whatever arrived when
        the tenant's generator aborts mid-feed.

        Returns the pairs found, the id -> department map read from the same feed's own
        ``g:job_function`` field (:func:`_job_functions_from` — free, since this surface's whole
        body is already in hand for the URL walk; no listing surface here otherwise carries a
        department field at all), and, when the stream ended early rather than completing, why —
        an aborted feed and a feed cut at ``_RSS_CAP`` both list a knowingly short board.
        Reported rather than recorded for the same reason :meth:`_search_job_urls` reports
        (ADR-0053)."""
        response = self._fetch(  # retry seam, as in `_fetch_sitemap`
            "GET",
            self.url(),
            headers={"User-Agent": USER_AGENT},
            timeout=_RSS_TIMEOUT,
            stream=True,
        )
        chunks: list[bytes] = []
        size = 0
        cut_short: str | None = None
        try:
            for chunk in response.iter_content():
                chunks.append(chunk)
                size += len(chunk)
                if size >= _RSS_CAP:
                    cut_short = _cap_reason("RSS feed", _RSS_CAP)
                    break
        except http.RequestsError:
            # Server-side abort: scrape the links that did arrive, and say so. No total to
            # compare against in a feed, so report how far it got instead.
            cut_short = (
                f"the tenant's RSS feed aborted {size:,} bytes in — "
                "postings past that point were not listed"
            )
        finally:
            response.close()
        if response.status_code != 200:
            # Reported for the surface line only: with nothing listed, no truncation is recorded.
            return [], {}, f"HTTP {response.status_code}"
        text = b"".join(chunks).decode("utf-8", "replace")
        return (
            _job_urls_from(text, self.slug),
            _job_functions_from(text),
            cut_short,
        )

    def _sitemal_fields(self) -> dict[str, dict[str, Any]]:
        """``{job_id: fields}`` off ``/sitemal.xml`` (module docstring) — a best-effort field
        fallback, never the listing authority. Degrades to ``{}`` on anything short of a clean
        200: a 404 (not every tenant has this surface), a mid-stream abort, or a body that doesn't
        parse as the expected feed. An id this misses stays as its failed page fetch left it, so a
        failure here costs only the rescue, never a Job a page read."""
        try:
            response = self._fetch(
                "GET",
                f"https://{self.slug}/sitemal.xml",
                headers={"User-Agent": USER_AGENT},
                timeout=_RSS_TIMEOUT,
                stream=True,
            )
        except http.RequestsError as exc:
            self._sitemal_failure = classify_exception(exc)
            return {}
        chunks: list[bytes] = []
        size = 0
        aborted = None
        try:
            for chunk in response.iter_content():
                chunks.append(chunk)
                size += len(chunk)
                if size >= _RSS_CAP:
                    break
        except http.RequestsError:
            # keep whatever arrived — partial coverage still saves detail fetches — but say so
            aborted = f"aborted {size:,} bytes in"
        finally:
            response.close()
        if response.status_code != 200:
            self._sitemal_failure = f"HTTP {response.status_code}"
            return {}
        items = _sitemal_items(b"".join(chunks).decode("utf-8", "replace"))
        self._sitemal_failure = aborted or (None if items else "no readable items")
        return items

    def fetch_raw(self) -> Any:
        # Each of the three surfaces hands back *why* its list came up short, and the truncation
        # is recorded only in the branch where that surface is what the Board returns. A surface
        # that lost the fallback race can list nothing at all (a search walk that 503s on its
        # first page), and the surface that then answers may answer with the whole board — which
        # must never inherit the loser's truncation (ADR-0053).
        kind, text, sitemap_cut_short = self._fetch_sitemap()
        listed = _job_urls_from(text, self.slug) if kind == "urlset" else []
        surface = "sitemap-urlset" if listed else ""
        # id -> department, read off the RSS feed's own `g:job_function` field. Populated only
        # when `rss-stream` is the surface that answers: that is the one path where this feed's
        # full body is already being paid for (the other two surfaces carry no department field
        # at all — module docstring), so this is free there and deliberately not fetched
        # elsewhere (jobs.sap.com's own feed is 16 MB at ~30 KB/s — reading it just for a
        # department label on a tenant whose `/search/` already works would cost ~9 minutes/run
        # for nothing `/search/` doesn't already answer cheaply).
        #
        # Scope check, live 2026-09-22: `apply.careers.hsbc.com`, `aramarkcareers.com`,
        # `basf.jobs` and `ace1950.jobs2web.com` — the four tenants a prior pass measured
        # `g:job_function` coverage on directly against their RSS feed — all currently serve a
        # plain **urlset** at `/sitemap.xml`, not RSS, so none of them reach `rss-stream` today
        # and this fix does not recover their department right now. Whether that reflects a
        # since-changed sitemap mode on those tenants or a different original measurement
        # surface is unknown; only `jobs.tetrapak.com` (module docstring's own `rss-stream`
        # example) was directly confirmed live to benefit — see :meth:`_rss_job_urls`.
        job_functions: dict[str, str] = {}
        search_cut_short = rss_cut_short = None
        if listed and sitemap_cut_short:
            self.mark_truncated(sitemap_cut_short)
        if not listed:
            listed, search_cut_short, search_total = self._search_job_urls()
            if listed:
                surface = "search-pages"
                if search_cut_short and search_total:
                    # Measured against the Board's own stated total (ADR-0121).
                    self.mark_truncated_unless_negligible(
                        len(listed), search_total, search_cut_short
                    )
                elif search_cut_short:
                    self.mark_truncated(search_cut_short)
        if not listed and kind == "rss":
            listed, job_functions, rss_cut_short = self._rss_job_urls()
            if listed:
                surface = "rss-stream"
                if rss_cut_short:
                    self.mark_truncated(rss_cut_short)
        # NB: all three surfaces empty is indistinguishable here from a dead vanity host, since
        # each maps its own non-200 to "nothing" and falls through — so a gone SuccessFactors
        # tenant cannot currently earn an ADR-0058 gone-verdict. A root-of-host probe was tried
        # and rejected on measurement: of 12 hosts this ledger already calls dead, 9 answer
        # `GET /` with 200 (the jobs2web parking page), so the probe would have cost one extra
        # request per empty board per run and still missed three quarters of the dead ones. The
        # real fix is for the three surfaces to distinguish "errored" from "legitimately empty"
        # and raise only when every one of them errored.
        # Which of the three surfaces answered, and how much the detail pass will cost. This
        # tenant's cost is decided here and nowhere else — the RSS stream is the patient last
        # resort — so without this line a board that takes 37 minutes for 7 jobs
        # (cbscorporation.jobs, 2026-08-12) leaves no evidence of why.
        # Not on the common case — the urlset answering with postings — which is every Board,
        # every run, and says nothing the cost ledger does not.
        if surface != "sitemap-urlset":
            fallbacks = (
                f", search {search_cut_short or 'empty'}, "
                f"rss {(rss_cut_short or 'empty') if kind == 'rss' else 'n/a'}"
                if not listed
                else ""
            )
            _log.info(
                f"{self.board_key()}: {surface or 'nothing'} via sitemap "
                f"{kind or 'unknown'}{fallbacks} -> {len(listed)} job pages to fetch"
            )
        # The tech gate (ADR-0017), read off the URL's own slug plus — on `rss-stream` boards
        # only — the feed's own department, rather than the listing generally: unlike
        # eightfold's PCSX surface, the sitemap-urlset and search-pages surfaces carry no title
        # or department pre-fetch at all — every field otherwise comes from the job page. A
        # non-tech posting is never indexed, so skipping its detail costs nothing (ADR-0048's
        # 2026-09-16 amendment established that for eightfold's exact, listing-derived signal);
        # this is the same trade on an approximate, measured one instead — see
        # :func:`_title_from_slug`. Passing `department_of` unconditionally is safe even when
        # `job_functions` is empty (the two non-RSS surfaces): `.get()` on an empty dict is just
        # `None`, the same as before this field existed.
        #
        # Routed through `tech_detail_wanted` so this gate answers the same way every other one does —
        # in particular it is now conditional on `have_details`, the pipeline signal, which it
        # was not when it shipped in #503. That divergence had a visible cost: `filter_tech`
        # reported `successfactors 32,891/33,035 = 99.6% tech` in run 35193130454, because a
        # non-tech posting never reached the corpus at all, so this ATS's real tech share was no
        # longer readable from the pipeline's own data. `verify_scraper.py` and the enrichment
        # samplers construct scrapers directly and now see whole Boards again.
        #
        # Gated here rather than by handing the pass `title_of`, because what follows needs the
        # gated list itself: it is both what this returns and the truncation denominator below.
        tech_listed = self.tech_detail_wanted(
            listed,
            lambda pair: _title_from_slug(pair[0]),
            lambda pair: job_functions.get(pair[1]),
        )
        # Detail pass: every field comes from the job page, so fetch each one (bounded); a
        # failed fetch leaves fields None and parse drops just that job, unless the fallback
        # below fills it.
        pages = self.run_detail_pass(
            tech_listed, key_of=lambda pair: pair[1], what="detail fields"
        )
        unread = pages.missing
        # A page that says its posting is unavailable closes that id (module docstring): it is
        # not a Job, not a loss, and not the feed's to fill — the feed still lists it.
        open_listed = [
            pair for pair in tech_listed if pages.get(pair[1]) is not _CLOSED_POSTING
        ]
        if len(open_listed) < len(tech_listed):
            _log.info(
                f"{self.board_key()}: {len(tech_listed) - len(open_listed)} of "
                f"{len(tech_listed)} "
                "job pages say the posting is not available — dropped as closed"
            )
        # /sitemal.xml (module docstring): the fallback for a page that yielded nothing. The page
        # stays the authority — it states the posting date the feed
        # never does — and `listed` stays the sole id authority; this only ever fills fields.
        # It also fills `location` alone on a page that read but stated none: some tenants render
        # the place as an unlabelled span no parser can anchor on, while the feed states it on
        # every item (careers.hcltech.com, 2026-09-25: 3,146 of 4,226 served rows had no location,
        # and the feed carried one on all 10,829 items).
        placeless = sum(
            1
            for _, job_id in open_listed
            if job_id in pages and not pages[job_id].get("location")
        )
        sitemal_fields = self._sitemal_fields() if unread or placeless else {}
        fields = [
            _with_feed_location(pages[job_id], sitemal_fields.get(job_id))
            if job_id in pages
            else sitemal_fields.get(job_id)
            for _, job_id in open_listed
        ]
        if placeless:
            placed = placeless - sum(
                1 for page in fields if page is not None and not page.get("location")
            )
            _log.info(
                f"{self.board_key()}: sitemal.xml placed {placed} of {placeless} job pages "
                "that stated no location"
            )
        lost = sum(1 for page in fields if page is None)
        if lost < unread:
            _log.info(
                f"{self.board_key()}: sitemal.xml filled {unread - lost} of {unread} "
                "unreadable job pages"
                + (f" (feed {self._sitemal_failure})" if self._sitemal_failure else "")
            )
        elif unread:
            _log.info(
                f"{self.board_key()}: sitemal.xml rescue unavailable "
                f"({self._sitemal_failure or 'none of the unread ids listed'}) — {unread} "
                "pages stay unread"
            )
        if lost:
            # Every field comes from the job page (or its fallback), so `parse` drops a Job
            # neither yielded. That makes the returned list knowingly short, and an unmarked
            # short list is exactly what `index sync` reads as a delisting — it would evict Jobs
            # that are still posted, purely because their detail fetch failed (ADR-0053).
            #
            # Measured against `tech_listed`, not `listed`: a non-tech posting was never going to
            # be indexed regardless of whether its detail was fetched, so it must not count
            # against how authoritative this Board's *tech* read is. This is the shape that
            # excluded whole 2,130-page Boards over a single unreadable page (ADR-0121). And
            # against `open_listed`: a page that said its posting is closed was read, not lost.
            self.mark_truncated_unless_negligible(
                len(open_listed) - lost,
                len(open_listed),
                f"{lost}/{len(open_listed)} job pages unreadable — those Jobs are listed but "
                "unbuilt",
            )
        # `department` folded in here, not read on the job page — the detail markup (JSON-LD
        # and the CSB microdata/label-span fallbacks) carries no department field on any tenant
        # sampled (module docstring), so the RSS feed's own `g:job_function` is the only source
        # there is, and it exists only for the `job_functions` this Board's surface populated.
        # `/sitemal.xml` states `g:job_function` too, but :func:`_sitemal_items` reads only the
        # fallback's title/description/location, so this applies whichever source filled a Job.
        items = [
            {
                "url": url,
                "id": job_id,
                "fields": (
                    {**page_fields, "department": job_functions.get(job_id)}
                    if page_fields is not None
                    else None
                ),
            }
            for (url, job_id), page_fields in zip(open_listed, fields)
        ]
        self.company = self._board_company(items)
        return items

    def detail_request(self, pair: tuple[str, str]) -> DetailRequest:
        return DetailRequest(pair[0], headers={"User-Agent": USER_AGENT})

    def read_detail(self, pair: tuple[str, str], response: Any) -> Mapping[str, Any]:
        """One job page's fields, or a loss named for a page that yielded no title.

        This is the pass the User-Agent denylist landed on: 102 Boards, five consecutive runs,
        56,120 postings listed and none ingested, and the only line on the subject read
        ``2127/2127 detail fields missing`` — because a 403 and a 200 that parsed to no title
        were one count (:data:`~headstart.scrapers.base.USER_AGENT`). The label is what separates
        "the origin refused us" from "the parser did not recognise the page", and those two call
        for opposite responses.

        A title-less page carrying RMK's unavailable shell is neither: the posting is closed,
        and :data:`_CLOSED_POSTING` says so, for :meth:`fetch_raw` to drop rather than rescue.
        """
        fields = _titled_fields(response.text, pair[0])
        if fields is None:
            if _UNAVAILABLE_SHELL.search(response.text):
                return _CLOSED_POSTING
            raise DetailLost("200 without a parseable title")
        return fields

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for item in raw:
            fields = item.get("fields") or {}
            title = (fields.get("title") or "").strip()
            if not title:
                continue  # page unreadable — nothing to keep the job by
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
                    url=item["url"],
                    posted_at=fields.get("posted_at"),
                    scraped_at=scraped_at,
                    description=html_to_text(fields.get("description")),
                    employment_type=fields.get("employment_type"),
                    requisition=fields.get("requisition"),
                )
            )
        return jobs

    def _board_company(self, items: list[dict[str, Any]]) -> str:
        """The name most of this Board's job pages state, or ``self.company`` when it is a real
        name already or no page states one (ADR-0217).

        The modal value, because the name is set once for the whole site: each of the 30 largest
        Boards stated one name on every page sampled (ten each, 2026-09-24; jobs.sap.com served
        one) — so a page that disagrees is more likely a stray than the Board's name. Read off the
        pages the Detail pass has already fetched, so it costs no request, and in `fetch_raw`
        rather than `parse` so the name is stated during the fetch, before the Board's company
        is settled. A Board whose pages were all unreadable, or all gated out as non-tech, keeps
        its host label and serves no row anyway.
        """
        if not company_name.looks_like_slug(self.company):
            return self.company
        modal = company_name.agreed_name(
            (item.get("fields") or {}).get("company") for item in items
        )
        return company_name.from_title(self.ats, modal, self.slug) or self.company

    def _salary_field(self, raw: Any) -> str | None:
        # Not yet measured: no structured compensation field has been looked for across either
        # the classic JSON-LD or the CSB-rendered microdata detail pages. Needs its own
        # measurement pass before this can claim more.
        return None


def _cap_reason(what: str, cap: int) -> str:
    """Why a stream that ran into its read ``cap`` left the board short (ADR-0053)."""
    return (
        f"the {what} hit the {cap // (1024 * 1024)} MB read cap — "
        "postings past it were not listed"
    )


def _sitemap_kind(text: str) -> str:
    if "base.google.com/ns/1.0" in text or "<rss" in text:
        return "rss"
    if "<urlset" in text:
        return "urlset"
    return "other"


# The pagination label's *structure*, never its words: the surrounding text is localised per
# tenant — "Results 1 – 10 of 219", "Ergebnisse 1 – 46 von 46", "Resultados 1 – 50 de 813" — and
# matching on "of" reads every non-English board as having no total at all. The shape is stable
# across all of them: inside the label, a bold range followed by a bold grand total.
#
# Matched in two bounded steps rather than one loose pattern. A single `.*?` from the class name
# to a pair of <b> spans is not anchored to the end of the label, so a tenant whose label is some
# *other* shape lets the match run on into unrelated markup and return a number scavenged from
# elsewhere on the page. That direction is the dangerous one — see :func:`_advertised_paging`.
_PAGINATION_LABEL = re.compile(
    r"paginationLabel[^>]*>((?:(?!</span>).)*)</span>", re.IGNORECASE | re.DOTALL
)
_LABEL_FIGURES = re.compile(
    r"<b>\s*([\d,]+)\s*[–—-]\s*([\d,]+)\s*</b>[^<]*<b>\s*([\d,]+)\s*</b>",
    re.IGNORECASE,
)


def _advertised_paging(page: str) -> tuple[int, int] | None:
    """``(rows this page lists, postings the board holds)`` per the ``/search/`` page's own
    pagination label, or None when it carries none this function recognizes.

    The label reads ``Results <b>1 – 10</b> of <b>219</b>`` in English and
    ``Ergebnisse <b>1 – 46</b> von <b>46</b>`` in German, so this matches its shape rather than
    its words. It answers the walk's two questions at once, both of which it otherwise has to
    guess from the page's link count: how far to step, and whether it reached the end.

    Returns None when the label is absent or unrecognised, and the caller then steps by what it
    counted and makes no claim about completeness. That direction matters in both roles: a
    wrongly-parsed total reads as a shortfall, which marks the Board unauthoritative and takes it
    out of the eviction scope entirely (ADR-0053), so closed postings would be served forever.
    An unknown total must never become a zero, which is why the figures are matched only *inside*
    the label element and rejected unless they order sanely.
    """
    label = _PAGINATION_LABEL.search(page)
    if not label:
        return None
    figures = _LABEL_FIGURES.search(label.group(1))
    if not figures:
        return None
    first, last, total = (int(g.replace(",", "")) for g in figures.groups())
    if not 0 < first <= last <= total:
        return None
    return last - first + 1, total


def _job_urls_from(text: str, host: str) -> list[tuple[str, str]]:
    """All ``(absolute url, id)`` job links in a sitemap/feed/search payload, de-duplicated by
    id in first-seen order. Links appear relative (search hrefs) and absolute (sitemap locs);
    both normalize to ``https://{host}{path}``."""
    pairs: dict[str, str] = {}
    for match in _JOB_PATH.finditer(text):
        job_id = match.group(2)
        if job_id not in pairs:
            pairs[job_id] = f"https://{host}{unescape(match.group(1))}"
    return [(url, job_id) for job_id, url in pairs.items()]


def _strip_cdata(value: str) -> str:
    """Unwrap a ``<![CDATA[...]]>`` section, or return ``value`` unchanged if it isn't one —
    ``/sitemal.xml``'s ``<description>`` is CDATA-wrapped (module docstring), the rest of its
    fields aren't."""
    match = _CDATA.match(value)
    return match.group(1) if match else value


def _strip_location_suffix(title: str, location: str | None) -> str:
    """``/sitemal.xml`` appends the location in parens to every title (module docstring); a job
    page's own title never does, so this brings the two surfaces back to the same shape rather
    than shipping a title whose form depends on which one happened to answer. Stripped only when
    the parenthesized tail is an exact match for ``location`` — anything else is a real "(...)"
    the title itself carries (e.g. "(m/w/d)") and must survive."""
    if location and title.endswith(f" ({location})"):
        return title[: -(len(location) + 3)].strip()
    return title


def _sitemal_items(text: str) -> dict[str, dict[str, Any]]:
    """``{job_id: fields}`` from a ``/sitemal.xml`` body (module docstring) — every ``<item>``
    that states both a job-page ``<link>`` (the id source, via the same :data:`_JOB_PATH` every
    other surface uses) and a non-empty title. No ``posted_at``/``employment_type``/``remote``:
    this feed states none of them on any tenant sampled."""
    fields: dict[str, dict[str, Any]] = {}
    for item_match in _SITEMAL_ITEM.finditer(text):
        item = item_match.group(1)
        link_match = _SITEMAL_LINK.search(item)
        job_id = None
        if link_match:
            path_match = _JOB_PATH.search(unescape(link_match.group(1)))
            if path_match:
                job_id = path_match.group(2)
        if not job_id:
            continue
        title_match = _SITEMAL_TITLE.search(item)
        title = unescape(title_match.group(1)).strip() if title_match else None
        if not title:
            continue
        location_match = _SITEMAL_LOCATION.search(item)
        location = unescape(location_match.group(1)).strip() if location_match else None
        description_match = _SITEMAL_DESCRIPTION.search(item)
        description = (
            html_to_text(_strip_cdata(description_match.group(1)))
            if description_match
            else None
        )
        fields[job_id] = {
            "title": _strip_location_suffix(title, location),
            "description": description,
            "location": _feed_location(location),
        }
    return fields


def _feed_location(location: str | None) -> str | None:
    """A ``g:location`` value as a place a search can find. The feed mostly states a bare ISO
    alpha-2 code (``IN`` on 8,643 of careers.hcltech.com's 10,829 items, 2026-09-25) or a city
    and code (``Taguig, PH``). Neither a search for "india" nor the India tag reads a code, so a
    final two-letter segment becomes its country name. Segments with no letter at all are feed
    junk (``#, LN, CN, _`` on careers.te.com, ``83, DK``, ``PT, 1990-266``) and are dropped."""
    if not location:
        return None
    segments = [s.strip() for s in location.split(",")]
    segments = [s for s in segments if any(c.isalpha() for c in s)]
    if segments and len(segments[-1]) == 2 and segments[-1].isupper():
        segments[-1] = ISO_ALPHA2_NAMES.get(segments[-1], segments[-1])
    return ", ".join(segments) or None


def _with_feed_location(
    page: dict[str, Any], feed: dict[str, Any] | None
) -> dict[str, Any]:
    """A read page's fields, with the feed's location where the page stated none. The page stays
    the authority on every other field (module docstring)."""
    if page.get("location") or not feed or not feed.get("location"):
        return page
    return {**page, "location": feed["location"]}


def _job_functions_from(text: str) -> dict[str, str]:
    """``{g:id: g:job_function}`` for every RSS ``<item>`` that states a real department label.

    ``g:job_function`` is a Google-jobs-feed extension field carried only on the RSS-shaped
    listing surface (module docstring's surfaces 2/3) — the plain urlset surface (most tenants)
    has no such field, and neither does any job-page markup sampled (classic JSON-LD or the CSB
    microdata/label-span fallbacks). Verified live 2026-09-22 on jobs.sap.com and
    jobs.tetrapak.com: `g:id` matches the same numeric id `_JOB_PATH` reads off the item's own
    `<link>`, and `g:job_function` states a clean label ("Sales", "Market Operations & Finance").

    A minority of tenants state an internal ATS configuration token here instead of a real
    department (e.g. ``ATS_WCMS_WEBFORM``, ``ATS_TALEO_APAC`` — measured live, basf.jobs,
    2026-09-22) — :data:`_ATS_TOKEN` rejects that shape rather than feeding it to the tech gate,
    which would otherwise classify on the literal string "ATS_WCMS_WEBFORM"."""
    out: dict[str, str] = {}
    for item in _RSS_ITEM.finditer(text):
        id_m = _RSS_ID.search(item.group(1))
        fn_m = _RSS_JOB_FUNCTION.search(item.group(1))
        if not id_m or not fn_m:
            continue
        value = unescape(fn_m.group(1)).strip()
        if value and not _ATS_TOKEN.match(value):
            out[id_m.group(1)] = value
    return out


def _title_from_slug(url: str) -> str:
    """The title implied by a job URL's slug, for the pre-detail tech gate in :meth:`fetch_raw`.

    SuccessFactors builds job URLs as ``{title}/{id}/`` on some tenants and
    ``{location}-{title}[-{state}-{zip}]/{id}/`` on others (see :func:`_location_from_slug`), so
    this is noisier than a clean title on tenants using the second shape — measured against real
    pages rather than assumed clean: 403/403 verdict agreement against careers.hcltech.com's
    title-only slugs and 400/400 against jobs.sap.com's location-prefixed, largely German ones,
    both 2026-09-16. The extra tokens never flipped a verdict in either sample —
    :func:`~headstart.jobs.tech_filter.classify`'s signals are word-bounded substrings, so noise
    around a real title rarely removes what was already there — but this is a measured
    tolerance, not a guarantee for every tenant's slug shape.
    """
    slug = url.rstrip("/").rsplit("/", 2)[-2]
    return unescape(unquote(slug).replace("-", " ")).strip()


def _page_fields(page: str, url: str | None = None) -> dict[str, Any]:
    """Every indexable field a job page yields. JSON-LD first, then the CSB microdata /
    label-token fallbacks — per field, because tenants mix the shapes.

    ``url`` is optional and used only for the location's last-resort tier
    (:func:`_location_from_slug`); every existing caller that has no URL handy keeps working
    unchanged and simply doesn't get that tier."""
    fields = _jsonld_fields(page) or {}
    if not fields.get("title"):
        fields["title"] = _csb_title(page)
    if not fields.get("description"):
        fields["description"] = _csb_description(page)
    if not fields.get("location"):
        fields["location"] = _csb_location(page)
    if not fields.get("location") and url and fields.get("title"):
        fields["location"] = _location_from_slug(fields["title"], url)
    if not fields.get("location"):
        # Last tier, because every tier above yields a place at least as fine. Some tenants'
        # job pages carry no location markup and no location in the URL either, and the only
        # geography anywhere on them is this one meta (measured on careers.theredsea.sa, 51 of
        # 70 residual nulls in a 14-board sample: the page has no JSON-LD, no
        # `careersite-propertyid="location"` and no address itemprops, only a bare "SA").
        #
        # Worth being exact about what this buys, because it is less than it looks. Where the
        # tenant configured a city the value is fully filterable ("Kuala Lumpur, MY, 50450",
        # "Iasi, RO"). Where it is a bare two-letter tag it is NOT: `geo.where("india")` is an
        # set of place *names*, so "IN" matches none of its 213 patterns (verified against
        # the live clause) -- "Karnataka, IN" only ever matched on "karnataka". So the bare-tag
        # rows gain a displayable country and stop being blank; they do not gain a place filter
        # unless one that reads country tags is added later.
        fields["location"] = _location_from_street_address(page)
    if not fields.get("posted_at"):
        fields["posted_at"] = _csb_posted_at(page)
    requisition = _INTERNAL_ID.search(page)
    fields["requisition"] = requisition_of(requisition and requisition.group(1))
    fields["company"] = _page_company(page)
    return fields


def _page_company(page: str) -> str | None:
    """The company this job page names, or None where what it names is not one.

    RMK titles a job page "{job} {localized 'Job Details'} | {Company}" and usually states the
    company again as ``hiringOrganization`` microdata; the title is read first and the
    microdata where it is missing. An unconfigured site fills that slot with something else: a
    lowercase identifier ("fmgl", "thaioilpub"), an instance id (:data:`_INSTANCE_ID`) or filler
    ("Apply now!", "our company", "-"). The vendor's demo company ("BestRun") is left to
    `company_name`'s vendor aliases.

    Not refused: a value equal to the tenant's own SuccessFactors company id. That test was the
    first design, and a census of the 1,469 Boards serving a host showed it refusing real names
    far more often than ids — "Bechtel", "Atos", "Bombardier", "Amtrak" and "Clariant" are each
    their tenant's id too — while every id-shaped value it caught is an instance id above.
    """
    title = company_name.title_of(page) or ""
    microdata = _HIRING_ORG.search(page)
    stated = (
        title.rsplit(" | ", 1)[1] if " | " in title else None,
        microdata.group(1) if microdata else None,
    )
    for name in stated:
        name = unescape(name or "").strip()
        if not re.search(r"\w", name) or company_name.looks_like_slug(name):
            continue
        if _INSTANCE_ID.fullmatch(name) or name.lower() in _FILLER_NAMES:
            continue
        return name
    return None


def _titled_fields(page: str, url: str | None = None) -> dict[str, Any] | None:
    """:func:`_page_fields`, but None on a page that loaded (200 OK) without a parseable title —
    a temporary placeholder, an anti-bot interstitial served with 200, or any page shape neither
    parser recognizes. `parse()` drops a Job with no title either way (there is nothing to keep
    it by), so a title-less page must count as a loss the same as a fetch failure: `_page_fields`
    alone always returns a dict, never None, so that loss was invisible to `report_detail_gaps`
    and `mark_truncated` never fired — `index sync` read the board as fully, authoritatively
    scraped and evicted the Job as a delisting (docs/pipeline/2026-08-23_false-board-eviction-
    root-cause.md §4)."""
    fields = _page_fields(page, url)
    return fields if fields.get("title") else None


def _jsonld_fields(page: str) -> dict[str, Any] | None:
    """The JobPosting fields from a classic RMK page's JSON-LD, or None without one."""
    node = find_job_posting(page)
    return None if node is None else job_posting_fields(node)


def _csb_title(page: str) -> str | None:
    match = _ITEMPROP_TITLE.search(page) or _OG_TITLE.search(page)
    if match and match.group(1).strip():
        return unescape(match.group(1)).strip()
    match = _TITLE_TAG.search(page)
    if not match:
        return None
    # "<title>Lead Data Scientist Job Details | Wipro Limited</title>" -> the job title
    title = re.sub(r"\s*Job Details\b.*$", "", unescape(match.group(1))).strip()
    return title or None


def _csb_description(page: str) -> str | None:
    """The longest ``itemprop="description"`` element's inner HTML (CSB pages render a short
    teaser and the full description under the same itemprop)."""
    best = None
    for match in _DESC_OPEN.finditer(page):
        content = _matched_content(page, match)
        if content and (best is None or len(content) > len(best)):
            best = content
    return best


def _matched_content(page: str, open_match: re.Match) -> str:
    """Inner HTML of the element opened at ``open_match``, by open/close tag counting."""
    tag = open_match.group(1).lower()
    token = re.compile(rf"<{tag}\b|</{tag}\s*>", re.IGNORECASE)
    depth = 1
    for match in token.finditer(page, open_match.end()):
        depth += -1 if match.group(0).startswith("</") else 1
        if depth == 0:
            return page[open_match.end() : match.start()]
    return page[open_match.end() :]


def _label_value(page: str, *labels: str) -> str | None:
    """The value span following the first present ``joblayouttoken`` label."""
    for label in labels:
        match = re.search(
            rf'joblayouttoken-label"[^>]*>\s*{re.escape(label)}\s*</span>\s*<span[^>]*>([^<]*)',
            page,
        )
        if match and match.group(1).strip():
            return unescape(match.group(1)).strip()
    return None


def _meta_itemprop(page: str, prop: str) -> str | None:
    """A ``<meta itemprop="..." content="...">`` microdata value (CSB pages carry the
    JobPosting schema this way instead of JSON-LD)."""
    match = re.search(rf'<meta itemprop="{prop}" content="([^"]*)"', page)
    value = unescape(match.group(1)).strip() if match else ""
    return value or None


def _careersite_prop(page: str, prop: str) -> str | None:
    """The tags-stripped text of a ``data-careersite-propertyid="{prop}"`` element — RMK's
    canonical single-field value. It wins over the label spans because many boards wrap the value
    in a nested element (``<span ...><p id="job-location">Durham, NC, US</p></span>``), where a
    plain label-value regex captures only the whitespace before the nested tag."""
    match = re.search(
        rf'data-careersite-propertyid="{prop}"[^>]*>(.*?)</span>', page, re.DOTALL
    )
    if not match:
        return None
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(1))).strip()
    return unescape(text) or None


def _csb_location(page: str) -> str | None:
    # The single ``location`` property (tags stripped) is the most reliable when present — it
    # holds the whole "City, Region, Country" even when wrapped in nested markup.
    prop = _careersite_prop(page, "location")
    if prop:
        return prop
    # else assemble from the city/state/country label spans (full names like "Sikkim, India"),
    # falling back field-by-field to the microdata metas (which hold truncated "Sikk"/"In").
    parts = [
        _label_value(page, "City:", "Location:")
        or _meta_itemprop(page, "addressLocality"),
        _label_value(page, "State/Province:", "State:")
        or _meta_itemprop(page, "addressRegion"),
        _label_value(page, "Country/Region:", "Country:")
        or _meta_itemprop(page, "addressCountry"),
    ]
    return ", ".join(p for p in parts if p) or None


# `[^\W_]+` rather than `\w+`: `\w` includes `_`, and SuccessFactors's own slug encoder uses `_`
# as its own separator (a literal "." in a title becomes "_" — measured on
# tuyendung.vietcombank.com.vn's "[II.2026_Nam ...]" titles), so keeping it as a token character
# would glue two real words together instead of splitting them.
_SLUG_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _location_from_street_address(page: str) -> str | None:
    """The ``streetAddress`` microdata, as a location, with the tenant's own junk dropped.

    Despite the name SuccessFactors puts a place here, at whatever grain the tenant configured —
    ``Kuala Lumpur, MY, 50450`` and ``Iasi, RO`` and bare ``SG`` all observed on live pages. It
    is the only geography left on tenants whose pages render no location markup and whose job
    URLs carry no location either, which is why it earns a tier at all.

    The one guard is measured, not defensive: a tenant's source data can leak a URL into the
    field (``careers.wataniaind.com`` serves ``content="SA, https://ma"``, its own job titles
    carrying the same fragment — 1 of 12 non-empty values in a 22-tenant sample). Segments
    holding a scheme are dropped rather than the whole value, so that page still yields ``SA``.
    """
    raw = _meta_itemprop(page, "streetAddress")
    if not raw:
        return None
    kept = [s.strip() for s in raw.split(",") if s.strip() and "://" not in s]
    return ", ".join(kept) or None


def _location_from_slug(title: str, url: str) -> str | None:
    """The posting's location recovered from its own URL slug, when the page carries no location
    markup at all — some CSB tenants' job pages genuinely never render one (measured 2026-08-24,
    30-board sample: 29.9% of jobs null, 62.5% of those on tenants where the page has neither
    JSON-LD nor CSB markup, only title/description). The slug itself still carries it:
    SuccessFactors builds job URLs as ``{location}-{title}[-{state}-{zip}]/{id}/`` — e.g.
    ``/job/Charlotte-Account-Manager-Customer-Development-NC-28277/1407690100/`` for a posting
    titled "Account Manager - Customer Development".

    Anchored on the title, which is already reliably extracted: tokenize both into words, then
    require the title's words, concatenated, to appear in the slug's words concatenated, starting
    exactly where a slug token starts. Whatever comes before that match is the location. Anything AFTER it is never used — that is
    where a trailing requisition id lives (``.../Foshan-City-Sr-Technician-528513/...`` for a
    title of just "Sr Technician" leaves a bare ``528513``, not a place), and appending it would
    fabricate a location worse than reporting none. This costs precision on US-style postings
    that do carry a real ``-NC-28277`` state/zip suffix — those are simply reported at city grain
    — but a location that is always genuinely a place beats one that occasionally isn't. It also
    costs the original punctuation: words are joined with plain spaces, so a multi-part prefix
    like "Gaoming District, Foshan City" (comma in the source) comes back as "Gaoming District
    Foshan City" — recovering which gap was a comma would mean guessing, so this doesn't.

    Returns None whenever the title cannot be found that way (title-cased differently than the
    slug encodes it, a title containing a literal "/" — which decodes before the path is split
    on "/" and shifts every segment after it, so the id/slug split below no longer lines up — or
    a tenant whose slug is the title with no location component at all — confirmed on
    careers.ijm.com, whose job URLs are the bare title verbatim) rather than guess from a partial
    match. Punctuation the encoder dropped mid-title is NO LONGER a None case: that is exactly
    what the concatenated match below exists to span.
    """
    from urllib.parse import urlparse

    path = unquote(urlparse(url).path)
    segments = [s for s in path.split("/") if s]
    if not segments:
        return None
    # the id is its own trailing segment (`_JOB_PATH`'s own shape); the slug is the one before it
    slug = (
        segments[-2] if len(segments) >= 2 and segments[-1].isdigit() else segments[-1]
    )
    slug_words = _SLUG_WORD.findall(slug)
    title_words = _SLUG_WORD.findall(title)
    if not slug_words or not title_words:
        return None
    # Matched on the *concatenated* lowercase words rather than the token sequence, because
    # SuccessFactors's slug encoder drops punctuation without putting a separator back, gluing
    # two title words into one slug token: "Werkstudent*in" -> "Werkstudentin",
    # "Projektcontroller/Finance" -> "ProjektcontrollerFinance", "(m/w/d)" -> "(mwd)". A
    # token-sequence comparison can never span that glue, so every such posting fell through to
    # no location at all even though its slug plainly carries one (measured on jobs.dkb.de:
    # 13 of 13 nulls were this, all recoverable as "Berlin", "Gera", "FrankfurtOder").
    #
    # Anchoring BOTH ends to token boundaries is what keeps this as strict as the sequence match
    # it replaces — the title may only ever consume whole slug tokens. The start anchor is what
    # stops a title matching mid-token and splitting a word off into the location. The end anchor
    # matters for a subtler case found in review: `str.find` takes the *first* occurrence, so a
    # title whose concatenation is a PREFIX of a longer token matches there instead of at its
    # real position and truncates the location — title "Sales Rep" against
    # `Berlin-Salesrepublic-Sales-Rep` yields "Berlin" where the whole prefix is "Berlin
    # Salesrepublic". Rejecting that occurrence is not enough on its own, since the right match
    # is further along, so the scan below walks occurrences until one lines up at both ends.
    # Measured over 45 live Boards / 2,287 jobs the two anchorings are
    # indistinguishable (same 1,882, same 405 gained, 0 changed, 0 lost, 0 truncations seen), so
    # the end anchor costs nothing real and closes a class that is demonstrably reachable.
    slug_lower = [w.lower() for w in slug_words]
    title_lower = [w.lower() for w in title_words]
    # character offsets at which slug tokens start and end, within the concatenated form
    token_starts: dict[int, int] = {}
    token_ends: set[int] = set()
    offset = 0
    for index, word in enumerate(slug_lower):
        token_starts[offset] = index
        offset += len(word)
        token_ends.add(offset)
    needle = "".join(title_lower)
    haystack = "".join(slug_lower)
    # Every occurrence is tried, not just the first: the first may be a longer token that merely
    # starts with the title, and refusing there would throw away a correct match further along.
    offset_of_match = haystack.find(needle)
    while offset_of_match >= 0 and not (
        offset_of_match in token_starts and offset_of_match + len(needle) in token_ends
    ):
        offset_of_match = haystack.find(needle, offset_of_match + 1)
    if offset_of_match < 0:
        return None
    match_at = token_starts[offset_of_match]
    prefix = slug_words[:match_at]
    if not prefix or all(w.isdigit() for w in prefix):
        return None
    return " ".join(prefix)


def _csb_posted_at(page: str) -> str | None:
    # The label span renders per-locale ("6/29/26", "25 Jun 2026", "Jun 25, 2026"); the meta
    # microdata is Java Date.toString, always UTC. First candidate that parses wins.
    candidates = (
        (
            _label_value(page, "Posting Start Date:", "Posting Date:"),
            ("%m/%d/%y", "%m/%d/%Y", "%d %b %Y", "%b %d, %Y"),
        ),
        (_meta_itemprop(page, "datePosted"), ("%a %b %d %H:%M:%S UTC %Y",)),
    )
    for value, formats in candidates:
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt).date().isoformat()  # noqa: DTZ007
            except (TypeError, ValueError):
                continue
    return None
