"""PyjamaHR job-board scraper (``jobs.pyjamahr.com/{slug}``).

Every tenant's public board is one path segment on a shared host, and that segment — the
**company slug** — is this scraper's ``slug``. It keys the whole API too: the listing and the
detail endpoint both take ``?company_slug={slug}`` (the careers page's own board route builds its
requests with it), and the ``company_uuid`` the original research keyed on is only an alternative
spelling of the same filter, available nowhere but the board page's SSR payload. The slug is the
URL, the sitemap key and the API key at once, so it is the identity. It is **case-sensitive**:
``PYJAMAHR`` answers an empty envelope where ``pyjamahr`` answers the board.

Everything below was measured 2026-09-22 against the live API — a census of every one of the 757
tenants the vendor's jobs sitemap and the Wayback Machine name (8,897 listing rows), a detail pass
over 1,741 of those postings, and ~3,800 requests in total — and is written up in
``docs/pyjamahr/2026-09-22_career-api-measurement.md``.

**The listing is one call.** The envelope is Django REST Framework's ``count``/``next``/
``previous``/``results`` at a default page of 10, but the undocumented ``limit`` parameter is
honoured with no ceiling — ``limit=999999999`` returns the 643-row Board whole with ``next`` null,
while ``page_size`` is ignored. ``count`` equalled the rows served on all 757 Boards. ``next`` is
still followed if it ever comes back non-null, so a future cap cannot silently shorten a Board.

**An unknown slug is not an error.** It answers HTTP 200 with ``count: 0`` — byte-identical to a
live Board with nothing open. The scraper cannot tell the two apart and does not try; the liveness
prober does (``jobs.pyjamahr.com/{slug}`` answers 404 for a slug that is not a tenant), which is
what keeps a dead tenant out of the scrape list in the first place.

**The listing carries no description, and the detail carries the fields that matter.** A listing
row has id, slug, title, location, ``other_locations``, ``department_name``, ``workplace_type`` and
both experience bounds. ``description`` (present and non-empty on 1,741 of 1,741 details),
``job_type``, the salary quartet and ``created_at`` exist only on
``/api/career/jobs/{id}/?company_slug=`` — which 404s without the company key, and 404s with
another tenant's. So this is a detail-pass ATS, and like oracle it does **not** take ADR-0048's
skip of the already-described: the detail is the only source of ``employment_type``, ``salary``
and ``posted_at``, and skipping it would blank three fields that had values. The skip it does
take is the tech gate (ADR-0166), as an **exact** site: ``parse`` reads ``title`` and
``department_name`` off the same listing row the gate reads and the detail overrides neither, so
the gate asks ``filter_tech``'s question with ``filter_tech``'s own inputs. A gated posting still
ships as a Job without a description. At ~25% tech that is three of four detail fetches saved.

**``published_internally`` rows are returned but not shown.** The API serves them (112 of 8,897
rows across 39 tenants) and the board's own page filters them out before rendering — the
``[company]`` route chunk drops every row where the flag is set. They are internal postings, and
this scraper drops them the same way. ``count`` includes them, so the shortfall check compares
against the unfiltered walk.

**``remote`` is dead; ``workplace_type`` is the tenant's answer.** The detail's ``remote``
boolean was ``false`` on every one of 1,741 postings, including the 102 whose ``workplace_type``
was ``REMOTE``. The type never disagrees with the location either — of 7,674 rows stating
``ON_SITE`` or ``HYBRID``, none names a remote location — so the stated type wins outright and the
location guess (``is_remote``) is consulted only for the 51 rows that state nothing. HYBRID is not
remote, ashby's rule.

Not mapped, on purpose:
  - ``valid_through``: it was in the past on 1,408 of 1,741 postings that the board still lists
    and shows — a default 60 days after ``created_at``, not a closing date.
  - ``seniority``/``skill``/``education``: the post-hoc extractors read the description.
  - ``company``: neither payload names the employer; the board page's ``<title>`` does, and
    equalled ``companyDetails.name`` on 757 of 757 pages (:meth:`board_page`, ADR-0114).

Known limits: 6 of 8,897 rows had no ``slug``, so their link falls back to the board page; 2
tenants publish two postings under one slug, and the page shows one of them. No rate limit was
found — 1,486 detail requests at concurrency 32 ran at 84 req/s with zero non-200s — and the API
is User-Agent-agnostic (bare, curl, python-requests and a browser string all 200).
"""

from __future__ import annotations

import json
from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper, DetailRequest

_API = "https://api.pyjamahr.com/api/career/jobs/"
_BOARD = "https://jobs.pyjamahr.com"

