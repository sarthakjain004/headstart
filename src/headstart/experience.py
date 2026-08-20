"""Extract a Job's required years of experience to a numeric range (enrichment).

A tiered cascade returning the first hit and which tier produced it (ADR-0009, ADR-0018). A concrete
number always wins; the seniority label is only a fallback when no number is stated:

  1. ``from_field``       — a structured field ("5+", "3 - 5 Years"), when a source provides one.
  2. ``from_description`` — experience-anchored regex over the free-text description.
  3. ``from_seniority``   — map a seniority label (the field, e.g. recruitee "entry_level", else the
                            title, e.g. "Senior Engineer") to a floor-years estimate. Fallback only.

Each tier is a pure function returning an :class:`ExperienceSpan` or ``None``. Widen recall by
adding to ``_tier2_patterns`` (the factory feeding both Tier-2 passes) or ``_SENIORITY``; a future
LLM tier is another ``from_*`` chained in :func:`extract`. Keeping each tier pure keeps the whole
thing unit-testable without I/O.

Five things about Tier 2 are load-bearing and easy to undo by accident (ADR-0060, ADR-0066,
ADR-0076):

* **The smallest stated requirement wins**, so :func:`_scan` collects every surviving match and
  selects; it must not return the first one it finds. A description stating several is read at its
  most permissive, because `search` filters `min_years <= your_years` and the alternatives are as
  often a cheaper *path* to the same job ("12+ years, or 10+ with a PhD") as an extra demand.
* **Ranges are tried before single values**, because a single-value pattern will otherwise match at
  a range's ceiling and report it as the floor ("2-4 years" served as 4+).
* **Every pattern carries its own guard flag.** A pattern that cannot fire without the literal word
  "experience" nearby is unguarded; every other pattern is guarded, because without the guards
  company age and founder tenure read as requirements ("spent the last 15 years building …"). The
  flag travels with the pattern rather than being recovered from its text — the old sniff for
  ``_WORK`` reported False for any pattern built from something else.
* **Text is folded to ASCII punctuation before matching**, so patterns downstream of
  :func:`from_description` may assume it and need not carry the typographic variants.
* **Spelled-out numbers run as a second pass**, so a description a digit pattern already answers
  keeps exactly the answer it had.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

_MAX_PLAUSIBLE_YEARS = (
    50  # reject absurd matches ("100 years"), almost always a parse error
)

# A stated *requirement* above this is never real — it is corporate narrative ("a combined 40+ years
# at Palantir building …"). Deliberately far below _MAX_PLAUSIBLE_YEARS, which guards arithmetic
# absurdity rather than genre.
#
# Applied to **every** pattern, not only the guarded ones. ADR-0060 restricted it on the grounds
# that "25 years of experience" is "a real, if rare, requirement" where a pattern is anchored on
# the word. Measured over the description store, that is not so: 1,066 descriptions receive a
# Tier-2 answer above 20 years and a hand-read of the top 30 found **no real requirement among
# them** — "PayPal has been revolutionizing commerce for more than 25 years", "federal contractor
# with more than 30 years of experience", "the founding team brings over 30 years", and two that
# are ages rather than tenures at all ("a 21 year old UMich grad", "Age Limit: Below 26 Years").
# The anchor word says nothing about genre; the magnitude does.
_MAX_PLAUSIBLE_REQUIREMENT = 20

# The smallest ceiling `_DIGITS`' third digit made reachable, hence the boundary ADR-0072 draws:
# below it ADR-0013's ceiling rule stands (drop an absurd `hi`, keep the real floor).
_SMALLEST_THREE_DIGIT_YEARS = 100


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
#
# The leading qualifier is optional because a handful of boards type the bound into the field
# rather than selecting it: ">3 years", ">2yrs", "Minimum 3 years". It is a floor either way, which
# is what `min_years` already means, so the prefix is consumed rather than interpreted. Only `>`
# and `min`/`minimum` are accepted — they are the forms the corpus actually contains.
_FIELD = re.compile(
    r"^\s*(?:min(?:imum)?\.?\s*)?>?\s*(\d{1,3})\s*"
    r"(?:\+|(?:to|-|\u2013|\u2014)\s*(\d{1,3}))?",
    re.IGNORECASE,
)


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
# Typographic punctuation, folded to its ASCII twin before matching. **Every mapping is one
# character to one character**, so `str.translate` preserves offsets exactly and the narrative
# guards — which slice `text` around `match.start()` — keep pointing at what they did before.
#
# Measured over the 328,930-description store — `present` counts descriptions containing the
# character, `decisive` counts those whose Tier-2 answer changes or disappears without the mapping:
#
#     U+2011 non-breaking hyphen  present 17,252   decisive   262
#     U+2013 en dash              present108,751   decisive 15,882
#     U+2019 right single quote   present231,810   decisive  5,953
#     U+2014 em dash              present 97,008   decisive    19
#     U+201C/D double quotes      present ~33,000  decisive     7 each
#     U+2018 left single quote    present  9,226   decisive     3
#     U+F0B7 Word bullet          present    551   decisive     2
#     U+2212 minus sign           present     47   decisive     1
#     U+2012 figure dash          present     11   decisive     0
#     U+2015 horizontal bar       present     53   decisive     0
#     U+30FB katakana dot         present  1,052   decisive     0
#
# The three zero-scoring entries are kept: each is the same character class as one that does pay
# (a dash, a bullet), costs nothing at run time, and would otherwise be a silent gap the next
# corpus could fall into. Folding is preferred over widening each character class because these
# characters are *noise* in a requirement, not signal — the alternative is threading twelve code
# points through `_GAP`, `_WORDS`, `_WORK` and `_RANGE_TAIL` and re-deriving the risk in each.
_FOLD = str.maketrans(
    {
        "\u2011": "-",  # non-breaking hyphen — by far the most common blocker
        "\u2012": "-",  # figure dash
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2015": "-",  # horizontal bar
        "\u2212": "-",  # minus sign
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\uf0b7": "\u2022",  # Word's Wingdings bullet, pasted straight out of a .docx
        "\u30fb": "\u2022",  # katakana middle dot, used as a bullet on JP boards
    }
)


# `_GAP` is 45, which reaches the "N+ years <noun phrase> experience" class the corpus is full of
# ("3+ years of production-grade C++ and/or Rust experience" — 37 characters, answering nothing at
# 30). It sat at 30 only while `_scan` answered with the leftmost match: a wider gap then also
# decided *which* requirement a multi-requirement description reported, measured at 2,690 jobs,
# mean +5.7 years. `_scan` now answers with the smallest stated floor regardless of position
# (ADR-0076), so the width buys recall and nothing else.
# `'` and `"` are here as the *targets* of `_FOLD`, which turns the curly forms into them before
# any of this runs; `·` and `•` are the bullet characters boards actually emit. The curly forms
# themselves are deliberately absent — folding means they can never reach a Tier-2 pattern.
_GAP = (
    r"[\w\s.'\":/()&,·•+-]{0,45}?"  # what may sit between the number and "experience"
)
_YEARS = (
    r"(?:years?|yrs?)"  # "yrs" is common enough in the corpus to be worth accepting
)

# Number words, because a requirement is as often written out as digitised: "A minimum of four
# years of relevant experience", "Minimum five years of experience designing software", "Two years
# of civil engineering experience". 5,911 descriptions in the store state their requirement this
# way and matched nothing at all before. Capped at twelve — beyond that a requirement is written in
# digits in every example read, and each extra word widens what the work-word patterns can reach.
#
# Ranges get it too ("four to seven years", "Three to six years"), which is why the number group is
# substituted into both slots rather than only the first.
#
# **Run as a second pass, not folded into the first.** Two reasons, and they point the same way.
# Correctness: these *patterns* cannot change a digit answer, because they never run when one
# exists. (One shared piece does reach the digit pass — `_RANGE_TAIL` learns spelled-out floors, so
# "three to 5 years" reads 3-5 where it read 5. That is the ceiling-as-floor fix applied to one
# more spelling, and it is why `_RANGE_TAIL` needs its leading `\b`.) Cost: the alternation
# defeats the literal-prefix scan `re` uses on
# `\d{1,2}`, and paying that on every description rather than only the ~38% that miss measured 6.5x
# slower end to end (0.31s -> 2.02s per 3,000 descriptions).
_WORD_NUM = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_DIGITS = r"(\d{1,3})"
# Hand-factored rather than `"|".join(_WORD_NUM)`: `re` does not build a trie out of an alternation,
# so sharing each first letter across its branches is what keeps the second pass affordable
# (measured 0.75s -> 0.47s per 3,000 descriptions on the pattern this appears in, when the
# digit branch was `\d{1,2}`; re-measured at `\d{1,3}` the pass is 1.193s -> 1.201s, unchanged).
_DIGITS_OR_WORDS = r"(\d{1,3}|t(?:hree|welve|wo|en)|f(?:our|ive)|s(?:ix|even)|e(?:ight|leven)|nine|one)"


def _years_from_token(token: str) -> int:
    """A matched number token as an int, whether it arrived as digits ("5") or a word ("five")."""
    word = _WORD_NUM.get(token.lower())
    return word if word is not None else int(token)


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
_WORDS = r"(?:[\w'/&.-]+[\s,]+){0,4}?"  # filler between the connector and the work word


class _Tier2Pattern(NamedTuple):
    """A Tier-2 pattern and whether it needs the narrative guards.

    `guarded` is False only for a pattern that cannot fire unless the literal word "experience" is
    nearby, which is what makes it unable to reach corporate narrative in the first place.
    """

    regex: re.Pattern[str]
    guarded: bool


def _tier2_patterns(num: str) -> list[_Tier2Pattern]:
    """The Tier-2 pattern set, over whichever number group is passed in.

    Built by a factory so the digits-only pass and the digits-or-words pass cannot drift apart:
    a phrasing added here is added to both, and the **ranges-before-single-values** ordering that
    stops a single-value pattern binding to a range's ceiling is stated once.

    Each entry pairs the pattern with whether it needs the narrative guards. A pattern that
    requires the literal word "experience" cannot reach company history and is left unguarded;
    every pattern that matches without it can, and is guarded. The flag is carried here rather
    than recovered afterwards by looking for `_WORK` inside `pattern.pattern` — that sniff was
    true only while the work-word patterns were the sole unguarded-context ones, and silently
    reported False for any new pattern built from something other than `_WORK`.
    """
    return [
        # number-first range then "experience": "7 to 12 years of experience", "3-5 years' experience"
        _Tier2Pattern(
            re.compile(
                num
                + r"\s*(?:to|-|or)\s*"
                + num
                + r"\s*\+?\s*"
                + _YEARS
                + _GAP
                + "experience",
                re.IGNORECASE,
            ),
            guarded=False,
        ),
        # "experience" then a range (reversed): "Experience: 8 – 12 Years"
        _Tier2Pattern(
            re.compile(
                "experience"
                + _GAP
                + num
                + r"\s*(?:to|-)\s*"
                + num
                + r"\s*\+?\s*"
                + _YEARS,
                re.IGNORECASE,
            ),
            guarded=False,
        ),
        # "7+ years of proven experience", "5 plus years … experience", "minimum 3 years of experience"
        _Tier2Pattern(
            re.compile(
                num + r"\s*(?:\+|plus)?\s*" + _YEARS + _GAP + "experience",
                re.IGNORECASE,
            ),
            guarded=False,
        ),
        # reversed single: "experience of 5+ years", "Experience: 5 years"
        _Tier2Pattern(
            re.compile(
                "experience" + _GAP + num + r"\s*(?:\+|plus)?\s*" + _YEARS,
                re.IGNORECASE,
            ),
            guarded=False,
        ),
        # "5+ years in software testing", "7 years of professional engineering", "4+ years building …"
        _Tier2Pattern(
            re.compile(
                num + r"\s*(?:\+|plus)?\s*" + _YEARS + _CONN + _WORDS + _WORK,
                re.IGNORECASE,
            ),
            guarded=True,
        ),
        # "5+ years in <anything>" — the same shape as the pattern above with the work vocabulary
        # dropped. `_WORK` can only ever enumerate the domains someone thought of, and the misses
        # are a long tail no list closes: "3+ years in product marketing", "7+ years in hardware
        # quality", "5+ years in system and network administration". The literal "in" is what
        # replaces the vocabulary as the anchor — it is the connector requirement prose uses and
        # company history does not ("In just two years, we achieved …" has no "years in").
        _Tier2Pattern(
            re.compile(
                num + r"\s*(?:\+|plus)?\s*" + _YEARS + r"\s+in\s+[a-z]",
                re.IGNORECASE,
            ),
            guarded=True,
        ),
        # "5+ years shipping production C++", "4+ years specializing in Flutter". `_WORK` already
        # carries the common verbs (build/design/develop/lead/manage/test), so a bare gerund is
        # what is left: shipping, deploying, crafting, administering, enabling, conducting.
        _Tier2Pattern(
            re.compile(
                num
                + r"\s*(?:\+|plus)?\s*"
                + _YEARS
                + r"\s+(?:of\s+)?[a-z]+ing\b(?=\s+\w)",
                re.IGNORECASE,
            ),
            guarded=True,
        ),
        # A trailing parenthetical, which is how a requirement stated as prose gets its number:
        # "In-depth knowledge of PHP (3+ years)", "Proven experience in C++ … (3+ years)",
        # "Microsoft 365 administration and migration activities (3-5 years)". The number sits
        # after the thing it qualifies, so no forward-looking pattern reaches it.
        _Tier2Pattern(
            re.compile(
                r"\((?:typically\s+|approx\.?\s+|around\s+|min\.?\s+|minimum\s+(?:of\s+)?)?"
                + num
                + r"\s*(?:\+|(?:-|to)\s*"
                + num
                + r")?\s*\+?\s*"
                + _YEARS
                + r"\b[^)]{0,30}\)",
                re.IGNORECASE,
            ),
            guarded=True,
        ),
    ]


_DESC_PATTERNS = _tier2_patterns(_DIGITS)
#: Second pass, tried only when :data:`_DESC_PATTERNS` finds nothing (see `_WORD_NUM`).
_NUM_WORD_PATTERNS = _tier2_patterns(_DIGITS_OR_WORDS)

# Company age, founder tenure, benefits: "N years" that is never a requirement. These read as
# requirements to a work-word pattern ("spent the last 15 years building …") and were previously
# excluded only as a side effect of demanding an of/in/as connector.
_NARRATIVE_BEFORE = re.compile(
    r"\b(?:spent|combined|celebrat\w*|founded|established|history|anniversar\w*|"
    r"vest\w*|sabbatical|tenure|runway)\b[\w\s,'-]{0,25}$",
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
# The leading `\b` is load-bearing: `_DIGITS_OR_WORDS` spells numbers out, and without a boundary
# any word *ending* in one supplies a floor — "GET THE JOB DONE - 5+ years" read 1-5 off "d-ONE",
# "Everyone - 6+ years" read 1-6, "on the phone - 8+ years" read 1-8.
_RANGE_TAIL = re.compile(
    r"\b" + _DIGITS_OR_WORDS + r"\s*(?:-|~|to|or|and)\s*$", re.IGNORECASE
)


# Fixed idioms in which "N years" is never a requirement, however the surrounding sentence reads:
# an award streak ("on the Cloud 100 for four years in a row"), an equity schedule ("competitive
# equity (4 year vest)"), a graduation window ("within ~1 year of graduating"). Unlike
# `_NARRATIVE_BEFORE`, which keys on a word appearing *before* the number, these are recognisable
# only from what follows it — and they are checked for **every** pattern, guarded or not, because
# the idiom is what makes the number not a requirement, not which pattern happened to find it.
#
# Deliberately only these three. "N years running" and "N years in business" were tried and
# reverted: measured against the served table they cost 4 and 6 real requirements respectively
# ("4+ years running distributed systems at scale", "3+ years in business development") to buy
# roughly two narrative rejections each. An idiom earns a place here only if it is unambiguous —
# a phrase that is *usually* narrative is a net loss, because the requirement reading is the one
# a candidate is filtering on.
# Applied with `.match()` from the start of the number the pattern captured, never `.search()` over
# a window: searching re-anchors on whatever "years" comes first in the window, which lets an idiom
# qualifying a *different* number disqualify this one — "5+ years of experience. Equity (4 year
# vest)" lost its 5 to the vest schedule two sentences away. Anchoring is what ties the idiom to
# the match. `row(?![\w-])` because `\b` is satisfied by the hyphen in "a row-level security team".
_NARRATIVE_SPAN = re.compile(
    r"\S{1,8}(?:\s*(?:to|-|or)\s*\S{1,8})?\s*\+?\s*(?:years?|yrs?)\b"
    r"[\s\w'()-]{0,18}?\b(?:in\s+a\s+row(?![\w-])|vest\w*|of\s+graduat\w*)\b",
    re.IGNORECASE,
)


# "up to N years" states a *ceiling*, so reading it as `min_years` inverts the posting — a job open
# to "candidates with up to 3 years of experience" was being served as requiring 3, hiding it from
# the juniors it addresses. The faithful reading is a floor of 0 with N as the top, which is what
# `_scan` now records (ADR-0076); withdrawing the number instead served the posting as stating no
# requirement at all, and left the scan hunting for a later occurrence — on one row, the company
# boilerplate "more than 50 years of experience". Checked for every pattern, guarded or not,
# because the inversion does not depend on which pattern found the number: the example above fires
# an experience-anchored one.
#
# It must sit *immediately* before the number, not merely nearby: a 25-character window turns
# "Bonus up to 20 percent and 6+ years in backend systems" and "up to date knowledge and 5+ years
# building services" into rejections of a real requirement.
_CEILING_BEFORE = re.compile(r"\bup\s+to\s*$", re.IGNORECASE)


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
    text = text.translate(_FOLD)
    return _scan(text, _DESC_PATTERNS) or _scan(text, _NUM_WORD_PATTERNS)


def _scan(text: str, patterns: list[_Tier2Pattern]) -> ExperienceSpan | None:
    """One pass of the Tier-2 patterns over already-folded text, answered by its smallest floor.

    Every match that survives the guards is collected and the **smallest** `min_years` among them
    wins (ADR-0076), rather than whichever the leftmost pattern reached first. Its own `max_years`
    travels with it: a floor from one sentence paired with a ceiling from another describes nothing
    anybody wrote. Selecting rather than returning early is what lets `_GAP` be as wide as recall
    wants, since position no longer decides the answer.
    """
    spans: list[ExperienceSpan] = []
    for pattern, guarded in patterns:
        # Every occurrence, so a rejected match falls through to the next one — "Founded 12 years
        # ago. Requires 5+ years building …" still yields 5 rather than nothing. Resumed from just
        # past the matched *number* rather than from the match's end, because `finditer`'s
        # non-overlapping walk hides a smaller requirement sitting inside a longer match: "10 years
        # (Master's degree with 6 years) related experience" offers only the 10, and "Age Range:
        # 28-35 years 5-8 years' experience" offers nothing at all, the real requirement swallowed
        # by an age the guards then reject. Past the number, not one character into it, or `\d{1,3}`
        # matches "05" out of "105" and re-opens the truncation ADR-0013 closed.
        pos = 0
        while (match := pattern.search(text, pos)) is not None:
            pos = match.start(1) + len(match.group(1))
            lo = _years_from_token(match.group(1))
            hi = (
                _years_from_token(match.group(2))
                if match.lastindex and match.lastindex >= 2 and match.group(2)
                else None
            )
            if _NARRATIVE_SPAN.match(text[match.start(1) : match.end() + 20]):
                continue
            if lo > _MAX_PLAUSIBLE_REQUIREMENT:
                continue
            if guarded and _is_narrative(text, match):
                continue
            if _CEILING_BEFORE.search(
                text[max(0, match.start(1) - 10) : match.start(1)]
            ):
                # "up to N years": the number is the top of the range, and the posting states no
                # floor at all. Guarded first, so "up to 25 years" is still refused as narrative.
                spans.append(ExperienceSpan(0, hi if hi is not None else lo, "regex"))
                continue
            if hi is None:
                # Recover the floor when this match is a range's ceiling ("2-4 years" -> 2, not 4).
                tail = _RANGE_TAIL.search(
                    text[max(0, match.start(1) - 12) : match.start(1)]
                )
                floor = _years_from_token(tail.group(1)) if tail else None
                if floor is not None and floor < lo:
                    lo, hi = floor, lo
            if lo > _MAX_PLAUSIBLE_YEARS:
                continue
            if hi is not None and hi >= _SMALLEST_THREE_DIGIT_YEARS:
                # A 3-digit ceiling condemns the span, floor included (ADR-0072): the rule below
                # would drop `hi` and keep a floor the sentence never offered as a requirement.
                # Below 100, ADR-0013's rule stands — "3 to 99 years" is still 3.
                continue
            if hi is not None and (hi < lo or hi > _MAX_PLAUSIBLE_YEARS):
                hi = None
            spans.append(ExperienceSpan(lo, hi, "regex"))
    return min(spans, key=lambda span: span.min_years, default=None)


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
            r"\b(senior\w*|mid[\s-]?senior|\bsr\b|experienced|executive)\b",
            re.IGNORECASE,
        ),
        5,
    ),
    # The one manager class that carries a floor. Calibrated the ADR-0018 way over
    # `data/jobs/tech` (75,166 tech jobs): of the 1,094 titles holding "engineering manager", the
    # 753 that also state a number have a median `min_years` of **5** — and the same run reproduces
    # every existing mapping exactly (senior 5, lead/staff 7, director 10), so this label is worth
    # a 5, not the 7 its rung on the ladder suggests. It sits in the 5 block for that reason.
    # Deliberately narrower than "<tech discipline> manager": adding platform/infrastructure/data/
    # technical reached 3 more titles corpus-wide, every one of them ops or facilities ("IT
    # Infrastructure Manager", "Facilities Technical Manager-Muskogee, OK") — the no-reliable-floor
    # class this pattern exists to exclude, along with the "Program Manager Non Tech" / "Project
    # Manager" / "Business Development Manager" bulk of the 1,003 uncovered manager titles (#189).
    # "Software development manager" was tried too and dropped: its own 19-sample median is 8, not
    # 5 — a different, unmeasured class this pattern must not silently fold in at the wrong value.
    (
        re.compile(r"\bengineering\s+manager\b", re.IGNORECASE),
        5,
    ),
    (
        re.compile(
            r"\b(associate|mid[\s_-]?level|intermediate|medior|middle(?![\s-]*east))\b",
            re.IGNORECASE,
        ),
        3,
    ),
    (
        re.compile(
            r"\b(intern|internship|trainee|graduate|\bgrad\b|student\w*|entry[\s_-]?level|junior|\bjr\b|apprentice|fresher|early[\s-]?career)\b",
            re.IGNORECASE,
        ),
        0,
    ),
]


# Numeric / roman level suffixes on the title ("Software Engineer 1", "Data Scientist III", "SDE II")
# also encode seniority: I/1 = entry, II/2 = mid, III/3 = senior, IV/V = staff.
#
# The ladder is as often written with an `L`/`IC` prefix or the word "Level" — "DEVELOPER L3",
# "TEST ENGINEER L4", "Security Managed Services Engineer (L1)", "Operating Engineer Level 1".
# Measured over the served table, those spellings sit on 1,274 titles the cascade covers no other
# way. They get the SAME mapping as the bare numeral deliberately: this is one spelling of the
# ordinal `_LEVEL_YEARS` already trusts, not a new claim about what a level means. Ladders do
# disagree on where L3 sits, but that disagreement applies identically to "Developer 3" and is
# therefore an argument about `_LEVEL_YEARS`, not about which spellings reach it.
_LEVEL = re.compile(
    r"\b(?:engineer|developer|programmer|analyst|scientist|architect|sde|swe)\s*"
    r"(?:\(\s*)?(?:l|ic|level\s*)?(iii|ii|iv|i|v|[1-5])\b",
    re.IGNORECASE,
)
# "Level 1 Support Engineer" states the same ordinal before the role noun, so `_LEVEL` cannot see
# it. Kept separate and spelled out in full — a bare "L1" anywhere in a title is too easy to
# collide with a product or grade code, whereas the word "Level" is unambiguous.
_LEVEL_WORD = re.compile(r"\blevel\s*([1-5])\b", re.IGNORECASE)
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
    match = _LEVEL.search(title or "") or _LEVEL_WORD.search(title or "")
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
