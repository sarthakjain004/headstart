"""Jobvite career-site scraper (jobs.jobvite.com/{slug}).

Everything here was measured against the live pool on 2026-09-07 — 517 tenants, ~7,600 requests
in all; the captures and the per-board table are in ``docs/jobvite/``.

**There is no public JSON jobs API, and that is a measured result, not an assumption.** A HAR of
three real boards (Chromium, 256 entries; the captures are not committed — see
``docs/jobvite/LOG.md``) shows the listing and the detail
page are plain server-rendered navigations: the only first-party XHRs anywhere are
``/{slug}/search/facets?nl=1`` (the filter taxonomy — regions/locations/departments/categories/
jobTypes, no postings) and ``/{slug}/job/{id}/recommend?nl=1`` (five ``{jobEId,title}`` pairs, no
fields). The careers-site Angular bundle contains exactly one ``$http.get``, and it is the facets
one. The historical partner API answers ``401 {"messages":["Invalid api/secret…"]}`` on
``api.jobvite.com/api/v2/job`` with or without a ``companyId``, so it needs a per-tenant key we
cannot have for unaffiliated tenants — the same dead end as Zoho's authenticated API.
``app.jobvite.com/CompanyJobs/Careers.aspx?c={companyEId}`` is a legacy alias that serves the
identical HTML (byte-for-byte 28,814 on barracuda), not a richer surface.

**The listing surface is ``/search``, walked by its own ``jv-pagination-next`` link — not
``/jobs``.** ``/jobs`` is what a naive read reaches for and it is *silently short on 139 of 434
live boards*: on a branded career site it is a marketing landing page carrying no job list at all
(43 boards return zero jobs there while ``/search`` serves 6-78), and elsewhere it simply lists
fewer. Zero boards had ``/jobs`` carry a posting ``/search`` lacked. ``/jobs/viewall`` exists on
only 28 boards and is likewise a subset. ``/search`` pages at exactly **50 postings**, with a
``?p=N`` (0-indexed) next link and a counter that states the true board size — ``1-50 of 2,831``
on the pool's largest board, which needs 57 pages. Reading only page 0, as the first draft of this
investigation did, would have capped 65 of the 66 boards over 50 postings at 50.

**The listing yields ids, and a title where the row states one plainly.** Five row templates are
live and they disagree about where every field sits: ``<td class="jv-job-list-name"><a>`` (classic),
``<div class="tr">`` with div cells (lhhcareers), an extra-column variant carrying
``jv-job-list-req``/``jv-job-list-type`` (von), an ``<a>`` that *wraps* the name element
(aryaka), and one with no anchor at all — ``<tr onclick="window.location.href='…'">`` (agscareer).
Each successive row-shaped parse this investigation tried returned **zero rows** on a different
19%, 15% and 1.4% of boards, and did so silently. The one invariant every template shares is the
``/{slug}/job/{id}`` path itself, so that is what is scanned for. Validated against the boards'
own counters: **395 of 434 exact, 6 short, 0 over** — no stray link is ever mistaken for a
posting. The title is read only off a classic anchor whose whole text is the title
(``<a href="/{slug}/job/{id}">Title</a>``), for the tech gate below; on 30 random live Boards
(2026-09-28) 1,242 of 1,378 postings had one, and all 1,242 equalled the page's own title.

**The tech gate asks with the department that promotes most** (ADR-0166). The department is on
the page alone, and ``tech_filter``'s rule 4 promotes a vague title under a technical department
("Estimator" under "Engineering"), so a title-only gate is unsafe: on those 30 Boards it would
have skipped 42 of the 247 tech postings. Asked with :data:`_MOST_PROMOTING_DEPARTMENT` it
skipped none and still skipped 515 of the 1,242 titled pages (41%). A row with no plain title is
always fetched, and a gated posting still ships as a Job carrying its listing title, so the full
set stays whole and the tech filter drops it downstream exactly as before.

**A short walk is what ``distinct ids < counter total`` means, so a short Board is walked again.**
``/search`` pagination is not stable between requests: a posting can fill two slots of one walk,
and the posting it displaced is then on no page of that walk. Measured 2026-09-28 on ``fprs``
(counter ``1-50 of 1,783``, stable on every page): four walks minutes apart read 1,765, 1,735,
1,738 and 1,765 distinct ids, and their union grew 1,765, 1,772, 1,777, 1,783 — exactly the
counter. A missed id is a live posting (``o0CIAfwE?nl=1`` answered 200 with its JobPosting), so
reading one walk as the Board flapped postings in and out of the index (ADR-0083 evicts on a
second consecutive miss). This corrects the 2026-09-07 reading of the same shortfall as "one
posting in two slots", which counted the repeated slot but not the posting it pushed out. So
:meth:`_listing` walks again, up to :data:`_MAX_WALKS`, while the union is short of the
counter and each walk still finds something new. A Board whose re-walks proved the listing
unstable and is still short is reported through ``mark_truncated_unless_negligible`` (ADR-0121);
one whose first re-walk found nothing new is short stably and only logged (ADR-0256). Only a
Board short on its first walk pays for a second.

**ADR-0111's alias dedupe does not apply here, and deliberately gets no override.** Every Board is
a path on one host, so :meth:`BaseScraper.alias_key`'s default returns ``jobs.jobvite.com`` for all
of them — which is the degenerate case its own docstring describes: the key is not itself a live
slug, so ``alias_ledger.resolve`` labels the whole ledger ``migrated`` and returns nothing. Inert,
not wrong. Overriding it to return the slug would make every Board its own key and return nothing
just the same, so it would be code that buys no behaviour. The duplication Jobvite *does* have —
parent tenants that also serve their subsidiaries' postings, ~2.5% of rows — is not aliasing
between hostnames and is left to ADR-0023's duplicate prune, which is the mechanism for it.

**A dead tenant answers 302, and following it looks exactly like an empty board.** Jobvite sends
``Location: http://search.jobvite.com?invalid=1``, which lands on a 174 KB marketing page with a
**200** — so a redirect-following fetch reads a departed tenant as a live board with zero
postings, and ``index sync`` would evict every row it ever had. Hence ``allow_redirects=False``
here and a raise on anything but 200. Measured over the whole pool: 83 of 517 tenants redirect —
78 with ``invalid=1``, one to an ``app.jobvite.com`` login wall (an internal board), and four to
the customer's own domain with ``?p=search&nl=1`` (the tenant moved its career site off the
Jobvite-hosted surface; the destination renders its listing client-side and serves no
``/job/`` links, so there is nothing left to read there either). The other 434 answer 200 on both
``/jobs`` and ``/search``, so on this ATS a live board never redirects.

**Fields come from a per-job detail pass** (ADR-0050), which is where every field except the id
lives. Most pages carry a schema.org ``JobPosting`` JSON-LD block; some tenants' templates emit
none at all (nutanix), so ``_posting_of`` falls back to the rendered ``jv-header`` /
``jv-job-detail-meta`` / ``jv-job-detail-description`` blocks — the same shape trakstar needed for
the same reason (#179). ``hiringOrganization`` is polymorphic: a bare string on most tenants, an
``{"@type":"Organization","name":…}`` object on others, so both are read. ``baseSalary`` is a
``MonetaryAmount`` that is *present but empty* on the large majority of postings; it is still read
because when it is populated it is a real structured figure, and an empty one costs nothing.

**The detail is read at ``/job/{id}?nl=1``, with redirects refused** (ADR-0231). The plain job page
of a tenant that moved its career site 302s to that site, which renders no posting; ``?nl=1`` is
the page Jobvite's embed widget frames and still answers 200 with it. A Job is built from its
detail page or not at all: there is no listing-derived fallback, so a lost page is a labelled gap
and a truncation mark, and the fix for one is in reading the page.

ADR-0048's ``needs_detail`` skip-list is deliberately **not** consulted, unlike eightfold's and
zwayam's. Those two skip the detail fetch for a Job whose description we already hold because
their *listing* still supplies location and the rest; here the listing supplies at most a title,
so skipping the page would serve a tech Job with no location, date or department.

**Known property, not a defect: Jobvite tenants nest.** A parent tenant serves its subsidiaries'
postings too (``ziffdavis`` ⊇ ``ookla``/``ign``/``spiceworks``, ``firstcash-holdings-inc`` ⊇
``affcareers``, ``gvwgroup`` ⊇ ``autocar``), and two pairs are outright the same board under two
slugs (``samtec``/``samtec-sp``, its Portuguese-localised twin — the pagination counter there
reads ``1-50 de 166``, which is why the total is parsed as the last number in the counter rather
than by an English keyword). Across the pool 579 of 22,857 distinct postings (2.5%) are served by
exactly two boards, never more. That reaches the index as two rows under two Board keys, which is
ADR-0023's duplicate-prune case, so nothing is done about it here beyond saying so.
"""

