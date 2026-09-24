"""Greenhouse job-board scraper (boards-api.greenhouse.io).

The bare ``/jobs`` list is metadata-only. ``?content=true`` inlines each posting's full
description (and a ``departments`` array) in the same single request — ~12x the payload but
no per-job fetch — so we use it to populate description and department. It also inlines each
posting's per-tenant ``metadata`` custom fields, which carry compensation data on a real
minority of tenants (see ``_salary_field``).

Reading ``metadata`` into ``salary`` does NOT need a `doc_prep.DERIVATIONS_VERSION` bump: like
smartrecruiters' own native-compensation field (see that scraper's docstring), ``salary`` is a
re-observed FACT_FIELD, so once a Board is rescraped its now-populated raw ``Job.salary`` differs
from the stored one and `refresh_row`'s `salary_inputs_moved` reprocesses it — no version sweep
required. A bump is for when unchanged input starts parsing differently; here the input itself
changes from ``None`` to a real string.
"""

from __future__ import annotations

from typing import Any

from headstart import log, salary
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_log = log.get(__name__)

# Names carrying a genuine currency-range/point disclosure (real minority of tenants) score
# higher when they mention one of these words — task evidence, doordashusa (2026-09-15): "USA:
# Pay Transparency Range" and "Canada: Pay Transparency Range" beat "US1 - Base Salary Band
# Midpoint"/"US4 - Base Salary Band Minimum" for the same job. Excludes bare "salary" — several
# tenants' band/midpoint fields already say "Salary" without being the field worth preferring.
_PREFERRED_NAME_WORDS = ("transparency", "range", "pay")
# Equity/RSU grants are not cash salary and must never be reported as one — real risk, not
# hypothetical: doordashusa's "US1 - Equity Band Midpoint" is `value_type: "currency"` exactly
# like a real salary field, and non-zero on 146/455 sampled jobs (real grant values, $50k-$100k),
# so it would otherwise win by elimination whenever the real salary fields are absent for a job.
_EXCLUDED_NAME_WORDS = ("equity",)


def _format_amount(v: float) -> str:
    """Fixed-point, never scientific notation or a trailing ".0" — the same defect class
    keka.py's own ``_format_num`` fixes (Python's ``:g`` switches to scientific past 1,000,000,
    which neither ``salary.py``'s ``_RANGE`` regex nor ``_num()`` can parse)."""
    return f"{v:f}".rstrip("0").rstrip(".") or "0"


def _compensation_range(value_type: str, value: Any) -> tuple[float, float] | None:
    """(min, max) from one ``metadata`` entry's ``value``, or None if it carries no usable
    number. A ``"currency"`` entry is a single point (min == max); a ``"currency_range"`` entry
    states both ends explicitly. Both need a real ``unit`` (currency) — an entry with none
    (real: doordashusa's "US1 - Equity Band Midpoint" for jobs where it IS zero-valued also
    carries ``unit: None``) or with 0.0/0.01 placeholder amounts on BOTH ends (real: mongodb's
    "Job Post Range (Canada)" `{unit: None, min_value: "0.0", max_value: "0.0"}`, okta-style
    tenants) is not real compensation data and is declined here rather than downstream, so a
    placeholder never gets a chance to look like a stated near-zero salary."""
    if not isinstance(value, dict) or not value.get("unit"):
        return None
    try:
        if value_type == "currency":
            lo = hi = float(value["amount"])
        else:
            lo, hi = float(value["min_value"]), float(value["max_value"])
    except (KeyError, TypeError, ValueError):
        return None
    if max(lo, hi) <= 0.01:
        return None
    return min(lo, hi), max(lo, hi)


