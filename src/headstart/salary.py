"""Salary extraction: a stated field, then the description — never a fabricated estimate.

Mirrors ``headstart.experience``'s tiered-cascade shape (ADR-0009, ADR-0018) with one deliberate
difference: there is no third, seniority-style fallback tier. A missing years-of-experience number
can be reasonably floor-estimated from a title ("Senior" plausibly means 5+); a missing salary
cannot be guessed from a title without fabricating a dollar figure that risks misleading a real
financial decision. ``extract()`` returns ``None`` rather than estimate — unknown stays unknown,
and unknown is not exclusionary (docs/salary-extraction/README.md; CONTEXT.md's "Salary" entry).

Every extracted figure is **period-normalized to an annual amount in its native currency** — no
cross-currency conversion. An hourly or monthly figure is annualized; a $ and a ₹ figure are never
compared numerically, so ``currency`` travels as its own field rather than being folded away.

Built from real evidence, not speculative patterns: the Tier-2 regexes and guards below came from
reading actual `workable` description text during the salary-extraction pilot
(docs/salary-extraction/workable.md) — including two confirmed false-positive classes (company
revenue/funding narrative, and benefit-contribution amounts like "$2,400 HSA contribution") that
would otherwise misread as a salary figure exactly the way experience.py's narrative guards exist
for company-tenure phrases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MAX_PLAUSIBLE_ANNUAL = {
    "USD": 800_000,
    "GBP": 700_000,
    "EUR": 750_000,
    "CAD": 900_000,
    "AUD": 900_000,
    "INR": 20_00_00_000,  # 2 crore
    "HKD": 6_000_000,
    "SEK": 6_000_000,
}
_MIN_PLAUSIBLE_ANNUAL = {
    "USD": 10_000,
    "GBP": 8_000,
    "EUR": 8_000,
    "CAD": 10_000,
    "AUD": 10_000,
    "INR": 100_000,  # 1 lakh
    "HKD": 80_000,
    "SEK": 150_000,
}
_HOURLY_TO_ANNUAL = 2080  # 40hr/wk * 52wk, the standard full-time-equivalent convention
_DAILY_TO_ANNUAL = 260  # 5 days/wk * 52wk


@dataclass(frozen=True, slots=True)
class SalarySpan:
    """A stated salary, period-normalized to an annual figure in its native currency.

    ``max_annual`` is None for an open-ended figure ("$120k+", a single stated number with no
    range). ``currency`` is None when a number was found but its currency could not be determined
    (a bare, ambiguous "$" or an unmarked figure) — still worth keeping the amount for, since
    ``has_salary``-style presence filtering doesn't need currency, only a numeric range filter
    would, and that isn't built yet.
    """

    min_annual: int
    max_annual: int | None
    currency: str | None
    source: str  # "field" | "regex" — no "seniority": see the module docstring


def extract(
    salary: str | None, description: str | None, ats: str | None = None
) -> SalarySpan | None:
    """Run the cascade: a concrete figure from the structured field, then from the description.
    None if neither yields one — never a fabricated estimate (see module docstring)."""
    return from_field(salary, ats) or from_description(description, ats)


# --- Tier 1: parse Job.salary, a string we already formatted per-scraper -----------------------
# These are OUR OWN output shapes (each scraper's private `_salary()`-style helper), not organic
# free text — so a small per-ATS dispatch beats one generic regex guessing across shapes that
# don't converge. Populated so far from what's already known in each scraper's source (verified
# during the salary-extraction planning pass, 2026-08-21); extended per-ATS as that ATS gets its
# own research pass in docs/salary-extraction/.

_CURRENCY_CODE = re.compile(r"\b(USD|EUR|GBP|INR|CAD|AUD|HKD|SEK)\b", re.IGNORECASE)
_RANGE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*[-–]\s*(\d[\d,]*(?:\.\d+)?)")
_SINGLE_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)")


def _num(s: str) -> int:
    return round(float(s.replace(",", "")))


def _period_multiplier(text: str) -> int:
    low = text.lower()
    if any(p in low for p in ("per-hour", "per hour", "/hr", "hourly")):
        return _HOURLY_TO_ANNUAL
    if any(p in low for p in ("per-month", "per month", "/mo", "monthly", "mensual")):
        return 12
    return 1  # annual is the default for every known Tier-1 format


def _bounded(
    min_annual: int, max_annual: int | None, currency: str | None
) -> SalarySpan | None:
    """Reject a figure outside plausible bounds for its currency rather than trust a parse error
    or a mis-scaled magnitude through. Unknown currency gets the widest (USD) bound as a floor
    check only — better than no check, not a substitute for knowing the currency."""
    lo = _MIN_PLAUSIBLE_ANNUAL.get(currency or "USD", _MIN_PLAUSIBLE_ANNUAL["USD"])
    hi = _MAX_PLAUSIBLE_ANNUAL.get(currency or "USD", _MAX_PLAUSIBLE_ANNUAL["USD"])
    if min_annual < lo or min_annual > hi:
        return None
    if max_annual is not None and (max_annual < lo or max_annual > hi):
        return None
    return SalarySpan(min_annual, max_annual, currency, "field")


def _field_lever_recruitee_teamtailor(value: str) -> SalarySpan | None:
    """lever: "50000-70000 USD per-year-salary" | recruitee: "50000-70000 EUR per year" |
    teamtailor: "40000-60000 EUR YEAR" — all converge on RANGE + CODE + optional period."""
    m = _RANGE.search(value)
    if not m:
        return None
    code_m = _CURRENCY_CODE.search(value)
    currency = code_m.group(1).upper() if code_m else None
    mult = _period_multiplier(value)
    lo, hi = _num(m.group(1)) * mult, _num(m.group(2)) * mult
    return _bounded(min(lo, hi), max(lo, hi), currency)


def _field_keka(value: str) -> SalarySpan | None:
    """ "25000-30000 INR" — period is not in the payload at all (confirmed in keka.py's own
    docstring), so this is left un-annualized; a future keka-specific research pass may find the
    period is knowable from context this string alone doesn't carry."""
    m = _RANGE.search(value)
    if not m:
        return None
    code_m = _CURRENCY_CODE.search(value)
    currency = code_m.group(1).upper() if code_m else None
    lo, hi = _num(m.group(1)), _num(m.group(2))
    return _bounded(min(lo, hi), max(lo, hi), currency)


