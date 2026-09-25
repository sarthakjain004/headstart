"""JazzHR careers-board scraper (``{slug}.applytojob.com``).

Everything JazzHR publishes is server-rendered HTML; there is no JSON API to prefer. That is
measured, not assumed: a Chromium HAR capture driving a live board (2026-09-07, see
``docs/jazzhr/2026-09-07_surface-investigation.md``) made **119 requests** across the career
page, the embed listing and a job detail page, of which **17 were XHR/fetch and every one was
telemetry** — New Relic ``bam.nr-data.net``, Google Analytics, reCAPTCHA. Not one first-party
data call. The board's own ``/js/apply/jobs.js`` issues no request at all. So the two HTML
surfaces below are the whole surface.

**The listing is ``/apply/jobs``, the vendor's embed listing — not ``/apply/``, the career
page.** Three reasons, all measured over a 1,000-tenant random sample of
``data/ats-tenants-merged/jazzhr.csv`` (4,647 tenants):

* The career page is **per-tenant themed** — 903 responsive, 22 classic ``resumator-*``, and a
  tail of fully custom themes — so parsing it means maintaining a template zoo. ``/apply/jobs``
  renders one table (``<tr id="row_job_…">``) for every one of them.
* It carries two things the career page does not: the posting's ``resumator_department``
  (24.4% of 22,287 rows) and its internal ``job_{YYYYMMDDHHMMSS}_{KEY}`` id.
* It lists **exactly the same openings**. 918 of the 925 tenants that served a board agreed
  job-for-job; all 7 that differed did so by one, and every one was traced by hand to a link
  the career page carries *outside* its openings list — an evergreen talent-pool posting
  ("Don't see the job you're looking for?", "Submit Resume For Future Consideration",
  "Welcome To The Candidate Center") or, once, a non-job ``/apply/vendor/309`` link. The embed
  listing is the openings list; the career page is the openings list plus the tenant's prose.

**No cap and no pagination.** The largest board in the sample, ``milehighadjustershoustoninc``,
served all **3,450** postings in one 3.1 MB response in 0.63s, and ``/apply/`` returned the
identical 3,450 keys. Nothing here calls :meth:`~BaseScraper.mark_truncated`, because nothing
has been observed to truncate and the listing states no total to check a short answer against
(the trap ADR-0053's guards exist to avoid).

**A departed tenant answers 200, not 404.** Two shapes: the wildcard host serves
``JazzHR - Inactive Career Page`` (66 of 1,000), or ``/apply/`` 302s to the vendor's own
job-seeker page (9 of 1,000). Neither renders the ``jobs_table`` shell, which is what
``check_liveness.p_jazzhr`` keys on — the freshteam/zoho soft-404 precedent. A slug that was
never a tenant behaves the same way (wildcard DNS resolves, then 302s), so DNS never settles a
board here.

**Every field beyond title/location/department needs the per-job detail page.** The listing has
no description, employment type, experience or salary. The detail page states them two ways and
the HTML wins on both coverage and detail:

* ``id="resumator-job-{employment,type,experience}"`` — 400/400 of a re-fetched sample. The
  ``-type`` spelling is what fully custom themes emit, ``-employment`` what the stock ones do.
* A schema.org ``JobPosting`` JSON-LD — only **69.7%** of 1,526 pages, and *per posting*, not
  per tenant (163 of 717 multi-posting tenants had it on some jobs and not others). Its
  ``employmentType`` is also the coarser field: the schema enum collapses JazzHR's own
  "Contracted to Full Time" / "Part Time to Full Time" / "Temporary to Full Time" / "Full Time"
  into one ``FULL_TIME``.

So the HTML supplies ``employment_type``, ``experience`` and ``description``, and the JSON-LD is
read only for the two things HTML never states: ``datePosted`` and ``baseSalary``. Scored against
the JSON-LD on the 286 sampled pages that carry both, the HTML description matched exactly on 270
and to within 2% on all 286 (the rest is entity/whitespace handling), and the experience label
matched on 286/286.

**``posted_at`` is the JSON-LD's ``datePosted`` or nothing — deliberately not the listing's own
timestamp.** Every listing row carries ``job_{YYYYMMDDHHMMSS}_…``, which is tempting because it
is 100% present, but it is the *record creation* time and not the publication date: across the
1,063 sampled pages carrying both, it was earlier than ``datePosted`` on 311 and **later on
zero**, sometimes by years (``job_20171027…`` on a posting dated 2026-06-17). Using it would
silently age ~29% of postings. So 30.3% of Jobs here carry no ``posted_at``, and that is the
honest answer rather than a wrong one.

``Job.salary`` is assembled from the JSON-LD ``baseSalary`` (a real ``MonetaryAmount``:
currency + ``unitText`` HOUR/YEAR + min/max) into the ``"MIN-MAX CUR UNIT"`` shape
``salary.to_field`` spells — the one ``salary.from_field`` reads for rippling and ashby. jazzhr
is registered for the bare unit words there rather than on the generic reader, which annualizes
none of them and therefore rejected every hourly figure. Present on 25.8% of detail pages;
368 of the 393 real values in the sample parse, and the 25 that don't are the plausibility
bound correctly rejecting tenant data-entry errors (an hourly rate typed under ``unitText:
YEAR``, e.g. "35-60 USD YEAR").

``remote`` stays :func:`~headstart.models.is_remote` on the listing location. JazzHR does emit
schema.org ``jobLocationType: TELECOMMUTE`` on 145 of the 1,063 JSON-LD pages, and it was
checked rather than assumed: on 60 of 60 sampled, the listing location already read exactly
"Remote", so reading it would add nothing.
"""

