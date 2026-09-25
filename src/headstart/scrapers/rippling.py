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

from headstart import salary
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    DetailLost,
    DetailRequest,
    DetailWithoutDescription,
)

_API = "https://api.rippling.com/platform/api/ats/v1/board"
_DETAIL_WORKERS = 8


def _department_of(record: dict[str, Any]) -> str | None:
    """A record's department, whether it states a bare string or a ``{"id", "label"}`` object.

    The listing item's ``department`` object carries ``{"id", "label"}`` — measured live
    2026-09-22 across 76 postings on 3 boards, never a ``name`` key. ``label`` is the
    human-readable value (e.g. "Engineering"); falls back to ``id`` when absent. One expression
    rather than two: the tech gate reads it off the listing item and ``parse`` reads it off the
    item then the detail, and a gate that unpacked the dict differently from ``parse`` would
    classify on a different string than ``filter_tech`` does."""
    dept = record.get("department")
    if isinstance(dept, dict):
        dept = dept.get("label") or dept.get("id")
    return dept or None


def _location(rows: list[dict], detail: dict) -> str | None:
    """Every place a posting names, ``; ``-joined in first-seen order.

    A multi-location posting is listed once per work location: rows sharing one ``uuid`` and
    differing only in ``workLocation`` (measured live 2026-09-23 across 25 Boards: 141 of 143
    multi-row postings; the other 2 repeat a row exactly). The detail's ``workLocations`` names
    the same places (136/143; the other 7 spell a state differently), so it is read only when no
    row states a label.
    """
    labels = [
        wl["label"]
        for wl in (row.get("workLocation") for row in rows)
        if isinstance(wl, dict) and wl.get("label")
    ]
    return "; ".join(dict.fromkeys(labels or detail.get("workLocations") or [])) or None


def _format_amount(v: float) -> str:
    """Fixed-point, never scientific notation or a trailing ".0" — keka's ``_format_num`` fix:
    ``:g`` writes 2,000,000 as "2e+06", which ``salary.py`` cannot parse, so a band reaching a
    million (live: heymarvin's INR bands, 2026-09-22) lost its salary entirely."""
    return f"{v:f}".rstrip("0").rstrip(".") or "0"


