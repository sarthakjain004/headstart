"""Manatal's legacy public Jobs and distinct advanced JobPost career pages.

Measured 2026-10-03: 20 legacy Boards yielded 921 Jobs with full descriptions in
the anonymous listing. Advanced Boards publish different UUID JobPosts: Manatal's
own advanced site held 40 posts, while its legacy API held 32 Jobs. Preserve each
surface's identity rather than silently querying a legacy API for an advanced host.
See docs/manatal/2026-10-03_public-api-measurement.md.
"""

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

from headstart.boards.company_name import title_of
from headstart.jobs.job import Job, html_to_text, is_remote
from headstart.network import http
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    BoardUnreadable,
    DetailLost,
    DetailRequest,
    DetailWithoutDescription,
    gone_board_error,
)
from headstart.scrapers.pacer import Pacer

# 2,048 short-ramp requests succeeded through concurrency 128, but a sustained
# 16-starts/s census first received 429 after 1,221 requests (~79 seconds).
# Keep the demonstrated long-run two-starts/s budget; concurrency is not quota.
_API_PACER = Pacer(0.5)
_HTML_PACER = Pacer(1.25)
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

_TYPES = {
    "full_time": "Full-time",
    "part_time": "Part-time",
    "contractor": "Contract",
    "consultancy": "Contract",
    "internship": "Internship",
    "temporary": "Temporary",
}


def departed(status: int, body: Any) -> bool:
    """Five historical tenants gave this response; Invalid page is a different 404."""
    return (
        status == 404
        and isinstance(body, dict)
        and body.get("detail") == "No ClientPortalSettings matches the given query."
    )


def legacy_listing(body: Any) -> dict:
    """Validate the public count/list envelope before treating it as a Board read."""
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("results"), list)
        or type(body.get("count")) is not int
        or body["count"] < 0
        or "next" not in body
    ):
        raise BoardUnreadable("Manatal API returned no public Jobs envelope")
    if any(
        not isinstance(row, dict)
        or not row.get("id")
        or not row.get("hash")
        or not row.get("position_name")
        for row in body["results"]
    ):
        raise BoardUnreadable("Manatal listing has an incomplete Job identity")
    return body


