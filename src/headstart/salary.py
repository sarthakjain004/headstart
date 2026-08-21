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
# Mostly OUR OWN output shapes (each scraper's private `_salary()`-style helper: lever, recruitee,
# teamtailor, keka, darwinbox each get a calibrated `_field_*` parser below) — but not always: an
# ATS with no dedicated parser falls through to `_field_generic`, and at least two (ashby, personio
# — corrected claim, code review, PR #238) pass an HR system's own raw free-text field straight
# into `Job.salary` with zero scraper-side normalization, so `_field_generic` has to treat its
# input as organic free text too, not assume it's one of our own formats.

# Shared alternation for the 8 ISO codes this module recognizes — several independent regexes
# below embed it; kept as one string (code review finding, PR #235) so they can't drift apart.
_CURRENCY_CODES = "USD|EUR|GBP|INR|CAD|AUD|HKD|SEK"

_CURRENCY_CODE = re.compile(rf"\b({_CURRENCY_CODES})\b", re.IGNORECASE)
_RANGE = re.compile(r"(\d(?:[\d,]*\d)?(?:\.\d+)?)\s*[-–]\s*(\d(?:[\d,]*\d)?(?:\.\d+)?)")
_SINGLE_NUM = re.compile(r"(\d(?:[\d,]*\d)?(?:\.\d+)?)")


def _num(s: str) -> int:
    return round(float(s.replace(",", "")))


# "up to $X" (or "upto"/LPA's "up to ₹X") states a CEILING, not a floor — but SalarySpan.min_annual
# is a required int with no way to represent "ceiling known, floor unknown", so blindly assigning
# this number to min_annual (as every other connector correctly does for a floor-style figure:
# "starting at", "from", "is") would silently invert the claim: a job paying AT MOST $X would
# misreport as paying AT LEAST $X. Real, substantial, already-shipped prevalence measured directly
# against every ATS sampled so far (workable, workday, greenhouse, smartrecruiters, zoho combined:
# roughly 1,600 real occurrences across _LABELED, _BARE_HOURLY_OR_DAILY, and LPA alone) — found via
# a real zoho example during PR #238's review ("Salary: Up to ₹28 LPA" extracting as
# min_annual=2,800,000), not theoretical. Only matters for a SINGLE bare value (no captured `hi`);
# an actual stated range ("up to $50,000-$60,000") already states both bounds regardless of the
# connector, so it's unaffected. Shared by both tiers (not just Tier 2's description mining) —
# code review, PR #238: Tier 1's `_field_generic` fallback has the identical bare-single-value
# shape, and it's live-reachable, not theoretical: ashby/personio pass an HR system's raw free-text
# field straight into `Job.salary` with no scraper-side normalization (unlike lever/recruitee/
# teamtailor/keka/darwinbox, each of which has its own calibrated `_field_*` parser for a shape
# *we* format), so "Up to €50,000" from either of those ATSes hit this exact bug too.
_CEILING_CONNECTOR_WINDOW = (
    15  # chars scanned before the number; mirrors _CONTEXT_WINDOW's naming
)
_UP_TO_CONNECTOR = re.compile(r"\bup\s*to\s*[$£€₹]?\s*$", re.IGNORECASE)