#: The page size asked for. Any positive integer is honoured and no ceiling was found (module
#: docstring), and the largest Board in the census is 643 rows, so this reads every known Board
#: in one call. Not a cap: `next` is followed whenever it is set.
_LIMIT = 1000
#: Our ceiling on that walk. Reaching it means the Board did not end, we stopped reading it.
_MAX_PAGES = 100
#: Concurrent detail fetches. Measured clean at 32 (1,486 requests, 84 req/s, zero non-200s);
#: half that here for the same reason oracle gives — `harvest` scrapes Boards concurrently and
#: each Board fans out, so peak in-flight is the product — and because 16 is what icims, zwayam
#: and oracle already run.
_DETAIL_WORKERS = 16

#: `job_type` codes -> labels. The five values observed across 1,741 details, in frequency order
#: (1,667 / 48 / 16 / 8 / 2). An unobserved code passes through as the provider spells it.
_JOB_TYPE_LABELS: dict[str, str] = {
    "FULLTIME": "Full Time",
    "CONTRACT-BASED": "Contract",
    "PART-TIME": "Part Time",
    "INTERN": "Internship",
    "FREELANCER": "Freelance",
}

#: `salary_type` codes -> the period spelling `salary._period_multiplier` reads. The three values
#: observed (1,022 / 641 / 78). An unobserved code yields no salary at all rather than a figure
#: read at the wrong period: annual is the parser's default, so a WEEKLY figure passed through
#: bare would be served 52x too low.
_SALARY_PERIODS: dict[str, str] = {
    "ANNUAL": "per-year",
    "MONTHLY": "per-month",
    "HOURLY": "per-hour",
}


def _public(items: list[dict]) -> list[dict]:
    """The rows the board itself shows: everything not flagged `published_internally`."""
    return [i for i in items if not i.get("published_internally")]


def _location(item: dict) -> str | None:
    """`location` plus `other_locations`, "; "-joined in order without repeats — the multi-place
    form workday and freshteam already use, so a posting open in several cities matches the
    substring location filter on each of them. `other_locations` is non-empty on 15.7% of rows
    and repeats `location` on one."""
    places: list[str] = []
    for place in [item.get("location"), *(item.get("other_locations") or [])]:
        if place and place not in places:
            places.append(place)
    return "; ".join(places) or None


def _remote(item: dict, location: str | None) -> bool | None:
    """The tenant's stated `workplace_type` when there is one, else the location guess.

    The order is safe rather than merely convenient: across 7,674 rows stating ON_SITE or HYBRID
    not one names a remote location, so there is no measured case of the guess having anything
    to add to a stated type. The detail's `remote` boolean is not consulted at all — it was
    false on every REMOTE posting measured (module docstring)."""
    workplace = item.get("workplace_type")
    if workplace:
        return workplace == "REMOTE"
    return is_remote(location)


def _digits(value: float) -> str:
    """A float as digits — salary bounds and experience years both arrive as floats. Never
    `:g`, which writes 1,200,000 as `1.2e+06`, the trap keka's own salary test pins."""
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _experience(item: dict) -> str | None:
    """`min_experience`..`max_experience` as the "N-M years" string `experience.from_field`
    reads. Both are floats on every row measured, always integral and never inverted. Coinciding
    bounds stay a range ("3-3 years"): the parser keeps that stated ceiling, where a bare
    "3 years" would read as open-ended."""
    lo, hi = item.get("min_experience"), item.get("max_experience")
    if lo is None:
        return None
    if hi is None:
        return f"{_digits(lo)} years"
    return f"{_digits(lo)}-{_digits(hi)} years"