def _field_darwinbox(value: str) -> SalarySpan | None:
    """ "INR 3 - 5 (Annual)" — lakhs, not absolute rupees (ADR-0019's own documented example,
    confirmed against the scraper's real payload semantics: no real tech salary is INR 3-5/year).
    Multiply by 100,000 before bounding. The parenthesized suffix is the scraper's own
    ``salary_timeframe`` field (darwinbox.py) and is **not** always "Annual" — it's a real,
    variable value, so `_period_multiplier` still applies on top of the lakhs conversion. Missing
    that would have let a monthly figure read as annual-and-in-bounds, quietly 12x too low
    (code-review finding, PR #234)."""
    if "INR" not in value.upper():
        return None
    period_mult = _period_multiplier(value)
    m = _RANGE.search(value)
    if not m:
        single = _SINGLE_NUM.search(value)
        if not single:
            return None
        lo = hi = _num(single.group(1)) * 100_000 * period_mult
        return _bounded(lo, hi, "INR")
    lo = _num(m.group(1)) * 100_000 * period_mult
    hi = _num(m.group(2)) * 100_000 * period_mult
    return _bounded(min(lo, hi), max(lo, hi), "INR")


#: ATS -> its Tier-1 parser. An ATS not listed here (including one not yet given its own research
#: pass) falls through to `_field_generic`.
_FIELD_PARSERS = {
    "lever": _field_lever_recruitee_teamtailor,
    "recruitee": _field_lever_recruitee_teamtailor,
    "teamtailor": _field_lever_recruitee_teamtailor,
    "keka": _field_keka,
    "darwinbox": _field_darwinbox,
}


def _field_generic(value: str) -> SalarySpan | None:
    """Best-effort for an ATS with no calibrated parser yet: a range or single figure plus
    whatever currency code/period the string happens to state. Deliberately conservative — no
    per-ATS quirk handling, so it under-extracts rather than mis-extracts."""
    code_m = _CURRENCY_CODE.search(value)
    currency = code_m.group(1).upper() if code_m else None
    mult = _period_multiplier(value)
    m = _RANGE.search(value)
    if m:
        lo, hi = _num(m.group(1)) * mult, _num(m.group(2)) * mult
        return _bounded(min(lo, hi), max(lo, hi), currency)
    single = _SINGLE_NUM.search(value)
    if single:
        v = _num(single.group(1)) * mult
        return _bounded(v, None, currency)
    return None


