"""Extract a Job's required years of experience to a numeric range (enrichment).

A tiered cascade returning the first hit and which tier produced it (ADR-0009, ADR-0018). A concrete
number always wins; the seniority label is only a fallback when no number is stated:

  1. ``from_field``       — a structured field ("5+", "3 - 5 Years"), when a source provides one.
  2. ``from_description`` — experience-anchored regex over the free-text description.
  3. ``from_seniority``   — map a seniority label (the field, e.g. recruitee "entry_level", else the
                            title, e.g. "Senior Engineer") to a floor-years estimate. Fallback only.

Each tier is a pure function returning an :class:`ExperienceSpan` or ``None``. Widen recall by
appending to ``_DESC_PATTERNS`` or ``_SENIORITY``; a future LLM tier is another ``from_*`` chained in
:func:`extract`. Keeping each tier pure keeps the whole thing unit-testable without I/O.

Two things about Tier 2 are load-bearing and easy to undo by accident. **Ranges are tried before
single values**, because a single-value pattern will otherwise match at a range's ceiling and report
it as the floor ("2-4 years" served as 4+). And the work-word patterns — the ones that match without
the word "experience" nearby — carry narrative guards, because without them company age and founder
tenure read as requirements ("spent the last 15 years building …").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MAX_PLAUSIBLE_YEARS = (
    50  # reject absurd matches ("100 years"), almost always a parse error
)

# A stated *requirement* above this is never real — it is corporate narrative the work-word
# patterns can otherwise reach ("a combined 40+ years at Palantir building …"). Deliberately far
# below _MAX_PLAUSIBLE_YEARS, which guards arithmetic absurdity rather than genre; applied only to
# the two work-word patterns, so "25 years of experience" still parses where it is anchored.
_MAX_PLAUSIBLE_REQUIREMENT = 20


@dataclass(frozen=True, slots=True)
class ExperienceSpan:
    """A required-experience range in whole years. ``max_years`` is None for open-ended ("5+")."""

    min_years: int
    max_years: int | None
    source: (
        str  # which tier produced it: "field" | "regex" | "seniority" (future: "llm")
    )


# --- Tier 1: parse a structured field like "5+", "3 to 5", "3-5" -------------------------------
# \d{1,3} (not {1,2}) so a 3-digit value is captured whole and the plausibility guard can reject it
# ("100" must not truncate to a plausible-looking "10"); anything real is < 100 anyway.
_FIELD = re.compile(r"^\s*(\d{1,3})\s*(?:\+|(?:to|-|–|—)\s*(\d{1,3}))?", re.IGNORECASE)


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
    if hi is not None and (
        hi < lo or hi > _MAX_PLAUSIBLE_YEARS
    ):  # malformed ("3-1") or implausible ("3 to 99"); Tier 2 already guards this
        hi = None
    return ExperienceSpan(lo, hi, "field")


# --- Tier 2: regex over the description ----------------------------------------------------------
# Mostly anchored to "experience" so "40-year old C++ code" is NOT matched; the work-word patterns
# relax that to "N years <work word>" (the common "5+ years in software testing" phrasing) while
# still excluding "per year" / "10 years ago". The gap class allows . : · • so "Min. 10 Years" and
# "Experience · 7 years" match. Tried in order; **ranges before single values**, which is what stops
# a single-value pattern binding to the top of a range (see `_RANGE_TAIL` for the rest of that fix).
#
# `_GAP` stays at 30 deliberately. Widening it to 45 would gain the "N+ years <noun phrase>
# experience" class, but `search` returns the LEFTMOST match, so a wider gap also changes *which*
# requirement wins on a description stating several — measured at 2,690 jobs, mean +5.7 years. That
# is a semantic choice about multi-requirement postings (is the floor the first stated, or the
# largest?), not part of the range fix, so it is left to its own decision.
_GAP = r"[\w\s.'’:/()&,·•+-]{0,30}?"  # what may sit between the number and "experience"
_YEARS = (
    r"(?:years?|yrs?)"  # "yrs" is common enough in the corpus to be worth accepting
)
# The work-context words that make a bare "N years …" a requirement rather than prose.
_WORK = (
    r"(?:experience|work\w*|hands[\s-]?on|professional|industry|relevant|engineer\w*|"
    r"software|develop\w*|design\w*|programming|coding|build\w*|lead\w*|manag\w*|technical|"
    r"\bdev\b|\bqa\b|test\w*|data|cloud|security|devops|full[\s-]?stack|back[\s-]?end|front[\s-]?end)"
)
# of/in/as is optional: "4+ years building distributed systems" and "3+ years hands-on engineering"
# are as common as "5+ years of engineering". Making it optional is what `_NARRATIVE_*` then has to
# pay for — the connector used to be the only thing keeping corporate history out.
_CONN = r"\s+(?:of|in|as)?\s*"
_WORDS = (
    r"(?:[\w'’/&.-]+[\s,]+){0,4}?"  # filler between the connector and the work word
)
_DESC_PATTERNS = [
    # number-first range then "experience": "7 to 12 years of experience", "3-5 years' experience"
    re.compile(
        r"(\d{1,2})\s*(?:to|-|–|—|or)\s*(\d{1,2})\s*\+?\s*"
        + _YEARS
        + _GAP
        + "experience",
        re.IGNORECASE,
    ),
    # "experience" then a range (reversed): "Experience: 8 – 12 Years"
    re.compile(
        "experience" + _GAP + r"(\d{1,2})\s*(?:to|-|–|—)\s*(\d{1,2})\s*\+?\s*" + _YEARS,
        re.IGNORECASE,
    ),
    # "7+ years of proven experience", "5 plus years … experience", "minimum 3 years of experience"
    re.compile(
        r"(\d{1,2})\s*(?:\+|plus)?\s*" + _YEARS + _GAP + "experience", re.IGNORECASE
    ),
    # reversed single: "experience of 5+ years", "Experience: 5 years"
    re.compile(
        "experience" + _GAP + r"(\d{1,2})\s*(?:\+|plus)?\s*" + _YEARS, re.IGNORECASE
    ),
    # "5+ years in software testing", "7 years of professional engineering", "4+ years building …"
    re.compile(
        r"(\d{1,2})\s*(?:\+|plus)?\s*" + _YEARS + _CONN + _WORDS + _WORK, re.IGNORECASE
    ),
]

# The work-word patterns: the ones that match without the literal word "experience" nearby, so only
# they can reach corporate narrative, and only they carry the guards below. **Derived, not written
# down** — the "ranges before single values" ordering means a new range phrasing has to be inserted
# rather than appended, and a hardcoded index set would silently re-bind to the wrong pattern.
_WORK_WORD_PATTERNS = {i for i, p in enumerate(_DESC_PATTERNS) if _WORK in p.pattern}

# Company age, founder tenure, benefits: "N years" that is never a requirement. These read as
# requirements to a work-word pattern ("spent the last 15 years building …") and were previously
# excluded only as a side effect of demanding an of/in/as connector.
_NARRATIVE_BEFORE = re.compile(
    r"\b(?:spent|combined|celebrat\w*|founded|established|history|anniversar\w*|"
    r"vest\w*|sabbatical|tenure|runway)\b[\w\s,'’-]{0,25}$",
    re.IGNORECASE,
)
# Case-sensitive **on purpose**: "at Palantir" is tenure, but "at a startup" / "at the company" are
# ordinary requirement prose, and under re.I the `[A-Z]` would match both and discard a real number.
_NARRATIVE_AFTER = re.compile(r"^\s*(?:[Aa][Gg][Oo]\b|at\s+[A-Z])")

# A range separator sitting immediately before the number a single-value pattern matched — i.e. the
# match is a range's ceiling. Variable width, so `re` cannot express it as a lookbehind; it is
# applied as an explicit backward look instead. This is what actually fixes the ceiling-as-floor
# bug, for every pattern at once and for separators the range patterns never enumerate: "2 ~ 4",
# "between 2 and 4". `and` is safe here only because the digit must sit immediately before it —
# "3 year and 10 year anniversary" has "year" in between, so it does not read as a range.
_RANGE_TAIL = re.compile(r"(\d{1,2})\s*(?:-|–|—|~|to|or|and)\s*$", re.IGNORECASE)


def _is_narrative(text: str, match: re.Match) -> bool:
    """Whether this match sits in corporate history rather than in a requirement."""
    return bool(
        _NARRATIVE_BEFORE.search(text[max(0, match.start() - 40) : match.start()])
        or _NARRATIVE_AFTER.match(text[match.end() : match.end() + 14])
    )


def from_description(text: str | None) -> ExperienceSpan | None:
    """Tier 2 — mine the description with experience-anchored regex (for sources without a field)."""
    if not text:
        return None
    for index, pattern in enumerate(_DESC_PATTERNS):
        # finditer, not search: a guarded rejection must fall through to the next *occurrence*, so
        # "Founded 12 years ago. Requires 5+ years building …" still yields 5 rather than nothing.
        for match in pattern.finditer(text):
            lo = int(match.group(1))
            hi = (
                int(match.group(2))
                if match.lastindex and match.lastindex >= 2 and match.group(2)
                else None
            )
            if index in _WORK_WORD_PATTERNS and (
                _is_narrative(text, match) or lo > _MAX_PLAUSIBLE_REQUIREMENT
            ):
                continue
            if hi is None:
                # Recover the floor when this match is a range's ceiling ("2-4 years" -> 2, not 4).
                tail = _RANGE_TAIL.search(
                    text[max(0, match.start(1) - 12) : match.start(1)]
                )
                if tail and int(tail.group(1)) < lo:
                    lo, hi = int(tail.group(1)), lo
            if lo > _MAX_PLAUSIBLE_YEARS:
                continue
            if hi is not None and (hi < lo or hi > _MAX_PLAUSIBLE_YEARS):
                hi = None
            return ExperienceSpan(lo, hi, "regex")
    return None


# --- Tier 3 (fallback): map a seniority label to a floor-years estimate --------------------------
# Used only when no concrete number was found (ADR-0018): a source's seniority field (recruitee
# "entry_level", workable "Mid-Senior level", personio "experienced") or, absent a field, the title
# ("Senior Engineer"). The number is a rough floor for the "<= N years" filter — a signal beats none.
# Years per tier are calibrated to the DATA (ADR-0018): for jobs that carry both a seniority label and
# a concrete number in the description, the median description-min_years per tier is ~1 (entry), 3
# (associate/mid), 5 (senior / "experienced" / smartrecruiters' "executive" level), 10 (director).
_SENIORITY = [
    (
        re.compile(
            r"\b(director|vice[\s-]?president|\bvp\b|chief|\bcto\b|\bceo\b|head of|principal|distinguished|fellow)\b",
            re.IGNORECASE,
        ),
        10,
    ),
    (re.compile(r"\b(lead|staff|architect|expert)\b", re.IGNORECASE), 7),
    (
        re.compile(
            r"\b(senior|mid[\s-]?senior|\bsr\b|experienced|executive)\b", re.IGNORECASE
        ),
        5,
    ),
    (re.compile(r"\b(associate|mid[\s_-]?level|intermediate)\b", re.IGNORECASE), 3),
    (
        re.compile(
            r"\b(intern|internship|trainee|graduate|\bgrad\b|student|entry[\s_-]?level|junior|\bjr\b|apprentice|fresher|early[\s-]?career)\b",
            re.IGNORECASE,
        ),
        0,
    ),
]


# Numeric / roman level suffixes on the title ("Software Engineer 1", "Data Scientist III", "SDE II")
# also encode seniority: I/1 = entry, II/2 = mid, III/3 = senior, IV/V = staff.
_LEVEL = re.compile(
    r"\b(?:engineer|developer|programmer|analyst|scientist|architect|sde|swe)\s*"
    r"(iii|ii|iv|i|v|[1-5])\b",
    re.IGNORECASE,
)
_LEVEL_YEARS = {
    "i": 0,
    "1": 0,
    "ii": 3,
    "2": 3,
    "iii": 5,
    "3": 5,
    "iv": 7,
    "4": 7,
    "v": 7,
    "5": 7,
}


def from_seniority(
    field: str | None, title: str | None = None
) -> ExperienceSpan | None:
    """Tier 3 (fallback) — map a seniority label to a floor-years estimate, from the source's field
    (else the title). Word labels first ("Senior"), then a numeric/roman level suffix ("Engineer II")."""
    text = f"{field or ''} {title or ''}"
    if not text.strip():
        return None
    for pattern, years in _SENIORITY:
        if pattern.search(text):
            return ExperienceSpan(years, None, "seniority")
    match = _LEVEL.search(title or "")
    if match:
        return ExperienceSpan(_LEVEL_YEARS[match.group(1).lower()], None, "seniority")
    return None


def extract(
    field: str | None, description: str | None, title: str | None = None
) -> ExperienceSpan | None:
    """Run the cascade: a concrete number from the structured field, then from the description, and
    only if neither yields one, a floor estimate from the seniority label (field or title). Concrete
    numbers always win over the seniority fallback (per ADR-0018). None if nothing matches.
    """
    return (
        from_field(field)
        or from_description(description)
        or from_seniority(field, title)
    )