from __future__ import annotations

import re
from typing import Any

from headstart import salary
from headstart.models import Job, html_to_text, is_remote
from headstart.network import http
from headstart.scrapers.base import BaseScraper, DetailLost, DetailRequest
from headstart.scrapers.job_posting_jsonld import find_job_posting, jsonld_nodes

#: Detail-pass width. Every tenant is a subdomain of one Cloudflare-fronted origin, so this is a
#: bound on that origin rather than on a per-tenant host. 8 is the base default and is well inside
#: what the investigation measured: ~6,100 requests at 10-way concurrency across ~1,900 distinct
#: tenants on 2026-09-07 returned zero 403s, zero 429s and zero 5xx.
_DETAIL_WORKERS = 8

#: One listing row of the ``/apply/jobs`` table. Anchored on ``<tr`` on purpose: the same page
#: also renders a mobile layout that repeats every posting as ``<div id="row_job_…"
#: class="jobs_row">``, so an id-only match returns each job twice (60 elements for 30 postings on
#: ``10pearls``). Anchoring on the desktop table's row element makes the dedupe unnecessary.
_ROW = re.compile(r'<tr id="row_job_\w+"[^>]*>(.*?)</tr>', re.DOTALL)
_ROW_LINK = re.compile(
    r'href="/apply/jobs/details/([A-Za-z0-9]+)[^"]*"[^>]*>(.*?)</a>', re.DOTALL
)
_ROW_DEPARTMENT = re.compile(
    r'<span class="resumator_department">(.*?)</span>', re.DOTALL
)
_ROW_CELLS = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)

#: The detail page's labelled attributes. Both spellings are real: stock themes emit
#: ``resumator-job-employment``, fully custom ones ``resumator-job-type``. The closing-tag
#: alternation excludes ``</i>`` deliberately — the responsive theme puts a Font Awesome
#: ``<i>`` icon inside the element, and a bare ``</\w+>`` would stop on that and capture nothing.
_ATTRIBUTE = re.compile(
    r"""id=['"]resumator-job-(employment|type|experience)['"][^>]*>(.{0,400}?)</(?:div|h2|h3|p|span)>""",
    re.DOTALL,
)
#: Department is the one attribute the responsive theme labels only by ``title=`` (no id), while
#: the classic theme gives it an id like the others — so one pattern covers both.
_DEPARTMENT = re.compile(
    r"""(?:id=['"]resumator-job-department['"]|title="Department")[^>]*>(.{0,300}?)</(?:div|h2|h3|p|span)>""",
    re.DOTALL,
)
#: The classic theme prefixes each attribute's value with its own label ("<strong>Type: </strong>").
_ATTRIBUTE_LABEL = re.compile(r"<strong>.*?</strong>", re.DOTALL)

