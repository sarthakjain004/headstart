"""TikTok careers scraper (lifeattiktok.com) — a Single source scraper (ADR-0139): one company,
one board, ``slug`` fixed to ``"lifeattiktok.com"`` rather than discovered.

**The marketing frontend (``lifeattiktok.com``) is not the data source and, measured 2026-09-11,
answers every path with a bare 503** — the home page, ``/search/{id}``, and even ``/robots.txt``,
across three plain attempts each and three separate curl_cffi TLS impersonations
(chrome/chrome124/safari/edge), body ``503 Service Temporarily Unavailable`` / server ``TLB``. A
disallowed ``robots.txt`` is the one path a bot wall almost never blocks, so this reads as a real
origin outage rather than a fingerprint-based block — but it is unverified beyond that, and the
job-detail URLs this scraper emits (``lifeattiktok.com/search/{id}``, the reference implementation's
own convention) are accordingly unconfirmed to render; they are the right canonical link either way.

**The real data source is a separate, healthy host**, found by inspecting the reference scraper in
the sibling ``kalil0321/ats-scrapers`` project and confirmed live:

    POST https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts
    body {"limit": 100, "offset": N, "keyword": "", "category_id_list": [],
          "subject_id_list": [], "location_code_list": [], "job_function_id_list": []}
    -> {"code": 0, "data": {"job_post_list": [...], "count": N, "BaseResp": {...}}}

Only the ``website-path: tiktok`` header is required — measured with four header combinations
against the same request: no ``website-path`` at all is HTTP 400 (``invalid request``), and a
*wrong* value (``bytedance``) is also 400, but ``website-path: tiktok`` alone (no ``Origin``, no
``Referer``) is 200 with the same body as the full browser-shaped header set. The reference
scraper's docstring claims ``Origin``/``Referer`` are required too; that is not what this host
does today.

Pagination is offset/limit, capped at 100/page and stopping on a short or empty page or once
``offset >= count`` — same shape as every other offset-paginated ATS here. **No rate limit found**
in a 12-request sample (offsets 0-1100, ~12s wall clock): all 200, no ``Retry-After``, no 429/5xx.
12 requests is a small sample, not a stress test — it rules out an aggressive per-request wall, not
a sustained-volume one, but the whole board is one page count (~43 pages at limit=100).

**Two independent full sweeps, run back-to-back 2026-09-12, collected identical id sets** — 4,239
postings each (the board grew from the 4,231 measured 2026-09-11), zero ids in one sweep and not
the other, confirming the ``offset >= count`` terminator is reliable rather than an artifact of one
lucky crawl. **A live-confirmed failure mode the terminator alone does not cover:** a malformed
request (e.g. a negative ``offset``) answers HTTP 200 with ``{"code": -4000001, "data": null}`` —
an application-level error the transport layer's own retry/raise never sees, since it is a
*successful* HTTP response. Without a separate check, ``data: null`` collapses to the same empty
batch a legitimately finished board serves, and a mid-crawl failure would silently read as "the
board ended" — precisely the zwayam-class trap `base.py`'s ``USER_AGENT`` comment documents
(a 403 costing 102 Boards' descriptions while reading identically to "unparseable"). ``fetch_raw``
therefore checks the envelope's own ``code`` first and calls :meth:`~BaseScraper.mark_truncated`
on anything nonzero, before ever reading ``data``.

**No detail pass — the listing is the whole Job.** Every one of 100 sampled postings carried
non-null ``description`` and ``requirement`` (concatenated here for ``description``); ``code``,
``title``, ``recruit_type``, ``job_category`` and ``city_info`` were 100% non-null too.
``job_post_info`` (salary, level, expiry) and ``job_subject``/``department_info``/``tag_list`` were
null on every sampled row — this tenant simply doesn't populate them, not a truncation. **There is
no posted-date field anywhere in the payload** (measured across the same 100-row sample: zero
non-null timestamp of any kind, despite the reference scraper reading ``publish_time``/
``post_time`` keys that do not exist in this response), so ``posted_at`` is always ``None`` here.

**ByteDance-platform-sharing check, done because a sibling agent is building ``ats="bytedance"``
against ``jobs.bytedance.com`` and the two brands could plausibly share one backend filtered by a
brand parameter.** They do not, on the measured evidence: ``jobs.bytedance.com`` is a different
host (302 on its root) whose own ``/api/v1/search/job/posts`` returns 405 for this request shape,
and passing ``website-path: bytedance`` to *this* host (``api.lifeattiktok.com``) is rejected with
400 — the header is validated per-tenant, not a brand filter this API accepts. The shared
``website-path`` header convention suggests both career sites may be built on the same internal
recruiting-platform tooling, but they are two separate hosts with two separate request shapes, so
each needs its own scraper.
"""

