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

**The listing yields ids and nothing else, on purpose.** Five row templates are live and they
disagree about where every field sits: ``<td class="jv-job-list-name"><a>`` (classic),
``<div class="tr">`` with div cells (lhhcareers), an extra-column variant carrying
``jv-job-list-req``/``jv-job-list-type`` (von), an ``<a>`` that *wraps* the name element
(aryaka), and one with no anchor at all — ``<tr onclick="window.location.href='…'">`` (agscareer).
Each successive row-shaped parse this investigation tried returned **zero rows** on a different
19%, 15% and 1.4% of boards, and did so silently. The one invariant every template shares is the
``/{slug}/job/{id}`` path itself, so that is what is scanned for. Validated against the boards'
own counters: **395 of 434 exact, 6 short, 0 over** — no stray link is ever mistaken for a
posting.

**A short walk is not what ``distinct ids < counter total`` means.** All 6 short boards were
verified page by page: the counter is stable across every page, the last page is reached, and the
shortfall is Jobvite serving one posting in two slots. Re-measured 2026-09-07 on ``fprs``, which
is large enough that the effect is unmistakable: the counter says 1,933, the walk visits 1,933
slots across 39 pages, and **1,896 ids are distinct — 37 postings appear twice**. (These are
point-in-time counts on a live board; an earlier note cited ``cascade`` at 71/70 and its own
capture recorded 71/69, which is board churn, not disagreement about the mechanism.) So the count
is *not* wired to :meth:`~BaseScraper.mark_truncated` —
only actually stopping with a next link still on offer is (ADR-0053; its exclusion has no drain,
so a wrong mark is permanent).

**ADR-0111's alias dedupe does not apply here, and deliberately gets no override.** Every Board is
a path on one host, so :meth:`BaseScraper.alias_key`'s default returns ``jobs.jobvite.com`` for all
of them — which is the degenerate case its own docstring describes: the key is not itself a live
slug, so ``board_aliases.resolve`` labels the whole ledger ``migrated`` and returns nothing. Inert,
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

ADR-0048's ``needs_detail`` skip-list is deliberately **not** consulted, unlike eightfold's and
zwayam's. Those two skip the detail fetch for a Job whose description we already hold because
their *listing* still supplies title, location and the rest; here the listing supplies an id and
nothing else, so skipping the page would not save a request — it would drop the Job.

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

import re
from typing import Any

from headstart import http, log
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper, DetailLost, DetailRequest
from headstart.scrapers.job_posting_jsonld import find_job_posting, hiring_organization

_log = log.get(__name__)

#: Detail pages are 40-110 KB each and every one hits the same origin, so the fan-out stays
#: narrow. Also the async stream width (``BaseScraper.fan_out_async``).
_DETAIL_WORKERS = 6

#: Hard stop for the pagination walk. The largest board in the pool is 2,831 postings = 57 pages,
#: so this is ~3.5x headroom; it exists so a ``next`` link that ever pointed at itself could not
#: spin forever, not as a cap anyone is expected to reach.
_MAX_PAGES = 200

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
        ``headstart.company_name`` already models for eightfold and keka.

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
        ids = self._listing_ids()
        if not ids:
            return {"ids": [], "postings": {}}
        # No tech gate and no ADR-0048 skip: the listing states ids alone (module docstring).
        postings = self.run_detail_pass(
            ids, key_of=lambda job_id: job_id, what="detail pages"
        )
        if postings.missing:
            # Load-bearing detail pass: the listing carries no title, so `parse` cannot build a
            # Job without the page and drops it. That is a short list for a reason `harvest`
            # cannot see, which is exactly what ADR-0053 exists to travel alongside it.
            self.mark_truncated(
                f"{postings.missing}/{len(ids)} detail pages could not be read"
            )
        return {"ids": ids, "postings": postings}

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
        if response.status_code != 200:
            raise http.RequestsError(
                f"{url} -> {response.status_code} "
                f"{response.headers.get('location') or ''}".strip()
            )
        return response.text

    def _listing_ids(self) -> list[str]:
        """Every posting id on this board, walking ``/search`` by its own next link.

        Ids only: five row templates are live and each hides a different field somewhere else
        (module docstring), while the ``/{slug}/job/{id}`` path is in all five. Order is the
        board's own, de-duplicated — a posting can occupy two pagination slots.
        """
        job_path = re.compile(rf"/{re.escape(self.slug)}/job/({_JOB_ID})")
        url: str | None = self.url()
        ids: list[str] = []
        seen: set[str] = set()
        pages, stated = 0, None
        while url and pages < _MAX_PAGES:
            page = self._page(url)
            pages += 1
            if stated is None:
                stated = total_of(page)
            new = 0
            for job_id in job_path.findall(page):
                if job_id in seen:
                    continue
                seen.add(job_id)
                ids.append(job_id)
                new += 1
            match = _NEXT.search(page)
            # `new == 0` also stops the walk: a next link that returned nothing new is either the
            # end or a loop, and either way there is nothing further to read.
            if not match or not new:
                return ids
            href = match.group(1)
            url = (
                f"https:{href}"
                if href.startswith("//")
                else f"https://jobs.jobvite.com{href}"
            )
        self.mark_truncated(
            f"stopped at the {_MAX_PAGES}-page cap with a next link still offered"
        )
        _log.info(
            f"{self.board_key()}: hit the {_MAX_PAGES}-page walk cap after {len(ids)} postings "
            f"(the board's own counter stated {stated})"
        )
        return ids

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
        jobs: list[Job] = []
        for job_id in raw.get("ids") or []:
            posting = postings.get(job_id)
            if not posting:
                # No page, no fields — not even a title, which the listing never carried.
                # `fetch_raw` has already marked the Board truncated for exactly this count.
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
