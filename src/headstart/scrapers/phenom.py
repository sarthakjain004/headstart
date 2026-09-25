"""Phenom (PhenomPeople) career-site scraper.

Adapted from jobhive's Phenom scraper (kalil0321/ats-scrapers, MIT). Little of that original
survives — four of its load-bearing assumptions are measured false below — but the attribution
stays: it is the licence term, not a description of how much code is left.

The ``slug`` is the board host (``careers.mastercard.com``, ``jobs.tjx.com``), like eightfold and
icims. Every tenant serves one JSON endpoint, ``POST /widgets``, whose behaviour is selected by a
``ddoKey`` discriminator: ``refineSearch`` lists, ``jobDetail`` reads one posting.

Everything below was measured live on 2026-09-16 across the 98-tenant seed (91 reachable,
155,738 postings) — ``docs/phenom/2026-09-16_widgets-api-measurement.md``.

**No CSRF, no session, no Referer.** The upstream implementation seeds a session with a GET and
replays a CSRF token, on the stated grounds that the POST 403s without one. It does not: a bare
client with no prior GET, no cookie jar, no ``Origin``/``Referer`` and no token answers 200 with
the full payload. The GET is one wasted request per Board, so it is not made.

**The listing carries no description.** It states ``descriptionTeaser`` — a ~350-character
marketing blurb — and no ``description`` key at all. The upstream parser reads
``item["description"] or item["descriptionTeaser"]``, so the first operand is always absent and
every posting would be served its teaser. The real body is on the detail payload only (5,121
characters against the teaser's 350 on the same posting), which is why this is a detail-pass ATS.

**The page size caps at 500, silently.** ``size=1000`` returns exactly 500 rows and a 200 — a
clamp, not an error. Upstream uses 100, which is five times the calls for the same rows.

**Reading stops at ``from + size >= 10000``** — an Elasticsearch ``max_result_window``, and the
sharpest edge here. Past it the response is not an error and not empty-with-a-total: ``totalHits``
itself comes back **0**, so a loop that re-reads the total each page is told the Board is finished.
Measured on ``jobs.cvshealth.com`` (19,649 postings): ``from=9000&size=500`` reads 500 rows and
reports 19,649; ``from=9500&size=500`` reads 0 and reports 0. Five of the 91 seed tenants are over
the wall, so this is not a theoretical cap — such a Board is truncated by construction and says so
(:meth:`~BaseScraper.mark_truncated`, ADR-0053), which is what keeps ``index sync`` from reading
the unreachable tail as a mass delisting. None of those five is in the shipped ledger (the largest
Board there is ~9.4k), so that arm is exercised by the tests rather than in production today.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from headstart import company_name
from headstart.jobs.job import Job, host_of, html_to_text
from headstart.network import http
from headstart.network.fetcher import Fetcher
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    DetailLost,
    DetailRequest,
    DetailWithoutDescription,
)

#: Rows per listing call. The endpoint clamps anything larger to 500 without saying so.
_PAGE_SIZE = 500

#: ``from + size`` must stay **under** this. 10,000 is the ordinary Elasticsearch
#: ``max_result_window``, and the boundary is exclusive on the sum: ``9000+500`` reads, ``9500+500``
#: and ``9990+10`` both read zero. So the last reachable posting is index 9,998.
_RESULT_WINDOW = 10_000

#: Concurrency for the per-Job detail pass. No rate limit was found in 360 requests against
#: ``careers.mastercard.com`` (zero 429s, zero non-200s, no ``Retry-After`` ever sent), and
#: throughput knees here: conc 16 gave 14.9 req/s at p50 0.88s, conc 32 only 15.9 req/s at p50
#: 1.04s with a 8.05s tail and the run's only transport errors. Past the knee costs latency and
#: buys nothing.
_DETAIL_WORKERS = 16

#: The locale prefix every tenant is probed with first. It is not a guess about the tenant — a
#: wrong one redirects to the right one, which is how :meth:`PhenomScraper._prefix` learns it.
_PROBE_PREFIX = ("us", "en")

_PREFIX_RE = re.compile(r"^https?://[^/]+/([^/?#]+)/([^/?#]+)")

#: The landing page's ``og:site_name`` value, whichever order the tag writes its attributes in.
_OG_SITE_NAME = re.compile(
    r'<meta\b(?=[^>]*\bproperty="og:site_name")[^>]*\bcontent="([^"]*)"', re.IGNORECASE
)


def _listing_title(row: dict) -> str | None:
    """A listing posting's title, for the tech gate. Named rather than an inline lambda because
    this gate is a *measured* tolerance and not an exact one — ``parse`` prefers the detail's own
    ``title`` over this one — so an accessor that quietly started reading a neighbouring key
    (``descriptionTeaser`` sits right beside it) would classify on the wrong string and nothing
    would raise."""
    return row.get("title")


def _listing_department(row: dict) -> str | None:
    """A listing posting's department, for the tech gate. Named for the domain field (`department`,
    the same word jazzhr's and gem's accessors use) rather than for the wire key it happens to
    read: `category` is the listing's own department label, and it is what ``parse`` reads first.
    See :func:`_listing_title`."""
    return row.get("category")


class PhenomScraper(BaseScraper):
    """Phenom scraper — ``slug`` is the board host."""

    ats = "phenom"
    # scraper: f"https://{slug}/{cc}/{lang}/job/{id}" where the slug IS the board host. Tenants
    # sit on their own vanity domains (careers.mastercard.com, jobs.tjx.com, workwithus.circlek.com)
    # and the legacy *.phenompeople.com namespace is dead, so there is nothing narrower to anchor
    # on — same shape as eightfold's and icims's host slugs.
    #
    # The `cc` segment is NOT always "us": measured across the 91 reachable seed tenants, 30 are
    # something else — `global` (25), `ca` (2), `amer`, `gb`, `na` — so a `us`-anchored pattern
    # would reject a third of this provider's real links. Both segments therefore stay loose.
    # The trailing title slug is omitted deliberately; see `job_url`.
    url_shape = r"https://[^/]+/[^/]+/[^/]+/job/[^/?#]+"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    def __init__(
        self, slug: str, company: str | None = None, fetcher: Fetcher | None = None
    ) -> None:
        super().__init__(slug, company, fetcher)
        # Resolved by `fetch_raw` before anything needs it; `job_url` renders whichever prefix the
        # Board turned out to use, and falls back to the probe prefix for a Board never fetched
        # (the liveness prober builds a scraper and reads `url()` without calling `fetch_raw`).
        self._cc, self._lang = _PROBE_PREFIX

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """The board host, which a pool row may carry in either column.

        `jobs.job.host_of`, not a local split, for the reason its own docstring gives: the scraper,
        the liveness prober and the ledger repair have to agree on what a host is, and the one time
        they did not it cost 312 Boards recorded live with zero jobs. Same shape oracle, icims and
        zwayam use.
        """
        return host_of(url) or tenant.strip().lower()

    def url(self) -> str:
        return f"https://{self.slug}/{self._cc}/{self._lang}/search-results"

    def board_page(self) -> str:
        """The careers landing page, whose ``<title>`` carries the company name — and, where its
        wrappers fail, its ``og:site_name`` (:meth:`company_from_page`).

        A page title rather than the `companyName` field every detail payload states, which looks
        like the better source and is not. That field is **per posting, not per Board**, and it
        disagrees with itself: `careers.dhl.com` returns ``Blue Dart Express Limited`` — a
        subsidiary — on all 12 sampled postings of a 9,376-job DHL board, and `careers.honda.com`
        splits 7/5 across two legal entities. Taking the first one to arrive would name a Board
        after whichever posting happened to sort first.

        It also would not survive a second run. ADR-0048 skips the detail fetch for a Job whose
        description the store already holds, so a steady-state Board fetches *no* details and
        would have nothing to read the name from — measured: `careers.zelis.com` resolves to
        ``Zelis`` on the first scrape and reverts to ``careers.zelis.com`` on every one after.
        A title is one request, on the Board itself, every run.
        """
        return f"https://{self.slug}/{self._cc}/{self._lang}"

    def company_from_page(self, page: str | None) -> str | None:
        """The title's name, else the page's ``og:site_name``.

        Title first because the user chose brand before legal name, and where both yield a name
        the title is the brand ("Careers at MITRE" against "The MITRE Corporation"). The site name
        rescues a landing page titled as marketing copy ("Join Air Canada: Explore Careers and
        Job Opportunities"), which no wrapper can strip safely. Measured on the 79 live Boards
        2026-09-24: the 22 serving their host all stated an ``og:site_name``, and it named the
        employer on each landing page that answered.
        """
        name = super().company_from_page(page)
        if name:
            return name
        # A field the tenant set, so `from_field`'s guards (ADR-0212), under phenom's aliases.
        match = _OG_SITE_NAME.search(page or "")
        return company_name.from_field(self.ats, match.group(1) if match else None)

    # --- locale prefix ----------------------------------------------------------------------

    def _prefix(self) -> tuple[str, str]:
        """This Board's ``(cc, lang)`` path segments, learned from where it redirects.

        The prefix does **not** affect the listing — ``country``/``lang`` in the POST body are
        cosmetic, and all seven tenants the upstream seed marks ``global`` return byte-identical
        totals whether asked as ``us`` or ``global``. It decides one thing only: whether the links
        we serve resolve.

        And a wrong prefix does not 404. It answers **200** and redirects to the tenant's own
        landing page, so a job URL built on it renders a careers homepage with no posting on it —
        a dead link that looks alive to anything checking status codes. Measured: 30 of the 91
        reachable tenants are not ``us``, so hardcoding one (as the upstream implementation does,
        and it omits the segment from its job URLs entirely) silently breaks a third of them.

        That same redirect is the fix: ask for ``/us/en/search-results`` and read the prefix off
        whatever URL we land on. One request per Board, self-correcting, and no hand-maintained
        per-tenant locale column to drift.
        """
        cc, lang = _PROBE_PREFIX
        try:
            response = self._fetch(
                "GET",
                f"https://{self.slug}/{cc}/{lang}/search-results",
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                timeout=30,
                allow_redirects=True,
            )
        except http.RequestsError as exc:
            # The listing POST below does not need the prefix, so a failed probe costs link
            # accuracy on this Board, not its postings. Keep the probe default and carry on
            # rather than failing a Board whose jobs are perfectly readable — but say so: a
            # wrong prefix serves links that answer 200 and show nothing.
            self._log.info(
                f"{self.board_key()}: locale probe raised {type(exc).__name__} — "
                f"building links on {cc}/{lang}"
            )
            return cc, lang
        landed = str(getattr(response, "url", "") or "")
        match = _PREFIX_RE.match(landed)
        if not match:
            self._log.info(
                f"{self.board_key()}: locale probe landed on {landed[:80]} — "
                f"building links on {cc}/{lang}"
            )
            return cc, lang
        return match.group(1), match.group(2)

    # --- listing ----------------------------------------------------------------------------

    def widgets_url(self) -> str:
        """The tenant's one JSON endpoint. Public: the liveness probe posts its count here
        (ADR-0203)."""
        return f"https://{self.slug}/widgets"

    #: The one set of headers and the one timeout both widget calls send. Declared once because
    #: the listing and the detail request post to the same endpoint, and two copies drift.
    _WIDGET_HEADERS: ClassVar[dict[str, str]] = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Content-Type": "application/json",
    }
    _WIDGET_TIMEOUT: ClassVar[int] = 45

    def _widgets(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._fetch(
            "POST",
            self.widgets_url(),
            json=payload,
            headers=self._WIDGET_HEADERS,
            timeout=self._WIDGET_TIMEOUT,
        )
        response.raise_for_status()
        return response.json() or {}

    def _search_payload(self, start: int, size: int) -> dict[str, Any]:
        return {
            "lang": f"{self._lang}_{self._cc}",
            "deviceType": "desktop",
            "country": self._cc,
            "pageName": "search-results",
            "ddoKey": "refineSearch",
            "sortBy": "",
            "subsearch": "",
            "from": start,
            "jobs": True,
            "counts": True,
            "all_fields": ["category", "country", "state", "city"],
            "size": size,
            "clearAll": False,
            "jdsource": "facets",
            "isSliderEnable": False,
            "pageId": "page20",
            "siteType": "external",
            "keywords": "",
            "global": True,
            "selected_fields": {},
            "locationData": {},
        }

    def _listing(self) -> list[dict]:
        """Every posting the result window will give up, newest page last.

        ``totalHits`` is read **once, from the first page**, and never re-read. Past the window
        the endpoint reports ``totalHits: 0`` alongside its empty page, so a loop that refreshed
        the total would take that 0 as "the Board is finished" and mark a 19,649-posting Board
        complete at 9,500 — the exact shape of silent under-reading that ADR-0053 exists to catch.
        """
        jobs: list[dict] = []
        seen: set[str] = set()
        total: int | None = None
        start = 0
        while True:
            size = min(_PAGE_SIZE, _RESULT_WINDOW - start - 1)
            if size <= 0:
                break
            widgets = self._widgets(self._search_payload(start, size))
            body = widgets.get("refineSearch") or {}
            page = ((body.get("data") or {}).get("jobs")) or []
            if total is None:
                total = body.get("totalHits")
                if not body:
                    self.note_unreadable_board(
                        "a `refineSearch` widget", f"keys {sorted(widgets)[:5]}"
                    )
            if not page:
                break
            for job in page:
                native_id = str(job.get("jobId") or "")
                # Deduped by id rather than trusted positionally: the window is an offset walk
                # over a live index, so a posting added between two pages shifts every later one
                # and re-serves a row already read.
                if native_id and native_id not in seen:
                    seen.add(native_id)
                    jobs.append(job)
            start += len(page)
            if total is not None and start >= total:
                break
        if total and total >= _RESULT_WINDOW:
            # A hard cap: unreachable, not merely unread, so this is the unconditional verdict
            # rather than `mark_truncated_unless_negligible` (ADR-0121). The Board leaves the
            # eviction scope this run instead of having its unreachable tail read as delistings.
            self.mark_truncated(
                f"{len(jobs)} of {total} readable — the result window closes at {_RESULT_WINDOW}"
            )
        elif total:
            # A shortfall inside the window is *measurable* against the Board's own stated total,
            # so it goes to the tolerant verdict (ADR-0121): at or above MIN_AUTHORITATIVE_SHARE
            # the list stays authoritative and the few missing ids fall to ADR-0083's per-Job
            # grace period, below it the Board leaves the eviction scope. Without this branch a
            # walk that ended early — a page the edge dropped, or dedupe collapsing a shifting
            # index — would report a short list as a complete one and feed the difference to
            # `index sync` as delistings. oracle and eightfold both gate their walks the same way.
            self.mark_truncated_unless_negligible(
                len(jobs), total, f"read {len(jobs)} of {total} listed"
            )
        return jobs

    # --- detail -----------------------------------------------------------------------------

    def _detail_payload(self, native_id: str) -> dict[str, Any]:
        return {
            "lang": f"{self._lang}_{self._cc}",
            "deviceType": "desktop",
            "country": self._cc,
            "pageName": "job-details",
            "ddoKey": "jobDetail",
            "jobId": native_id,
            "siteType": "external",
            "isSliderEnable": False,
            "pageId": "page7",
        }

    def detail_request(self, row: dict) -> DetailRequest:
        # The same POST `_widgets` sends for the listing, with the detail payload as its body.
        return DetailRequest(
            self.widgets_url(),
            method="POST",
            headers=self._WIDGET_HEADERS,
            timeout=self._WIDGET_TIMEOUT,
            options={"json": self._detail_payload(str(row["jobId"]))},
        )

    def read_detail(self, row: dict, response: Any) -> dict | DetailWithoutDescription:
        """The one posting in a detail response.

        An unknown id is **not** a 404 and not an empty list — it is a 200 whose envelope simply
        has no ``job`` key. Labelled rather than merely counted, because a Board whose ids have
        gone stale and a Board the edge is refusing produce the same gap count and call for
        opposite responses.
        """
        body = response.json() or {}
        job = ((body.get("jobDetail") or {}).get("data") or {}).get("job")
        if not job:
            raise DetailLost("no job on a 200")
        if not job.get("description"):
            # Kept for the fields the detail states beside it; a gap for the description.
            return DetailWithoutDescription(job, "200 without description")
        return job

    def fetch_raw(self) -> Any:
        self._cc, self._lang = self._prefix()
        listed = self._listing()
        # ADR-0048: a Job whose description the store already holds needs no detail fetch. Safe
        # here in a way it is not on oracle, because everything else this payload supplies —
        # employment type, department, location, remote — the *listing* also states, so a skipped
        # Job keeps real values rather than blanking fields that had them. eightfold makes the
        # same call for the same reason.
        # Two skips, both over the listing rows: the ADR-0166 tech gate drops what `filter_tech`
        # would drop anyway, and `needs_detail` drops what the description store already holds.
        # The gate is a *measured* tolerance rather than exactness — `parse` prefers the detail's
        # `title` over the listing's, and falls back to the detail's `category`/`jobFamilyGroup`
        # where the listing states no `category` — so a posting the two payloads label
        # differently could disagree. Measured live 2026-09-17 over all 10 Boards the 2026-09-17
        # pre-filter corpus covers, 15,321 postings: 2,126 kept by the gate, 2,126 by the filter,
        # zero disagreements. Both fallbacks were then probed where they could actually fire —
        # `careers.dhl.com` is the one Board that leaves `category` empty on most rows (7,269 of
        # 9,515), and on 300 of those, sampled at random and fetched, **0** details state a
        # `category`/`jobFamilyGroup` the listing did not and **0** carry a different `title`.
        # Re-check it if either payload's field set moves.
        #
        # Reported, not marked truncated: a missing detail costs one Job its description, but the
        # Job is still listed and still emitted, so the Board's *list* is whole — and ADR-0053 is
        # about the list, not the fields. Every row `_listing` keeps has a `jobId`.
        details = self.run_detail_pass(
            listed,
            key_of=lambda row: str(row["jobId"]),
            what="descriptions",
            title_of=_listing_title,
            department_of=_listing_department,
            skip_held=True,
        )
        return {"jobs": listed, "details": details}

    # --- parse ------------------------------------------------------------------------------

    def job_url(self, native_id: str) -> str:
        """The careers-site page for one posting.

        The title slug real links carry (``/job/R-289042/Lead-Software-Engineer``) is **cosmetic**:
        verified on three tenants, the bare ``/job/{id}`` form renders the same posting with its
        JSON-LD intact, and even a deliberately wrong slug resolves by id. So it is left off —
        ADR-0153 hands this method an id and nothing else, and a title is not recoverable from one.
        """
        return f"https://{self.slug}/{self._cc}/{self._lang}/job/{native_id}"

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        listed = raw.get("jobs") or []
        details = raw.get("details") or {}
        jobs: list[Job] = []
        for row in listed:
            native_id = str(row.get("jobId") or "")
            if not native_id:
                continue
            detail = details.get(native_id) or {}
            jobs.append(
                Job(
                    id=self.job_id(native_id),
                    ats=self.ats,
                    company=self.company,
                    title=(detail.get("title") or row.get("title") or "").strip(),
                    location=_location(row, detail),
                    remote=_remote(row, detail),
                    # `category` is the listing's own department label and is present far more
                    # often than the detail's `jobFamilyGroup`, which many tenants leave blank.
                    department=(
                        row.get("category")
                        or detail.get("category")
                        or detail.get("jobFamilyGroup")
                        or None
                    ),
                    url=self.job_url(native_id),
                    posted_at=row.get("postedDate") or row.get("dateCreated"),
                    scraped_at=scraped_at,
                    # The detail body only. `descriptionTeaser` is a marketing blurb — ~350
                    # characters against the real body's 5,121 on the same posting — so falling
                    # back to it would serve a summary as the description and, worse, mark the Job
                    # described so nothing ever fetched the real one.
                    description=html_to_text(detail.get("description")),
                    experience=None,  # no native field on either payload; ADR-0018 tiers cover it
                    employment_type=(
                        row.get("type")
                        or detail.get("type")
                        or detail.get("jobType")
                        or None
                    ),
                    salary=self._salary_field({"listed": row, "detail": detail}),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        """No native compensation field exists on this ATS — measured, not assumed.

        Phenom stores no compensation of its own: it passes through whatever custom fields the
        tenant's backing ATS happens to send, under names the tenant invents. Across the 91
        reachable seed tenants, 43 carry *some* salary-ish key and not one of them is usable as a
        field:

        - ``salary: "Salary"``, ``salaryHourly: "false"``, ``payment: "0"``,
          ``salaryVisibility``, ``compensationGrade: "C5"``, ``compensationGradeId:
          "Grade 6 - Manager / Consultant"`` — labels, flags and internal bands, no amounts.
        - ``compensationRange: "The pay range for this role is $19.88 - $33.94 hourly. Individual
          compensation will be determined by..."`` — a real number, inside a prose paragraph.
        - ``psStartingWageRate: "From $37.86+ per hour"`` beside ``psMaximumWageRate: "53.44"`` —
          real, but a tenant-private pair of differently-shaped strings.

        There is no key that means the same thing on two tenants, so there is nothing to dispatch
        on. The prose cases are not lost: they sit in the description this scraper does fetch, and
        `salary.extract`'s Tier-2 regex reads them there. Returning a tenant-specific guess here
        would only put a grade label or a bare ``0`` in front of that.
        """
        return None


def _location(listed: dict, detail: dict) -> str | None:
    """The most complete location string either payload states.

    ``cityStateCountry`` before ``location``: the second is the raw posting string and often
    carries a postcode (``"Colombo, Sri Lanka, 00400"``), while the first is Phenom's own
    normalised join (``"Pune, Mahārāshtra, India"``).
    """
    for value in (
        listed.get("cityStateCountry"),
        detail.get("cityStateCountry"),
        listed.get("location"),
        detail.get("location"),
        listed.get("cityState"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _remote(listed: dict, detail: dict) -> bool | None:
    """Whether the posting states itself remote, or None where it says nothing.

    Tenants spell the field two ways (``remote``, ``RemoteType``) and populate it with their own
    vocabulary — ``On-Site``, ``Hybrid``, ``Onsite Only`` were the three values seen across a
    four-tenant sample. ``Hybrid`` maps to None rather than False, matching `workday._remote_from`'s
    convention: a hybrid posting is not the remote role a remote filter is looking for, but calling
    it explicitly non-remote overstates what the Board said.

    Two more measured live 2026-09-22: Sutter Health spells the key ``remoteType`` (1,233/1,233
    rows), and Honda states "Remote Eligible up to 20%" beside "100% Onsite" — a partial share is
    hybrid by the same convention, not remote.
    """
    for value in (
        listed.get("remote"),
        listed.get("RemoteType"),
        listed.get("remoteType"),
        detail.get("remote"),
        detail.get("RemoteType"),
        detail.get("remoteType"),
    ):
        if not isinstance(value, str) or not value.strip():
            continue
        normalised = value.strip().lower()
        if "hybrid" in normalised or ("%" in normalised and "100%" not in normalised):
            return None
        if "remote" in normalised or "work from home" in normalised:
            return True
        if (
            "on-site" in normalised
            or "onsite" in normalised
            or "in office" in normalised
        ):
            return False
    return None