def _states_a_ceiling_only(text: str, lo_start: int) -> bool:
    """Whether an "up to" connector (any amount of internal whitespace, including none — "upto")
    immediately precedes the number starting at ``lo_start`` (see :data:`_UP_TO_CONNECTOR`)."""
    window = text[max(0, lo_start - _CEILING_CONNECTOR_WINDOW) : lo_start]
    return bool(_UP_TO_CONNECTOR.search(window))


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
        # Real free-text ATS fields reach this branch (ashby, personio — see the Tier-1 section
        # comment above), so the same ceiling-vs-floor risk Tier 2 has applies here too: "Up to
        # €50,000" would otherwise misreport €50,000 as a floor (code review, PR #238).
        if _states_a_ceiling_only(value, single.start(1)):
            return None
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
        (?:salary|compensation|pay(?:ing)?|remuneration|base\s+salary|wage)\s*(?:range|rate)?
        (?:\s+for\s+\w+(?:\s+\w+){0,2})?\s*:?\s*
        (?:upto|up\s+to|of\s+up\s+to|is\s+up\s+to|of|is|from|starting(?:\s+(?:salary|at|rate))?)?
        | (?P<bare_starting>starting\s+at)  # bare "starting at $X" — no salary/pay/wage word;
                                            # named so _scan can demand a period hint nearby (see
                                            # its call site) rather than default-annual-guessing,
                                            # since nothing else here confirms this is even a wage
    )\s*
    (?P<sym>[$£€₹])?\s*
    (?P<lo>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?
    (?:\s*(?:@CODES@)\b)?
    (?:\s*[-–—to]{1,3}\s*(?P<sym2>[$£€₹])?\s*(?P<hi>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?
       (?:\s*(?:@CODES@)\b)?)?
    """.replace("@CODES@", _CURRENCY_CODES),
    re.IGNORECASE | re.VERBOSE,
)

_BARE_RANGE = re.compile(
    r"(?P<sym>[$£€₹])\s*(?P<lo>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?"
    r"(?:\s*[-–—]\s*|\s+to\s+)"
    r"(?P<sym2>[$£€₹])?\s*(?P<hi>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?",
    re.IGNORECASE,
)

# "between $X and $Y" — real, common phrasing (greenhouse: "the base pay for this role will be
# between $60,000 and $70,000", "the salary range for this position is between $90,000 and
# $125,000") not covered by _BARE_RANGE (which requires -/–/—/to, not "and") or a widened
# _LABELED connector: the filler before "between" varies too much to enumerate safely ("will be",
# "is", "is expected to be", ...), and a bare, unanchored "and" would risk joining two unrelated
# dollar mentions ("$50,000 in RSUs and $10,000 signing bonus"). Anchoring on the literal word
# "between" immediately before the first number is what makes this safe without a label word.
_BARE_BETWEEN = re.compile(
    r"\bbetween\s+(?P<sym>[$£€₹])\s*(?P<lo>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?"
    r"\s+and\s+"
    r"(?P<sym2>[$£€₹])?\s*(?P<hi>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?",
    re.IGNORECASE,
)

# A bare "$X/hour" or "$X per day" with no label word or connector at all — real, common on
# smartrecruiters' retail/logistics/care-work postings ("Support Workers ... £8.72 per hour",
# "BENEFITS & SCHEDULING: $22.50/HOUR!!") where the job description has no "salary"/"pay" word
# anywhere nearby. Safe specifically because the pattern's OWN match already includes an explicit
# hourly/daily rate marker — that's the positive confirmation _LABELED's bare "starting at" branch
# needed a separate _STRONG_PERIOD_HINT check for. Deliberately scoped to hourly/daily only, not
# monthly/yearly: a bare, unlabeled "$X/month" or "$X/year" is far more likely to be something
# else (rent, a subscription) and hasn't been measured safe the way this narrower shape has.
# `(?<!\+)` excludes a leading "+" — real bug, found via the cross-ATS regression diff (not the
# original 94-case sample): shift-differential lists ("+$4.50/hr -> Mon-Thu Nights +$9.00/hr ->
# Fri-Sun Nights") state several genuinely different add-on figures, and when the plausibility
# floor happened to filter out all but one of them, _resolve() saw a lone "unambiguous" match and
# wrongly reported a differential as the base wage. "+$X" is a reliable, structural signal this is
# an add-on, not the rate itself — excluding it fixes the mechanism at its source rather than
# guessing which survivor to trust.
_BARE_HOURLY_OR_DAILY = re.compile(
    r"(?<!\+)(?P<sym>[$£€₹])\s?(?P<lo>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*"
    r"(?:/\s*hr\b|/\s*hour\b|per\s+hour|hourly\b|/\s*day\b|per\s+day\b|daily\b)",
    re.IGNORECASE,
)

# A bare number range with a currency CODE (not symbol) trailing it — "50,000-70,000 USD/year",
# common when a scraper's own Tier-1 phrasing ("USD per-year-salary") style leaks into free text
# too. Requires the code immediately after (within a few chars) so it doesn't fire on two
# unrelated numbers that happen to share a paragraph with an unrelated currency mention.
_BARE_RANGE_CODE = re.compile(
    r"(?P<lo>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?"
    r"\s*[-–—]\s*"
    r"(?P<hi>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?"
    rf"\s*(?:{_CURRENCY_CODES})\b",
    re.IGNORECASE,
)

# Same idea, but the code trails EACH side rather than the range as a whole — real workday text:
# "between 518,910.00 SEK - 815,430.00 SEK" (European-market postings state it this way; the
# single-trailing-code shape above wouldn't match, the code appears twice, once per number).
_BARE_RANGE_CODE_EACH = re.compile(
    rf"(?P<lo>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:{_CURRENCY_CODES})\b"
    r"\s*[-–—]\s*"
    rf"(?P<hi>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:{_CURRENCY_CODES})\b",
    re.IGNORECASE,
)

# A number-then-symbol pattern ("51882€", the international convention several non-US/UK
# postings use) was tried and reverted (workday pass, PR #235): `_num()` treats "." as a true
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

# "Level 1: $X - $Y Level 2: $A - $B ..." — a real, near-single-company template (greenhouse:
# confirmed 494/495 corpus occurrences are SpaceX, 1 is xAI) disclosing several compensation bands
# for one role at once. Every generic pattern below would see these as multiple genuinely-
# different numbers and correctly refuse to guess which one is "the" salary (_resolve's ambiguity
# rule) — but that's too conservative here: each band is explicitly labeled as part of the SAME
# role's stated range, so the envelope (lowest floor to highest ceiling across all bands) is real,
# stated information, not a guess. Checked before the generic cascade in from_description so it
# wins over the ambiguous-therefore-None outcome the bands would otherwise produce.
_LEVEL_BAND = re.compile(
    r"Level\s+\d+\s*:\s*"
    r"(?P<sym>[$£€₹])\s*(?P<lo>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?"
    r"\s*[-–—]\s*"
    r"(?P<sym2>[$£€₹])?\s*(?P<hi>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?",
    re.IGNORECASE,
)

_PERIOD_HINT = re.compile(
    r"\b(?:/\s*hr\b|/\s*hour\b|per\s+hour|hourly|\bhr\b|"
    r"/\s*mo\b|/\s*month\b|per\s+month|monthly|\bmo\b|"
    r"/\s*yr\b|/\s*year\b|per\s+year|per\s+annum|annually|annual|\ba\s+year\b|\byr\b|"
    r"/\s*day\b|per\s+day|daily|\bday\b)"
    # No leading \b: British informal "p/h" ("£21.50p/h") is glued directly onto the number with
    # no separating character, so there's no word-to-non-word transition for \b to anchor on
    # (both the trailing digit and "p" are word characters) — the same class of boundary issue
    # the "/hour" fix already had to work around, but unfixable the same way since there's no
    # non-word character anywhere in "0p" to anchor against. Still requires a real trailing \b.
    r"|p\s*/\s*h\b",
    re.IGNORECASE,
)

# _PERIOD_HINT minus the bare "annual(ly)"/"per annum" alternative, derived from it (not hand-
# copied — code review finding, PR #235: the two must not be able to drift apart) so a future
# marker added to one is added to both automatically. Used only to gate the bare "starting at $X"
# match (see _scan): an explicit /hour, /day, /mo, or /yr marker is a real signal this is a
# recurring RATE (wage-shaped), but "annual(ly)" alone isn't, since one-time relocation/tuition/
# stipend amounts are described that way just as often as real salaries are.
_STRONG_PERIOD_HINT = re.compile(
    _PERIOD_HINT.pattern.replace(r"per\s+annum|annually|annual|", ""), re.IGNORECASE
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


#: Added to an "after"-side period hint's distance when it looks like a new sentence starting
#: right where the number ends (see _distance) — large enough to always lose to any "before" hint
#: within the ~50-char window _period_from_window searches, while still letting the hint win if
#: it's the only candidate at all.
_NEW_SENTENCE_PENALTY = 1000


def _distance(hint_match: re.Match, rel_start: int, rel_end: int) -> int:
    """How far `hint_match` sits from the number's own span (`rel_start`-`rel_end`) — used by
    `_period_from_window` to prefer the period hint CLOSEST to the number, not just the first one
    found scanning the window left-to-right (real bug: "Shift: Day Salary Range: $44.00 -
    $57.00/hour" read "Day", the work-shift type, as the period because it sat earlier in the
    window than the genuine "/hour" right after the number). The period word can land on either
    side of the number ("hourly rate of $X" vs. "$X per year"), so distance is measured against
    whichever side it's actually on. An "after" hint that starts a new sentence right where the
    number ends usually isn't describing the number at all — real bug, found while verifying the
    fix above: "Competitive hourly rate: $25-35 Annual continuing education benefit..." read
    "Annual" (opening an unrelated new sentence about a DIFFERENT benefit) over the genuine
    "hourly" that already precedes the number. Sentence-initial capitalization (title case, not
    ALL-CAPS emphasis like "$22.50/HOUR!!") is the signal: heavily deprioritize it so a real
    "before" hint wins instead, but still fall back to it if it's the only candidate at all."""
    hint_text = hint_match.group(0)
    if hint_match.start() >= rel_end:
        dist = hint_match.start() - rel_end
        if hint_text[0].isupper() and not hint_text.isupper():
            dist += _NEW_SENTENCE_PENALTY
        return dist
    if hint_match.end() <= rel_start:
        return rel_start - hint_match.end()
    return (
        0  # overlaps the number itself — shouldn't happen, but don't crash if it does
    )


def _period_from_window(text: str, start: int, end: int) -> int:
    """Two checks, in order. First, pick the period hint closest to the number (see _distance).
    Second, if a comma with real prose words separates the number from whichever hint won, treat
    it as no hint at all — real bug found on greenhouse: "base salary of $90,000-$100,000, plus
    weekly and monthly bonus opportunities" wrongly read "monthly" as the SALARY's period (×12 ->
    $1.08M-$1.2M) when it describes the separate BONUS instead. Requiring BOTH a comma AND
    leftover letters (not just any words anywhere in the gap) matters: common, genuine phrasing
    like "Competitive hourly rate of 19-21 USD" (period word BEFORE the number, real words in
    between, no comma at all) must keep working — an earlier, broader "any letters" version of
    this guard broke ~150 genuine matches across workable+workday before being caught by a full
    corpus diff. A comma alone, or comma-separated digits/symbols, are still fine too — "$15.86 -
    $19.86, hourly." is a genuine trailing-descriptor shape (workday), and a bilingual restatement
    like "$17.60 - $25.90 / 17,60$ - 25,90$ (per hour / de l'heure)" has commas from
    European-format duplicate numbers but no real words, and "per hour" genuinely applies to the
    English figure too."""
    window_start = max(0, start - 20)
    window = text[window_start : end + 30]
    matches = list(_PERIOD_HINT.finditer(window))
    if not matches:
        return 1
    rel_start, rel_end = start - window_start, end - window_start
    m = min(matches, key=lambda hm: _distance(hm, rel_start, rel_end))
    # Checked AFTER finding the hint (not by pre-trimming the window) so the number's own trailing
    # digit stays available for _PERIOD_HINT's leading \b to anchor against (e.g. "0" before
    # "/hour").
    gap = (
        window[rel_end : m.start()]
        if m.start() >= rel_end
        else window[m.end() : rel_start]
    )
    if "," in gap and re.search(r"[a-zA-Z]", gap.replace(",", "")):
        return 1
    hint = m.group(0).lower()
    if "hr" in hint or "hour" in hint or "p/h" in hint.replace(" ", ""):
        return _HOURLY_TO_ANNUAL
    if "day" in hint or "daily" in hint:
        return _DAILY_TO_ANNUAL
    if "mo" in hint or "month" in hint:
        return 12
    return 1  # yr/year/annum/annual(ly) — already annual


def _span_from_match(
    text: str, m: re.Match, lo_raw: str, hi_raw: str | None
) -> SalarySpan | None:
    """Shared match -> SalarySpan conversion: false-positive guard, "k" shorthand, period
    multiplier, currency guess, plausibility bounds. Used by every Tier-2 scanner that turns one
    regex match into a figure (:func:`_scan`, :func:`_scan_level_bands`) — code review finding,
    PR #236: this shape was duplicated between them before being extracted here."""
    if _has_false_positive_context(text, m.start(), m.end()):
        return None
    if hi_raw is None and _states_a_ceiling_only(text, m.start("lo")):
        return None
    # "k" shorthand: the pattern already consumed an optional trailing k/K without capturing it
    # separately, so detect it from the matched text itself.
    matched = m.group(0)
    k_mult = 1000 if re.search(r"\d[kK]\b", matched) else 1
    mult = _period_from_window(text, m.start(), m.end()) * k_mult
    currency = _guess_currency(m.groupdict().get("sym"), matched)
    lo = _num(lo_raw) * mult
    hi = _num(hi_raw) * mult if hi_raw else None
    span = _bounded(min(lo, hi) if hi else lo, max(lo, hi) if hi else None, currency)
    if span is None:
        return None
    return SalarySpan(span.min_annual, span.max_annual, span.currency, "regex")


def _scan(text: str, pattern: re.Pattern) -> list[SalarySpan]:
    found: list[SalarySpan] = []
    for m in pattern.finditer(text):
        gd = m.groupdict()
        lo_raw, hi_raw = gd.get("lo"), gd.get("hi")
        if not lo_raw:
            continue
        if gd.get("bare_starting") and not _STRONG_PERIOD_HINT.search(
            text[max(0, m.start() - 20) : m.end() + 30]
        ):
            # A bare "starting at $X" with no salary/pay/wage word AND no strong rate marker
            # (hr/day/mo/yr) nearby has nothing confirming it's even a wage — real phrasing like
            # "relocation assistance starting at $15,000" or "tuition reimbursement starting at
            # $25,000 annually" reads as a fabricated salary otherwise (found via adversarial
            # testing, not observed in the sampled corpus — PR #235). Skip rather than default to
            # annual, per the no-fabrication principle.
            continue
        span = _span_from_match(text, m, lo_raw, hi_raw)
        if span is not None:
            found.append(span)
    return found


def _scan_lpa(text: str) -> list[SalarySpan]:
    found: list[SalarySpan] = []
    for m in _LPA.finditer(text):
        if _has_false_positive_context(text, m.start(), m.end()):
            continue
        gd = m.groupdict()
        if not gd.get("hi") and _states_a_ceiling_only(text, m.start("lo")):
            continue
        lo = round(float(gd["lo"]) * 100_000)
        hi = round(float(gd["hi"]) * 100_000) if gd.get("hi") else None
        span = _bounded(min(lo, hi) if hi else lo, max(lo, hi) if hi else None, "INR")
        if span is not None:
            found.append(SalarySpan(span.min_annual, span.max_annual, "INR", "regex"))
    return found


def _scan_level_bands(text: str) -> SalarySpan | None:
    """Envelope every "Level N: $X - $Y" band into one min-to-max span (see :data:`_LEVEL_BAND`)
    — deliberately NOT run through :func:`_resolve`, since several genuinely different numbers are
    the expected, correct shape here, not an ambiguity to reject."""
    spans: list[SalarySpan] = []
    for m in _LEVEL_BAND.finditer(text):
        span = _span_from_match(text, m, m.group("lo"), m.group("hi"))
        if span is not None:
            spans.append(span)
    if not spans:
        return None
    if len({s.currency for s in spans}) > 1:
        return None  # genuinely inconsistent currencies across bands — don't guess
    return SalarySpan(
        min(s.min_annual for s in spans),
        max(s.max_annual or s.min_annual for s in spans),
        spans[0].currency,
        "regex",
    )


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
    """Scan free text for a stated salary, trying patterns in confidence order: leveled
    compensation bands (several genuinely different numbers that are still one real, stated
    envelope — see :func:`_scan_level_bands`), LPA (a distinctive, unambiguous marker when
    present), an explicit "Salary:"/"Compensation:"-style label, a bare currency-symbol range, a
    bare number range anchored by a trailing currency code, an anchored "between $X and $Y"
    phrase, then — last, lowest-priority of all — a bare hourly/daily rate with no label at all
    ("$X/hour" or "$X per day" standing alone). "Between" runs before the fully bare hourly/daily
    pattern but after everything else, because it tends to describe a narrower sub-detail ("new
    hires usually start between $X and $Y") rather than the headline figure a labeled or bare
    range states ("expected range is $X to $Y") when a description states both — real example,
    greenhouse pass, PR #236. The bare hourly/daily pattern runs last of all since it requires no
    label whatsoever — any more specific tier that already matched is more likely to be the real
    figure. Multiple, mutually-inconsistent genuine matches within one tier are ambiguous and stop
    the cascade there (never fall through to a lower-confidence tier to paper over the conflict)
    — the same no-fabrication principle extended from estimation to disambiguation."""
    text = description or ""
    if not text:
        return None
    level_bands = _scan_level_bands(text)
    if level_bands is not None:
        return level_bands
    for pattern in (
        _scan_lpa(text),
        *(
            _scan(text, p)
            for p in (
                _LABELED,
                _BARE_RANGE,
                _BARE_RANGE_CODE,
                _BARE_RANGE_CODE_EACH,
                _BARE_BETWEEN,
                _BARE_HOURLY_OR_DAILY,
            )
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