def from_field(salary: str | None, ats: str | None = None) -> SalarySpan | None:
    """Parse the structured ``Job.salary`` string a scraper already formatted."""
    value = (salary or "").strip()
    if not value:
        return None
    parser = _FIELD_PARSERS.get(ats or "", _field_generic)
    return parser(value)


# --- Tier 2: regex-scan the description, only reached when Tier 1 finds nothing -----------------
# Patterns and guards below are evidence-based, mined from real `workable` description text during
# the pilot (docs/salary-extraction/workable.md) — not written ahead of real samples.

# Two confirmed false-positive classes from real workable data, guarded against exactly like
# experience.py guards narrative company-tenure phrases:
#   - company revenue/funding: "$8 billion in annual revenue", "Series B this year (€30 million)"
#   - benefit-contribution amounts: "$2,400 company contribution to Health Savings Account (HSA)"
# A match is rejected if one of these context words appears within 40 chars either side of it —
# "signing bonus" and "revenue" both trail their number in real text ("$50,000 signing bonus"),
# so both directions matter; catching only one side is a silent miss, not a narrower guard.
#
# Deliberately NOT bare "hsa"/"401(k)": those are benefit *category* names, and once the guard
# started checking the after-window too (see below) they started rejecting genuine salaries that
# happen to be followed by an unrelated benefits list — real corpus examples: "Pay range:
# $150,000 - $195,000 per year with bonus potential 401(k) Dental insurance...", "Competitive
# salary of $71,700-$85,300 annually 401(k) Dental insurance...". "contribution" alone still
# catches the real false positive ("$2,400 company CONTRIBUTION to ... (HSA)") without that
# collateral damage, because a benefits-list mention never itself says "contribution".
_FALSE_POSITIVE_CONTEXT = re.compile(
    r"\b("
    r"revenue|valuation|series\s+[a-e]\b|funding|raised|arr\b|"
    r"contribution|sign(?:ing|-on)\s+bonus|referral\s+(?:bonus|program|fee)"
    r")\b",
    re.IGNORECASE,
)
_CONTEXT_WINDOW = 40


def _has_false_positive_context(text: str, start: int, end: int) -> bool:
    """Whether a false-positive trigger word appears within :data:`_CONTEXT_WINDOW` chars either
    side of the match spanning ``text[start:end]``. Two independent, separately-bounded checks —
    not one combined-and-searched string — so a full budget is available on *each* side; sharing
    one budget between "before" and "after" silently starved whichever side came second (found
    live: "We offer a $50,000 - $60,000 signing bonus" extracted uncaught until this was fixed,
    since the old check's post-match slice started at the match's own start, not its end, leaving
    almost no real lookahead)."""
    before = text[max(0, start - _CONTEXT_WINDOW) : start]
    after = text[end : end + _CONTEXT_WINDOW]
    return bool(
        _FALSE_POSITIVE_CONTEXT.search(before) or _FALSE_POSITIVE_CONTEXT.search(after)
    )


_CURRENCY_SYM = {
    "$": None,
    "£": "GBP",
    "€": "EUR",
    "₹": "INR",
}  # "$" resolved below (see _guess_dollar_currency)

# Anchored patterns, tried in this order — an explicit "Salary:"/"Pay range:"/"Compensation:"
# label first (highest confidence, matches "Salary: upto £29,000", "Compensation: $100-120k",
# "Pay Rate: $34-58/hr" from real samples), then a bare currency-symbol range/single anywhere in
# text as a fallback.
_LABELED = re.compile(
    r"""
    (?:annual\s+)?
    (?:
        (?:salary|compensation|pay|remuneration|base\s+salary|wage)\s*(?:range|rate)?
        (?:\s+for\s+\w+(?:\s+\w+){0,2})?\s*:?\s*
        (?:upto|up\s+to|of|is|from|starting(?:\s+(?:salary|at|rate))?)?
        | starting\s+at  # a bare "starting at $X", no salary/pay/wage word required
    )\s*
    (?P<sym>[$£€₹])?\s*
    (?P<lo>\d[\d,]*(?:\.\d+)?)\s*(?:[kK])?
    (?:\s*(?:USD|EUR|GBP|INR|CAD|AUD|HKD|SEK))?
    (?:\s*[-–to]{1,3}\s*(?P<sym2>[$£€₹])?\s*(?P<hi>\d[\d,]*(?:\.\d+)?)\s*(?:[kK])?
       (?:\s*(?:USD|EUR|GBP|INR|CAD|AUD|HKD|SEK))?)?
    """,
    re.IGNORECASE | re.VERBOSE,
)

