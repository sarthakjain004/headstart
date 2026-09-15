"""JOIN job-board scraper (join.com).

A company's openings live at ``https://join.com/companies/{slug}``. That careers page embeds a
Next.js ``__NEXT_DATA__`` blob carrying the numeric ``companyId``; the public jobs API then
lists the openings (paginated):
    https://join.com/api/public/companies/{companyId}/jobs?locale=en&page=N&pageSize=50
The list is summary-only for description, so each posting's description is fetched from its
detail endpoint
    https://join.com/api/public/jobs/{id}?locale=en
in a bounded thread pool. A failed detail fetch leaves description None — the job is still kept.
Compensation (``salaryAmountFrom``/``salaryAmountTo``/``salaryFrequency``) is NOT summary-only,
though — it's already on the listing item itself (see ``_salary_field``), so ``salary`` doesn't
depend on the detail pass at all.

Reading it into ``salary`` does NOT need a `doc_prep.DERIVATIONS_VERSION` bump: like
smartrecruiters' own native-compensation field (see that scraper's docstring), ``salary`` is a
re-observed FACT_FIELD, so once a Board is rescraped its now-populated raw ``Job.salary`` differs
from the stored one and `refresh_row`'s `salary_inputs_moved` reprocesses it — no version sweep
required. A bump is for when unchanged input starts parsing differently; here the input itself
changes from ``None`` to a real string.
"""

from __future__ import annotations

import json
import re
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text
from headstart.scrapers.base import USER_AGENT, BaseScraper

_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)
_PAGE_SIZE = 5  # 2026-07: the API rejects larger values ("pageSize: Invalid value")
_MAX_PAGES = 2000  # 2000 * 5 = 10k jobs ceiling per company
_DETAIL_WORKERS = 8
_REMOTE = {
    "REMOTE": True,
    "FULLY_REMOTE": True,
    "ONSITE": False,
    "ON_SITE": False,
}  # else None
# join's `salaryFrequency` values mapped to a phrase `salary.py`'s `_period_multiplier` (used by
# `_field_generic`, join's Tier-1 parser — it has no dedicated one) already recognizes. PER_YEAR
# maps to "": `_field_generic`'s default multiplier is already annual, nothing to add. PER_WEEK
# and PER_DAY are documented platform values (not seen live in this pass) deliberately left
# unmapped: `_period_multiplier` has no weekly/daily phrase to annualize them correctly, and
# guessing one risks silently mislabeling a period rather than declining — see `_salary_field`.
_PERIOD_PHRASE = {"PER_YEAR": "", "PER_MONTH": "per month", "PER_HOUR": "per hour"}


