"""Rippling job-board scraper (ats.rippling.com, public board API).

A company's board lives at ``https://ats.rippling.com/{slug}``; its openings are listed at
    https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs        (summary only)
and each posting's full record — including the HTML ``description`` (a ``{company, role}``
object) — is at
    https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uuid}
fetched in a bounded thread pool. A failed detail fetch leaves description None — job still kept.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_API = "https://api.rippling.com/platform/api/ats/v1/board"
_DETAIL_WORKERS = 8


def _location(item: dict) -> str | None:
    wl = item.get("workLocation") or {}
    if isinstance(wl, dict) and wl.get("label"):
        return wl["label"]
    wls = (item.get("_detail") or {}).get("workLocations") or []
    return wls[0] if wls else None


def _employment_type(detail: dict) -> str | None:
    """``employmentType.label`` is a clean 6-value enum (SALARIED_FT, HOURLY_FT, ...); ``.id``
    is tenant free text (347 distinct spellings measured live, 130 of them singletons). The two
    subfields are inverted from what their names suggest. Falls back to ``.id`` only when
    ``.label`` is ``None`` — checked with ``is not None``, not truthiness, so a genuinely
    empty-string label (were one ever seen) can't silently pick up ``.id`` instead, the same
    class of bug ``_pay_range`` below fixes for ``rangeStart``/``rangeEnd``. See
    docs/salary-extraction/rippling.md for the live measurement.
    """
    et = detail.get("employmentType") or {}
    label = et.get("label")
    return label if label is not None else et.get("id")


def _description(detail: dict) -> str | None:
    d = detail.get("description")
    if isinstance(d, dict):
        return d.get("role") or d.get(
            "company"
        )  # `role` is the posting; `company` is the blurb
    return d if isinstance(d, str) else None


def _pay_range(ranges: list | None) -> str | None:
    """Format the true min/max across every payRangeDetails entry sharing the MAJORITY
    (currency, frequency) unit, e.g. '150000-250000 USD YEAR'.

    A job can carry more than one entry (e.g. per-level or per-region bands) — reading only
    entry [0] understates the real span whenever a later entry carries a wider range in the
    SAME unit. See docs/salary-extraction/rippling.md for the live measurement.

    Grouped by the (currency, frequency) unit shared by the MOST entries, rather than pooled
    across all entries regardless of unit — found in review, live: a real job (journaltech)
    carries three USD/YEAR entries (160000-200000) alongside one CAD/YEAR entry
    (155000-190000); pooling blindly produced "155000-200000 USD YEAR", mislabeling a CAD
    figure as USD. This scraper has no basis for converting across currencies, so entries
    outside the majority unit are excluded from the span rather than blended into it.

    Anchored on the majority group rather than positionally on entry [0] — entry [0]'s unit
    is not known to be guaranteed non-minority by the API, so anchoring there would let a
    minority-currency entry the API happens to list first narrow the reported range to just
    that outlier. Ties fall back to entry [0]'s unit, preserving prior behavior when there is
    no real majority to prefer.

    ``rangeStart``/``rangeEnd`` are checked with ``is not None``, not truthiness — the same
    class of bug ashby's ``_salary`` docstring documents fixing (a real job with
    ``minValue=0`` would otherwise have its floor silently dropped).
    """
    entries = [r for r in (ranges or []) if r]
    if not entries:
        return None
    units = [(r.get("currency"), r.get("frequency")) for r in entries]
    counts = Counter(units)
    best = max(counts.values())
    tied = [u for u, c in counts.items() if c == best]
    unit = units[0] if units[0] in tied else tied[0]
    same_unit = [r for r in entries if (r.get("currency"), r.get("frequency")) == unit]
    los = [r["rangeStart"] for r in same_unit if r.get("rangeStart") is not None]
    his = [r["rangeEnd"] for r in same_unit if r.get("rangeEnd") is not None]
    if not los and not his:
        return None
    lo = min(los) if los else None
    hi = max(his) if his else None
    span = (
        f"{lo:g}-{hi:g}"
        if lo is not None and hi is not None
        else f"{(lo if lo is not None else hi):g}"
    )
    currency, frequency = unit
    return " ".join(str(x) for x in (span, currency, frequency) if x)


class RipplingScraper(BaseScraper):
    ats = "rippling"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    def url(self) -> str:
        return f"{_API}/{self.slug}/jobs"

    def fetch_raw(self) -> Any:
        resp = http.fetch(
            "GET",
            self.url(),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=30,
        )
        # Raise, don't return [] — a swallowed listing error reads as an empty board and
        # hides a dead one from the ADR-0058 quarantine forever.
        resp.raise_for_status()
        data = resp.json()
        items = (
            data
            if isinstance(data, list)
            else (data.get("items") or data.get("jobs") or [])
        )
        # Fill each posting's detail concurrently (bounded); a failed fetch leaves ``_detail`` {}.
        if self.async_fanout_enabled():
            details = self.fan_out_async(
                items,
                lambda session, it: self._detail_async(session, it.get("uuid")),
                default={},
            )
        else:
            details = self.fan_out(
                items,
                lambda it: self._detail(it.get("uuid")),
                workers=_DETAIL_WORKERS,
                default={},
            )
        # {} is this scraper's failure sentinel (a real record is never empty), so map
        # falsy to None for the gap count.
        self.report_detail_gaps([d or None for d in details], "details")
        for item, detail in zip(items, details):
            item["_detail"] = detail
        return items

    def _detail_url(self, uuid: str) -> str:
        return f"{_API}/{self.slug}/jobs/{uuid}"

    @staticmethod
    def _extract_detail(response: Any) -> dict:
        return response.json() if response.status_code == 200 else {}

    def _detail(self, uuid: str | None) -> dict:
        """GET one posting's full record (``{}`` on failure). Sync path."""
        if not uuid:
            return {}
        try:
            resp = http.fetch(
                "GET",
                self._detail_url(uuid),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=30,
            )
        except http.RequestsError:
            return {}
        return self._extract_detail(resp)

    async def _detail_async(self, session: Any, uuid: str | None) -> dict:
        """Same as :meth:`_detail` but over the shared multiplexed ``AsyncSession``."""
        if not uuid:
            return {}
        try:
            resp = await http.fetch_async(
                session,
                "GET",
                self._detail_url(uuid),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=30,
            )
        except http.RequestsError:
            return {}
        return self._extract_detail(resp)

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for it in raw:
            detail = it.get("_detail") or {}
            location = _location(it)
            dept = it.get("department") or detail.get("department")
            if isinstance(dept, dict):
                dept = dept.get("name")
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{it['uuid']}",
                    ats=self.ats,
                    company=detail.get("companyName") or self.company,
                    title=(it.get("name") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=dept,
                    url=it.get("url")
                    or f"https://ats.rippling.com/{self.slug}/jobs/{it['uuid']}",
                    posted_at=detail.get("createdOn"),
                    scraped_at=scraped_at,
                    description=html_to_text(_description(detail)),
                    employment_type=_employment_type(detail),
                    salary=_pay_range(detail.get("payRangeDetails")),
                )
            )
        return jobs