_BARE_RANGE = re.compile(
    r"(?P<sym>[$£€₹])\s*(?P<lo>\d[\d,]*(?:\.\d+)?)\s*(?:[kK])?"
    r"(?:\s*[-–]\s*|\s+to\s+)"
    r"(?P<sym2>[$£€₹])?\s*(?P<hi>\d[\d,]*(?:\.\d+)?)\s*(?:[kK])?",
    re.IGNORECASE,
)

# A bare number range with a currency CODE (not symbol) trailing it — "50,000-70,000 USD/year",
# common when a scraper's own Tier-1 phrasing ("USD per-year-salary") style leaks into free text
# too. Requires the code immediately after (within a few chars) so it doesn't fire on two
# unrelated numbers that happen to share a paragraph with an unrelated currency mention.
_BARE_RANGE_CODE = re.compile(
    r"(?P<lo>\d[\d,]*(?:\.\d+)?)\s*(?:[kK])?"
    r"\s*[-–]\s*"
    r"(?P<hi>\d[\d,]*(?:\.\d+)?)\s*(?:[kK])?"
    r"\s*(?:USD|EUR|GBP|INR|CAD|AUD|HKD|SEK)\b",
    re.IGNORECASE,
)

# Same idea, but the code trails EACH side rather than the range as a whole — real workday text:
# "between 518,910.00 SEK - 815,430.00 SEK" (European-market postings state it this way; the
# single-trailing-code shape above wouldn't match, the code appears twice, once per number).
_BARE_RANGE_CODE_EACH = re.compile(
    r"(?P<lo>\d[\d,]*(?:\.\d+)?)\s*(?:USD|EUR|GBP|INR|CAD|AUD|HKD|SEK)\b"
    r"\s*[-–]\s*"
    r"(?P<hi>\d[\d,]*(?:\.\d+)?)\s*(?:USD|EUR|GBP|INR|CAD|AUD|HKD|SEK)\b",
    re.IGNORECASE,
)

# A number-then-symbol pattern ("51882€", the international convention several non-US/UK
# postings use) was tried and reverted (workday pass, PR TBD): `_num()` treats "." as a true
# decimal point, which is correct for the simple French integer case that motivated it
# ("51882€") but actively WRONG for the European thousands-separator convention real postings
# also use ("37.500,00$" is thirty-seven thousand five hundred, not 37.5) — confirmed producing
# real, incorrect SalarySpans on real workday data, not just a theoretical risk. It also
# collided with workday's own site URLs, which embed a literal "$" as a path delimiter
# ("/inst/1$9925/9925$27033.html"). Removed rather than shipped producing wrong numbers; see
# docs/salary-extraction/workday.md's known-gaps section — proper support needs real
# locale-aware number parsing (distinguishing "," and "." as decimal vs. thousands separator),
# which this module doesn't have.

# LPA ("Lakhs Per Annum") gets its own first-class pattern rather than falling through the
# generic currency-symbol patterns above — it names neither a symbol nor a period marker those
# patterns look for, and it's the standard way Indian tech postings state a salary (this repo's
# scope note: "India is a strong sub-segment", CLAUDE.md). "8-12 LPA", "8 LPA", "Rs 8-12 LPA".
_LPA = re.compile(
    r"(?:₹|rs\.?|inr)?\s*(?P<lo>\d+(?:\.\d+)?)\s*(?:[-–]\s*(?P<hi>\d+(?:\.\d+)?))?\s*LPA\b",
    re.IGNORECASE,
)

_PERIOD_HINT = re.compile(
    r"\b(?:/\s*hr\b|/\s*hour\b|per\s+hour|hourly|\bhr\b|"
    r"/\s*mo\b|/\s*month\b|per\s+month|monthly|\bmo\b|"
    r"/\s*yr\b|/\s*year\b|per\s+year|per\s+annum|annually|annual|\byr\b|"
    r"/\s*day\b|per\s+day|daily|\bday\b)",
    re.IGNORECASE,
)


def _guess_currency(sym: str | None, code_context: str) -> str | None:
    if sym and sym != "$":
        return _CURRENCY_SYM.get(sym)
    code_m = _CURRENCY_CODE.search(code_context)
    if code_m:
        return code_m.group(1).upper()
    if sym == "$":
        return "USD"  # statistically dominant in this corpus; genuinely ambiguous otherwise
    return None


