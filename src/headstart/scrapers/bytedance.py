"""ByteDance's own in-house careers system (jobs.bytedance.com) — a single-company board, not a
platform other companies rent (ADR-0139). There is exactly one tenant, so ``slug`` is fixed to
``"jobs.bytedance.com"``, never discovered.

Adapted with reference to jobhive's ByteDance scraper (kalil0321/ats-scrapers, MIT); every fact
below was re-measured live against the real endpoint on 2026-09-11 rather than trusted from it —
full numbers in ``docs/bytedance/2026-09-11_api-measurement.md``.

**The public browse page has moved off ``jobs.bytedance.com``, but the API has not.**
``GET https://jobs.bytedance.com/en/position`` answers a bare 302 to
``https://joinbytedance.com/search`` — a Next.js app built as ``bd-career-site`` and served from
ByteDance's own Feishu CDN (path segment ``atsx-throne``, an internal ATS codename). That app is
fully client-rendered: the static HTML carries no job data at all, only ``<script>`` bundle tags.
The listing and filter calls it makes are cross-origin, back to ``jobs.bytedance.com`` — found by
downloading those bundles and reading the minified API client (module ``61215`` in
``8321-22536180820e7ed8.js``), not by guessing.

**Endpoint, verified live.** Base ``https://jobs.bytedance.com/api/v1/public/supplier``, one call:
``POST {base}/search/job/posts`` with a JSON body of
``{keyword, limit, offset, job_category_id_list, location_code_list, recruitment_id_list,
subject_id_list, tag_id_list}`` (the five ``*_id_list`` filters all accept ``[]`` — no facet has to
be picked to read the whole board). Two headers are **required**, not cookie fallbacks: a request
missing ``accept-language``/``website-path`` returns HTTP 400 ``invalid request`` with no JSON
body; a well-formed request but an unrecognised ``website-path`` value (tried ``"cn"``) also 400s.
``"en-US"``/``"en"`` — the app's own default when no locale cookie is set — work and are the only
values this scraper needs, since only the English corpus is indexed (CLAUDE.md's English-only
search scope). No auth cookie, Origin header, or Referer is needed; a bare ``curl`` with this
repo's own User-Agent gets the same 200 a browser does.

**One call reads the whole global board — no region looping.** With no location filter, the API
returns every posting regardless of country: the same unfiltered query's first page alone spanned
the US, Singapore, Malaysia, Thailand, the UAE, Hong Kong, the UK, Mexico and South Korea. Trying a
non-default ``website-path`` 400s rather than narrowing the result, so there is no live locale
knob to split the request by — measured 2026-09-11, one working header pair against three rejected
ones (``cn``, ``experienced``, ``students``). ``data.count`` was **1,395** at measurement time and
matched the number of ids returned exactly once every page had been read.

**The listing IS the detail — no second fetch.** Every posting in ``job_post_list`` already
carries ``description`` and ``requirement`` in full (measured: 0 of 100 sampled postings show any
sign of truncation or an HTML tag — both fields are plain text with literal ``\\n`` newlines, not
markup), so ``has_detail_pass`` stays False. A ``GET {base}/job/posts/{id}`` route exists in the
bundle's route table (``JOB_DETAIL = "/job/posts/"``) but was never called: probed live with a few
plausible bodies, it answered ``{"code":-9000002,...,"message":"params is invalid"}`` on a 200 —
worth documenting as a discovered but unused route, not worth chasing when the field it would add
is already on every listed row.

**Pagination is offset+limit with no observed cap.** ``limit`` was tried at 10, 100, 1,000 and
2,000 and every one came back un-clamped (2,000 alone read all 1,395 postings in a single call);
``offset`` past the end returns an empty ``job_post_list`` with ``count`` unchanged, the ordinary
end-of-list shape. This scraper still pages at :data:`_PAGE_SIZE` rather than requesting one giant
page, on the same reasoning as every other paginated scraper here: a limit that happens to cover
today's board is not a contract, and a silent clamp introduced later would otherwise look like a
truncation this code can't detect.

**No rate limit found.** 15 sequential POST requests (10 at limit=10, 5 at limit=100, spanning
offsets 0-590) all returned 200, ~2.3-4.3s each — server-side latency, not throttling; no 429s, no
slowdown pattern.

**No ``posted_at`` anywhere in the payload.** Unlike icims (which fabricates a date on 22% of
boards) or Oracle (which states one on every requisition), a ByteDance posting's fields are id,
code, title, description, requirement, recruit_type, job_category, city_info (a nested
place-name chain up to country), tag_list, job_subject, vacancies, department_info,
job_post_info (salary/experience/degree — all null in every one of the 100 sampled postings) and
process_type/channel_online_status. There is no date field to read or fabricate, so ``posted_at``
is always None here — a measured fact, not an oversight.

**Job detail page:** ``https://jobs.bytedance.com/en/position/{id}`` answers 200 for a real id
(verified: the id from a live search result). It is itself a client-rendered shell like the search
page, so a bogus id also answers 200 — this scraper does not depend on that page's content, only
on it being the stable link shape ByteDance's own site would route a person to.

**Is this the same platform TikTok's careers site runs on?** Partially confirmed, not fully.
``lifeattiktok.com`` answered every probe with HTTP 503 (``x-tt-system-error: 23``) rather than
content, so its API shape could not be read live — but its 503 page itself carries
``Server: TLB`` and the ``x-tt-*`` trace-header family, the same internal load-balancer and header
convention ``jobs.bytedance.com`` and ``joinbytedance.com`` both answer with. That is ByteDance's
own infrastructure fingerprint, not a coincidence of two unrelated vendors — so the two career
sites likely share a backend, but the TikTok-specific request/response shape remains unverified
(the site was erroring, not just slow, across every attempt on 2026-09-11). If TikTok's scraper
(built separately) finds the same ``/api/v1/public/supplier`` routes live on its own host, the two
should be reconsidered as one scraper with a brand filter rather than two independents — see
``docs/bytedance/2026-09-11_api-measurement.md``.
"""