_DESCRIPTION_ID = re.compile(r'id="(?:job-description|resumator-job-description)"')
_DIV_TAG = re.compile(r"<div\b|</div>", re.IGNORECASE)


def _description_html(page: str) -> str | None:
    """Inner HTML of the description element, found by counting ``<div>`` nesting from its
    opening tag.

    The description is a rich-text blob with arbitrary nested markup, so the end of the element
    cannot be matched by a regex — and the sibling that follows it differs per theme, so
    anchoring on that is what breaks when a tenant restyles. Counting the tags is theme-agnostic:
    it found a description on 400/400 pages of a re-fetched sample, against 69.7% for the JSON-LD.
    """
    marker = _DESCRIPTION_ID.search(page)
    if not marker:
        return None
    start = page.find(">", marker.end()) + 1
    depth, pos = 1, start
    while depth:
        tag = _DIV_TAG.search(page, pos)
        # An unclosed element: keep the text there is rather than dropping the description.
        if not tag:
            return page[start:]
        depth += 1 if tag.group(0).lower().startswith("<div") else -1
        pos = tag.end()
    return page[start : pos - len("</div>")]


def _attributes(page: str) -> dict[str, str]:
    """``{"employment": …, "experience": …, "department": …}`` from the detail page's labelled
    attribute elements — whichever of the three theme spellings this tenant uses."""
    found: dict[str, str] = {}
    for kind, chunk in _ATTRIBUTE.findall(page):
        value = html_to_text(_ATTRIBUTE_LABEL.sub(" ", chunk))
        if value:
            found["employment" if kind in ("employment", "type") else kind] = value
    department = _DEPARTMENT.search(page)
    if department:
        value = html_to_text(_ATTRIBUTE_LABEL.sub(" ", department.group(1)))
        if value:
            found["department"] = value
    return found


def _row_title(row: tuple[str, str, str | None, str | None]) -> str:
    """A listing row's title, for the tech gate. Named rather than `row[1]`, because this gate is
    a measured tolerance rather than an exact one — an index that silently drifted onto
    `location` would classify on the wrong string and nothing would raise."""
    return row[1]


def _row_department(row: tuple[str, str, str | None, str | None]) -> str | None:
    """A listing row's department, for the tech gate. See :func:`_row_title`."""
    return row[3]


def _rows(listing: str) -> list[tuple[str, str, str | None, str | None]]:
    """``(key, title, location, department)`` per posting on the ``/apply/jobs`` table.

    A page with the table shell but no rows is a live board with nothing open and returns ``[]``.
    A page *without* the shell is a departed tenant, and :meth:`JazzHRScraper._listing` raises on
    it rather than letting it reach here — see the reasoning there.
    """
    rows: list[tuple[str, str, str | None, str | None]] = []
    for chunk in _ROW.findall(listing):
        link = _ROW_LINK.search(chunk)
        if not link:
            continue
        department = _ROW_DEPARTMENT.search(chunk)
        cells = _ROW_CELLS.findall(chunk)
        rows.append(
            (
                link.group(1),
                html_to_text(link.group(2)) or "",
                html_to_text(cells[-1]) if len(cells) > 1 else None,
                html_to_text(department.group(1)) if department else None,
            )
        )
    return rows