def _period_from_window(text: str, start: int, end: int) -> int:
    window = text[max(0, start - 20) : end + 30]
    m = _PERIOD_HINT.search(window)
    if not m:
        return 1
    hint = m.group(0).lower()
    if "hr" in hint or "hour" in hint:
        return _HOURLY_TO_ANNUAL
    if "day" in hint or "daily" in hint:
        return _DAILY_TO_ANNUAL
    if "mo" in hint or "month" in hint:
        return 12
    return 1  # yr/year/annum/annual(ly) — already annual


def _scan(text: str, pattern: re.Pattern) -> list[SalarySpan]:
    found: list[SalarySpan] = []
    for m in pattern.finditer(text):
        if _has_false_positive_context(text, m.start(), m.end()):
            continue
        gd = m.groupdict()
        lo_raw, hi_raw = gd.get("lo"), gd.get("hi")
        if not lo_raw:
            continue
        # "k" shorthand: the pattern already consumed an optional trailing k/K without capturing
        # it separately, so detect it from the matched text itself.
        matched = m.group(0)
        k_mult = 1000 if re.search(r"\d[kK]\b", matched) else 1
        mult = _period_from_window(text, m.start(), m.end()) * k_mult
        currency = _guess_currency(gd.get("sym"), matched)
        lo = _num(lo_raw) * mult
        hi = _num(hi_raw) * mult if hi_raw else None
        span = _bounded(
            min(lo, hi) if hi else lo, max(lo, hi) if hi else None, currency
        )
        if span is not None:
            found.append(
                SalarySpan(span.min_annual, span.max_annual, span.currency, "regex")
            )
    return found


def _scan_lpa(text: str) -> list[SalarySpan]:
    found: list[SalarySpan] = []
    for m in _LPA.finditer(text):
        if _has_false_positive_context(text, m.start(), m.end()):
            continue
        gd = m.groupdict()
        lo = round(float(gd["lo"]) * 100_000)
        hi = round(float(gd["hi"]) * 100_000) if gd.get("hi") else None
        span = _bounded(min(lo, hi) if hi else lo, max(lo, hi) if hi else None, "INR")
        if span is not None:
            found.append(SalarySpan(span.min_annual, span.max_annual, "INR", "regex"))
    return found


#: Sentinel: this tier found match(es), but they disagree — ambiguous, stop the whole cascade
#: rather than falling through to a lower-confidence tier that might paper over the conflict.
_AMBIGUOUS = object()


def _resolve(spans: list[SalarySpan]) -> SalarySpan | None | object:
    """One match wins outright; several mutually-consistent ones agree, so the first stands;
    several that disagree are ambiguous (:data:`_AMBIGUOUS`); none means this tier found nothing,
    try the next. Shared by every Tier-2 pattern so the "don't guess when ambiguous" rule can't
    drift between them."""
    if not spans:
        return None
    if len(spans) == 1 or _mutually_consistent(spans):
        return spans[0]
    return _AMBIGUOUS


def from_description(
    description: str | None, ats: str | None = None
) -> SalarySpan | None:
    """Scan free text for a stated salary, trying patterns in confidence order: LPA (a
    distinctive, unambiguous marker when present), an explicit "Salary:"/"Compensation:"-style
    label, a bare currency-symbol range, then a bare number range anchored by a trailing currency
    code. Multiple, mutually-inconsistent genuine matches within one tier are ambiguous and stop
    the cascade there (never fall through to a lower-confidence tier to paper over the conflict)
    — the same no-fabrication principle extended from estimation to disambiguation."""
    text = description or ""
    if not text:
        return None
    for pattern in (
        _scan_lpa(text),
        *(
            _scan(text, p)
            for p in (_LABELED, _BARE_RANGE, _BARE_RANGE_CODE, _BARE_RANGE_CODE_EACH)
        ),
    ):
        result = _resolve(pattern)
        if result is _AMBIGUOUS:
            return None
        if result is not None:
            return result
    return None


def _mutually_consistent(spans: list[SalarySpan]) -> bool:
    """Whether every span in ``spans`` roughly agrees (same currency, overlapping-ish range) —
    duplicate mentions of the same figure, not two different genuine numbers."""
    first = spans[0]
    for s in spans[1:]:
        if s.currency != first.currency:
            return False
        if abs(s.min_annual - first.min_annual) > max(first.min_annual * 0.05, 1000):
            return False
    return True