from __future__ import annotations

from typing import Any

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_BASE = "https://jobs.bytedance.com/api/v1/public/supplier"
_SEARCH_URL = f"{_BASE}/search/job/posts"

#: The API's own limit accepted un-clamped up to 2,000 in testing (module docstring); paginated
#: here anyway rather than requested as one giant page, since an un-clamped limit today is not a
#: guarantee the API keeps that shape tomorrow.
_PAGE_SIZE = 200
#: Fetch-bound ceiling: 50 x 200 = 10,000 postings, well above the measured board (1,395).
_MAX_PAGES = 50

#: Required on every call (module docstring) — a request missing either 400s with no JSON body.
#: Fixed to English: CLAUDE.md scopes the search corpus to English only, and no other
#: ``website-path`` value tested (``"cn"``) was accepted.
_HEADERS = {
    "Content-Type": "application/json",
    "accept-language": "en-US",
    "website-path": "en",
}


class ByteDanceScraper(BaseScraper):
    """ByteDance's own careers API — a single-company board (ADR-0139). ``slug`` is fixed to
    ``"jobs.bytedance.com"``."""

    ats = "bytedance"

    def __init__(self, slug: str, company: str | None = None) -> None:
        super().__init__(slug, company or "ByteDance")

    def url(self) -> str:
        return _SEARCH_URL

    def alias_key(self) -> str | None:
        """A single-company board has no sibling host to alias against (ADR-0139's consequence:
        decide this per scraper rather than default it), so this Board resolves to itself."""
        return self.slug

    def _search(self, offset: int) -> dict[str, Any]:
        response = http.fetch(
            "POST",
            _SEARCH_URL,
            headers={**_HEADERS, "User-Agent": USER_AGENT},
            json={
                "keyword": "",
                "limit": _PAGE_SIZE,
                "offset": offset,
                "job_category_id_list": [],
                "location_code_list": [],
                "recruitment_id_list": [],
                "subject_id_list": [],
                "tag_id_list": [],
            },
            timeout=30,
            **self._egress(),
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(
                f"bytedance search returned code {payload.get('code')}: "
                f"{payload.get('message') or 'no message'}"
            )
        return payload.get("data") or {}

    def fetch_raw(self) -> Any:
        """Page through ``search/job/posts`` until the board runs out (module docstring: no
        observed limit cap, so paging is this scraper's own choice, not the API's requirement)."""
        posts: list[dict] = []
        total = 0
        offset = 0
        for _ in range(_MAX_PAGES):
            data = self._search(offset)
            total = data.get("count") or total
            batch = data.get("job_post_list") or []
            posts.extend(batch)
            offset += _PAGE_SIZE
            if not batch or (total and len(posts) >= total):
                break
        else:
            self.mark_truncated(
                f"hit the {_MAX_PAGES}-page cap at {len(posts)} of {total or 'unknown'} "
                "postings — the rest unread"
            )
        if total and len(posts) < total:
            self.mark_truncated_unless_negligible(
                len(posts),
                total,
                f"read {len(posts)} of {total} postings — the rest is unread, not absent",
            )
        return posts

    def job_url(self, job_id: str) -> str:
        return f"https://jobs.bytedance.com/en/position/{job_id}"

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for p in raw:
            job_id = str(p.get("id") or "")
            title = (p.get("title") or "").strip()
            if not job_id or not title:
                continue
            location = _location(p.get("city_info"))
            category = p.get("job_category") or {}
            recruit_type = p.get("recruit_type") or {}
            description = html_to_text(
                "\n\n".join(
                    part
                    for part in (p.get("description"), p.get("requirement"))
                    if part
                )
            )
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{job_id}",
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=is_remote(location),
                    department=category.get("en_name") or category.get("i18n_name"),
                    url=self.job_url(job_id),
                    posted_at=None,  # not exposed by this API (module docstring)
                    scraped_at=scraped_at,
                    description=description,
                    employment_type=recruit_type.get("en_name")
                    or recruit_type.get("i18n_name"),
                )
            )
        return jobs


def _location(city_info: Any) -> str | None:
    """ "City, Region, Country" from ``city_info``'s nested ``parent`` chain, skipping a level
    whose name exactly repeats the one just added (many tenants state the same name at two or
    three levels — Singapore's city/state/country are all literally "Singapore")."""
    parts: list[str] = []
    node = city_info
    while isinstance(node, dict):
        name = (node.get("en_name") or node.get("i18n_name") or "").strip()
        if name and (not parts or parts[-1] != name):
            parts.append(name)
        node = node.get("parent")
    return ", ".join(parts) or None