class _AdvancedHTML(HTMLParser):
    """Read the measured server-rendered cards and detail, without executing scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.captures = {}
        self.card = None
        self.cards = []
        self.links = []
        self.company = None
        self.total = None
        self.description = None
        self.title = None
        self.metadata = []

    def _inside_class(self, name):
        return any(
            name in (attrs.get("class") or "").split() for _tag, attrs in self.stack
        )

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        classes = (attrs.get("class") or "").split()
        if tag in _VOID:
            return
        self.stack.append((tag, attrs))
        depth = len(self.stack)
        if tag == "article" and "job-card" in classes:
            self.card = {"metadata": []}
        if tag == "a" and "job-title-link" in classes and self.card is not None:
            self.card.update(
                id=attrs.get("data-job-id"),
                position_name=attrs.get("data-job-title"),
                href=attrs.get("href"),
            )
        if tag == "a" and "page-link" in classes and attrs.get("href"):
            self.links.append(attrs["href"])
        kind = None
        if tag == "h4" and "text-h4" in classes:
            kind = "company"
        elif "search-header-right" in classes:
            kind = "total"
        elif "job-post-description" in classes:
            kind = "description"
        elif "single-job-title" in classes:
            kind = "title"
        elif (
            tag == "li"
            and self.card is not None
            and any(a.get("aria-label") == "Job details" for _t, a in self.stack)
        ):
            kind = "card_meta"
        elif tag == "li" and self._inside_class("job-location"):
            kind = "detail_meta"
        if kind:
            self.captures[depth] = (kind, [])

    def handle_data(self, data):
        if any(tag in {"style", "script"} for tag, _attrs in self.stack):
            return
        for _kind, chunks in self.captures.values():
            chunks.append(data)

    def handle_endtag(self, tag):
        position = next(
            (i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag),
            None,
        )
        if position is None:
            return
        for depth in sorted([d for d in self.captures if d > position], reverse=True):
            kind, chunks = self.captures.pop(depth)
            value = " ".join(" ".join(chunks).split())
            if kind == "company" and value.startswith("Jobs at "):
                self.company = value.removeprefix("Jobs at ").strip()
            elif kind == "total":
                match = re.search(r"([\d,]+)\s+Open Positions?", value, re.IGNORECASE)
                if match:
                    self.total = int(match.group(1).replace(",", ""))
            elif kind == "card_meta" and self.card is not None:
                self.card["metadata"].append(value)
            elif kind == "detail_meta":
                self.metadata.append(value)
            elif kind in {"description", "title"}:
                setattr(self, kind, value or None)
        if tag == "article" and self.card is not None:
            self.cards.append(self.card)
            self.card = None
        del self.stack[position:]


def advanced_listing(page: str, url: str) -> dict:
    parser = _AdvancedHTML()
    parser.feed(page)
    if parser.total is None:
        raise BoardUnreadable("Manatal advanced page has no public posting total")
    rows = []
    for card in parser.cards:
        if not card.get("id") or not card.get("position_name") or not card.get("href"):
            raise BoardUnreadable(
                "Manatal advanced card has no complete posting identity"
            )
        link = urljoin(url, card["href"])
        if urlsplit(link).hostname != urlsplit(url).hostname or not re.fullmatch(
            r"/jobs/[0-9a-f-]+/?", urlsplit(link).path
        ):
            raise BoardUnreadable("Manatal advanced card links outside its Board")
        metadata = card["metadata"]
        rows.append(
            {
                "id": card["id"],
                "hash": card["id"],
                "position_name": card["position_name"],
                "url": link,
                "location_display": metadata[0] if metadata else None,
                "organization_name": metadata[1]
                if len(metadata) > 1 and metadata[1] != "-"
                else None,
            }
        )
    current = int(parse_qs(urlsplit(url).query).get("page", ["1"])[0])
    following = []
    for link in parser.links:
        target = urljoin(url, link)
        if urlsplit(target).hostname != urlsplit(url).hostname:
            continue
        try:
            page_number = int(parse_qs(urlsplit(target).query).get("page", ["0"])[0])
        except ValueError:
            continue
        if page_number > current:
            following.append((page_number, target))
    return {
        "rows": rows,
        "count": parser.total,
        "next": min(following)[1] if following else None,
        "company": parser.company,
    }


class ManatalScraper(BaseScraper):
    ats = "manatal"
    spare_on_transport_error = True
    has_detail_pass = (
        True  # advanced HTML only; legacy JSON already carries descriptions
    )
    detail_workers = 1
    api_pacer = _API_PACER
    html_pacer = _HTML_PACER
    url_shape = r"https://(?:www\.careers-page\.com/[^/?#]+/job/[A-Za-z0-9]+|[a-z0-9-]+\.careers-page\.com/jobs/[0-9a-f-]+)"

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        value = (url or tenant).strip()
        if "://" in value:
            parsed = urlsplit(value)
            host = (parsed.hostname or "").lower()
            if host in {"careers-page.com", "www.careers-page.com"}:
                parts = parsed.path.strip("/").split("/")
                value = unquote(
                    parts[3]
                    if parts[:3] == ["api", "v1.0", "c"] and len(parts) > 3
                    else parts[0]
                )
            elif host in {"api.manatal.com", "core.api.manatal.com"}:
                parts = parsed.path.strip("/").split("/")
                if parts[:3] != ["open", "v3", "career-page"] or len(parts) < 5:
                    raise ValueError("not a public Manatal Jobs API")
                value = unquote(parts[3])
            elif host.endswith(".careers-page.com"):
                return host
            else:
                raise ValueError("not a supported Manatal career URL")
        # Archive captures include malformed links whose "slug" is an RGB
        # colour, a JS expression or pasted text. None of 39 such candidates
        # resolved live; all measured legacy labels use this public slug alphabet.
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ValueError("Manatal needs a legacy slug or an advanced career host")
        return value.lower()

    @property
    def advanced(self) -> bool:
        return self.slug.endswith(".careers-page.com")

    def url(self) -> str:
        if self.advanced:
            return f"https://{self.slug}/"
        return f"https://api.manatal.com/open/v3/career-page/{quote(self.slug, safe='')}/jobs/"

    def job_url(self, native_id: str) -> str:
        if self.advanced:
            return f"https://{self.slug}/jobs/{native_id}"
        return (
            f"https://www.careers-page.com/{quote(self.slug, safe='')}/job/{native_id}"
        )

    def _pacer(self, url: str) -> Pacer:
        return (
            self.api_pacer
            if urlsplit(url).hostname == "api.manatal.com"
            else self.html_pacer
        )

    def _fetch(self, method: str, url: str, **kwargs: Any) -> Any:
        self._pacer(url).wait()
        return super()._fetch(method, url, **kwargs)

    async def _fetch_async(
        self, session: Any, method: str, url: str, **kwargs: Any
    ) -> Any:
        await self._pacer(url).wait_async()
        return await super()._fetch_async(session, method, url, **kwargs)

    def fetch_raw(self) -> Any:
        if self.advanced:
            raw = self.advanced_jobs()
            # Detail metadata can add a department/type absent from a listing, so
            # no unmeasured title-only gate or held-description skip is applied.
            raw["details"] = (
                self.run_detail_pass(
                    raw["rows"],
                    key_of=lambda row: str(row["id"]),
                    what="Manatal advanced job details",
                )
                if self.truncated is None
                else {}
            )
            return raw
        rows = {}
        page, expected = 1, 0
        while True:
            try:
                response = self._fetch(
                    "GET",
                    self.url(),
                    # The public frontend names this supported ordering. Without
                    # it Mercor's offset walk repeated ids and lost 961/16,888.
                    # Explicit ordering gave disjoint pages and stable repeats.
                    params={
                        "page": page,
                        "page_size": 100,
                        "ordering": "-is_pinned_in_career_page,-last_published_at",
                    },
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    timeout=30,
                )
                try:
                    body = response.json()
                except ValueError as exc:
                    response.raise_for_status()
                    raise BoardUnreadable("Manatal API did not return JSON") from exc
                if page == 1 and departed(response.status_code, body):
                    raise gone_board_error(
                        "Manatal client has no public career settings"
                    )
                response.raise_for_status()
                body = legacy_listing(body)
            except (http.RequestsError, ValueError) as exc:
                if page == 1:
                    raise
                self.mark_truncated(f"Manatal page {page} failed: {type(exc).__name__}")
                break
            expected = max(expected, body["count"])
            before = len(rows)
            for row in body["results"]:
                rows[row["id"]] = row
            if not body["next"] or len(rows) == before:
                if len(rows) < expected:
                    self.mark_truncated(f"Manatal read {len(rows)} of {expected}")
                break
            page += 1
        organization_is_department = False
        if rows:
            # Company/departments are optional enrichment. Failure here must not
            # discard the complete list already read from the public Jobs API.
            try:
                board = self._fetch_once(
                    "GET", f"https://www.careers-page.com/{quote(self.slug, safe='')}"
                )
                board.raise_for_status()
                page_text = board.text
            except http.RequestsError as exc:
                self._log.info(
                    f"{self.board_key()}: company page unavailable: {type(exc).__name__}"
                )
                page_text = ""
            title = title_of(page_text) or ""
            match = re.fullmatch(r"\s*-\s*(.+?)\s*\|\s*Career Page\s*", title)
            self.adopt_company(match.group(1) if match else None)
            organization_is_department = bool(
                re.search(
                    r"""organization_singular_name\s*=\s*["']department["']""",
                    page_text,
                )
            )
        return {
            "rows": list(rows.values()),
            "organization_is_department": organization_is_department,
        }

    def advanced_jobs(self) -> dict:
        """All publicly linked pages, or a visibly incomplete list on any refusal."""
        url = self.url()
        visited, rows = set(), {}
        expected = 0
        while url:
            if url in visited:
                self.mark_truncated("Manatal advanced pagination repeated a page")
                break
            visited.add(url)
            try:
                response = self._fetch(
                    "GET",
                    url,
                    headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                    timeout=30,
                    attempts=1,
                )
                response.raise_for_status()
                page = advanced_listing(response.text, url)
            except (http.RequestsError, ValueError) as exc:
                if not rows:
                    raise
                self.mark_truncated(
                    f"Manatal advanced listing stopped: {type(exc).__name__}"
                )
                break
            self.adopt_company(page["company"])
            expected = max(expected, page["count"])
            previous = len(rows)
            for row in page["rows"]:
                rows[row["id"]] = row
            if len(rows) == previous and page["next"]:
                self.mark_truncated("Manatal advanced pagination added no JobPosts")
                break
            url = page["next"]
        if len(rows) < expected:
            self.mark_truncated(f"Manatal advanced read {len(rows)} of {expected}")
        return {
            "rows": list(rows.values()),
            "advanced": True,
            "organization_is_department": True,
        }

    def detail_request(self, item: dict) -> DetailRequest:
        return DetailRequest(item["url"], options={"attempts": 1})

    def read_detail(self, item: dict, response: Any) -> Any:
        parser = _AdvancedHTML()
        parser.feed(response.text)
        if not parser.title:
            raise DetailLost("no public Manatal JobPost")
        metadata = parser.metadata
        remote = metadata[-1] if metadata else None
        result = {
            "position_name": parser.title,
            "plain_description": parser.description,
        }
        if remote in {"Remote", "Hybrid", "On-Site"} and len(metadata) >= 3:
            result["contract_details"] = metadata[-2]
            result["is_remote"] = {"Remote": True, "Hybrid": None, "On-Site": False}[
                remote
            ]
            result["hybrid"] = remote == "Hybrid"
            if len(metadata) >= 4:
                result["organization_name"] = metadata[1]
        return (
            result
            if parser.description
            else DetailWithoutDescription(result, "public JobPost has no description")
        )

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs = []
        for row in raw["rows"]:
            if raw.get("advanced"):
                row = {**row, **raw.get("details", {}).get(str(row["id"]), {})}
            location = (
                row.get("location_display")
                or ", ".join(
                    dict.fromkeys(
                        row[k] for k in ("city", "state", "country") if row.get(k)
                    )
                )
                or None
            )
            remote = row.get("is_remote")
            jobs.append(
                Job(
                    id=self.job_id(str(row["id"])),
                    ats=self.ats,
                    company=self.company,
                    title=row["position_name"],
                    location=location,
                    remote=None
                    if row.get("hybrid")
                    else remote
                    if isinstance(remote, bool)
                    else is_remote(location),
                    department=row.get("organization_name")
                    if raw.get("organization_is_department")
                    else None,
                    url=self.job_url(row["hash"]),
                    posted_at=None,
                    scraped_at=scraped_at,
                    description=row.get("plain_description")
                    if raw.get("advanced")
                    else html_to_text(row.get("description")),
                    employment_type=_TYPES.get(
                        row.get("contract_details"), row.get("contract_details")
                    ),
                    salary=self._salary_field(row),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        # All 49 visible native salaries in 921 rows omit their period. A measured
        # GBP450–650 figure is a daily rate; don't silently turn it into annual pay.
        # Stated units in the description remain available to the shared extractor.
        return None