from __future__ import annotations

from typing import Any

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_API = "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts"
_PAGE_SIZE = 100
# Generous ceiling: the measured board is ~4,231 postings (~43 pages at 100/page). Reaching this
# means the board did not end, we stopped reading it — same convention as oracle.py/eightfold.py.
_MAX_PAGES = 500


class TikTokScraper(BaseScraper):
    """TikTok's own careers site — a Single source scraper (ADR-0139), ``slug`` fixed to the
    marketing host even though the marketing host itself is not the data source (module
    docstring)."""

    ats = "tiktok"

    def url(self) -> str:
        return f"https://{self.slug}/"

    def alias_key(self) -> str | None:
        # ADR-0139: a Single source scraper has no sibling tenant to alias against, and the
        # base implementation's redirect probe would just hit the 503 described above. This
        # board is its own canonical identity.
        return self.slug

    def _headers(self) -> dict[str, str]:
        # Only `website-path` is required (module docstring); `Origin`/`Referer` are included
        # anyway since they cost nothing and match what a real browser sends.
        return {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "website-path": "tiktok",
            "Origin": f"https://{self.slug}",
            "Referer": f"https://{self.slug}/",
        }

    def _page(self, offset: int) -> dict[str, Any]:
        response = http.fetch(
            "POST",
            _API,
            json={
                "limit": _PAGE_SIZE,
                "offset": offset,
                "keyword": "",
                "category_id_list": [],
                "subject_id_list": [],
                "location_code_list": [],
                "job_function_id_list": [],
            },
            headers=self._headers(),
            timeout=30,
            **self._egress(),
        )
        response.raise_for_status()
        return response.json() or {}

    def fetch_raw(self) -> Any:
        posts: list[dict] = []
        total = 0
        offset = 0
        for _ in range(_MAX_PAGES):
            envelope = self._page(offset) or {}
            code = envelope.get("code")
            if code:
                # A live, HTTP-200 application-level error (e.g. a negative offset returns
                # `code=-4000001, data=None`) — measured 2026-09-12. `data` is then None, which
                # collapses to the same empty batch a legitimately finished board serves, so
                # without this check a mid-crawl failure silently reads as "the board ended"
                # and this run truncates the list without ever saying so (the zwayam-class
                # bug `base.py`'s USER_AGENT comment warns about). Whatever posts this walk
                # already has are real and kept; the rest is unread, not absent.
                self.mark_truncated(
                    f"API returned code {code} at offset {offset} — the rest unread, "
                    f"not absent ({len(posts)} postings read so far)"
                )
                break
            payload = envelope.get("data") or {}
            batch = payload.get("job_post_list") or []
            total = payload.get("count") or total
            posts.extend(batch)
            offset += len(batch)
            if not batch or len(batch) < _PAGE_SIZE or (total and offset >= total):
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

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for post in raw:
            job_id = post.get("id")
            title = (post.get("title") or "").strip()
            if not job_id or not title:
                continue
            location = _location_of(post.get("city_info"))
            category = post.get("job_category") or {}
            subject = post.get("job_subject") or {}
            recruit_type = post.get("recruit_type") or {}
            description = "\n\n".join(
                part
                for part in (post.get("description"), post.get("requirement"))
                if part
            )
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{job_id}",
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=is_remote(location),
                    department=category.get("en_name") or subject.get("en_name"),
                    url=f"https://{self.slug}/search/{job_id}",
                    posted_at=None,  # no date field exists anywhere in this API (module docstring)
                    scraped_at=scraped_at,
                    description=html_to_text(description) if description else None,
                    employment_type=recruit_type.get("en_name"),
                )
            )
        return jobs


def _location_of(city_info: Any) -> str | None:
    """ "City, Region, Country" from the nested ``city_info -> parent -> parent`` chain, dropping
    a level whose name is already a prefix of one already kept (some tenants' region already
    states the country, the same shape eightfold's JSON-LD location handles)."""
    parts: list[str] = []
    node = city_info
    while isinstance(node, dict):
        name = (node.get("en_name") or "").strip()
        if name and name not in parts:
            parts.append(name)
        node = node.get("parent")
    return ", ".join(parts) or None
