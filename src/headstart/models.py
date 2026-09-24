"""Core data model and normalization helpers for HeadStart."""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class Job:
    """A single job posting, normalized to be ATS-agnostic."""

    id: str  # globally unique: "{ats}:{slug}:{native_id}"
    ats: str  # source ATS: "greenhouse" | "lever" | "ashby"
    company: str
    title: str
    location: str | None
    remote: bool | None
    department: str | None
    url: str
    posted_at: str | None  # ISO-8601 if the source provides it
    scraped_at: str  # ISO-8601 UTC, when this run fetched it
    # Optional richer fields — populated only when the source exposes them. Values are kept
    # as the provider phrases them (not normalized across ATSes); None when not available.
    description: str | None = None  # plain text, tags/entities stripped
    experience: str | None = None  # e.g. "3-5 Years", "Mid-Senior level"
    employment_type: str | None = None  # e.g. "Full-time", "Intern", "Contract"
    salary: str | None = None
    # The ATS's own requisition id as it states it (ADR-0210), filled only where a served row
    # needs it to be matched across ATSes: an Eightfold career site's posting names its backing
    # Board's requisition, and a row on that Board carries the same id. None everywhere else.
    requisition: str | None = None

    def __post_init__(self) -> None:
        # The one point every scraper's Jobs pass through, so the display text is cleaned once
        # rather than per scraper. Each defect was served on 2026-09-24: entities left in 56
        # titles (smartrecruiters, zwayam) and a company (pyjamahr), 3,851 companies with edge
        # whitespace, and locations carrying markup (teamtailor's own feed) or one place per
        # line (50 icims rows, one workday). `object.__setattr__` because the dataclass is frozen.
        object.__setattr__(self, "title", _unescaped(self.title))
        object.__setattr__(self, "company", _unescaped(self.company))
        object.__setattr__(self, "location", _location_text(self.location))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def requisition_of(value: Any) -> str | None:
    """A stated requisition id as ``Job.requisition`` stores it: text, trimmed, None when absent.

    ATSes state one as a number (Greenhouse's ``internal_job_id``) or a string (Workday's
    ``R-100``), and two rows match only on equal strings (ADR-0210)."""
    text = "" if value is None else str(value).strip()
    return text or None


_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_LINE_BREAKS = re.compile(r"\s*[\r\n]+\s*")


def _unescaped(value: str | None) -> str | None:
    """``value`` with its entities decoded and its ends stripped; the inside is left as stated."""
    return value if value is None else html.unescape(value).strip()


def _location_text(value: str | None) -> str | None:
    """A location as display text: tags dropped, entities decoded, a line break read as a list.

    Tags go before entities are decoded, so an escaped ``&lt;Remote&gt;`` survives as text. A line
    break separates places (all 50 icims rows served with one on 2026-09-24), so it becomes the
    ``"; "`` the scrapers already join places with, not a space that runs two places into one.
    """
    if not value:
        return None
    text = _LINE_BREAKS.sub("; ", _TAGS.sub(" ", value).strip())
    return _WS.sub(" ", html.unescape(text)).strip() or None


def host_of(url: str | None) -> str:
    """The bare host of a url — no scheme, path, query or trailing slash.

    One definition because the rule has to hold in four places at once: a scraper whose slug *is*
    a host (``personio``, ``zoho``), the liveness prober for that same ATS, and the ledger repair.
    They disagreed once, and it was expensive: discovery had stored raw Common Crawl captures —
    job deep links, some carrying ``utm_*`` — in the ledger's ``url`` column, and Personio's
    ``url()`` appends ``/xml``. On ``.../job/186062?language=de`` that suffix landed *inside* the
    query string, so Personio served the ordinary HTML job page with a 200: the scrape died in
    ``ET.fromstring`` (678 ParseErrors across 19 runs) while the prober, splitting the same wrong
    way, recorded all 312 such boards live with zero jobs.
    """
    return (url or "").split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0]


def html_to_text(value: str | None) -> str | None:
    """Strip HTML tags/entities from a description blob into clean, single-spaced text.

    Unescapes twice because some sources (e.g. Darwinbox) entity-encode their HTML, so one
    pass leaves the tags as text and the second clears entities inside the stripped content.
    """
    if not value:
        return None
    text = _TAGS.sub(" ", html.unescape(value))
    return _WS.sub(" ", html.unescape(text)).strip() or None


def is_remote(location: str | None) -> bool | None:
    """Best-effort remote detection from a location string.

    Returns None when there is no location to judge from.
    """
    if not location:
        return None
    return "remote" in location.lower()


def epoch_ms_to_iso(ms: int | None) -> str | None:
    """Convert a millisecond Unix timestamp to an ISO-8601 UTC string."""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()
