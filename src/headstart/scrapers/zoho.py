"""Zoho Recruit career-site scraper.

Zoho server-renders the job list into the careers page as an HTML-entity-encoded
JSON array inside `<input type="hidden" value="[...]" id="jobs">` (value before id —
that order is what `JOBS_INPUT` relies on). There is no XHR or CSRF handshake for the
listing — we GET the page and extract that array.

A Zoho company's `slug` is its full careers host, e.g. "pnbcsl.zohorecruit.in"
(the data center varies: .in / .com / .eu), so the slug carries the right host.

**Known limit, confirmed not a bug in this scraper**: the public, unauthenticated career-site
widget embeds at most ~750 jobs into that one response, with no working pagination mechanism
this scraper (or any unauthenticated HTTP client) can reach. Confirmed 2026-08-22 by direct
investigation, not assumed: 3 independent tenants in a 3,000-board sample each landed on exactly
750 (the sample-wide maximum — nothing observed exceeds it); URL query-string variants (page,
offset, start, fromIndex, pageIndex) never changed the response; the page's own front-end JS
(`career-website-common.js`) reads jobs exclusively from this server-embedded blob with no
follow-up AJAX call for more; no field anywhere in the page (`#jobs`, `#meta`, `#pageJson`,
`#moduleMeta`) reveals a true total distinct from what's embedded, so a board with exactly 750
real openings and one with 5,000 (750 shown) are indistinguishable from here. Real pagination
exists only in Zoho Recruit's authenticated private API (`fromIndex`/`toIndex` on `getRecords`,
per Zoho's own public docs), which needs a per-tenant OAuth token this scraper has no way to
obtain for the thousands of unaffiliated companies it reads — a board over the ceiling silently
loses the excess here, not from a defect in this file. See docs/salary-extraction/zoho.md's
"Post-merge correction" section for the full writeup and the open question of whether pursuing a
fix (a headless browser, or per-tenant API access) is worth its cost.

**There is a second listing surface this scraper does not read** (2026-09-07): an RSS feed at
`/jobs/{Portal}/rss`, named by the tenant's own `robots.txt`, which the investigation above never
checked. It does *not* beat the ceiling — 9 of the 10 at-ceiling boards have it disabled and the
tenth returns fewer items than its widget — so the paragraph above stands. What it does show is
that the widget can omit jobs for reasons unrelated to the cap: a majority of small boards that
have a feed serve fewer jobs from the widget than from the feed, by up to 9x. So the count this
file returns is not a reliable board size at the small end. See
docs/zoho/2026-09-07_the-rss-second-listing-surface.md.

**The company** is the page's own ``org_info.company_name`` when the tenant set a real one, else
the careers page ``<title>`` read through `company_name`'s zoho patterns ("Jobs at MasonBlue
Technologies, LLC", "Careers @ thinkbridge"), else whatever ``org_info`` holds, else the ledger's.
Of the 51 Boards serving an identifier on 2026-09-24, ``org_info`` held that identifier on most
("agrocommercialbyliotis"); the title is on the page this scrape already fetches, so it costs
nothing.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from headstart import company_name, log
from headstart.fetcher import Fetcher
from headstart.models import Job, host_of, html_to_text
from headstart.scrapers.base import BaseScraper, DetailLost, DetailRequest

_log = log.get(__name__)

#: The widget embeds at most ~this many jobs in one response and offers no working
#: pagination; the docstring above records it as the sample-wide maximum.
_EMBED_CEILING = 750

#: The listing's hidden ``<input id="jobs">``. Public: the liveness probe counts the same
#: input off the same page (ADR-0203).
JOBS_INPUT = re.compile(r'value="([^"]*)"\s+id="jobs"')
_CONFIG_AFTER_JOBS = re.compile(r'id="jobs">\s*<input[^>]*\bvalue="([^"]*)"')
_SLUG = re.compile(r"[^A-Za-z0-9]+")
# a job's detail page embeds its full record as `var jobs = JSON.parse('…')` — a JS
# single-quoted string (\xNN hex escapes) wrapping JSON
_DETAIL_JOBS = re.compile(r"jobs\s*=\s*JSON\.parse\('((?:[^'\\]|\\.)*)'\)")
_JS_ESCAPE = re.compile(r"\\x([0-9a-fA-F]{2})|\\(.)")


class _PostingClosed(DetailLost):
    """The detail page says the posting is gone: a closure, not a gap in the read."""

    def __init__(self) -> None:
        super().__init__("posting explicitly unavailable")


#: The verdict a closed posting's detail page renders in place of its record, in the Board's
#: language — each seen live 2026-09-25 (a sweep of /jobs/Careers/{unknown id} across the
#: ledger; the English and Portuguese ones also on listed ids, harrisonconsultingsolutions and
#: resourceit). Not "this page is currently unavailable.": that is the .com data centre's answer
#: to a throttled client, served for live postings too.
_UNAVAILABLE_VERDICTS = (
    "This job posting is no longer available.",
    "A postagem desta vaga não está mais disponível.",
    "Esta vaga de emprego já não está disponível.",
    "Este anuncio de empleo ya no está disponible.",
    "Cette offre d’emploi n’est plus disponible.",
    "Dieses Jobangebot ist nicht mehr verfügbar.",
    "Questa pubblicazione di lavoro non è più disponibile.",
    "Deze vacature is niet langer beschikbaar.",
    "Denna jobbpublicering är inte längre tillgänglig.",
    "Dette jobopslag er ikke længere tilgængeligt.",
    "Ta oferta pracy nie jest już dostępna.",
    "Ez a meghirdetett állás már nem érhető el.",
    "Ovaj oglas za posao više nije dostupan.",
    "Эта вакансия больше не доступна.",
    "Bu iş gönderesi artık kullanılamıyor.",
    "لم تعد نشرة الوظائف هذه متاحة.",
    "此职位发布不再可用。",
    "この求人は終了しています。",
)
#: The loss label for the .com data centre's throttle shell (a 302 to /html/portal.html reading
#: "this page is currently unavailable."), measured 2026-09-25 after ~1,000 requests from one IP
#: and lifting within ~7 min. It says nothing about the posting, so the Job stays; the label keeps
#: it apart from a page shape that moved, so a CI gap line can say whether it is the CI-only
#: "no jobs blob" loss (``docs/zoho/2026-09-25_closed-posting-shells.md``).
_THROTTLE_LOSS = ".com throttle shell (page currently unavailable)"
_THROTTLE_SHELL = "this page is currently unavailable."
_DETAIL_WORKERS = (
    6  # detail pages are ~1.7MB each — bandwidth, not rate limits, is the constraint
)
#: State/City/Country values that carry no place information — filtered out of the joined
#: `location`, not just left to win the old truthy-`or` (14 State occurrences measured, and the
#: empty segment a trailing-comma City leaves behind after the join splits it back apart).
_JUNK_LOCATION_SEGMENTS = {"", ".", "-", "--"}


def _js_unescape(s: str) -> str:
    """Decode the JS single-quoted-string layer: \\xNN hex escapes, \\<char> pass-through."""
    return _JS_ESCAPE.sub(
        lambda m: chr(int(m.group(1), 16)) if m.group(1) else m.group(2), s
    )


def _zoho_location(
    city: str | None, state: str | None, country: str | None
) -> str | None:
    """``City, State, Country`` for the served ``location`` — replaces the old ``City or (State,
    Country)`` fallback, which discarded a real Country on 85.69% of jobs (13,759/16,056) whenever
    City was present, 168 of them invisible to ``headstart.geo.where("india")`` as a result
    (audit: experiment/location-audit-2026-08-25/zoho.md).

    Joined raw, then re-split on comma and de-duped/filtered per segment — the same technique
    darwinbox and keka already use for their own location strings. One pass over the joined
    string, rather than pairwise checks between City/State/Country, clears three things at once:
    junk segments (".", "-", "--" — 14 State values), an empty segment left by a City that itself
    ends in a comma ("Hyderabad,"), and a State or Country that's already present verbatim inside
    City — both the simple case (City == State, 743 jobs, "Riyadh, Riyadh") and the fixture's own
    pnbcsl record, whose City lists a dozen cities including "Delhi", its own State value.
    """
    raw = ", ".join(v for v in (city, state, country) if v and v.strip())
    segments: list[str] = []
    seen: set[str] = set()
    for seg in raw.split(","):
        text = seg.strip()
        key = text.lower()
        if not text or text in _JUNK_LOCATION_SEGMENTS or key in seen:
            continue
        seen.add(key)
        segments.append(text)
    return ", ".join(segments) or None


def _merge_detail(record: dict, detail: dict | None) -> dict:
    """The listing record, overlaid with the detail record's fields wherever the detail page
    returned a truthy value. The detail page is already fetched for every published job (see
    ``fetch_raw``'s docstring) and measured a strict superset over the listing — zero value
    conflicts, zero fields lost across 205 paired tenants (experiment/location-audit-2026-08-25/
    zoho.md) — so this recovers ``Date_Opened`` (+49.3pp), ``Work_Experience`` (+31.2pp),
    ``State`` (+37.6pp) and ``Industry`` (+20.0pp) for free. Falls back to the bare listing record
    when the detail fetch failed or found nothing."""
    if not detail:
        return record
    merged = dict(record)
    merged.update({k: v for k, v in detail.items() if v not in (None, "", [], {})})
    return merged


def _description_text(record: dict) -> str | None:
    """Job_Description with Salary/Currency appended when present (unchanged from PR #238's
    behaviour) — Salary/Currency are free-text, per-tenant strings ("5-10 Lakhs", "DOE"), not a
    clean structured field, so they ride along in the description text for Tier-2 mining too, the
    same treatment smartrecruiters' customField compensation gets in ``smartrecruiters.py``, on
    top of now also feeding the structured ``Job.salary`` field (`_salary_field`)."""
    description = record.get("Job_Description") or ""
    comp = " ".join(
        f"{label}: {value}"
        for label, value in (
            ("Salary", record.get("Salary")),
            ("Currency", record.get("Currency")),
        )
        if value
    )
    combined = f"{description} {comp}".strip() if comp else description
    return combined or None


class ZohoScraper(BaseScraper):
    ats = "zoho"
    url_shape = r"https://[^/]+/jobs/Careers/\d+/.+"
    detail_workers = _DETAIL_WORKERS  # also the async stream width (base.fan_out_async)
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    def __init__(
        self, slug: str, company: str | None = None, fetcher: Fetcher | None = None
    ) -> None:
        super().__init__(slug, company, fetcher)
        # Listed ids whose detail page says the posting is gone, filled by `read_detail`.
        self._unavailable_ids: set[str] = set()

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        # Host only, e.g. acme.zohorecruit.in — the same normalisation personio needs, for the
        # same reason: `url()` appends `/jobs/Careers`, so a stored job deep link would put that
        # suffix inside the path or query and fetch something that is not the board. Zoho's
        # ledger carries 44 pathy / 19 query rows; none is live today, so this is a latent
        # version of the bug that cost personio 678 ParseErrors a run, not an active one.
        return host_of(url)

    def url(self) -> str:
        return f"https://{self.slug}/jobs/Careers"

    def job_url(self, jid: str, title: str) -> str:
        """The served link, with a title slug and ``source`` param :meth:`detail_request` (the
        fetch route for the same posting) does not carry — the two differ on purpose, so this
        is its own formula rather than a wrapper around that one (ADR-0153)."""
        return f"https://{self.slug}/jobs/Careers/{jid}/{_SLUG.sub('-', title)}?source=CareerSite"

    def fetch_raw(self) -> Any:
        # Every published, non-locked job gets a detail-page fetch — not just the ones whose
        # listing lacks Job_Description. This used to be gated on a missing description (some
        # tenants configure the careers site without that column, 28 of 71 in the corpus), but
        # Salary/Currency live ONLY on the detail page (`_detail_record_of`'s docstring), never on
        # the listing, so gating on description presence meant the ~60% of jobs whose listing
        # already carries a description (live-measured 2026-08-24: 150 tenants sampled, 130
        # successfully probed) never had
        # their detail page fetched at all — Salary was structurally invisible for them,
        # independent of any extraction bug. User decision 2026-08-24: pay the bandwidth cost
        # (detail pages are ~1.7MB each) for full Salary coverage rather than leave the gap.
        page = self._get()
        ids = [
            r["id"]
            for r in self._records(page)
            if r.get("id") and not r.get("Is_Locked") and r.get("Publish", True)
        ]
        # No tech gate: a department-blind gate would drop 47.4% of zoho's tech postings
        # (ADR-0166). No held-description skip either: a stored description does not hold the
        # Salary above.
        self._unavailable_ids.clear()
        details = self.run_detail_pass(
            ids, key_of=lambda job_id: job_id, what="detail pages"
        )
        return {
            "page": page,
            "details": details,
            "unavailable": frozenset(self._unavailable_ids),
        }

    @staticmethod
    def _records(page: str) -> list[dict]:
        """The job records embedded in a careers/detail page's jobs `<input>`.

        A page without the input returns ``[]`` — that is what an empty board serves. A page
        *with* the input whose JSON will not parse raises instead: that is Zoho changing its
        page shape under us, and swallowing it would read as every zoho board emptying at
        once — sync would evict all their rows as delistings (the eightfold-flap failure
        class), with nothing in any log saying why.
        """
        match = JOBS_INPUT.search(page)
        if not match:
            return []
        return json.loads(html.unescape(match.group(1)))

    def detail_request(self, job_id: str) -> DetailRequest:
        return DetailRequest(f"https://{self.slug}/jobs/Careers/{job_id}")

    def read_detail(self, job_id: str, response: Any) -> dict:
        try:
            return self._detail_record_of(response.text)
        except DetailLost as lost:
            if isinstance(lost, _PostingClosed):
                self._unavailable_ids.add(job_id)
            raise

    @staticmethod
    def _detail_record_of(page: str) -> dict:
        """The job record embedded in a detail page. Salary/Currency, a fuller State, Date_Opened
        and Work_Experience all live on the detail page at meaningfully higher coverage than the
        listing (found via a code-review-triggered re-probe on PR #238, after an earlier check
        against the listing wrongly called Salary a dead end; the wider field-by-field gap
        measured in experiment/location-audit-2026-08-25/zoho.md). ``parse()`` merges this over
        the thinner listing record (``_merge_detail``) rather than reading only a computed
        description string, so every field the detail page carries gets a chance to reach the Job.

        Raises :class:`DetailLost` naming which of five ways the page carried no record, so the
        Board's gap line reports the shape of the failure and not only its size — a page shape
        that moved and a page that never arrived are one count otherwise."""
        m = _DETAIL_JOBS.search(page)
        if not m:
            if any(verdict in page for verdict in _UNAVAILABLE_VERDICTS):
                raise _PostingClosed()
            if _THROTTLE_SHELL in page:
                raise DetailLost(_THROTTLE_LOSS)
            raise DetailLost("no jobs blob on the page")
        try:
            records = json.loads(_js_unescape(m.group(1)))
        except json.JSONDecodeError:
            raise DetailLost("unparseable jobs blob") from None
        if not records:
            raise DetailLost("empty jobs blob")
        return records[0]

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        # raw is fetch_raw's {page, details, unavailable}; a bare page string means no detail pass
        page, details, unavailable = (
            (raw, {}, frozenset())
            if isinstance(raw, str)
            else (raw["page"], raw["details"], raw.get("unavailable", frozenset()))
        )
        records = self._records(page)
        if not records:
            return []
        if len(records) >= _EMBED_CEILING:
            # The docstring calls this out as silent, and it was: a board with exactly 750 real
            # openings and one with 5,000 look identical from here, and nothing said which run
            # hit it. Not mark_truncated — the widget exposes no true total to compare against,
            # so landing on the ceiling is strong evidence, not proof, and ADR-0053 exclusion
            # has no drain.
            _log.info(
                f"{self.board_key()}: {len(records)} records, at or over the ~{_EMBED_CEILING} "
                "widget ceiling — anything past it is unread, not absent"
            )

        company = self._board_company(page)
        jobs: list[Job] = []
        for r in records:
            if r.get("Is_Locked") or not r.get("Publish", True):
                continue
            jid = r.get("id")
            if not jid:
                continue
            # The listing still carries it but its own page says it is gone: a closure, so no
            # Job, and the Board stays authoritative (not `mark_truncated`) so eviction sees the
            # id absent and ADR-0083 evicts it on the second consecutive absence. Measured
            # 1,247-1,953 a run on 23-31 Boards (docs/pipeline/2026-09-24_five-run-log-review.md).
            if jid in unavailable:
                continue
            # The detail record wins field-by-field when it landed — measured a strict superset
            # over the listing (`_merge_detail`'s docstring) — and falls back to the bare listing
            # record if the detail fetch failed.
            detail = details.get(jid)
            d = _merge_detail(r, detail)
            title = (r.get("Posting_Title") or r.get("Job_Opening_Name") or "").strip()
            # Except for the description of a Job the store already holds (ADR-0208): the
            # listing renders it differently from the detail page, so a failed detail would
            # replace the held text with another rendering of the same posting, and flip it back
            # the next time the detail lands. No text here keeps the held one.
            held = not self.needs_detail(jid)
            description = (
                None if held and not detail else html_to_text(_description_text(d))
            )
            jobs.append(
                Job(
                    id=self.job_id(jid),
                    ats=self.ats,
                    company=company,
                    title=title,
                    location=_zoho_location(
                        d.get("City"), d.get("State"), d.get("Country")
                    ),
                    remote=bool(d.get("Remote_Job")),
                    department=(d.get("Industry") or "").strip() or None,
                    url=self.job_url(jid, title),
                    posted_at=d.get("Date_Opened") or None,
                    scraped_at=scraped_at,
                    description=description,
                    experience=d.get("Work_Experience"),
                    employment_type=d.get("Job_Type"),
                    salary=self._salary_field(d),
                )
            )
        return jobs

    def _salary_field(self, raw: dict) -> str | None:
        """``Job.salary`` from the merged detail record's ``Salary`` (plus ``Currency`` when
        present) — e.g. "250,000 - 300,000 USD" — so ``salary.extract``'s field tier and the
        served ``salary`` display column (ADR-0019) stop being permanently empty for zoho.
        Previously ``Salary``/``Currency`` were only ever spliced into the description text; the
        value itself never reached ``Job.salary`` (audit:
        experiment/location-audit-2026-08-25/zoho.md). This is strictly additive — the splice
        into the description (``_description_text``) stays, as a fallback for `salary.extract`'s
        description-mining tier on any tenant's phrasing the field tier's parser doesn't handle."""
        salary_text = (raw.get("Salary") or "").strip()
        if not salary_text:
            return None
        currency_text = (raw.get("Currency") or "").strip()
        return f"{salary_text} {currency_text}" if currency_text else salary_text

    def _board_company(self, page: str) -> str:
        """The Board's company (module docstring): a real ``org_info`` name, else the page title's,
        else the ``org_info`` identifier, else the ledger's."""
        stated = self._company_name(page)
        if stated and not company_name.looks_like_slug(stated):
            return stated
        titled = company_name.from_title(
            self.ats, company_name.title_of(page), stated or self.slug
        )
        return titled or stated or self.company

    @staticmethod
    def _company_name(raw: str) -> str | None:
        """Best-effort: the careers page embeds org_info.company_name in a config blob."""
        m = _CONFIG_AFTER_JOBS.search(raw)
        if not m:
            return None
        try:
            cfg = json.loads(html.unescape(m.group(1)))
        except json.JSONDecodeError:
            return None
        return ((cfg.get("org_info") or {}).get("company_name") or "").strip() or None
