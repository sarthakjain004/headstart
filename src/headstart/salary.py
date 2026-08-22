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
    "PLN": 3_000_000,
    "CHF": 900_000,  # same raw figure as CAD/AUD, but CHF trades stronger than either — genuinely
    # more generous in real terms, a deliberate choice given Swiss finance/pharma pay, not a
    # same-tier match (code review finding, PR #241: the original comment implied equivalence)
    "AED": 3_000_000,  # scaled from the USD ceiling via ~3.67 AED/USD, not independently sourced
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
    "PLN": 30_000,  # ~52% of 2026 full-time minimum wage annualized (4,806/mo x 12 = 57,672)
    "CHF": 20_000,  # below every 2026 cantonal minimum-wage floor (Geneva's highest, ~51,000/yr)
    "AED": 30_000,  # keka pass, 2026-08-22: real observed AED figures (91 raw numbers, 11
    # companies) are all monthly-scale (topping at 25,000) — keka's own period-omitted payload
    # means these can't be annualized, so this floor is set to comfortably reject them as
    # implausible-if-treated-as-annual (the correct, safe outcome) while still admitting a
    # genuinely low but real annual figure, not independently sourced from a UAE minimum wage.
}
_HOURLY_TO_ANNUAL = 2080  # 40hr/wk * 52wk, the standard full-time-equivalent convention
_DAILY_TO_ANNUAL = 260  # 5 days/wk * 52wk
_WEEKLY_TO_ANNUAL = 52


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
# teamtailor, ashby, personio, keka, darwinbox each get a calibrated `_field_*` parser below) — but
# not always: an ATS with no dedicated parser falls through to `_field_generic`, and any such ATS
# may pass an HR system's own raw free-text field straight into `Job.salary` with zero scraper-side
# normalization (corrected claim, code review, PR #238 — ashby and personio were both once believed
# to be exactly this, until each pass's own direct API inspection found a structured field one
# level deeper instead; check freshly for every new ATS rather than assuming either way), so
# `_field_generic` has to treat its input as organic free text too, not assume it's one of our own
# formats.

# Shared alternation for the ISO codes this module recognizes — several independent regexes
# below embed it; kept as one string (code review finding, PR #235) so they can't drift apart.
# PLN and CHF joined on recruitee's pass (PR #241): real, multi-company evidence (19 and 7
# distinct companies respectively in a 3,000-board sample) of amounts that were already clearing
# _bounded's USD-shaped fallback with currency=None — adding them resolves the currency field
# rather than changing which values pass, and calibrates a currency-appropriate bound instead of
# relying on the fallback. Bounds sourced from 2026 minimum-wage data (see _MIN_PLAUSIBLE_ANNUAL).
# AED joined on keka's pass (2026-08-22): 11 distinct companies, same resolves-the-currency-field
# reasoning — real observed AED figures are all monthly-scale, so this doesn't change which values
# pass today (keka's own period-omitted payload means they're correctly rejected either way), but
# gives any genuinely-annual AED figure (here or on a future ATS) a properly-calibrated bound
# instead of the coarser USD-shaped fallback.
_CURRENCY_CODES = "USD|EUR|GBP|INR|CAD|AUD|HKD|SEK|PLN|CHF|AED"

_CURRENCY_CODE = re.compile(rf"\b({_CURRENCY_CODES})\b", re.IGNORECASE)
_RANGE = re.compile(r"(\d(?:[\d,]*\d)?(?:\.\d+)?)\s*[-–]\s*(\d(?:[\d,]*\d)?(?:\.\d+)?)")
_SINGLE_NUM = re.compile(r"(\d(?:[\d,]*\d)?(?:\.\d+)?)")