def _employment_type(detail: dict) -> str | None:
    """``employmentType.label`` is a clean 6-value enum (SALARIED_FT, HOURLY_FT, ...); ``.id``
    is tenant free text (347 distinct spellings measured live, 130 of them singletons). The two
    subfields are inverted from what their names suggest. Falls back to ``.id`` only when
    ``.label`` is ``None`` — checked with ``is not None``, not truthiness, so a genuinely
    empty-string label (were one ever seen) can't silently pick up ``.id`` instead, the same
    class of bug ``_salary_field`` below fixes for ``rangeStart``/``rangeEnd``. See
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


class RipplingScraper(BaseScraper):
    ats = "rippling"
    url_shape = r"https://ats\.rippling\.com/[^/]+/jobs/[0-9a-f-]+"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    def url(self) -> str:
        return f"{_API}/{self.slug}/jobs"

    def job_url(self, uuid: str, native_url: str | None = None) -> str:
        """The listing's own ``url`` field when present, else the derived ATS route."""
        return native_url or f"https://ats.rippling.com/{self.slug}/jobs/{uuid}"

    def fetch_raw(self) -> Any:
        resp = self._fetch(
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
        if isinstance(data, dict) and "items" not in data and "jobs" not in data:
            self.note_unreadable_board(
                "a list, or an `items`/`jobs` key", f"keys {sorted(data)[:5]}"
            )
        # The tech gate (ADR-0017): `parse` reads `name` and `department` off this listing item,
        # falling back to the detail only for a department the listing omitted. The listing
        # item's `department` carries `{id, label}` (measured live 2026-09-22, 76/76 postings
        # on 3 boards) and `_department_of` reads that directly, so the detail fallback below
        # exists only for the rare listing item missing the key entirely.
        #
        # Gained/lost through the gate itself, measured live 2026-09-22 across 156 postings on
        # 13 boards (10fitness, aalo-atomics and 11 others): 0 gained, 0 lost — title alone
        # already agreed with `is_tech`'s department-aware verdict on every sampled posting.
        # This fix's measured value is `Job.department` itself, always None before this change
        # and now correctly populated (served field + the post-detail `filter_tech` pass, which
        # reads `Job.department` independently of this pre-detail gate) — a small board sample
        # finding no gate-level swing is expected, not evidence the fix does nothing.
        #
        # The gate runs over every location row, before the rows merge, and not through
        # `run_detail_pass`'s own gate — which would ask only the one row the merge keeps.
        wanted = self.tech_detail_wanted(
            items, lambda row: row.get("name"), _department_of
        )
        # One detail per posting, not one per location row — `parse` merges a posting's rows.
        postings = list({row.get("uuid"): row for row in wanted}.values())
        details = self.run_detail_pass(
            postings,
            key_of=lambda posting: posting.get("uuid") or None,
            what="details",
        )
        # A failed or gated fetch leaves ``_detail`` {}.
        self.attach_details(
            items, postings, [details.get(posting.get("uuid")) for posting in postings]
        )
        return items

    def detail_request(self, posting: dict) -> DetailRequest:
        if not posting.get("uuid"):
            raise DetailLost("no job uuid")
        return DetailRequest(
            f"{_API}/{self.slug}/jobs/{posting['uuid']}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    def read_detail(
        self, posting: dict, response: Any
    ) -> dict | DetailWithoutDescription:
        record = response.json()
        if not record:
            raise DetailLost("empty record on a 200")
        if not _description(record):
            # Kept for the department `parse` falls back to; a gap for the description.
            return DetailWithoutDescription(record, "200 without description")
        return record

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        # A multi-location posting is N rows sharing one `uuid` (see `_location`) — grouped so it
        # serves as one Job with every location, rather than colliding down to one row's in the
        # harvest's within-Board first-wins dedupe. Only one row of a group carries the detail.
        by_uuid: dict[str, list[dict]] = {}
        for it in raw:
            by_uuid.setdefault(it["uuid"], []).append(it)
        jobs: list[Job] = []
        for rows in by_uuid.values():
            it = rows[0]
            detail = next((r["_detail"] for r in rows if r.get("_detail")), {})
            location = _location(rows, detail)
            dept = _department_of(it) or _department_of(detail)
            jobs.append(
                Job(
                    id=self.job_id(it["uuid"]),
                    ats=self.ats,
                    company=detail.get("companyName") or self.company,
                    title=(it.get("name") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=dept,
                    url=self.job_url(it["uuid"], it.get("url")),
                    posted_at=detail.get("createdOn"),
                    scraped_at=scraped_at,
                    description=html_to_text(_description(detail)),
                    employment_type=_employment_type(detail),
                    salary=self._salary_field(detail.get("payRangeDetails")),
                )
            )
        return jobs

    def _salary_field(self, raw: list | None) -> str | None:
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
        class of bug ashby's ``_salary_field`` docstring documents fixing (a real job with
        ``minValue=0`` would otherwise have its floor silently dropped).
        """
        entries = [r for r in (raw or []) if r]
        if not entries:
            return None
        units = [(r.get("currency"), r.get("frequency")) for r in entries]
        counts = Counter(units)
        best = max(counts.values())
        tied = [u for u, c in counts.items() if c == best]
        unit = units[0] if units[0] in tied else tied[0]
        same_unit = [
            r for r in entries if (r.get("currency"), r.get("frequency")) == unit
        ]
        los = [r["rangeStart"] for r in same_unit if r.get("rangeStart") is not None]
        his = [r["rangeEnd"] for r in same_unit if r.get("rangeEnd") is not None]
        if not los:
            # blank, or a ceiling alone — `salary.extract` reads a lone figure as a floor, so
            # "up to 120000" would serve as a 120k minimum (recruitee and iCIMS refuse the same)
            return None
        lo = min(los)
        hi = max(his) if his else None
        currency, frequency = unit
        return salary.to_field(
            _format_amount(lo),
            _format_amount(hi) if hi is not None else None,
            currency,
            frequency,
        )
