"""Extract a Job's required years of experience to a numeric range (enrichment).

A tiered cascade, cheapest-first. Each tier is a pure function returning an :class:`ExperienceSpan`
or ``None``; :func:`extract` runs them in order and returns the first hit, recording which tier
produced it. Source-agnostic by design — it takes a structured ``field`` (when a source provides
one, e.g. Wellfound's ``years_experience``) and the free-text ``description``.

Extending it is the point: widen recall by appending to ``_DESC_PATTERNS``; add a tier (an LLM pass,
seniority inference) by writing another ``from_*`` function and adding it to the cascade in
:func:`extract`. Keeping each tier pure keeps the whole thing unit-testable without I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MAX_PLAUSIBLE_YEARS = 50  # reject absurd matches ("100 years"), almost always a parse error


@dataclass(frozen=True, slots=True)
class ExperienceSpan:
    """A required-experience range in whole years. ``max_years`` is None for open-ended ("5+")."""

    min_years: int
    max_years: int | None
    source: str  # which tier produced it: "field" | "regex" (future: "llm" | "inferred")


# --- Tier 1: parse a structured field like "5+", "3 to 5", "3-5" -------------------------------
_FIELD = re.compile(r"^\s*(\d{1,2})\s*(?:\+|(?:to|-|–|—)\s*(\d{1,2}))?", re.IGNORECASE)


def from_field(value: str | None) -> ExperienceSpan | None:
    """Tier 1 — parse a source's structured experience field. Deterministic, no description needed."""
    if not value:
        return None
    match = _FIELD.match(value)
    if not match:
        return None
    lo = int(match.group(1))
    hi = int(match.group(2)) if match.group(2) else None
    if lo > _MAX_PLAUSIBLE_YEARS:
        return None
    if hi is not None and hi < lo:  # guard malformed ranges like "3-1"
        hi = None
    return ExperienceSpan(lo, hi, "field")


# --- Tier 2: context-anchored regex over the description -----------------------------------------
# Anchored to "experience" so "40-year old C++ code" is NOT matched. The {0,25}? lets adjectives sit
# between the number and "experience" ("7+ years of proven experience"). Tried in order; range first.
_DESC_PATTERNS = [
    # "7 to 12 years of experience", "3-5 years' experience" — captures (lo, hi)
    re.compile(r"(\d{1,2})\s*(?:to|-|–|—|or)\s*(\d{1,2})\s*\+?\s*years?[\w\s'’/&,-]{0,25}?experience", re.I),
    # "7+ years of proven experience", "9+ years' experience", "minimum 3 years of experience"
    re.compile(r"(\d{1,2})\s*\+?\s*years?[\w\s'’/&,-]{0,25}?experience", re.I),
    # reversed: "experience of 5+ years", "Experience: 5 years"
    re.compile(r"experience[\w\s'’:/()&,-]{0,25}?(\d{1,2})\s*\+?\s*years?", re.I),
]


def from_description(text: str | None) -> ExperienceSpan | None:
    """Tier 2 — mine the description with experience-anchored regex (for sources without a field)."""
    if not text:
        return None
    for pattern in _DESC_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        lo = int(match.group(1))
        hi = int(match.group(2)) if match.lastindex and match.lastindex >= 2 and match.group(2) else None
        if lo > _MAX_PLAUSIBLE_YEARS:
            continue
        if hi is not None and (hi < lo or hi > _MAX_PLAUSIBLE_YEARS):
            hi = None
        return ExperienceSpan(lo, hi, "regex")
    return None


def extract(field: str | None, description: str | None) -> ExperienceSpan | None:
    """Run the cascade cheapest-first: structured field, then the description. None if nothing found.

    Add later tiers (LLM, seniority inference) by chaining more ``from_*`` calls here.
    """
    return from_field(field) or from_description(description)