from __future__ import annotations

import math
import re
from typing import Any

from headstart.jobs.job import Job, html_to_text, is_remote
from headstart.network import http
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    DetailLost,
    DetailRequest,
    gone_board_error,
)
from headstart.scrapers.job_posting_jsonld import find_job_posting, hiring_organization

#: Detail pages are 40-110 KB each and every one hits the same origin, so the fan-out stays
#: narrow. Also the async stream width (``BaseScraper.fan_out_async``).
_DETAIL_WORKERS = 6

#: Hard stop for the pagination walk. The largest board in the pool is 2,831 postings = 57 pages,
#: so this is ~3.5x headroom; it exists so a ``next`` link that ever pointed at itself could not
#: spin forever, not as a cap anyone is expected to reach.
_MAX_PAGES = 200
#: Postings per ``/search`` page (module docstring), so a stated total implies a page count.
_PAGE_SIZE = 50
#: Walks of a Board whose distinct ids fall short of its counter (module docstring). On fprs
#: (2026-09-28, counter 1,783) the union of successive walks reached 1,765, 1,772, 1,777 and
#: 1,783: four walks closed the gap.
_MAX_WALKS = 4
#: The department :func:`~headstart.jobs.tech_filter.is_tech` promotes a vague title under most,
#: which the listing-title gate asks with because only the page states the real one (module
#: docstring).
_MOST_PROMOTING_DEPARTMENT = "Software Engineering"