def _num(s: str) -> int:
    """Parse a captured number in either US (comma=thousands, period=decimal: "50,000.00",
    "50,000") or European (period=thousands, comma=decimal: "50.000,00", "50.000", "14,00")
    convention. Found on personio's pass (2026-08-22): real German-formatted salary text was
    silently mis-read two different ways by the old always-strip-commas, period-is-decimal
    assumption. "49.000" (forty-nine THOUSAND) was read as 49 — a safe-looking undercount that
    happened to fail the plausibility floor here, but not guaranteed to in general. "14,00 EUR"
    (fourteen euros, decimal-comma) was read as 1400 — a genuine, dangerous *overestimate* that
    can clear the plausibility bounds and silently corrupt a real value, exactly the failure
    class this module's no-fabrication principle exists to prevent, not just an undercount.

    Disambiguated the same way for both separators, on real evidence: a trailing group of
    exactly 2 digits is a decimal fraction (currency amounts overwhelmingly carry 0 or 2 decimal
    places in every corpus sampled so far, never 3); a trailing group of exactly 3 digits is a
    thousands-separator (grouping is always in 3s, in both conventions). When both separators
    appear in the same number, the LAST one is the true decimal marker and the other is
    thousands-grouping, regardless of which character each one is.

    Only ever the LAST occurrence of a separator is treated as a possible decimal point — a real
    posting typo (greenhouse pass, 2026-08-22: "$100,000.00 - $125,000,00", comma fat-fingered in
    place of the period before the cents) repeats the same separator character right up to the
    decimal group ("125,000,00"). Blindly converting every comma to a period would leave TWO
    periods in the string and crash `float()`; `rpartition` isolates the last group and strips
    every earlier occurrence outright, regardless of how many there are."""
    if "," in s and "." in s:
        if s.rindex(",") > s.rindex("."):
            s = s.replace(".", "").replace(",", ".")  # European: 1.234.567,89
        else:
            s = s.replace(",", "")  # US: 1,234,567.89
        return round(float(s))
    if "," in s:
        head, _, tail = s.rpartition(",")
        if len(tail) == 2:
            return round(
                float(head.replace(",", "") + "." + tail)
            )  # European decimal: 14,00
        return round(float(s.replace(",", "")))  # US thousands: 50,000
    if "." in s and re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        return round(float(s.replace(".", "")))  # European thousands: 49.000
    # Defensive mirror of the comma fix above: strip every period but the last before falling
    # through to a bare decimal read, in case the same typo pattern repeats with periods instead
    # of commas (not observed yet, but the failure mode — an uncaught ValueError — is cheap to
    # close off given it already happened once with the other separator).
    return round(float(s.replace(".", "", max(0, s.count(".") - 1))))


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
# shape, and it's live-reachable, not theoretical: any ATS with no dedicated `_field_*` parser
# passes an HR system's raw free-text field straight into `Job.salary` with no scraper-side
# normalization (unlike lever/recruitee/teamtailor/ashby/personio/keka/darwinbox, each of which
# has its own calibrated `_field_*` parser for a shape *we* format), so "Up to €50,000" from such
# an ATS would hit this exact bug too.
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
    """Phrase-shaped period markers only ("per hour", "/hr", "monthly", ...) — safe for ANY
    Tier-1 field value, including one that's genuinely free text an HR system supplied verbatim
    (darwinbox, or any ATS with no dedicated `_field_*` parser that falls to `_field_generic` —
    see the Tier-1 section comment above). Used directly by
    `_field_generic` and `_field_darwinbox`; see :func:`_period_multiplier_structured` for the
    narrower, bare-word-recognizing variant safe only for known-structured field shapes."""
    low = text.lower()
    if any(p in low for p in ("per-hour", "per hour", "/hr", "hourly")):
        return _HOURLY_TO_ANNUAL
    if any(p in low for p in ("per-month", "per month", "/mo", "monthly", "mensual")):
        return 12
    return 1  # annual is the default for every known Tier-1 format