class JoinScraper(BaseScraper):
    ats = "join"
    url_shape = r"https://join\.com/companies/[^/]+/.+"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    def url(self) -> str:
        return f"https://join.com/companies/{self.slug}"

    def job_url(self, id_param: str) -> str:
        return f"https://join.com/companies/{self.slug}/{id_param}"

    def _company(self) -> dict:
        """The company object (incl. numeric id) from the careers page __NEXT_DATA__.

        A non-200 careers page raises rather than reading as an empty board: swallowing it
        hid dead boards from the ADR-0058 quarantine as "alive with zero jobs". A 200 page
        without __NEXT_DATA__ still returns ``{}`` — that is the page's shape, not an error.
        """
        resp = self._fetch(
            "GET", self.url(), headers={"User-Agent": USER_AGENT}, timeout=30
        )
        resp.raise_for_status()
        m = _NEXT.search(resp.text)
        if not m:
            return {}
        state = (json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}
        return (state.get("initialState") or {}).get("company") or {}

    def fetch_raw(self) -> Any:
        company = self._company()
        cid = company.get("id")
        if not cid:
            return {"company": company, "items": []}
        items: list[dict] = []
        page = 1
        while page <= _MAX_PAGES:
            api = (
                f"https://join.com/api/public/companies/{cid}/jobs"
                f"?locale=en&page={page}&pageSize={_PAGE_SIZE}"
            )
            data = self._fetch(
                "GET",
                api,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=30,
            ).json()
            if isinstance(data, list):
                # The API's validation-error shape, not items. Whatever remained is unread, so
                # this is a truncation too, not an end (ADR-0053).
                self.mark_truncated(
                    f"validation-error response at page {page} — {len(items)} jobs read"
                )
                break
            items.extend(data.get("items") or [])
            if page >= (data.get("pagination") or {}).get("pageCount", 1):
                break
            page += 1
        if page > _MAX_PAGES:
            # Only reachable by exhausting the cap — every natural end breaks out above.
            self.mark_truncated(
                f"hit the {_MAX_PAGES}-page cap at {len(items)} jobs — the rest unread"
            )
        # Fill each posting's description concurrently (bounded); a failed fetch leaves it None.
        if self.async_fanout_enabled():
            descriptions = self.fan_out_async(
                items,
                lambda session, it: self._job_description_async(session, it.get("id")),
            )
        else:
            descriptions = self.fan_out(
                items,
                lambda it: self._job_description(it.get("id")),
                workers=_DETAIL_WORKERS,
            )
        self.report_detail_gaps(descriptions, "descriptions")
        for item, description in zip(items, descriptions):
            item["_description"] = description
        return {"company": company, "items": items}

    def _detail_url(self, jid: str) -> str:
        return f"https://join.com/api/public/jobs/{jid}?locale=en"

    def _extract_description(self, response: Any) -> str | None:
        """Description, or intro/tasks/requirements joined, from a detail response (None on
        non-200), with every ``None`` labelled by what lost it — an instance method for that
        reason (:meth:`~BaseScraper.note_detail_loss`): a refused Board and a Board whose
        postings simply carry no body count the same in a bare gap total."""
        if response.status_code != 200:
            self.note_detail_loss(f"HTTP {response.status_code}")
            return None
        d = response.json()
        text = (
            d.get("description")
            or "\n\n".join(
                s for s in (d.get("intro"), d.get("tasks"), d.get("requirements")) if s
            )
            or None
        )
        if text is None:
            self.note_detail_loss("no description on a 200")
        return text

    def _job_description(self, jid) -> str | None:
        """GET one posting's detail and return its description body (None on failure). Sync path."""
        if not jid:
            self.note_detail_unattempted("no job id")
            return None
        try:
            resp = self._fetch(
                "GET",
                self._detail_url(jid),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=30,
            )
        except http.RequestsError as exc:
            self.note_detail_loss(type(exc).__name__)
            return None
        return self._extract_description(resp)

    async def _job_description_async(self, session: Any, jid) -> str | None:
        """Same as :meth:`_job_description` but over the shared multiplexed ``AsyncSession``."""
        if not jid:
            self.note_detail_unattempted("no job id")
            return None
        try:
            resp = await self._fetch_async(
                session,
                "GET",
                self._detail_url(jid),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=30,
            )
        except http.RequestsError as exc:
            self.note_detail_loss(type(exc).__name__)
            return None
        return self._extract_description(resp)

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        company_name = (raw.get("company") or {}).get("name") or self.company
        jobs: list[Job] = []
        for it in raw.get("items", []):
            city = it.get("city") or {}
            country = it.get("country") or {}
            location = (
                ", ".join(
                    p
                    for p in (
                        city.get("label") or city.get("name"),
                        country.get("name"),
                    )
                    if p
                )
                or None
            )
            jobs.append(
                Job(
                    id=self.job_id(it["id"]),
                    ats=self.ats,
                    company=company_name,
                    title=(it.get("title") or "").strip(),
                    location=location,
                    remote=_REMOTE.get((it.get("workplaceType") or "").upper()),
                    department=(it.get("category") or {}).get("name"),
                    url=self.job_url(it.get("idParam", "")),
                    posted_at=it.get("createdAt"),
                    scraped_at=scraped_at,
                    description=html_to_text(it.get("_description")),
                    employment_type=(it.get("employmentType") or {}).get("name"),
                    salary=self._salary_field(it),
                )
            )
        return jobs

    def _salary_field(self, raw: dict) -> str | None:
        """``Job.salary`` from the listing item's ``salaryAmountFrom``/``salaryAmountTo`` (each
        ``{amount, currency}``, ``amount`` in MINOR units — cents) and ``salaryFrequency``.

        Read off the LISTING item itself, not the per-job detail response `_job_description` already
        fetches — confirmed live, 2026-09-15 (job 16244456, indie-solutions): both carry the
        identical figures for the same job, so reading the listing needs no extra request and
        doesn't depend on the detail fetch succeeding.

        Both amounts are absent together (not present as keys at all, not zero) on postings where
        the employer never entered a number — real, live, the common case (most sampled postings)
        — treated as no signal. ``salaryFrequency`` is populated even then (defaults to "PER_YEAR"),
        so its presence alone is not a signal either; only a real amount is. An unrecognized
        frequency (see :data:`_PERIOD_PHRASE`) declines the field entirely rather than guess."""
        lo_amt = (raw.get("salaryAmountFrom") or {}).get("amount")
        hi_amt = (raw.get("salaryAmountTo") or {}).get("amount")
        if lo_amt is None and hi_amt is None:
            return None
        period = _PERIOD_PHRASE.get(raw.get("salaryFrequency"))
        if period is None:
            return None
        currency = (raw.get("salaryAmountFrom") or raw.get("salaryAmountTo") or {}).get(
            "currency"
        )
        span = (
            f"{lo_amt // 100}-{hi_amt // 100}"
            if lo_amt is not None and hi_amt is not None
            else str((lo_amt if lo_amt is not None else hi_amt) // 100)
        )
        return " ".join(p for p in (span, currency, period) if p)