_JOB_ID = r"[A-Za-z0-9]+"
#: The ``jv-pagination-next`` anchor, which is how the walk advances. Attribute order varies by
#: template, so href and class are matched in the order the live pages actually write them.
_NEXT = re.compile(r'<a[^>]+href="([^"]+)"[^>]*class="[^"]*jv-pagination-next')
#: ``1-50 of 2,831``, or ``1-50 de 166`` on a Portuguese-localised board — the total is taken as
#: the last number so no locale's connecting word has to be known.
_PAGINATION = re.compile(r'class="jv-pagination-text[^"]*">(.*?)</div>', re.DOTALL)
_NUMBER = re.compile(r"[\d,]+")

#: The rendered job title. Read only up to its first nested tag: one template (agscareer) puts
#: the location inside this heading as a ``<br><h3>Canada</h3>``, and stripping tags first turned
#: "Account Executive- Slots" into "Account Executive- Slots Canada". Neither the class list nor
#: the level is fixed: mini-circuits-review writes ``<h2 class="jv-header u-text-left">``,
#: nbbj-review ``<h3>`` and lordco-internal ``<h4>``, and matching only ``<h2 class="jv-header">``
#: lost every page of all three (2026-09-25). Each page carries one ``jv-header``, the title.
_HTML_TITLE = re.compile(
    r'<(?P<level>h[1-6]) class="(?:[^"]* )?jv-header(?: [^"]*)?">(?P<title>.*?)</(?P=level)>',
    re.DOTALL,
)
_HTML_META = re.compile(r'<p class="jv-job-detail-meta">(.*?)</p>', re.DOTALL)
#: The description container's *opening* tag; its extent is found by depth-counting
#: :data:`_DIV_TAG` (as taleo_be does). Ending at the first ``</div>`` followed by ``<div`` cut
#: bodies whose sections are nested divs down to the intro: live 2026-09-22 on nutanix, 4 of 8
#: postings lost their tail, oLhCAfwY keeping 1,489 of 5,792 chars.
_HTML_DESCRIPTION = re.compile(r'<div class="jv-job-detail-description"[^>]*>')
_DIV_TAG = re.compile(r"<(?P<close>/?)div\b", re.IGNORECASE)
#: ``jv-job-detail-meta`` reads "Category<separator>City, Region<separator>Req.Num.: N" — the
#: separator is an empty ``<span class="jv-inline-separator">``, so it survives tag-stripping only
#: if the split happens first.
_SEPARATOR = re.compile(r"<span class='jv-inline-separator'></span>")


