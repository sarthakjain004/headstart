"""BambooHR career-site scraper.

Adapted from kalil0321/ats-scrapers' ``bamboohr.py`` — same attribution convention as
``phenom.py``: every claim below was re-checked against the live API rather than trusted from
that implementation, and three of the four turned out wrong or incomplete (see the numbered list
below). Measurements are from a live sweep of ~1,500 jobs across 20 real tenants on 2026-09-16
(seeded from `experiment/ats-scraper-candidates/artifacts/parquet/bamboohr.parquet`, a
19,365-job/2,745-company sample of live BambooHR postings) plus a handful of fabricated slugs
probed to characterize a dead tenant.

Listing: ``GET https://{slug}.bamboohr.com/jobs/embed2.php`` — static, server-rendered HTML with
department-grouped ``<li id="bhrPositionID_{id}">`` blocks (title, terse "City, ST" location).
**No pagination parameter exists, and none was needed**: the highest-volume tenant found in the
sample — `theweitzcompany`, ~158 open jobs — returned every one in a single response, with no
"showing N of M" marker and no repeated ids. That isn't just a sample-size coincidence: unlike
Zoho's JS-hydrated widget (a real 750-job ceiling, see ``zoho.py``'s docstring), this page is
plain server-rendered HTML with no client-side "load more" — there is nowhere for a silent cap to
hide the way Phenom's ``size=1000``-returning-500 or Zoho's ceiling do. Not proof for a tenant
past ~160 jobs (none was found to test against), but the measured evidence and the mechanism
agree, and there's nothing here resembling freshteam's hard 1000-job widget cap.

**Dead vs. live-but-empty, from the same HTTP 200.** A genuinely nonexistent tenant's widget
answers `200` with an **empty body** (0 bytes) — checked against 5 fabricated slugs, all 0 bytes.
A live tenant, even with zero open roles, always serves the ~1.2KB `BambooHR-ATS-board` wrapper
(a "We currently have no open positions" blank state) — checked against 3 confirmed-live-but-
jobless tenants (`1global`, `1inch`, `22bet`). `fetch_raw` keys on that wrapper's presence, not
the status code, which is 200 either way; `check_liveness.py`'s `p_bamboohr` uses the same signal.

Detail: ``GET https://{slug}.bamboohr.com/careers/{id}/detail`` — the clean JSON XHR the page's
own SPA hydrates from. No CSRF/session/Referer needed — a bare GET succeeded on every one of
~1,500 detail fetches in this measurement. It supplies almost everything the listing doesn't:
``description``, ``employmentStatusLabel``, ``compensation``, ``datePosted``,
``minimumExperience``, ``departmentLabel``, and a canonical ``location``/``locationType``.

Four things checked against kalil0321's implementation rather than trusted from it:

1. **`locationType == "2"` is Hybrid, not remote.** Upstream reads `'1'`/`'2'`/`'true'` as
   `is_remote=True`. Measured over 1,508 live detail fetches (20 tenants): `"0"`=1,256 (83.3%,
   onsite), `"1"`=112 (7.4%, remote), `"2"`=140 (9.3%, hybrid) — a real, sizeable bucket upstream
   would mislabel wholesale as remote. Cross-checked against the widget's own terse location text,
   which independently confirms a "(Hybrid)" suffix on every `"2"` sampled (10/10, e.g.
   "Toronto, ON (Hybrid)"). `Job.remote` is already tri-state for exactly this case —
   `ashby.py`'s `_remote` and `workday.py`'s `_remote_from` both resolve Hybrid to `None` rather
   than guess either way — so this scraper follows that existing convention (see `_remote` below)
   instead of inventing a new one.
2. **The 25,000-char description cap is upstream's own choice, not BambooHR's.** 31 of 1,508
   sampled descriptions (2.1%) run longer than 25k chars (max observed: 41,214). Truncating there
   would silently drop real content on a measurable slice of postings for no documented reason,
   so this scraper doesn't reproduce the cap.
3. **`minimumExperience` is a real, well-populated native field upstream never reads at all.**
   614 of 627 sampled jobs (97.9%) carry a seniority-tier label — "Entry-level", "Mid-level",
   "Experienced", "Manager/Supervisor", "Senior Manager/Supervisor", "Executive", "Senior
   Executive" — matching `Job.experience`'s documented shape ("e.g. ... Mid-Senior level"
   verbatim). `headstart.experience.extract()`'s seniority tier already knows how to turn most of
   these words into a numeric floor downstream; upstream's `_apply_opening_to_job` never even
   reads this field, so it was pure loss before.
4. **No auth needed, full stop** — upstream doesn't claim otherwise, but it's worth stating this
   was actually checked: no cookie jar, no Referer, no CSRF token, on the same ~1,500-request pass.

Rate limit: **none found.** An 80-request sweep at concurrency 4/8/16/32 and a 300-request sweep
at 32/64/128 both stayed all-200 with no latency blowup up to 122 req/s; the full ~1,500-request
detail pass at concurrency 32 (~50 req/s sustained) was also all-200. `detail_workers` is set to
16 — comfortably under everything measured clean, not the ceiling itself.

**The company name is ``/careers/company-info``'s ``result.name``** (:meth:`BambooHRScraper.
board_page`). `/careers` renders no server-side `<title>` — a client-rendered SPA — but the JSON
that SPA loads its header from states the tenant's account name: 194 of 200 affected Boards
(2026-09-24), e.g. `cintel` -> "Cintel Inc", `jcifederal` -> "Johnson Controls Federal Systems".
One ~400-byte GET per Board; the rest keep their slug.

`compensation` is free-text prose, not a structured min/max field — `'£28,090'`,
`'$160,000 - $190,000 + based on experience'`, `'Negotiable'`, `'30-35 (DOE)'` (503 non-null
values sampled, no consistent shape). `salary.py`'s `_field_generic` already extracts a
range/single figure plus whatever ISO currency code or period phrase the string states, and
declines the ambiguous shapes ("Negotiable", "$100K+ ...") rather than guess — exactly the
caution this field needs, so no dedicated `_field_bamboohr` parser was added.

**ADR-0017 tech gate, added 2026-09-22: this was the only ``has_detail_pass`` ATS without one.**
The widget's own markup already groups positions under a department header —
``<li id="bhrDepartmentID_…">`` wrapping a ``BambooHR-ATS-Department-Header`` ``<div>`` and a
nested ``<ul>`` of that department's ``<li id="bhrPositionID_…">`` rows (verified live 2026-09-22
on ``a3.bamboohr.com``: 7 department blocks wrapping 28 positions, and job 577's detail
``departmentLabel`` — ``'ASH IV'`` — is the *identical string* its department header already
carries, not an approximation of it). :func:`_department_map` reads that for free at listing
time, so the gate (:meth:`BambooHR.fetch_raw`) can ask ``is_tech(title, department)`` — the same
question ``filter_tech`` asks downstream — before spending a detail fetch on a posting it will
drop. Department also now survives a detail the gate skipped or that failed: ``parse`` falls back
to the listing's own mapped department when ``departmentLabel`` is absent, rather than nulling the
one field the tech filter itself reads.

**Listing parse-drift guard, added 2026-09-22.** If the ``bhrPositionID_`` marker is present in
the fetched HTML but ``_POSITION`` matches nothing, the row markup changed under the regex — not
a Board with nothing open — so this is `note_unreadable_board`'d rather than returned as an empty,
whole listing (which ADR-0083's one-scrape grace period would then evict). `_POSITION` is the
looser, id-only regex of the two scrapers measured against this ATS (`jazzhr.py`/`jobvite.py`
match a named wrapper element instead), so this is cheap insurance against a silent full-Board
eviction, not a bug with a live reproduction today.
"""

