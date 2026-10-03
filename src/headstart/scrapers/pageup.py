"""PageUp public RSS Boards, addressed by account/channel/language.

Measured 2026-10-03: 17 public Boards returned 4,354 RSS Jobs, with full
namespaced descriptions on every row. The plain RSS description is only a teaser.
The largest feed contained 2,487 Jobs and no pagination; the HTML walk had 2,477,
all present in RSS. Categories preserve 42 tech Jobs a title-only gate would lose.
See docs/pageup/2026-10-03_public-api-measurement.md.
"""

import re
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

from headstart.boards.company_name import title_of
from headstart.jobs.job import Job, html_to_text, is_remote
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    BoardUnreadable,
    gone_board_error,
)
from headstart.scrapers.pacer import Pacer

HOST = "careers.pageuppeople.com"
_JOB_NS = "{http://pageuppeople.com/}"
_JOB_PATH = re.compile(r"/job/(\d+)(?:[/?#]|$)")
# Direct RSS ramps stayed successful through 128; throughput flattened after 64
# while p95 rose from 2.9 to 7.4 seconds across Boards. Eight starts/s stays below
# the measured many-Board throughput. Spare is a one-request transport recovery,
# not a throughput route: its burst ramp timed out at concurrency eight.
_PACER = Pacer(1 / 8)


class MigratedBoard(BoardUnreadable):
    """A retained RSS feed whose public Job click has become a generic career search."""


class _ImmediateRefresh(HTMLParser):
    target: str | None = None

    def handle_starttag(
        self, tag: str, attributes: list[tuple[str, str | None]]
    ) -> None:
        attrs = dict(attributes)
        if tag != "meta" or (attrs.get("http-equiv") or "").lower() != "refresh":
            return
        match = re.fullmatch(
            r"\s*0(?:\.0+)?\s*;\s*url\s*=\s*(.+?)\s*",
            attrs.get("content") or "",
            re.IGNORECASE,
        )
        if match and self.target is None:
            self.target = match[1].strip("\"'")


def _landing_url(response: Any) -> str:
    """G8 Education and The Star return 200 yet immediately leave the Job page.

    Read their standard zero-delay meta refresh; no JavaScript is executed or
    guessed. Delayed/session refreshes are not used as migration evidence.
    """
    refresh = _ImmediateRefresh()
    refresh.feed(response.text)
    return urljoin(response.url, refresh.target) if refresh.target else response.url


def read_board(fetch: Callable, slug: str) -> tuple[str, str]:
    """Read the public Board and RSS together; old feeds can outlive a migration."""
    base = f"https://{HOST}/{slug}"
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    _PACER.wait()
    board = fetch("GET", f"{base}/listing/", headers=headers, timeout=30)
    if board is None:
        raise BoardUnreadable("PageUp public Board was not reached")
    board.raise_for_status()
    if board.status_code != 200:
        raise BoardUnreadable(f"PageUp Board answered HTTP {board.status_code}")
    _PACER.wait()
    response = fetch("GET", f"{base}/rss", headers=headers, timeout=60)
    if response is None:
        raise BoardUnreadable("PageUp RSS was not reached")
    response.raise_for_status()
    if response.status_code != 200:
        raise BoardUnreadable(f"PageUp RSS answered HTTP {response.status_code}")
    items = feed_items(response.text)
    if urlsplit(_landing_url(board)).hostname != HOST:
        if not items:
            raise BoardUnreadable("PageUp Board moved and its retained feed is empty")
        target = items[0].findtext("link") or ""
        if urlsplit(target).hostname != HOST or not _JOB_PATH.search(target):
            raise BoardUnreadable("PageUp feed has no recognized public Job link")
        _PACER.wait()
        detail = fetch("GET", target, headers=headers, timeout=30)
        if detail is None:
            raise BoardUnreadable("PageUp migrated Job was not reached")
        if detail.status_code in {404, 410}:
            raise BoardUnreadable(
                "one retained PageUp Job is gone; Board migration is unconfirmed"
            )
        detail.raise_for_status()
        landing = urlsplit(_landing_url(detail))
        if (
            landing.hostname != HOST
            and landing.path.rstrip("/").lower() in {"", "/jobs/search"}
            and not landing.query
            and not landing.fragment
        ):
            raise MigratedBoard(
                "PageUp Job links now open a generic external career search"
            )
        if landing.hostname != HOST:
            raise BoardUnreadable("unverified external PageUp Job destination")
    return board.text, response.text


def _company(page: str) -> str | None:
    title = title_of(page) or ""
    patterns = (
        r"^(.+?) / Careers(?:\s|$)",
        r"^(.+?) - Recent Jobs$",
        r"^(?:Current Opportunities|Job Openings|Internal Job Openings|Current jobs|Job vacancies|Employment Opportunities)\s*[-|:]\s*(.+)$",
        r"^Job Search - Jobs at (.+)$",
        r"^(.+?) Careers \|",
    )
    for pattern in patterns:
        if match := re.search(pattern, title, re.IGNORECASE):
            name = match.group(1).removeprefix("Careers at ").strip()
            # DHA and Mercedes (2/21 sampled pages) use "Jobs - Recent Jobs".
            # Its first word is a page label, not the employer.
            return None if name.lower() == "jobs" else name
    return None