def _description_html(page: str) -> str | None:
    """The HTML inside ``jv-job-detail-description`` up to its *own* closing tag, or None
    when the page has no such container or its divs never balance."""
    opening = _HTML_DESCRIPTION.search(page)
    if not opening:
        return None
    depth = 1
    for tag in _DIV_TAG.finditer(page, opening.end()):
        depth += -1 if tag.group("close") else 1
        if depth == 0:
            return page[opening.end() : tag.start()]
    return None


def total_of(page: str) -> int | None:
    """The board size the page's own pagination counter states, or None when it has none.

    An empty board has no counter at all — that is what the 33 zero-posting boards in the pool
    serve — so None means "no total offered", never zero.
    """
    match = _PAGINATION.search(page)
    if not match:
        return None
    numbers = _NUMBER.findall(html_to_text(match.group(1)))
    return int(numbers[-1].replace(",", "")) if numbers else None


def _location(posting: dict) -> str | None:
    """``City, Region, Country`` from the first ``jobLocation``, or the meta line's own text.

    Segments are dropped when empty and de-duplicated case-insensitively: the addresses carry
    the tenant's raw values, trailing commas and all ("Bangalore ", "Karnataka,"), and a
    single-city board routinely repeats the city as the region.
    """
    locations = posting.get("jobLocation") or []
    if not (locations and isinstance(locations[0], dict)):
        return posting.get("_location") or None
    address = locations[0].get("address") or {}
    segments: list[str] = []
    seen: set[str] = set()
    for key in ("addressLocality", "addressRegion", "addressCountry"):
        text = (address.get(key) or "").strip().strip(",").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        segments.append(text)
    return ", ".join(segments) or None


