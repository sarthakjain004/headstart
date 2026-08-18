"""Zoho Recruit career-site scraper.

Zoho server-renders the job list into the careers page as an HTML-entity-encoded
JSON array inside `<input type="hidden" value="[...]" id="jobs">` (value before id —
that order is what `_JOBS_INPUT` relies on). There is no XHR or CSRF handshake for the
listing — we GET the page and extract that array.

A Zoho company's `slug` is its full careers host, e.g. "pnbcsl.zohorecruit.in"
(the data center varies: .in / .com / .eu), so the slug carries the right host.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from headstart.models import Job, host_of, html_to_text
from headstart.scrapers.base import BaseScraper

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


def _js_unescape(s: str) -> str:
    """Decode the JS single-quoted-string layer: \\xNN hex escapes, \\<char> pass-through."""
    return _JS_ESCAPE.sub(
        lambda m: chr(int(m.group(1), 16)) if m.group(1) else m.group(2), s
    )


class ZohoScraper(BaseScraper):
    ats = "zoho"
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
        # The careers page carries the whole list, but some tenants configure the careers
        # site without the Job_Description column (28 of 71 in the corpus) — for those,
        # each job's detail page does carry it, so fill the gap with a low-concurrency
        # detail pass.
        page = self._get()
        empty = [
            r["id"]
            for r in self._records(page)
            if r.get("id")
            and not r.get("Job_Description")
            and not r.get("Is_Locked")
            and r.get("Publish", True)
        ]
        details = {}
        if empty:
            fetched = self.fan_out(
                empty, self._detail_description, workers=_DETAIL_WORKERS
            )
            self.report_detail_gaps(fetched, "description backfills")
            details = {jid: d for jid, d in zip(empty, fetched) if d}
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

    def _detail_description(self, jid: str) -> str | None:
        """GET one job's detail page and pull Job_Description from its embedded record."""
        page = self._get(f"https://{self.slug}/jobs/Careers/{jid}")
        m = _DETAIL_JOBS.search(page)
        if not m:
            return None
        try:
            records = json.loads(_js_unescape(m.group(1)))
        except json.JSONDecodeError:
            return None
        return (records[0].get("Job_Description") or None) if records else None

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        # raw is fetch_raw's {page, details}; a bare page string means no detail pass
        page, details = (
            (raw, {}) if isinstance(raw, str) else (raw["page"], raw["details"])
        )
        records = self._records(page)
        if not records:
            return []

        company = self._company_name(page) or self.company
        jobs: list[Job] = []
        for r in records:
            if r.get("Is_Locked") or not r.get("Publish", True):
                continue
            jid = r.get("id")
            if not jid:
                continue
            title = (r.get("Posting_Title") or r.get("Job_Opening_Name") or "").strip()
            location = (
                r.get("City")
                or ", ".join(x for x in (r.get("State"), r.get("Country")) if x)
                or None
            )
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{jid}",
                    ats=self.ats,
                    company=company,
                    title=title,
                    location=location,
                    remote=bool(r.get("Remote_Job")),
                    department=(r.get("Industry") or "").strip() or None,
                    url=f"https://{self.slug}/jobs/Careers/{jid}/{_SLUG.sub('-', title)}?source=CareerSite",
                    posted_at=r.get("Date_Opened") or None,
                    scraped_at=scraped_at,
                    description=html_to_text(
                        r.get("Job_Description") or details.get(jid)
                    ),
                    experience=r.get("Work_Experience"),
                    employment_type=r.get("Job_Type"),
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
