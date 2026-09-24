"""The one implementation behind the ``tiktok`` and ``bytedance`` Single source scrapers: both
careers sites are read from ByteDance's own recruiting backend, the ``supplier`` search API
(ADR-0198).

    POST https://{host}/api/v1/public/supplier/search/job/posts
    headers  website-path: {Board}, accept-language: en-US
    body     {"keyword": "", "limit": 200, "offset": N}
    -> {"code": 0, "data": {"job_post_list": [...], "count": N}}

**One backend, and the ``website-path`` header — not the host — picks the Board.** Measured live
2026-09-24: ``api.lifeattiktok.com`` answers ``website-path: en`` with ByteDance's Board (count
1,420) and ``jobs.bytedance.com`` answers ``website-path: tiktok`` with TikTok's (count 4,301); a
request without the header, or with ``bytedance``, is HTTP 400 ``invalid request`` on both. A
malformed field names the same Go struct on both hosts (``BizListJobPostReq``). ADR-0139 still
holds: each brand keeps its own ``ats``, registry entry, ledger row, ``COMPANY``, ``url_shape`` and
``slug``, and each subclass states only its host, its ``website-path`` value and its public links.

**The request body is the minimal one.** Each site's own client sends its own list of empty
``*_id_list`` filters, and the two lists differ; both hosts answered the TikTok list, the ByteDance
list and a body of just ``keyword``/``limit``/``offset`` with the same count and the same first
ids. ``accept-language: en-US`` is not required by either host, but on the ByteDance Board it
decides the language of every ``i18n_name`` (Chinese without it), so it is sent: the corpus is
English-only.

**A non-zero ``code`` on an HTTP 200 marks the Board truncated.** A negative ``offset`` or
``limit`` answers ``{"code": -4000001, "data": null}`` on both hosts. ``data: null`` would
otherwise read as the empty page a finished Board serves, so ``code`` is checked before ``data``,
and anything but 0 — a missing ``code`` included — keeps the postings already read and marks the
rest unread rather than raising, which would drop them for the run.

**The walk steps by the rows each page returned and ends on an empty page or at ``count``.** No
page short of the limit was seen before the last one in a full walk of either Board, and
``count`` never moved within a walk. A short page mid-walk therefore neither ends the walk nor
opens a gap after it. The backend serves nothing once ``offset + limit`` passes 10,000 (0 rows,
``count`` reported as 10,000), so :data:`_MAX_PAGES` stops the walk at exactly that window.

**The listing is the whole Job, with no posted date.** ``description`` and ``requirement`` are on
every row, so there is no detail pass. No field of the payload carries a date of any kind, so
``posted_at`` is always None. ``job_post_info`` (salary, level) is null on every sampled row of
both Boards. ``job_subject`` is a campus-cohort label ("PhD Graduates - 2027 Start"), not a team,
so it never stands in for ``department``.
"""

from __future__ import annotations

from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

#: Both hosts served ``limit=1,000`` un-clamped (measured 2026-09-24). Paged anyway: an un-clamped
#: limit today is not a contract, and the walk below survives a silent clamp.
_PAGE_SIZE = 200
#: 50 x 200 = 10,000, the backend's result window: past ``offset + limit = 10,000`` it answers 0
#: rows and a ``count`` of 10,000. Reaching the cap means the Board did not end.
_MAX_PAGES = 50


class SupplierSearchScraper(BaseScraper):
    """A Board read from the ``supplier`` search API. A subclass sets :attr:`search_url` and
    :attr:`website_path`, plus the identity every Single source scraper states (ADR-0139)."""

    #: This brand's own search endpoint.
    search_url: str
    #: The header value that selects this brand's Board on the shared backend.
    website_path: str

    def alias_key(self) -> str | None:
        # ADR-0139: a Single source scraper has no sibling Board to alias against, so this Board
        # is its own canonical identity.
        return self.slug

    def _search_page(self, offset: int) -> dict[str, Any]:
        response = self._fetch(
            "POST",
            self.search_url,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "accept-language": "en-US",
                "website-path": self.website_path,
            },
            json={"keyword": "", "limit": _PAGE_SIZE, "offset": offset},
            timeout=30,
        )
        response.raise_for_status()
        return response.json() or {}

    def fetch_raw(self) -> Any:
        posts: list[dict] = []
        total = 0
        offset = 0
        for _ in range(_MAX_PAGES):
            envelope = self._search_page(offset)
            code = envelope.get("code")
            if code != 0:
                self.mark_truncated(
                    f"API returned code {code} ({envelope.get('message') or 'no message'}) "
                    f"at offset {offset} — the rest unread, not absent "
                    f"({len(posts)} postings read so far)"
                )
                break
            data = envelope.get("data") or {}
            batch = data.get("job_post_list") or []
            total = data.get("count") or total
            posts.extend(batch)
            offset += len(batch)
            if not batch or (total and offset >= total):
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
            job_id = str(post.get("id") or "")
            title = (post.get("title") or "").strip()
            if not job_id or not title:
                continue
            location = _location(post.get("city_info"))
            description = "\n\n".join(
                part
                for part in (post.get("description"), post.get("requirement"))
                if part
            )
            jobs.append(
                Job(
                    id=self.job_id(job_id),
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=is_remote(location),
                    department=_english_name(post.get("job_category")),
                    url=self.job_url(job_id),
                    posted_at=None,  # no date field exists anywhere in this API
                    scraped_at=scraped_at,
                    description=html_to_text(description),
                    employment_type=_english_name(post.get("recruit_type")),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        # `job_post_info` (salary, level) is null on every sampled row of both Boards (module
        # docstring): there is no structured compensation field to read.
        return None


def _english_name(node: Any) -> str | None:
    """A named node's English label. ``i18n_name`` is the name in the requested locale, which the
    ``accept-language: en-US`` header makes English, so it stands in when ``en_name`` is empty."""
    if not isinstance(node, dict):
        return None
    return (node.get("en_name") or node.get("i18n_name") or "").strip() or None


def _location(city_info: Any) -> str | None:
    """ "City, Region, Country" from ``city_info``'s nested ``parent`` chain, naming each place
    once: Singapore states "Singapore" at all three levels."""
    parts: list[str] = []
    node = city_info
    while isinstance(node, dict):
        name = _english_name(node)
        if name and name not in parts:
            parts.append(name)
        node = node.get("parent")
    return ", ".join(parts) or None
