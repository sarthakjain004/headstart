"""Zoho Recruit career-site scraper.

Zoho server-renders the job list into the careers page as an HTML-entity-encoded
JSON array inside `<input type="hidden" value="[...]" id="jobs">` (value before id —
that order is what `_JOBS_INPUT` relies on). There is no XHR or CSRF handshake for the
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
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from headstart import log
from headstart.models import Job, host_of, html_to_text
from headstart.scrapers.base import BaseScraper

_log = log.get(__name__)

#: The widget embeds at most ~this many jobs in one response and offers no working
#: pagination; the docstring above records it as the sample-wide maximum.
_EMBED_CEILING = 750

_JOBS_INPUT = re.compile(r'value="([^"]*)"\s+id="jobs"')
_CONFIG_AFTER_JOBS = re.compile(r'id="jobs">\s*<input[^>]*\bvalue="([^"]*)"')
_SLUG = re.compile(r"[^A-Za-z0-9]+")
# a job's detail page embeds its full record as `var jobs = JSON.parse('…')` — a JS
# single-quoted string (\xNN hex escapes) wrapping JSON
_DETAIL_JOBS = re.compile(r"jobs\s*=\s*JSON\.parse\('((?:[^'\\]|\\.)*)'\)")
_JS_ESCAPE = re.compile(r"\\x([0-9a-fA-F]{2})|\\(.)")
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


def _salary_field(salary: Any, currency: Any) -> str | None:
    """``Job.salary`` from the detail record's ``Salary`` (plus ``Currency`` when present) — e.g.
    "250,000 - 300,000 USD" — so ``salary.extract``'s field tier and the served ``salary`` display
    column (ADR-0019) stop being permanently empty for zoho. Previously ``Salary``/``Currency``
    were only ever spliced into the description text; the value itself never reached ``Job.salary``
    (audit: experiment/location-audit-2026-08-25/zoho.md). This is strictly additive — the splice
    into the description (``_description_text``) stays, as a fallback for `salary.extract`'s
    description-mining tier on any tenant's phrasing the field tier's parser doesn't handle."""
    salary_text = (salary or "").strip()
    if not salary_text:
        return None
    currency_text = (currency or "").strip()
    return f"{salary_text} {currency_text}" if currency_text else salary_text


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
    detail_workers = _DETAIL_WORKERS  # also the async stream width (base.fan_out_async)
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

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
        details = {}
        if ids:
            # Multiplexed by default (ADR-0016); HEADSTART_ASYNC_FANOUT=0 falls back to threads.
            if self.async_fanout_enabled():
                fetched = self.fan_out_async(ids, self._detail_record_async)
            else:
                fetched = self.fan_out(
                    ids, self._detail_record, workers=_DETAIL_WORKERS
                )
            self.report_detail_gaps(fetched, "detail pages")
            details = {jid: d for jid, d in zip(ids, fetched) if d}
        return {"page": page, "details": details}

    @staticmethod
    def _records(page: str) -> list[dict]:
        """The job records embedded in a careers/detail page's jobs `<input>`.

        A page without the input returns ``[]`` — that is what an empty board serves. A page
        *with* the input whose JSON will not parse raises instead: that is Zoho changing its
        page shape under us, and swallowing it would read as every zoho board emptying at
        once — sync would evict all their rows as delistings (the eightfold-flap failure
        class), with nothing in any log saying why.
        """
        match = _JOBS_INPUT.search(page)
        if not match:
            return []
        return json.loads(html.unescape(match.group(1)))

    def _detail_url(self, jid: str) -> str:
        return f"https://{self.slug}/jobs/Careers/{jid}"

    @staticmethod
    def _detail_record_of(page: str) -> dict | None:
        """The job record embedded in a detail page (None when the embedded blob is missing,
        empty, or doesn't parse). Salary/Currency, a fuller State, Date_Opened and Work_Experience
        all live on the detail page at meaningfully higher coverage than the listing (found via a
        code-review-triggered re-probe on PR #238, after an earlier check against the listing
        wrongly called Salary a dead end; the wider field-by-field gap measured in
        experiment/location-audit-2026-08-25/zoho.md). ``parse()`` merges this over the thinner
        listing record (``_merge_detail``) rather than reading only a computed description
        string, so every field the detail page carries gets a chance to reach the Job."""
        m = _DETAIL_JOBS.search(page)
        if not m:
            return None
        try:
            records = json.loads(_js_unescape(m.group(1)))
        except json.JSONDecodeError:
            return None
        if not records:
            return None
        return records[0]

    def _detail_record(self, jid: str) -> dict | None:
        """GET one job's detail page and return its embedded record."""
        return self._detail_record_of(self._get(self._detail_url(jid)))

    async def _detail_record_async(self, session: Any, jid: str) -> dict | None:
        """Same as :meth:`_detail_record` over the shared multiplexed ``AsyncSession``."""
        return self._detail_record_of(
            await self._get_async(session, self._detail_url(jid))
        )

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        # raw is fetch_raw's {page, details}; a bare page string means no detail pass
        page, details = (
            (raw, {}) if isinstance(raw, str) else (raw["page"], raw["details"])
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
            _log.warning(
                f"{self.board_key()}: {len(records)} records, at or over the ~{_EMBED_CEILING} "
                "widget ceiling — anything past it is unread, not absent"
            )

        company = self._company_name(page) or self.company
        jobs: list[Job] = []
        for r in records:
            if r.get("Is_Locked") or not r.get("Publish", True):
                continue
            jid = r.get("id")
            if not jid:
                continue
            # The detail record wins field-by-field when it landed — measured a strict superset
            # over the listing (`_merge_detail`'s docstring) — and falls back to the bare listing
            # record if the detail fetch failed.
            d = _merge_detail(r, details.get(jid))
            title = (r.get("Posting_Title") or r.get("Job_Opening_Name") or "").strip()
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{jid}",
                    ats=self.ats,
                    company=company,
                    title=title,
                    location=_zoho_location(
                        d.get("City"), d.get("State"), d.get("Country")
                    ),
                    remote=bool(d.get("Remote_Job")),
                    department=(d.get("Industry") or "").strip() or None,
                    url=f"https://{self.slug}/jobs/Careers/{jid}/{_SLUG.sub('-', title)}?source=CareerSite",
                    posted_at=d.get("Date_Opened") or None,
                    scraped_at=scraped_at,
                    description=html_to_text(_description_text(d)),
                    experience=d.get("Work_Experience"),
                    employment_type=d.get("Job_Type"),
                    salary=_salary_field(d.get("Salary"), d.get("Currency")),
                )
            )
        return jobs

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