class GreenhouseScraper(BaseScraper):
    ats = "greenhouse"
    # Two legitimate shapes. The second is the EMBED form: a tenant may configure its own
    # board page, and then the API's `absolute_url` — and greenhouse's own canonical link —
    # both point there with the job in `?gh_jid=`. Verified live 2026-08-12:
    # job-boards.greenhouse.io/codeblack/jobs/4012421004 302s to
    # codeblack.netlify.app/?gh_jid=4012421004, and the embed's job endpoint returns that
    # posting. The page renders client-side, so `title_on_page` reads false on it — a limit
    # of an HTTP probe, not a broken link.
    url_shape = r"https://(?:(?:job-boards|boards)\.greenhouse\.io/.+/jobs/\d+|.+[?&]gh_jid=\d+)"

    def url(self) -> str:
        return (
            f"https://boards-api.greenhouse.io/v1/boards/{self.slug}/jobs?content=true"
        )

    def job_url(self, url: str) -> str:
        """Greenhouse's API states the job's own link directly (``absolute_url``); nothing to
        build, so this simply names that as the declared source (ADR-0153)."""
        return url

    def fetch_raw(self) -> Any:
        """The default JSON fetch, plus one **observation-only** check on the response envelope.

        Greenhouse answers with ``{"jobs": [...], "meta": {"total": N}}``, and this scraper has
        always read only ``jobs``. Measured 2026-08-23
        (docs/pipeline/2026-08-23_false-board-eviction-root-cause.md §4.1): the API sometimes
        returns a **silently short** list — HTTP 200, valid JSON, no error — and because nothing
        here can detect that, ``index sync`` evicts the missing postings as delistings
        (``databricks`` returned 816 of 821; ``metrostarsystems`` 84 of 90).

        ``len(jobs) != meta.total`` is the obvious guard, and it is deliberately **not** wired to
        :meth:`~BaseScraper.mark_truncated` yet. A sweep of 602 live boards found the two agreeing
        602/602, which only establishes they agree while the response is *healthy* — whether
        ``total`` stays authoritative *during* a short response is exactly the unknown, and
        marking a Board unauthoritative on a signal that might fire always (or never) is the
        failure ADR-0053's guards exist to avoid. So this logs and does nothing else; §4.1 records
        how to read this line (and, harder, a silence) into a decision on shipping the guard.
        It logs at INFO: it can fire once per Board, and ADR-0039's annotation budget is a
        run-level quota, so an observation-only tripwire is exactly what must not spend it.
        """
        raw = super().fetch_raw()
        total = (raw.get("meta") or {}).get("total")
        listed = len(raw.get("jobs") or [])
        if isinstance(total, int) and total != listed:
            _log.info(
                f"{self.board_key()}: envelope disagrees — {listed} jobs listed but "
                f"meta.total={total} (delta {total - listed}); the response is short and "
                "says so, so a mark_truncated guard on this signal would fire here"
            )
        return raw

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw.get("jobs", []):
            # `location.name` ships with un-trimmed padding on a real minority of tenants —
            # measured 2026-08-24, 22/178 sampled jobs ("Hybrid in Boston, MA   ", three
            # trailing spaces) — and nothing downstream strips it.
            location = ((j.get("location") or {}).get("name") or "").strip() or None
            department = (j.get("departments") or [{}])[0].get("name") or None
            jobs.append(
                Job(
                    id=self.job_id(j["id"]),
                    ats=self.ats,
                    company=j.get("company_name") or self.company,
                    title=(j.get("title") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=department,
                    url=self.job_url(j.get("absolute_url", "")),
                    posted_at=j.get("first_published") or j.get("updated_at"),
                    scraped_at=scraped_at,
                    description=html_to_text(j.get("content")),
                    salary=self._salary_field(j.get("metadata")),
                )
            )
        return jobs

    def _salary_field(self, raw: list[dict] | None) -> str | None:
        """``Job.salary`` from Greenhouse's per-tenant ``metadata`` custom fields — a real minority
        of tenants (doordashusa, datadog, mongodb; most publish nothing there) state compensation as
        a ``value_type: "currency"`` (single point) or ``"currency_range"`` (min/max) entry.

        Precedence, evidence-based (doordashusa carries both a `currency_range` "Pay Transparency
        Range" AND `currency` "Base Salary Band Midpoint"/"Minimum" entries on the SAME job,
        2026-09-15 live sample): a ``currency_range`` entry always beats a single ``currency`` point,
        and within the same type a name mentioning transparency/range/pay beats one that doesn't
        (:data:`_PREFERRED_NAME_WORDS`) — both real signals of "this is the stated range", not a
        band's one landmark value. Ties (e.g. two genuine country-specific ranges on the same job,
        "USA: Pay Transparency Range" vs "Canada: Pay Transparency Range") keep the first entry in
        metadata array order — simple and deterministic; the array is not documented to be ordered
        by relevance, but nothing in the corpus contradicts US-first for a US-based req either.
        No cross-entry combination (e.g. pairing a "Minimum" with a "Midpoint" into a synthetic
        range) — each candidate is scored and read on its own.

        The chosen figure carries no period marker: `metadata`'s ``value`` dict states a currency
        but never a period, and a field NAME can't be trusted to say either — doordashusa's own "USA:
        Pay Transparency Range" is the identical field name on an annual-paid "Account Manager, CPG"
        ($114,600-$168,500) and an hourly-paid "Account Executive" ($34-$50/hr), with nothing in the
        metadata to tell them apart (live, 2026-09-15). Guessing a period from magnitude was
        considered and rejected: it would need its own evidence-calibrated threshold the way
        darwinbox's lakhs heuristic has one, and this task's evidence doesn't supply it. Left
        unmarked, `salary.py`'s `_field_generic` (greenhouse has no dedicated Tier-1 parser) defaults
        to annual — correct for the real annual figures, and safely declined by the plausibility
        floor for the hourly ones (a $34-$50 "annual salary" fails it) rather than silently
        mislabeling them. That trade costs recall on genuine hourly disclosures (309/455 sampled
        doordashusa jobs clear the floor) in exchange for never fabricating a period."""
        best_score: tuple[int, int] | None = None
        best: tuple[tuple[float, float], str | None] | None = None
        for m in raw or []:
            value_type = m.get("value_type")
            if value_type not in ("currency", "currency_range"):
                continue
            name = (m.get("name") or "").lower()
            if any(word in name for word in _EXCLUDED_NAME_WORDS):
                continue
            amounts = _compensation_range(value_type, m.get("value"))
            if amounts is None:
                continue
            score = (
                1 if value_type == "currency_range" else 0,
                1 if any(word in name for word in _PREFERRED_NAME_WORDS) else 0,
            )
            if best_score is None or score > best_score:
                best_score = score
                best = (amounts, (m.get("value") or {}).get("unit"))
        if best is None:
            return None
        (lo, hi), currency = best
        return salary.to_field(
            _format_amount(lo), None if lo == hi else _format_amount(hi), currency
        )