class JobviteScraper(BaseScraper):
    ats = "jobvite"
    # scraper: f"https://jobs.jobvite.com/{slug}/job/{id}" (job_url below). Every tenant is on
    # that one host — a jobvite Board is a path, never a subdomain or a customer domain — so
    # unlike eightfold/successfactors this can anchor the host. The id is Jobvite's own opaque
    # 8-char EId. Verified live 2026-09-07 on four boards spanning all five row templates
    # (barracuda-networks-inc, nutanix, agscareer, samtec-sp): all 200, each with its job title
    # in the page `<title>`, so `title_on_page` bites here rather than reading false off a
    # client-rendered page.
    url_shape = r"https://jobs\.jobvite\.com/[^/]+/job/[A-Za-z0-9]+"
    detail_workers = _DETAIL_WORKERS  # also the async stream width (base.fan_out_async)
    has_detail_pass = True  # per-Job fetch fills every field but the id (ADR-0050)

    def url(self) -> str:
        return f"https://jobs.jobvite.com/{self.slug}/search"

    def board_page(self) -> str:
        """The board again — its ``<title>`` is ``"{Name} Careers"``, the wrapper
        ``headstart.boards.company_name`` already models for eightfold and keka.

        Worth the second request (ripplehire's precedent, and one per Board rather than per Job):
        measured across all 434 live boards 2026-09-07, it resolves a real name on **424 (97.7%)**
        — better than the 93.2% of postings whose JSON-LD states a ``hiringOrganization``, and it
        is the *only* source for the 29 boards whose template emits no JSON-LD at all, which
        otherwise serve their slug. The 10 misses all keep the slug: 7 whose name still carries
        "Careers" after the wrapper comes off, one Portuguese "carreras", one with no wrapper.
        ``parse`` still prefers the posting's own ``hiringOrganization`` where there is one — a
        parent tenant's posting can name the subsidiary that owns it, and this cannot."""
        return self.url()

    def fetch_raw(self) -> Any:
        ids, titles = self._listing()
        if not ids:
            return {"ids": [], "postings": {}}
        # The tech gate reads the listing's title under the department that promotes most; an id
        # whose row states no plain title gates as None, which that department keeps
        # (`is_tech(None, _MOST_PROMOTING_DEPARTMENT)`), so every gated id has a title. No
        # ADR-0048 skip: the page is the only source of every other field (module docstring).
        wanted = self.tech_detail_wanted(
            ids, titles.get, lambda job_id: _MOST_PROMOTING_DEPARTMENT
        )
        postings = self.run_detail_pass(
            wanted, key_of=lambda job_id: job_id, what="detail pages"
        )
        if postings.missing:
            # Load-bearing detail pass for a Job the gate kept: `parse` builds no Job without its
            # page (ADR-0231) and drops it. That is a short list for a reason `harvest` cannot
            # see, which is exactly what ADR-0053 exists to travel alongside it.
            self.mark_truncated(
                f"{postings.missing}/{len(wanted)} detail pages could not be read"
            )
        gated = set(ids) - set(wanted)
        return {
            "ids": ids,
            "postings": postings,
            "gated_titles": {job_id: titles[job_id] for job_id in gated},
        }

    def _page(self, url: str) -> str:
        """GET one board page, refusing to follow a redirect.

        Jobvite answers a departed tenant with a 302 onto its own marketing page, which returns
        200 — so a following fetch would hand back a valid page with no postings on it and this
        Board would read as emptied rather than gone. Raising instead surfaces it as the
        per-company failure it is.
        """
        response = self._fetch(
            "GET",
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            timeout=30,
            allow_redirects=False,
        )
        location = response.headers.get("location") or ""
        if "invalid=1" in location:
            # Jobvite's own "no such tenant" (module docstring), raised in the shape
            # `board_failures.is_gone` matches so the Board earns ADR-0162 gone-strikes: 22
            # raises across runs 36200233818..36218633315 earned none.
            raise gone_board_error(
                f"{url} -> {response.status_code} {location}; tenant departed"
            )
        if response.status_code != 200:
            raise http.RequestsError(
                f"{url} -> {response.status_code} {location}".strip()
            )
        return response.text

    def _listing(self) -> tuple[list[str], dict[str, str]]:
        """Every posting id on this board, walking ``/search`` by its own next link, and the
        title each classic row's anchor states (for the tech gate).

        Ids only otherwise: five row templates are live and each hides a different field
        somewhere else, while the ``/{slug}/job/{id}`` path is in all five. Order is first sight,
        de-duplicated. A walk short of the board's own counter is walked again, up to
        :data:`_MAX_WALKS`, while each walk still finds new ids (module docstring). A Board still
        short while walks were finding ids is reported through
        ``mark_truncated_unless_negligible``; one whose last walk found nothing new is short
        stably, and is only logged, as it always was — nothing shows it is missing a posting.
        """
        ids: list[str] = []
        titles: dict[str, str] = {}
        stated = None
        grew = False  # a re-walk found ids the walks before it missed: the pagination is unstable
        for walk in range(_MAX_WALKS):
            before = len(ids)
            stated, ended = self._walk(ids, titles, first=not walk)
            if not ended or not stated or len(ids) >= stated:
                return ids, titles
            if walk and len(ids) == before:
                if grew:
                    break
                self._log.info(
                    f"{self.board_key()}: {len(ids)} of {stated} postings, and walk {walk + 1} "
                    "found no new id — a stable shortfall, left as read"
                )
                return ids, titles
            grew = grew or bool(walk)
        self.mark_truncated_unless_negligible(
            len(ids),
            stated,
            f"{len(ids)} of {stated} postings after {walk + 1} walks of an unstable listing — "
            "the rest is unread, not absent",
        )
        return ids, titles

    def _walk(
        self, ids: list[str], titles: dict[str, str], *, first: bool
    ) -> tuple[int | None, bool]:
        """One walk of ``/search``, adding unseen ids to ``ids`` and their anchor titles to
        ``titles``. Returns the counter's total and whether the walk reached its end — False
        when it stopped at the page cap (marked truncated) or on a page that named no job. A
        re-walk (``first`` False) logs none of what the first walk already said."""
        job_path = re.compile(rf"/{re.escape(self.slug)}/job/({_JOB_ID})")
        anchor = re.compile(
            rf'<a href="/{re.escape(self.slug)}/job/({_JOB_ID})"[^>]*>([^<]*)</a>'
        )
        seen = set(ids)
        url: str | None = self.url()
        pages, stated, slots_read = 0, None, 0
        served: set[tuple[str, ...]] = set()
        while url and pages < _MAX_PAGES:
            page = self._page(url)
            pages += 1
            if stated is None:
                stated = total_of(page)
            for job_id, text in anchor.findall(page):
                title = html_to_text(text)
                if title:
                    titles.setdefault(job_id, title)
            page_ids = job_path.findall(page)
            slots_read += len(page_ids)
            for job_id in page_ids:
                if job_id not in seen:
                    seen.add(job_id)
                    ids.append(job_id)
            match = _NEXT.search(page)
            # A next link to a page whose ids this walk already served is a loop: the same pages
            # would come back forever. Pages are keyed by their ids, not their URL, since a loop
            # can change the query string. (A page of ids seen on an earlier walk is not a loop:
            # this walk re-reads them.)
            repeated = tuple(page_ids) in served
            served.add(tuple(page_ids))
            if not match or repeated:
                if stated and not slots_read and first:
                    self.note_unreadable_board(
                        f"job links matching /{self.slug}/job/{{id}}",
                        f"none on a page whose counter states {stated}",
                    )
                    return stated, False
                if match and first:
                    self._log.info(
                        f"{self.board_key()}: next link offered on page {pages} but it served a "
                        f"page already read — walk stopped at {len(ids)} of {stated}"
                    )
                elif first and stated and pages < math.ceil(stated / _PAGE_SIZE):
                    # A template change that stops `_NEXT` matching would otherwise serve page 0
                    # alone as the whole Board, with nothing in the log to say so.
                    self._log.info(
                        f"{self.board_key()}: walk ended with no next link on page {pages} of "
                        f"the {math.ceil(stated / _PAGE_SIZE)} the counter implies — {len(ids)} of "
                        f"{stated} ids read"
                    )
                return stated, True
            href = match.group(1)
            url = (
                f"https:{href}"
                if href.startswith("//")
                else f"https://jobs.jobvite.com{href}"
            )
        self.mark_truncated(
            f"stopped at the {_MAX_PAGES}-page cap after {len(ids)} postings with a next link "
            f"still offered (the board's own counter stated {stated})"
        )
        return stated, False

    def job_url(self, job_id: str) -> str:
        return f"https://jobs.jobvite.com/{self.slug}/job/{job_id}"

    @staticmethod
    def _posting_of(page: str) -> dict | None:
        """The JobPosting fields a detail page carries, or None when it carries none.

        Prefers the schema.org JSON-LD block; falls back to the rendered ``jv-job-detail-*``
        blocks for the tenants whose template emits no JSON-LD at all. The fallback has no
        ``datePosted`` to give — the page does not render one — so those Jobs carry no
        ``posted_at``, which is a property of the surface rather than of this parse.
        """
        posting = find_job_posting(page)
        if posting is not None and posting.get("title"):
            return posting
        title = _HTML_TITLE.search(page)
        if not title:
            return None
        heading = title.group("title")
        text = html_to_text(heading.split("<", 1)[0]) or html_to_text(heading)
        posting: dict[str, Any] = {"title": text}
        description = _description_html(page)
        if description:
            posting["description"] = description
        meta = _HTML_META.search(page)
        if meta:
            # "Category | City, Region | Req.Num.: N" — the department is the first segment and
            # the place the second; a trailing requisition number is not either of them.
            segments = [html_to_text(s) for s in _SEPARATOR.split(meta.group(1))]
            segments = [s for s in segments if s and not s.startswith("Req.Num.")]
            if segments:
                posting["industry"] = segments[0]
            if len(segments) > 1:
                posting["_location"] = segments[1]
        return posting

    def detail_request(self, job_id: str) -> DetailRequest:
        # `?nl=1` is the page Jobvite's embed widget frames. A tenant that moved its career site
        # onto its own domain 302s the plain job page there (wedgewood, 2026-09-25), where no
        # posting is rendered; the `nl=1` page still answers 200 with the posting. Redirects are
        # refused so any that remain are labelled `HTTP 302`, not misread as an empty page.
        return DetailRequest(
            f"{self.job_url(job_id)}?nl=1",
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            options={"allow_redirects": False},
        )

    def read_detail(self, job_id: str, response: Any) -> dict:
        """One detail page's posting, or a loss named for a page that carries none.

        Load-bearing here — the listing carries no title, so a lost page is a dropped Job and a
        marked truncation — which makes "refused" versus "arrived and did not parse" the
        difference between waiting out an origin and fixing a parser.
        """
        posting = self._posting_of(response.text)
        if posting is None:
            raise DetailLost("no posting on a 200")
        return posting

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        postings = raw.get("postings") or {}
        gated_titles = raw.get("gated_titles") or {}
        jobs: list[Job] = []
        for job_id in raw.get("ids") or []:
            posting = postings.get(job_id)
            if not posting and job_id in gated_titles:
                # Gated out by title (`fetch_raw`): the listing's title is all there is, which is
                # enough for the full set and for the tech filter to drop it again.
                posting = {"title": gated_titles[job_id]}
            if not posting:
                # No page, no fields — not even a title. `fetch_raw` has already marked the
                # Board truncated for exactly this count.
                continue
            location = _location(posting)
            jobs.append(
                Job(
                    id=self.job_id(job_id),
                    ats=self.ats,
                    company=hiring_organization(posting.get("hiringOrganization"))
                    or self.company,
                    title=html_to_text(posting.get("title")),
                    location=location,
                    remote=is_remote(location),
                    department=(posting.get("industry") or "").strip() or None,
                    url=self.job_url(job_id),
                    posted_at=(posting.get("datePosted") or "").strip() or None,
                    scraped_at=scraped_at,
                    description=html_to_text(posting.get("description")),
                    employment_type=(posting.get("employmentType") or "").strip()
                    or None,
                    salary=self._salary_field(posting),
                )
            )
        return jobs

    def _salary_field(self, raw: dict) -> str | None:
        """``baseSalary`` as a display string, or None when the block is present but empty.

        Jobvite emits the ``MonetaryAmount`` skeleton on essentially every posting and leaves
        every field of it blank on most, so an amount is what makes it worth keeping — a bare
        currency or a bare "Annually" says nothing.
        """
        salary = raw.get("baseSalary")
        if not isinstance(salary, dict):
            return None
        value = salary.get("value") or {}
        low = str(value.get("minValue") or "").strip()
        high = str(value.get("maxValue") or "").strip()
        if not (low or high):
            return None
        amount = f"{low} - {high}" if low and high else (low or high)
        parts = [
            amount,
            str(salary.get("currency") or "").strip(),
            str(value.get("unitText") or "").strip(),
        ]
        return " ".join(p for p in parts if p)