def _period_multiplier_structured(text: str) -> int:
    """:func:`_period_multiplier`, plus BARE unit-word recognition ("HOUR", "DAY", "MONTH" with
    no "per"/slash) for a field value KNOWN to be a short, machine-assembled
    "NUMBER-NUMBER CURRENCY UNIT" string with no other prose — real teamtailor field, confirmed
    on live data: the schema.org unitText this scraper's own _salary() passes through is exactly
    that shape ("15-17.5 GBP HOUR", "1500-1800 EUR MONTH", "120-130 GBP DAY"), and none of
    `_period_multiplier`'s phrase-shaped checks match a bare "HOUR", so every hourly/monthly/daily
    teamtailor figure was silently defaulting to the annual multiplier and then
    correctly-but-wrongly getting rejected by the plausibility bounds.

    Deliberately NOT the default `_period_multiplier` behavior, and used only by
    `_field_range_currency_interval` (whose five callers — lever.py, recruitee.py, teamtailor.py,
    ashby.py, personio.py — all assemble their string from a structured min/max/currency/interval
    quad, confirmed by reading each scraper's own formatter, never free text): a bare word is NOT
    safe against genuine free text, where it can match an unrelated mention instead of the
    salary's own period. Real, demonstrated regression caught by code review before merge
    (PR #239): applying bare-word matching to `_field_generic` (a genuinely free-text field, at
    the time reached via ashby and personio — both later moved OFF `_field_generic` once their own
    compensation data turned out to be structured, PR #240 and PR #243 respectively) silently
    misread "40,000 - 50,000 USD with 1 month severance included" as MONTHLY, 12x-inflating a
    correct annual figure into a wrong one that still happened to clear the plausibility bounds —
    a silent corruption, not a safe decline. `_field_darwinbox`'s `salary_timeframe` is equally
    unvalidated free text from Darwinbox's own API (confirmed: darwinbox.py never enumerates its
    possible values), so it stays on the safe `_period_multiplier` too.

    `week` joined the recognized bare words on ashby's own pass (PR #240): real, structured
    `interval` values include "1 WEEK" (a contractor-style weekly rate, confirmed on live data —
    "796 USD 1 WEEK", "2500-3500 USD 1 WEEK" — both annualize to plausible figures at ×52, 50 real
    occurrences measured before adding)."""
    mult = _period_multiplier(text)
    if mult != 1:
        return mult
    low = text.lower()
    if re.search(r"\bhour\b", low):
        return _HOURLY_TO_ANNUAL
    if re.search(r"\bday\b", low):
        return _DAILY_TO_ANNUAL
    if re.search(r"\bweek\b", low):
        return _WEEKLY_TO_ANNUAL
    if re.search(r"\bmonth\b", low):
        return 12
    return 1


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


