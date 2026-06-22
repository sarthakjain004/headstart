"""Core data model and normalization helpers for HeadStart."""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


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
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
