"""Subscriber filters and job matching — shared deterministic logic.

The same dimensions the dashboard filters on, expressed server-side so the bot
can decide which new jobs to notify a subscriber about.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Filter:
    q: str | None = None  # keyword substring over title + company + department
    location: str | None = None
    company: str | None = None
    ats: str | None = None
    remote: bool = False  # remote-only toggle

    def to_dict(self) -> dict[str, Any]:
        # Drop empty values so a filterless subscriber serializes to {}.
        return {k: v for k, v in asdict(self).items() if v not in (None, False, "")}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Filter":
        data = data or {}
        return cls(
            q=data.get("q"),
            location=data.get("location"),
            company=data.get("company"),
            ats=data.get("ats"),
            remote=bool(data.get("remote", False)),
        )

    def describe(self) -> str:
        parts = []
        if self.q:
            parts.append(f"keywords: {self.q}")
        if self.location:
            parts.append(f"location: {self.location}")
        if self.company:
            parts.append(f"company: {self.company}")
        if self.ats:
            parts.append(f"source: {self.ats}")
        if self.remote:
            parts.append("remote only")
        return "; ".join(parts) if parts else "no filters (all new jobs)"


def matches(job: dict[str, Any], f: Filter) -> bool:
    """True if `job` (a feed dict) satisfies every set field of `f`."""
    if f.remote and job.get("remote") is not True:
        return False
    if f.ats and (job.get("ats") or "").lower() != f.ats.lower():
        return False
    if f.company and f.company.lower() not in (job.get("company") or "").lower():
        return False
    if f.location and f.location.lower() not in (job.get("location") or "").lower():
        return False
    if f.q:
        hay = f"{job.get('title', '')} {job.get('company', '')} {job.get('department', '')}".lower()
        if f.q.lower() not in hay:
            return False
    return True