def _field_range_currency_interval(value: str) -> SalarySpan | None:
    """lever: "50000-70000 USD per-year-salary" | recruitee: "50000-70000 EUR per year" |
    teamtailor: "40000-60000 EUR YEAR" | ashby: "80000-100000 USD 1 YEAR" (assembled by
    ashby.py's own `_salary()` from the structured Salary-typed `compensationTiers[].components[]`
    entry) | personio: "48000.00 EUR yearly" (assembled by personio.py's own `_salary()` from the
    structured `<salaryInformation><min>/<max>/<currencyCode>/<type>` element — `_text()`'s prior
    read of the element's own direct text was always empty for this shape, a real Tier-1 dead end
    fixed the same way ashby's own was) — the "fix ambiguity at the source" latitude the
    salary-extraction plan already grants, not organic text, for both. | rippling:
    "62000-70000 USD YEAR" / "25-25 USD HOUR" (assembled by rippling.py's own `_pay_range()` from
    the structured `payRangeDetails[0]` entry; no fix needed here — the raw format already matched
    this parser's shape end-to-end, confirmed by testing before registering, not assumed). All
    converge on RANGE + CODE + optional period. Named for the shape, not each ATS that happens to
    produce it (renamed from `_field_lever_recruitee_teamtailor` when ashby joined — see
    CLAUDE.md's "re-check the name whenever what it does changes").

    A bare SINGLE value with no range ("60000 USD 1 YEAR", "35 USD 1 HOUR") falls back to
    `_SINGLE_NUM` — real on ashby's structured data specifically (a fixed-rate tier with only one
    of minValue/maxValue set, not a range; 24 confirmed real cases, zero on teamtailor's own
    corpus when checked, so this was a genuine gap in the shared parser, not a latent bug already
    shipped to an already-merged ATS)."""
    code_m = _CURRENCY_CODE.search(value)
    currency = code_m.group(1).upper() if code_m else None
    mult = _period_multiplier_structured(value)
    m = _RANGE.search(value)
    if m:
        lo, hi = _num(m.group(1)) * mult, _num(m.group(2)) * mult
        return _bounded(min(lo, hi), max(lo, hi), currency)
    single = _SINGLE_NUM.search(value)
    if single:
        v = _num(single.group(1)) * mult
        return _bounded(v, None, currency)
    return None


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
    "lever": _field_range_currency_interval,
    "recruitee": _field_range_currency_interval,
    "teamtailor": _field_range_currency_interval,
    "ashby": _field_range_currency_interval,
    "personio": _field_range_currency_interval,
    "rippling": _field_range_currency_interval,
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
        # Real free-text ATS fields reach this branch (any ATS with no dedicated `_field_*`
        # parser — see the Tier-1 section comment above), so the same ceiling-vs-floor risk
        # Tier 2 has applies here too: "Up to €50,000" would otherwise misreport €50,000 as a
        # floor (code review, PR #238).
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
#   - deal-size, not compensation: "ACV von EUR 80-150k" (personio: cybus-gmbh) — Annual Contract
#     Value, a SaaS sales metric describing what a customer pays, same false-positive class as the
#     existing revenue/valuation/funding guards above. Checked against the full 9-ATS corpus (582
#     real "ACV" occurrences, personio pass, 2026-08-22): every sampled context is the same sales
#     metric, no unrelated-word collision found — unlike "provision" below.
#
# A German "Provision" (commission) guard was tried in this same pass and reverted: it collided
# with the ordinary English word "provision" (a clause/stipulation) — "Pay Transparency Provision"
# is common US pay-disclosure boilerplate that sits right next to a genuine, correctly-formatted
# salary range (greenhouse: boxinc, 26 real postings wrongly suppressed by this alone). Re-checked
# against the real German evidence too: the one genuinely-bad case that motivated the guard
# ("300 – 450 € Provision pro erfolgreichem Abschluss", a per-deal commission amount) is already
# rejected by the plausibility floor alone (300-450 read as an annual figure is far too low) — the
# guard was net-harmful, not just redundant, since "Provision" mentioned near a range doesn't
# reliably mean the range itself is commission (real counter-examples: Autohaus Royal's "Fixum und
# ungedeckelter Provision (70.000-150.000 EUR Jahresbrutto)" and feld.energy's "Fixgehalt ...
# 50.000-60.000 EUR jährlich zzgl. Provision" both state a real, correctly-labeled base/total
# salary despite "Provision" appearing nearby).
_FALSE_POSITIVE_CONTEXT = re.compile(
    r"\b("
    r"revenue|valuation|series\s+[a-e]\b|funding|raised|arr\b|acv\b|"
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
#
# The optional leading-code groups (before `lo` and before `hi`) close a real, general gap found
# on keka's pass (2026-08-22): a currency CODE immediately before the number ("Salary: AED
# 30,000-35,000", "Monthly salary: AED 12,000 to AED 20,000") was never supported — only a
# leading SYMBOL ("$X") or a trailing CODE ("X USD") were. Not AED-specific: "Salary: USD
# 70,000-90,000" (an already-registered code) failed identically before this fix, confirmed by
# direct testing — AED's pass just supplied the first real evidence, since AED is conventionally
# written code-first far more often than the codes already in `_CURRENCY_CODES`. No new named
# group needed: the leading code becomes part of the overall match, which `_guess_currency`
# already scans for a code via `_CURRENCY_CODE.search()` on the full matched text.
#
# "stipend" joined the label alternation on the same keka pass: 13 distinct companies, always the
# PRIMARY stated compensation for an internship/trainee posting ("Stipend: 15000 INR per month"),
# not a side benefit alongside a separately-stated salary — the value is real, disclosed pay, just
# for a different role type, and this initiative extracts what a role actually pays rather than
# filtering by employment classification. The connector also gained a bare hyphen ("Stipend-
# 15000") alongside the existing optional colon, evidenced by the same real examples — safe
# because a number is still required immediately after, so "pay-per-view"-shaped text with no
# following digit still can't match.
#
# The "L" suffix (lakh, x100,000 — see _span_from_match) is the same keka pass's second numeric-
# shorthand addition, evidenced separately from "stipend": 5 companies, all label-anchored
# ("Compensation: ₹30L to ₹50L", "Salary : INR 3.0L to 4.5L", "CTC - 7L-8L"). Requires a trailing
# \b (unlike "k", which has none) so it can't partially swallow "Lakhs"/"Location"/any other L-word
# — "30 Lakhs" stops the optional group before the "a", leaving "akhs" unconsumed rather than
# misreading the "L" as this shorthand. Deliberately NOT added to any bare/unguarded pattern: a
# label is what makes "L" safe here the same way it makes "k" safe, and the broader "lacs"/"lakhs"
# word (checked the same pass) is dominated by unrelated insurance-coverage mentions ("group
# medical insurance of 3 lakhs") that only a label anchor keeps out.
#
# "ctc" (Cost To Company — India's standard term for total annual compensation) joined the same
# pass, separately evidenced: 74 occurrences, 33 distinct companies, 13 not already extracted via
# some other label ("CTC: ₹20,000 per month", "CTC :20000 to 25000 Per month"). Checked for the
# same acronym-collision risk as AED/401(k): real corpus text has "CTC" naming an unrelated
# business unit ("investigate... Freight charges for CTC Business units") — safe here because
# _LABELED's own connector is narrow (`[:\-]?` plus a small lead-in-word set) and demands a digit
# immediately after, so a bare mention with no adjacent figure never reaches the number groups at
# all; confirmed directly against that exact real text, not just reasoned about.
_LABELED = re.compile(
    r"""
    (?:annual\s+)?
    (?:
        (?:salary|compensation|pay(?:ing)?|remuneration|base\s+salary|wage|stipend|ctc)\s*(?:range|rate)?
        (?:\s+for\s+\w+(?:\s+\w+){0,2})?\s*[:\-]?\s*
        (?:upto|up\s+to|of\s+up\s+to|is\s+up\s+to|of|is|from|starting(?:\s+(?:salary|at|rate))?)?
        | (?P<bare_starting>starting\s+at)  # bare "starting at $X" — no salary/pay/wage word;
                                            # named so _scan can demand a period hint nearby (see
                                            # its call site) rather than default-annual-guessing,
                                            # since nothing else here confirms this is even a wage
    )\s*
    (?P<sym>[$£€₹])?\s*
    (?:(?:@CODES@)\b\s*)?
    (?P<lo>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK]|[lL]\b)?
    (?:\s*(?:@CODES@)\b)?
    (?:\s*[-–—to]{1,3}\s*(?P<sym2>[$£€₹])?\s*(?:(?:@CODES@)\b\s*)?
       (?P<hi>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK]|[lL]\b)?
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

# A bare number range with a currency SYMBOL trailing the range once — "50.000 - 56.000 €"
# (personio pass, 2026-08-22: 32 real occurrences, German-market postings that state no symbol
# until the very end). A version of this ("51882€", a bare number-then-symbol shape) was tried
# and reverted during workday's own pass (PR #235) for two reasons: `_num()` misread the European
# thousands-separator convention ("37.500,00" as 37.5, not 37500) — now fixed, see `_num()`'s own
# docstring — and it collided with workday's own site URLs, which embed a literal "$" as a path
# delimiter ("/inst/1$9925/9925$27033.html"). That second risk is sidestepped here by construction
# rather than by a URL-detection guard: scoped to €/£ only, deliberately excluding $ — real
# personio evidence shows 32/32 real trailing-symbol ranges use €, zero use $, so a real, common
# European convention is covered at zero cost to the workday collision. See
# docs/salary-extraction/workday.md's known-gaps section for the original finding.
_BARE_RANGE_SYMBOL = re.compile(
    r"(?P<lo>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?"
    r"\s*[-–—]\s*"
    r"(?P<hi>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?"
    r"\s*(?P<sym>[€£])",
    re.IGNORECASE,
)

# Same idea, but the symbol trails EACH side rather than the range as a whole — real personio
# text: "23.000 € – 27.000 €", "13€ - 15€/h" (2 distinct companies; the single-trailing-symbol
# shape above only captures the last one when both sides repeat the symbol, since its own `lo`
# would otherwise swallow the first symbol as stray text between the numbers).
_BARE_RANGE_SYMBOL_EACH = re.compile(
    r"(?P<lo>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?\s*(?P<sym>[€£])"
    r"\s*[-–—]\s*"
    r"(?P<hi>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?\s*[€£]",
    re.IGNORECASE,
)

# "minimum annual salary of $X, a midpoint of $Y, and a maximum salary of $Z" / "Minimum $X -
# Maximum $Y" — a real, explicit min/max compensation-band disclosure (ashby pass: 23 real
# occurrences, jobber + xero — a genuine range stated across two labeled endpoints, not two
# competing figures). Checked BEFORE _LABELED, same reason _scan_level_bands/_LPA are: _LABELED's
# own "salary...of $X" shape independently matches "minimum annual salary of $169,200" AND
# "maximum salary of $228,900" as two SEPARATE spans (169,200 vs 228,900 fail the 5% consistency
# check), so without running first, this band gets fragmented into a false ambiguity and declined
# before ever reaching here. A broader "minimum ... maximum" shape was also found on a third
# company (scribdinc, 16 jobs) but turned out to be a different pattern on closer reading — a
# "between $X [bracketed geographic aside] to $Y" range, not a minimum/maximum-labeled pair at
# all — left as a known gap (see docs/salary-extraction/ashby.md) rather than widening this
# pattern to a shape it wasn't built or verified for.
_MIN_MAX_BAND = re.compile(
    r"\bminimum\b.{0,40}?(?P<sym>[$£€₹])\s*(?P<lo>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?"
    r".{0,150}?\bmaximum\b.{0,40}?(?P<sym2>[$£€₹])?\s*(?P<hi>\d(?:[\d,]*\d)?(?:\.\d+)?)\s*(?:[kK])?",
    re.IGNORECASE | re.DOTALL,
)


def _scan_min_max_band(text: str) -> SalarySpan | None:
    m = _MIN_MAX_BAND.search(text)
    if not m:
        return None
    return _span_from_match(text, m, m.group("lo"), m.group("hi"))


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
    r"\b(?:per\s+hour|hourly|\bhr\b|\ban\s+hour\b|pro\s+stunde|"
    r"per\s+month|monthly|\bmo\b|pro\s+monat|"
    r"per\s+year|per\s+annum|annually|annual|\ba\s+year\b|\byr\b|pro\s+jahr|"
    r"per\s+day|daily|\bday\b)"
    # The slash-prefixed alternatives (/hr, /hour, /mo, /month, /yr, /year, /day) get NO leading
    # \b, on purpose, for two distinct reasons found on two different real corpora (teamtailor,
    # PR #239): "£40 /hour" (a SPACE before the slash, real, common phrasing — "Get paid between
    # £20 and £40 /hour") has no word-to-non-word transition for \b to anchor on, since a space
    # and a slash are both non-word characters; "£21.50p/h" (GLUED directly onto the number, zero
    # separator) has the same issue from the opposite direction, since a digit and "p" are both
    # word characters. Between them, a number and a slash-marker can be glued, space-separated, or
    # (this pattern already handled) directly adjacent — \b can only anchor reliably in the last
    # case, so it's dropped for all three rather than chasing each spacing variant as its own fix.
    # Still requires a real trailing \b so e.g. "/hours" (plural) isn't swept in by accident.
    r"|/\s*hr\b|/\s*hour\b|/\s*mo\b|/\s*month\b|/\s*yr\b|/\s*year\b|/\s*day\b"
    r"|p\s*/\s*h\b"
    # German markers (personio pass, 2026-08-22): real range-shape misses trailed a currency
    # symbol/code but stated the period auf Deutsch ("50.000 - 56.000 € / Jahr", "pro Stunde",
    # "/Std.") — 48 occurrences across 19 distinct companies, not a one-off. Bare "jahr"/"monat"/
    # "stunde" alone are deliberately NOT accepted (mirroring bare "hour"/"month" above): unguarded,
    # "Jahr" collides with ubiquitous, unrelated "X Jahre Erfahrung" (years of experience) phrasing.
    r"|/\s*std\b|/\s*stunde\b|/\s*monat\b|/\s*jahr\b",
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
    # German "jahr" (year) must be checked before the "hr" substring test below — "jahr" ends in
    # "hr" and would otherwise misclassify a German annual marker as hourly.
    if "jahr" in hint:
        return 1
    if (
        "hr" in hint
        or "hour" in hint
        or "stunde" in hint
        or "std" in hint
        or "p/h" in hint.replace(" ", "")
    ):
        return _HOURLY_TO_ANNUAL
    if "day" in hint or "daily" in hint:
        return _DAILY_TO_ANNUAL
    if "mo" in hint or "month" in hint:
        return 12
    return 1  # yr/year/annum/annual(ly)/jahr — already annual


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
    matched = m.group(0)
    # "401k"/"401(k)" is a US retirement-plan NAME, not a $401,000 figure — real, found on keka's
    # pass (2026-08-22): the label+hyphen-connector fix (above, in _LABELED's own definition) makes
    # "Equity compensation - 401K program" match _LABELED ("compensation" + "-" + "401" + the
    # pre-existing "k" shorthand), misreading the plan name as $401,000. _has_false_positive_context
    # can't catch this: "401k" is the matched NUMBER itself, not context text before/after the
    # match. Checked specifically (not a general exclusion of 401 as a number) since no other
    # common number+k US workplace term collides the same way.
    if re.search(r"401\s*\(?k\)?\b", matched, re.IGNORECASE):
        return None
    # "k"/"L" shorthand: the pattern already consumed an optional trailing k/K/L without capturing
    # it separately, so detect it from the matched text itself. Only _LABELED's own regex ever
    # consumes an "L" here (see its definition) — this stays effectively k-only for every other
    # scanner, since none of them capture a trailing L as part of their own match.
    magnitude_mult = (
        1000
        if re.search(r"\d[kK]\b", matched)
        else 100_000
        if re.search(r"\d[lL]\b", matched)
        else 1
    )
    mult = _period_from_window(text, m.start(), m.end()) * magnitude_mult
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
    """One match wins outright; several mutually-consistent ones agree, so the more informative
    one stands (a currency-bearing span over a currency-less one, if both are present — see
    :func:`_mutually_consistent`); several that disagree are ambiguous (:data:`_AMBIGUOUS`); none
    means this tier found nothing, try the next. Shared by every Tier-2 pattern so the "don't
    guess when ambiguous" rule can't drift between them."""
    if not spans:
        return None
    if len(spans) == 1:
        return spans[0]
    if _mutually_consistent(spans):
        return next((s for s in spans if s.currency), spans[0])
    return _AMBIGUOUS


def from_description(
    description: str | None, ats: str | None = None
) -> SalarySpan | None:
    """Scan free text for a stated salary, trying patterns in confidence order: leveled
    compensation bands (several genuinely different numbers that are still one real, stated
    envelope — see :func:`_scan_level_bands`), an explicit "minimum $X ... maximum $Y" band
    (:func:`_scan_min_max_band` — also checked before `_LABELED`, for the same reason: `_LABELED`
    would otherwise independently match "minimum...salary of $X" and "maximum salary of $Y" as
    two separate, mutually-inconsistent spans and decline the whole thing as ambiguous), LPA (a
    distinctive, unambiguous marker when present), an explicit "Salary:"/"Compensation:"-style
    label, a bare currency-symbol range, a
    bare number range anchored by a trailing currency code or symbol, an anchored "between $X and
    $Y" phrase, then — last, lowest-priority of all — a bare hourly/daily rate with no label at all
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
    min_max_band = _scan_min_max_band(text)
    if min_max_band is not None:
        return min_max_band
    for pattern in (
        _scan_lpa(text),
        *(
            _scan(text, p)
            for p in (
                _LABELED,
                _BARE_RANGE,
                _BARE_RANGE_CODE,
                _BARE_RANGE_CODE_EACH,
                _BARE_RANGE_SYMBOL,
                _BARE_RANGE_SYMBOL_EACH,
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
    """Whether every span in ``spans`` roughly agrees (same currency where both state one,
    overlapping-ish range) — duplicate mentions of the same figure, not two different genuine
    numbers. A ``None`` currency means "couldn't tell from THIS mention", not "no currency" — it
    doesn't contradict a sibling span that DID find one, so it's excluded from the currency check
    rather than treated as a distinct, clashing value (real, measured bug: the same real wage
    stated twice, once with a symbol and once without — "Compensation: $25.96 / hour" and, later
    in the same description, "Salary: 25.96/hour" — was flagged ambiguous solely because one span
    resolved a currency and the other didn't, found via teamtailor PR #239's cross-ATS diff; 24
    confirmed real cases across the corpus, already latent in already-merged ATSes before this
    pass, not introduced by it)."""
    first = spans[0]
    for s in spans[1:]:
        if s.currency and first.currency and s.currency != first.currency:
            return False
        if abs(s.min_annual - first.min_annual) > max(first.min_annual * 0.05, 1000):
            return False
    return True