def feed_items(text: str) -> list[ElementTree.Element]:
    """Reject an HTML/error response instead of recording it as an empty Board."""
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise BoardUnreadable("PageUp response is not RSS") from exc
    if root.tag != "rss" or root.find("channel") is None:
        raise BoardUnreadable("PageUp response has no RSS channel")
    if urlsplit(root.findtext("./channel/link") or "").hostname != HOST:
        raise BoardUnreadable("RSS channel does not name a public PageUp Board")
    items = root.findall("./channel/item")
    for item in items:
        link = item.findtext("link") or ""
        match = _JOB_PATH.search(urlsplit(link).path)
        if (
            urlsplit(link).hostname != HOST
            or not match
            or item.findtext(f"{_JOB_NS}refNo") != match.group(1)
        ):
            raise BoardUnreadable("PageUp RSS item has no matching public Job identity")
    return items


def _location(value: str | None) -> str | None:
    if not value:
        return None
    if "|" not in value:
        return value
    places = []
    for place in value.split(","):
        region, separator, city = place.partition("|")
        places.append(
            ", ".join(x.strip() for x in (city, region) if x.strip())
            if separator
            else region.strip()
        )
    return "; ".join(dict.fromkeys(p for p in places if p)) or None


class PageUpScraper(BaseScraper):
    ats = "pageup"
    spare_on_transport_error = True
    url_shape = (
        r"https://careers\.pageuppeople\.com/\d+/[^/?#]+/[^/?#]+/job/\d+(?:/[^?#]*)?"
    )

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        value = (url or tenant).strip()
        if "://" in value:
            parsed = urlsplit(value)
            if parsed.hostname != HOST:
                raise ValueError("not a classic PageUp career URL")
            value = parsed.path.strip("/")
        parts = value.strip("/").split("/")
        # Kinetic's measured mobile page uses the same public desktop Board.
        if parts[0].lower() == "mob":
            parts = parts[1:]
        if (
            len(parts) < 3
            or not parts[0].isdecimal()
            or not all(re.fullmatch(r"[A-Za-z0-9_-]+", p) for p in parts[1:3])
        ):
            raise ValueError("PageUp Board needs account/channel/language")
        if parts[1].lower() in {
            "ci",
            "uat",
            "cwuat",
            "ciuat",
            "uatinternal",
            "testint",
            "staging",
        }:
            raise ValueError("PageUp robots excludes this channel")
        return "/".join(parts[:3]).lower()

    def url(self) -> str:
        return f"https://{HOST}/{self.slug}/rss"

    def job_url(self, native_id: str) -> str:
        return f"https://{HOST}/{self.slug}/job/{native_id}"

    def fetch_raw(self) -> Any:
        try:
            page, feed = read_board(self._fetch, self.slug)
        except MigratedBoard as exc:
            raise gone_board_error(str(exc)) from exc
        except BoardUnreadable as exc:
            self.note_unreadable_board("a public PageUp Board and RSS feed", str(exc))
            raise
        self.adopt_company(_company(page))
        return feed

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs = {}
        for item in feed_items(raw):
            link = item.findtext("link") or ""
            match = _JOB_PATH.search(link)
            if not match:
                raise BoardUnreadable("PageUp item has no public Job id")
            native_id = match.group(1)
            location = _location(item.findtext(f"{_JOB_NS}location"))
            categories = list(
                dict.fromkeys(
                    node.text.strip()
                    for node in item.findall(f"{_JOB_NS}category")
                    if node.text and node.text.strip()
                )
            )
            posted = item.findtext("pubDate")
            jobs[native_id] = Job(
                id=self.job_id(native_id),
                ats=self.ats,
                company=self.company,
                title=item.findtext("title") or "",
                location=location,
                remote=None
                if (
                    re.search(r"partial\s+remote|hybrid", location or "", re.IGNORECASE)
                    or any(
                        "work arrangement|hybrid" in category.lower().split(",")
                        for category in categories
                    )
                )
                else is_remote(location),
                department="; ".join(categories) or None,
                url=self.job_url(native_id),
                posted_at=parsedate_to_datetime(posted).isoformat() if posted else None,
                scraped_at=scraped_at,
                description=html_to_text(item.findtext(f"{_JOB_NS}description")),
                employment_type=item.findtext(f"{_JOB_NS}workType"),
            )
        return list(jobs.values())

    def _salary_field(self, raw: Any) -> str | None:
        # The measured feed has no salary field; pay in prose uses the shared extractor.
        return None