class JazzHRScraper(BaseScraper):
    ats = "jazzhr"
    # scraper: f"https://{slug}.applytojob.com/apply/{key}" (job_url below). The board links
    # to /apply/{key}/{Title-Slug}, but the title slug is decorative — verified live
    # 2026-09-07 that /apply/{key} alone serves the posting (200 on 1,526 of 1,527 fetched that
    # way, the one miss a transport timeout) and that a WRONG title slug 200s too, so the key is
    # the whole route. A key that no longer exists answers 410, which is what makes this link
    # checkable at all. Keys are 10 alphanumerics on most tenants and a long hex string on a few
    # (both real: `/apply/KqDYKxUH4p/…` and `/apply/07350d2d7f03…/…`), hence the loose class.
    url_shape = r"https://[\w-]+\.applytojob\.com/apply/[A-Za-z0-9]+"
    detail_workers = _DETAIL_WORKERS  # also the async stream width (base.fan_out_async)
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    def url(self) -> str:
        return f"https://{self.slug}.applytojob.com/apply/jobs"

    def job_url(self, key: str) -> str:
        """A posting's own page. The board links to ``/apply/{key}/{Title-Slug}``, but the slug is
        decorative — ``/apply/{key}`` alone serves the same page (200 on 1,526 of 1,527 fetched,
        the one failure a transport timeout), and even a deliberately wrong title slug 200s. So
        this is the shape both the fetch and ``Job.url`` use, and the one
        ``scripts/eval/verify_filters.py`` asserts."""
        return f"https://{self.slug}.applytojob.com/apply/{key}"

    def _listing(self) -> str:
        """The board's ``/apply/jobs`` page, or a raise if this tenant has departed.

        A departed JazzHR tenant does not 404 — it answers **200** with a parked or job-seeker
        page carrying no ``jobs_table`` shell (75 of 1,000 tenants measured,
        `docs/jazzhr/artifacts/2026-09-07_liveness-probe-1000-tenants.txt`). Parsing that yields zero rows,
        which is indistinguishable from a live board with nothing open, and a whole-and-empty
        Board is the shape that deletes a company's postings: ADR-0083 withholds the eviction for
        exactly one scrape, then ``sync`` evicts every row.

        The liveness probe already tells the two apart on this same marker, but it runs on its own
        TTL cadence — a tenant that departs between sweeps reaches the scrape with a stale `live`
        verdict, which is precisely when this guard has to be the one that fires. Raising here
        makes it a Board error, so ADR-0053 drops the Board out of the eviction scope instead.
        `jobvite._page` takes the same position on its own 302-to-200 dead tenants.
        """
        listing = self._get()
        if 'id="jobs_table"' not in listing:
            raise http.RequestsError(
                f"{self.url()} -> 200 without the jobs_table shell; tenant departed"
            )
        return listing

    def fetch_raw(self) -> Any:
        # Every listed posting gets its detail page, with no ADR-0048 `needs_detail` skip. That
        # optimisation is only safe where the detail fetch supplies the description and nothing
        # else — true for eightfold, false here: this page is also the only source of
        # `employment_type`, `experience`, `posted_at` and `salary`, none of which the ADR-0050
        # description store holds. Skipping it for an already-described Job would blank four
        # fields that had values. Zoho hit exactly this and made the same call for the same
        # reason (`zoho.py fetch_raw` — gating on description presence made Salary structurally
        # invisible on ~60% of its jobs).
        listing = self._listing()
        rows = _rows(listing)
        # `_rows` skips any `row_job_` <tr> whose posting link it cannot read, and the skip is
        # the one thing on this page that can go wrong without anything failing: the shell is
        # present, `_listing` is satisfied, and a Board whose markup moved parses to zero keys —
        # which reads downstream as a live Board with nothing open, and costs it every indexed
        # row two runs later (ADR-0083). Counted against the shell's own rows because that is the
        # only total this listing states. Not marked truncated: this module's 1,000-tenant sweep
        # never saw a link-less row, so how many are benign is unmeasured, and a truncation guard
        # built on a guess is the one this repo has learned not to ship.
        listed = len(_ROW.findall(listing))
        self.note_unread_rows(listed - len(rows), listed, "carried no posting link")
        # The tech gate (ADR-0017). `_rows` states title and department per listing row, so the
        # gate asks `filter_tech`'s question before spending a page on the answer. Unlike
        # workday's, this is a *measured* tolerance rather than exactness: `parse` lets the
        # detail page's department win, so a tenant whose listing omits a department its detail
        # supplies could disagree. Measured live 2026-09-17 over 10 Boards / 1,748 postings,
        # chosen from the corpus as the ones where `department` does the most work (on
        # `vyvebroadband` a department-blind gate drops 30 of 31 tech postings, on
        # `idsinternational` 24 of 44): 845 kept by the gate, 845 by the filter, zero
        # disagreements. Re-check it if this page's markup moves.
        #
        # Reported, not marked truncated: a missing detail page costs this Job its description
        # and derived fields, but the Job itself is still listed and still emitted, so the
        # Board's list is whole (ADR-0053 is about the list, not the fields).
        details = self.run_detail_pass(
            rows,
            key_of=lambda row: row[0],
            what="detail pages",
            title_of=_row_title,
            department_of=_row_department,
        )
        return {"listing": listing, "details": details}

    def detail_request(
        self, row: tuple[str, str, str | None, str | None]
    ) -> DetailRequest:
        return DetailRequest(self.job_url(row[0]))

    def read_detail(
        self, row: tuple[str, str, str | None, str | None], response: Any
    ) -> str:
        """The posting's page, unless it carries none of what `parse` reads off one — no
        description, no labelled attribute and no JSON-LD posting. Such a 200 used to reach the
        Jobs unlabelled and add nothing to them; now it is a loss named on the gap line, so a 403
        across every page and a parser that stopped recognising them are no longer one number."""
        page = response.text
        if (
            _description_html(page) is None
            and not _attributes(page)
            and find_job_posting(page) is None
        ):
            raise DetailLost("no posting on a 200")
        return page

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        # raw is fetch_raw's {listing, details}; a bare string means no detail pass ran
        listing, details = (
            (raw, {}) if isinstance(raw, str) else (raw["listing"], raw["details"])
        )
        # The listing page's <title> is the vendor's ("JazzHR » Job Listings"), so its schema.org
        # Organization blob is the only place the tenant's own name appears — and it is there:
        # 300/300 boards probed 2026-09-07, properly cased and punctuated ("TWD Technologies
        # Ltd." for the slug `twd`). That makes `board_page()`/`company_name` unnecessary here.
        company = (
            next(jsonld_nodes(listing, "Organization"), {}).get("name") or ""
        ).strip()
        jobs: list[Job] = []
        for key, title, location, department in _rows(listing):
            page = details.get(key)
            attributes = _attributes(page) if page else {}
            posting = find_job_posting(page) if page else None
            jobs.append(
                Job(
                    id=self.job_id(key),
                    ats=self.ats,
                    company=company or self.company,
                    title=title,
                    location=location,
                    remote=is_remote(location),
                    # The detail page wins: it states a department on postings whose listing row
                    # leaves it blank far more often than the reverse (117 vs 8, of 1,526 paired).
                    department=attributes.get("department") or department,
                    url=self.job_url(key),
                    posted_at=(posting or {}).get("datePosted") or None,
                    scraped_at=scraped_at,
                    description=html_to_text(_description_html(page)) if page else None,
                    experience=attributes.get("experience"),
                    employment_type=attributes.get("employment"),
                    salary=self._salary_field((posting or {}).get("baseSalary")),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        """``Job.salary`` as ``"MIN-MAX CUR UNIT"`` from the JSON-LD ``baseSalary`` MonetaryAmount.

        Built by :func:`headstart.salary.to_field`; :func:`headstart.salary.from_field` reads it
        for jazzhr with the bare unit words, which is how an hourly figure gets annualized at
        all. A single-valued amount — a fixed rate with no range, 33 of the 393 in
        the sample — keeps the same shape minus the range, which that parser also handles.
        """
        if not isinstance(raw, dict):
            return None
        value = raw.get("value")
        if not isinstance(value, dict):
            return None
        unit = str(value.get("unitText") or "").strip()
        currency = str(raw.get("currency") or "").strip()
        low, high = value.get("minValue"), value.get("maxValue")
        if low is None and high is None:
            low = high = value.get("value")
        if low is None and high is None:
            return None
        return (
            salary.to_field(
                low if low is not None else high,
                high if low is not None else None,
                currency,
                unit,
            )
            or None
        )
