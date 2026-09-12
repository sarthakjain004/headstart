"""Google careers scraper — the first "single-company board" ATS (ADR-0139).

Google runs its own in-house careers system, not a rented third-party platform, so there is
exactly one tenant, forever: ``ats="google"``, ``slug="careers.google.com"`` (Google's own
public careers domain — the ADR's example is Amazon's equivalent ``amazon.jobs``). That vanity
host 301s to the real serving host (measured 2026-09-11:
``https://careers.google.com/jobs/results/`` -> ``https://www.google.com/about/careers/
applications/jobs/results/``), so :meth:`url` hits the real host directly rather than paying a
redirect on every request.

**No public JSON API** — confirmed live: ``careers.google.com/api/v3/search/`` (a plausible
guess) 404s, and the response CSP's ``connect-src`` lists only ``'self'``/Google's own
first-party hosts, no separate API origin for client-side XHR to call. The listing is the
server-rendered HTML at ``.../jobs/results?hl=en_US&page=N``.

**But the HTML embeds a full internal JSON payload per job — no detail pass needed.** Every
listing page carries two ``AF_initDataCallback({key: 'ds:N', ...})`` blocks (Google's generic
closure-compiled wire format, used across many Google web properties, not a stable public
contract). ``ds:1``'s ``data`` array is ``[jobs, null, total, page_size]``: ``jobs`` is a list of
20 per-job arrays each already carrying the full "About the job" / qualifications /
responsibilities HTML, every stated location, and the posting company — everything this scraper
needs. :func:`_ds1_data` locates and ``json.loads``s that array from the page text; the whole
thing parses as strict JSON despite the outer ``{key: '...', ...}`` wrapper being JS-literal
syntax; only the ``data:`` value itself is sliced out and decoded. This makes ``has_detail_pass``
False and the per-job field layout is genuinely fragile (positional indices into an undocumented,
versioned internal array with no field names) — :func:`_field` and :func:`_pair` index
defensively and a shape change silently drops the fields whose index moved, not the whole scrape.

**Pagination is a real, stated total, not blind walk-to-empty.** ``data[2]`` on page 1 was 3,414
on one fetch and 3,387 on a later fetch in the same minute — the board is live and churns during
a multi-page walk, the same category of drift Oracle/Eightfold already tolerate via
:meth:`~headstart.scrapers.base.BaseScraper.mark_truncated_unless_negligible`'s 0.99 share, reused
here rather than inventing a page-local slack constant. The pages needed are computed from page
1's total and fetched concurrently (:meth:`~headstart.scrapers.base.BaseScraper.fan_out`) —
20 concurrent page requests measured clean (2026-09-11: 20/20 HTTP 200, ~3.85 req/s aggregate,
no 429/403) — rather than a slow sequential walk, since the total is already known up front and
there is no short-page-mid-walk trap to defend against (every page sampled — 1, 2, 50, 100, 150,
165, 168, 170 — read exactly 20 postings; only the true last page, 171, was short at 14, and 3,414
= 170 x 20 + 14 exactly).

**No employment_type, no department.** The per-job array has two unlabelled enum fields (indices
11 and 20 here) with no accompanying string table found anywhere on the page — guessing what they
encode would misrepresent them, so both stay unset, same call Eightfold's PCSX API made for
``employment_type`` ("not exposed"). ``remote`` falls back to
:func:`~headstart.models.is_remote` on the location string; no explicit remote/hybrid flag was
found in the payload.

**``company`` is read per-job, not hardcoded "Google".** The careers site serves several
Alphabet brands through one board (measured: "Google" and "YouTube" both appear on page 1) — the
per-job array's own company field is used, falling back to ``self.company`` only when absent.

Full measurement and the field-index census: ``docs/google/2026-09-11_api-measurement.md``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_LISTING_URL = "https://www.google.com/about/careers/applications/jobs/results"
_JOB_URL = (
    "https://www.google.com/about/careers/applications/jobs/results/{id}?hl=en_US"
)

#: Fixed by the site; echoed as ``data[3]`` on every page sampled, never observed to vary.
_PAGE_SIZE = 20
#: Concurrent page fetches once page 1's total is known. Measured clean at this width 2026-09-11:
#: 20 concurrent requests, 20/20 HTTP 200, ~3.85 req/s aggregate, no 429/403.
_PAGE_WORKERS = 10
#: Ceiling on pages fetched. The board measured at 171 pages (3,414 postings) 2026-09-11; this
#: gives ~4x headroom before a runaway page count is treated as a hard cap rather than walked.
_MAX_PAGES = 400

_DS1_MARKER = "AF_initDataCallback({key: 'ds:1'"
_DS1_END = ");</script>"


def _ds1_data(page_html: str) -> list[Any] | None:
    """The ``ds:1`` payload's own ``data`` array: ``[jobs, null, total, page_size]``.

    String-sliced rather than regex-matched across the whole blob: the block is bounded by two
    literal, specific anchors (its own ``key: 'ds:1'`` opener and the ``);</script>`` that closes
    every such callback), so there is no nested-bracket ambiguity to resolve. None if either
    anchor, or the ``data:`` / ``, sideChannel:`` markers inside it, are missing — a page whose
    template changed enough to move those loses this scraper's only signal and must say so
    rather than parse garbage.
    """
    start = page_html.find(_DS1_MARKER)
    if start == -1:
        return None
    end = page_html.find(_DS1_END, start)
    if end == -1:
        return None
    block = page_html[start:end]
    data_start = block.find("data:[")
    data_end = block.rfind(", sideChannel:")
    if data_start == -1 or data_end == -1:
        return None
    try:
        parsed = json.loads(block[data_start + len("data:") : data_end])
    except ValueError:
        return None
    return parsed if isinstance(parsed, list) else None


def _field(job: list[Any], index: int) -> Any:
    """One positional field of a ``ds:1`` job array, or None past its end.

    Every field access in this module goes through here rather than a bare ``job[i]`` — the
    array's shape is Google's internal wire format, not a documented contract, so a shorter
    array (a field genuinely absent on some job type) must read as None, not raise.
    """
    return job[index] if 0 <= index < len(job) else None


def _pair(value: Any) -> Any:
    """The second element of a ``[null, value]`` pair field, or None.

    Several fields (description sections, notes) are wrapped this way — the first slot is null
    on every job sampled and is not decoded.
    """
    return value[1] if isinstance(value, list) and len(value) > 1 else None


def _location(locations: Any) -> str | None:
    """Every stated location's display string, joined the way the site's own UI joins them
    (`"Mountain View, CA, USA; Cambridge, MA, USA"`) — measured on the rendered page's location
    chip, not invented here."""
    if not isinstance(locations, list) or not locations:
        return None
    parts = [
        loc[0]
        for loc in locations
        if isinstance(loc, list) and loc and isinstance(loc[0], str) and loc[0]
    ]
    return "; ".join(parts) or None


def _posted_at(pair: Any) -> str | None:
    """A ``[seconds, nanos]`` timestamp field's date, or None.

    Verified not fabricated (2026-09-11): re-fetching the same page ~10s later left one job's
    triple unchanged, and across the 20 jobs on one page the values ranged from 2026-04-13 to
    2026-09-11 rather than all reading "now" — the ICIMS trap this checks for
    (`datePosted` fabricated on 22% of its boards, CLAUDE.md's ATS-provider notes). Uses the
    earliest of the three timestamp fields present on a job (index 12, "created"), which is
    always <= the other two on every job sampled.
    """
    if not isinstance(pair, list) or not pair:
        return None
    seconds = pair[0]
    if not isinstance(seconds, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _description(job: list[Any]) -> str | None:
    """ "About the job" + qualifications + responsibilities, in that reading order, tag-stripped
    once. Qualifications (index 4) already carries its own "Minimum qualifications:"/"Preferred
    qualifications:" ``<h3>`` headers from the source; the other two get one added here so the
    stripped text still reads as labelled sections rather than running together."""
    about = _pair(_field(job, 10))
    quals = _pair(_field(job, 4))
    responsibilities = _pair(_field(job, 3))
    parts: list[str] = []
    if about:
        parts.append(f"<h3>About the job</h3>{about}")
    if quals:
        parts.append(quals)
    if responsibilities:
        parts.append(f"<h3>Responsibilities</h3>{responsibilities}")
    return html_to_text("".join(parts)) if parts else None


class GoogleScraper(BaseScraper):
    """Google careers scraper — a single-company board (ADR-0139), no discovery, no detail pass."""

    ats = "google"
    has_detail_pass = (
        False  # every field, description included, comes off the listing page
    )

    def url(self) -> str:
        return f"{_LISTING_URL}?hl=en_US"

    def _page_url(self, page: int) -> str:
        return f"{_LISTING_URL}?hl=en_US&page={page}"

    def alias_key(self) -> str | None:
        """No sibling host to alias against — a single-company board resolves to itself
        (ADR-0139's per-scraper call, not the base default's redirect-following)."""
        return self.slug

    def _fetch_page(self, page: int) -> list[list[Any]]:
        data = _ds1_data(self._get(self._page_url(page)))
        jobs = _field(data, 0) if data is not None else None
        return jobs if isinstance(jobs, list) else []

    def fetch_raw(self) -> Any:
        first = _ds1_data(self._get(self._page_url(1)))
        jobs: list[list[Any]] = (
            list(_field(first, 0) or []) if first is not None else []
        )
        if first is None or not jobs:
            self.note_unreadable_board("ds:1 job data on page 1", "none found")
            return jobs
        total = _field(first, 2)
        total = total if isinstance(total, int) else 0
        if total > len(jobs):
            pages_needed = min(-(-total // _PAGE_SIZE), _MAX_PAGES)
            rest = list(range(2, pages_needed + 1))
            fetched = self.fan_out(rest, self._fetch_page, workers=_PAGE_WORKERS)
            for batch in fetched:
                if batch:
                    jobs.extend(batch)
            if pages_needed >= _MAX_PAGES:
                self.mark_truncated(
                    f"hit the {_MAX_PAGES}-page cap at {len(jobs)} of {total} postings — "
                    "the rest unread"
                )
        if total:
            self.mark_truncated_unless_negligible(
                len(jobs),
                total,
                f"read {len(jobs)} of {total} postings stated on page 1 — the board is live "
                "and its count moves during a multi-page walk, same as any page lost to a "
                "per-page fetch failure",
            )
        return jobs

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for item in raw or []:
            if not isinstance(item, list):
                continue
            native_id = _field(item, 0)
            title = _field(item, 1)
            if not native_id or not title:
                continue
            location = _location(_field(item, 9))
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{native_id}",
                    ats=self.ats,
                    company=_field(item, 7) or self.company,
                    title=str(title).strip(),
                    location=location,
                    remote=is_remote(location),
                    department=None,  # no team/org field found in the listing payload
                    url=_JOB_URL.format(id=native_id),
                    posted_at=_posted_at(_field(item, 12)),
                    scraped_at=scraped_at,
                    description=_description(item),
                    employment_type=None,  # the enum field found has no decoded label anywhere
                )
            )
        return jobs