class PyjamaHRScraper(BaseScraper):
    ats = "pyjamahr"
    # scraper: f"https://jobs.pyjamahr.com/{slug}/{job_slug}", the page's own canonical link
    # (verified 200 with a JobPosting JSON-LD; the numeric id and the uuid both 307 to a
    # `?job_uuid=` form of the board page instead). Tenant and job slugs are `[a-z0-9_-]`
    # across all 8,897 rows. The second segment is optional because 6 rows carry no slug and
    # link to the board page itself.
    url_shape = r"https://jobs\.pyjamahr\.com/[\w-]+(?:/[\w-]+)?"
    detail_workers = _DETAIL_WORKERS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    def url(self) -> str:
        return f"{_API}?company_slug={self.slug}&limit={_LIMIT}"

    def board_page(self) -> str:
        """The board page, whose ``<title>`` is the bare company name — equal to the SSR
        payload's ``companyDetails.name`` on 757 of 757 live tenants, so the shared title
        resolver reads it through a catch-all pattern (`company_name.PATTERNS["pyjamahr"]`)."""
        return f"{_BOARD}/{self.slug}"

    def job_url(self, job_slug: str | None) -> str:
        """The posting's canonical page, or the board page for the rare slugless row."""
        if job_slug:
            return f"{_BOARD}/{self.slug}/{job_slug}"
        return f"{_BOARD}/{self.slug}"

    def _listing(self) -> list[dict]:
        """Every row the API returns for this Board, `published_internally` ones included.

        One call in practice (`_LIMIT`), but `next` is followed whenever the API sets it, and a
        walk that ends short of `count` is reported: the two are equal on every one of the 757
        Boards measured, so a shortfall here is the API changing under us, not noise.
        """
        items: list[dict] = []
        count = 0
        url = self.url()
        for _ in range(_MAX_PAGES):
            data = json.loads(self._get(url))
            count = data.get("count") or count
            items.extend(data.get("results") or [])
            url = data.get("next")
            if not url:
                break
        else:
            self.mark_truncated(
                f"hit the {_MAX_PAGES}-page cap at {len(items)} of {count or 'unknown'} "
                "postings — the rest unread"
            )
        if count and len(items) < count:
            self.mark_truncated_unless_negligible(
                len(items),
                count,
                f"read {len(items)} of {count} postings — the rest is unread, not absent",
            )
        return items

    def fetch_raw(self) -> Any:
        listed = self._listing()
        # Details only for the rows `parse` will emit — an internal posting's detail is a fetch
        # for a Job that never ships — and, in the pipeline, only for the ones the tech filter
        # will keep. That gate is exact here: `parse` reads `title` and `department_name` off
        # this same listing row and the detail overrides neither (ADR-0166 §3). ADR-0048's skip
        # of the already-described is deliberately not taken (module docstring).
        # Reported, not marked truncated: a missing detail costs this Job its description and
        # three derived fields, but the Job is still listed and still emitted, so the Board's
        # list is whole (ADR-0053 is about the list, not the fields).
        details = self.run_detail_pass(
            [row for row in _public(listed) if row.get("id") is not None],
            key_of=lambda row: str(row["id"]),
            what="detail payloads",
            title_of=lambda row: row.get("title"),
            department_of=lambda row: row.get("department_name"),
        )
        return {"results": listed, "details": details}

    def detail_request(self, row: dict) -> DetailRequest:
        # The company key is required here too: without it, or with another tenant's, the
        # endpoint answers 404 `{"detail": "Not found."}` — the same body a posting that closed
        # between the listing and this call returns.
        return DetailRequest(f"{_API}{row['id']}/?company_slug={self.slug}")

    def read_detail(self, row: dict, response: Any) -> dict:
        return json.loads(response.text)

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        details = raw.get("details") or {}
        jobs: list[Job] = []
        for item in _public(raw.get("results") or []):
            job_id = str(item["id"])
            d = details.get(job_id) or {}
            location = _location(item)
            job_type = d.get("job_type")
            jobs.append(
                Job(
                    id=self.job_id(job_id),
                    ats=self.ats,
                    company=self.company,
                    title=(item.get("title") or "").strip(),
                    location=location,
                    remote=_remote(item, location),
                    department=item.get("department_name") or None,
                    url=self.job_url(item.get("slug")),
                    # Detail-only: the listing states no date. ISO-8601 with the tenant's own
                    # zone offset (+05:30 on 87% of rows, -07:00, +08:00, ... on the rest).
                    posted_at=d.get("created_at"),
                    scraped_at=scraped_at,
                    description=html_to_text(d.get("description")),
                    experience=_experience(item),
                    employment_type=(
                        _JOB_TYPE_LABELS.get(job_type, job_type) if job_type else None
                    ),
                    salary=self._salary_field(d),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        """``Job.salary`` from the detail's ``min_salary``/``max_salary``/``currency``/
        ``salary_type``, only when the tenant marks it visible.

        The gate and the bounds agree exactly on live data — of 1,741 details, all 1,236 marked
        visible carried both bounds and none of the 505 hidden carried either — so requiring
        every part is a statement of the measured shape, not caution against an imagined one. The
        spelling ("30000-40000 INR per-month") is what `salary._field_generic` reads: a range, a
        currency code, and a phrase-shaped period. `is_salary_visible` is the tenant's own
        publication choice, so a hidden figure stays hidden even when the API leaks it.
        """
        if not raw or not raw.get("is_salary_visible"):
            return None
        period = _SALARY_PERIODS.get(raw.get("salary_type") or "")
        lo, hi = raw.get("min_salary"), raw.get("max_salary")
        if period is None or lo is None or hi is None:
            return None
        currency = (raw.get("currency") or "").strip()
        return " ".join(
            part for part in (f"{_digits(lo)}-{_digits(hi)}", currency, period) if part
        )