from __future__ import annotations

import json
import re
from typing import Any

from headstart import company_name
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper, DetailLost, DetailRequest

#: One job's `<li>` block in the widget — id in the tag, title/location inside the body.
_POSITION = re.compile(
    r'<li\s+id="bhrPositionID_(?P<id>\d+)"[^>]*>(?P<body>.*?)</li>',
    re.IGNORECASE | re.DOTALL,
)
_TITLE = re.compile(r"<a[^>]*>(?P<title>.*?)</a>", re.IGNORECASE | re.DOTALL)
_LOCATION = re.compile(r"BambooHR-ATS-Location[^>]*>(?P<loc>[^<]*)<", re.IGNORECASE)
#: A department header — `<li id="bhrDepartmentID_…">` wraps a `<div class="…Department-
#: Header">NAME</div>` then a `<ul>` of that department's own `<li id="bhrPositionID_…">` rows
#: (module docstring). The header's own `<li>` doesn't close until after its nested positions
#: do, so it can't be bounded the way `_POSITION` bounds a position block; matching just the
#: header and walking match offsets in document order (see `_department_map`) sidesteps that
#: without needing an HTML parser.
_DEPARTMENT = re.compile(
    r'BambooHR-ATS-Department-Header"[^>]*>(?P<name>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)

#: The wrapper every LIVE tenant serves, jobs or not (see module docstring's dead-vs-empty
#: measurement) — a dead tenant's widget has none of this, just an empty body.
_LIVE_WRAPPER = "BambooHR-ATS-board"

#: locationType -> Job.remote. "2" is Hybrid and stays None — neither remote nor onsite is true,
#: matching ashby.py's _remote / workday.py's _remote_from (see module docstring, finding 1).
_LOCATION_TYPE_REMOTE: dict[str, bool | None] = {"0": False, "1": True, "2": None}


def _canonical_location(location: Any) -> str | None:
    """The detail record's structured ``location`` dict, joined "City, State, Country" —
    same granularity as zoho's `_zoho_location`, postal code omitted as too fine-grained for a
    display string."""
    if not isinstance(location, dict):
        return None
    parts = [
        str(location[k]).strip()
        for k in ("city", "state", "addressCountry")
        if location.get(k)
    ]
    return ", ".join(parts) or None


def _department_map(page: str) -> dict[str, str]:
    """``{position_id: department name}`` off the widget's own department blocks (module
    docstring). Built by walking department-header and position matches together in document
    order and carrying the most recently seen header forward — a position before any header (a
    straggler after the last department block) gets no entry, so its department is ``None``."""
    events = [(m.start(), "dept", m.group("name")) for m in _DEPARTMENT.finditer(page)]
    events += [(m.start(), "pos", m.group("id")) for m in _POSITION.finditer(page)]
    events.sort(key=lambda e: e[0])
    result: dict[str, str] = {}
    current: str | None = None
    for _, kind, value in events:
        if kind == "dept":
            current = html_to_text(value)
        elif current:
            result[value] = current
    return result


def _remote(location_type: Any, listing_location: str | None) -> bool | None:
    """The native `locationType` signal when the detail pass has one, else the listing's own
    terse text (also the only signal available if the detail fetch failed)."""
    if location_type in _LOCATION_TYPE_REMOTE:
        return _LOCATION_TYPE_REMOTE[location_type]
    return is_remote(listing_location)


class BambooHRScraper(BaseScraper):
    ats = "bamboohr"
    url_shape = r"https://[^/]+\.bamboohr\.com/careers/\d+"
    detail_workers = 16  # measured clean to conc=128 / 122 req/s — see module docstring
    has_detail_pass = True  # description, salary, experience etc. all come from /detail

    def url(self) -> str:
        return f"https://{self.slug}.bamboohr.com/jobs/embed2.php"

    def job_url(self, native_id: str) -> str:
        return f"https://{self.slug}.bamboohr.com/careers/{native_id}"

    def board_page(self) -> str:
        """The careers SPA's own ``company-info`` JSON — not a page with a title (module
        docstring), so :meth:`company_from_page` reads it."""
        return f"https://{self.slug}.bamboohr.com/careers/company-info"

    def company_from_page(self, page: str | None) -> str | None:
        """``result.name`` of the ``company-info`` JSON, through this ATS's guards."""
        try:
            data = json.loads(page or "")
        except json.JSONDecodeError:
            return None
        result = data.get("result") if isinstance(data, dict) else None
        stated = result.get("name") if isinstance(result, dict) else None
        return company_name.from_field(
            self.ats, stated if isinstance(stated, str) else None
        )

    def fetch_raw(self) -> Any:
        page = self._get()
        if _LIVE_WRAPPER not in page:
            # A tenant that was live when the liveness ledger last checked it but has since gone
            # dark — see the module docstring's dead-vs-empty measurement. Not `mark_truncated`:
            # there is nothing partial here to protect, this Board has no public board left.
            self.note_unreadable_board(
                f"the {_LIVE_WRAPPER} widget wrapper", "an unrecognized or empty body"
            )
            return {"page": "", "details": {}}
        matches = list(_POSITION.finditer(page))
        if not matches and "bhrPositionID_" in page:
            # The marker is present but nothing matched `_POSITION` — the row markup changed
            # under it, not a Board with nothing open (module docstring's parse-drift guard).
            self.note_unreadable_board(
                "bhrPositionID_ rows parsed from the widget",
                "the marker present but the row markup didn't match",
            )
            return {"page": "", "details": {}}
        # ADR-0017 tech gate (module docstring): `parse` reads the same title/department pair
        # off this same listing, so the gate reaches the verdict `filter_tech` will reach.
        departments = _department_map(page)
        candidates = []
        for m in matches:
            title_m = _TITLE.search(m.group("body"))
            if title_m:
                candidates.append(
                    {
                        "id": m.group("id"),
                        "title": html_to_text(title_m.group("title")),
                        "department": departments.get(m.group("id")),
                    }
                )
        self.note_unread_rows(
            len(matches) - len(candidates), len(matches), "carried no title link"
        )
        details = self.run_detail_pass(
            candidates,
            key_of=lambda candidate: candidate["id"],
            what="job details",
            title_of=lambda candidate: candidate["title"],
            department_of=lambda candidate: candidate["department"],
        )
        # Computed once here for the gate above and threaded through for `parse` to reuse,
        # rather than walking the same HTML's department blocks a second time per Board.
        return {"page": page, "details": details, "departments": departments}

    def detail_request(self, candidate: dict) -> DetailRequest:
        return DetailRequest(f"{self.job_url(candidate['id'])}/detail")

    def read_detail(self, candidate: dict, response: Any) -> dict:
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            raise DetailLost("unparseable detail JSON") from None
        opening = (
            (data.get("result") or {}).get("jobOpening")
            if isinstance(data, dict)
            else None
        )
        if not opening:
            raise DetailLost("empty jobOpening")
        return opening

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        page, details = (
            (raw["page"], raw["details"]) if isinstance(raw, dict) else (raw, {})
        )
        # `fetch_raw` already walked the department blocks once, for the gate — reuse that
        # rather than doing it again here. Recomputed only for a caller that built `raw` by
        # hand without a "departments" key (every real fetch_raw path always carries one).
        departments = (
            raw.get("departments")
            if isinstance(raw, dict) and raw.get("departments") is not None
            else _department_map(page)
        )
        jobs: list[Job] = []
        for m in _POSITION.finditer(page):
            native_id = m.group("id")
            body = m.group("body")
            title_m = _TITLE.search(body)
            title = html_to_text(title_m.group("title")) if title_m else None
            if not title:
                continue
            loc_m = _LOCATION.search(body)
            listing_location = html_to_text(loc_m.group("loc")) if loc_m else None
            opening = details.get(native_id) or {}
            location = _canonical_location(opening.get("location")) or listing_location
            jobs.append(
                Job(
                    id=self.job_id(native_id),
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=_remote(opening.get("locationType"), listing_location),
                    # departmentLabel first (the detail's own, when fetched); the listing's own
                    # mapped department otherwise — survives a detail the ADR-0017 gate skipped
                    # or that failed, where it used to go null along with everything else the
                    # detail alone supplied.
                    department=(opening.get("departmentLabel") or "").strip()
                    or departments.get(native_id),
                    url=self.job_url(native_id),
                    posted_at=opening.get("datePosted") or None,
                    scraped_at=scraped_at,
                    description=html_to_text(opening.get("description")),
                    experience=(opening.get("minimumExperience") or "").strip() or None,
                    employment_type=(opening.get("employmentStatusLabel") or "").strip()
                    or None,
                    salary=self._salary_field(opening),
                )
            )
        return jobs

    def _salary_field(self, raw: dict) -> str | None:
        """``compensation`` is free-text prose (see module docstring) — passed straight through
        for ``salary.py``'s ``_field_generic`` to parse conservatively; no dedicated
        ``_field_bamboohr`` parser (see module docstring's closing paragraph)."""
        value = (raw.get("compensation") or "").strip()
        return value or None
