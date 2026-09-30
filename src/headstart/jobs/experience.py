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

Six things about Tier 2 are load-bearing and easy to undo by accident (ADR-0060, ADR-0066,
ADR-0079, ADR-0350):

* **The smallest stated requirement wins**, so :func:`_scan` collects every surviving match and
  selects; it must not return the first one it finds. A description stating several is read at its
  most permissive, because the Search filter keeps `min_years <= your_years` and the alternatives are as
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
* **Recall widenings run as a third pass**, on the same terms: "five (5) years", a filler gap of 46-80
  characters, and "expertise"/"exp" for "experience" are read only where the first two passes found
  nothing, so no answer Tier 2 already gave can move (ADR-0066, ADR-0350).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

# The largest bound a stated range can carry: a structured field's floor and every ceiling, in both
# tiers (#697). Measured on the served metadata of 2026-09-28 (556,206 rows): the field floors
# above 20 were RippleHire's "21 - 30 Years" band, a real one, and "35 years" on a Zoho
# web-developer posting, which is not; the ceilings above 30 were bands like "8-45 years" and
# "12 - 50 Years", and regex spans like "10-40 years", whose floors are real and whose tops are not.
# A field is a band a recruiter picked, so it gets more room than Tier 2's narrative-prone floor
# below. It was 50 until #697, a guard against arithmetic nonsense ("100 years") only.
_MAX_PLAUSIBLE_YEARS = 30

# A stated *requirement* above this is never real — it is corporate narrative ("a combined 40+ years
# at Palantir building …"). A description's floor only; a field's floor and every ceiling face
# _MAX_PLAUSIBLE_YEARS instead.
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
#
# A few keka/zoho/darwinbox fields state months ("6 Months", "1 - 6 Months", "6 Months - 2 years"),
# so a "months" unit is captured after either number. A unitless floor takes the ceiling's unit
# ("1 - 6 Months"); a floor with its own non-month unit never reaches the ceiling at all.
_FIELD = re.compile(
    r"^\s*(?:min(?:imum)?\.?\s*)?>?\s*(?P<lo>\d{1,3})\s*(?P<lo_mo>months?\b)?\s*"
    r"(?:\+\s*(?P<plus_mo>months?\b)?"
    r"|(?:to|-|\u2013|\u2014)\s*(?P<hi>\d{1,3})\s*(?P<hi_mo>months?\b)?)?",
    re.IGNORECASE,
)


def from_field(value: str | None) -> ExperienceSpan | None:
    """Tier 1 — parse a source's structured experience field. Deterministic, no description needed.

    Months become whole years the way the filter reads them: the floor rounds down (`min_years
    <= N` — six months is open to someone with 0 whole years), the ceiling up (eighteen months is
    not "up to 1 year"), so the whole-year span always contains the stated one."""
    if not value:
        return None
    match = _FIELD.match(value)
    if not match:
        return None
    lo = int(match.group("lo"))
    hi = int(match.group("hi")) if match.group("hi") else None
    if match.group("lo_mo") or match.group("plus_mo") or match.group("hi_mo"):
        lo //= 12
    if hi is not None and match.group("hi_mo"):
        hi = -(-hi // 12)
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
# (ADR-0079), so the width buys recall and nothing else.
# `'` and `"` are here as the *targets* of `_FOLD`, which turns the curly forms into them before
# any of this runs; `·` and `•` are the bullet characters boards actually emit. The curly forms
# themselves are deliberately absent — folding means they can never reach a Tier-2 pattern.
_GAP = (
    r"[\w\s.'\":/()&,·•+#-]{0,45}?"  # what may sit between the number and "experience"
)
_YEARS = (
    r"(?:years?|yrs?)"  # "yrs" is common enough in the corpus to be worth accepting
)
#: What the third pass accepts for the word the Tier-2 patterns anchor on: "5+ years of expertise in Java",
#: "5+ yrs exp". `exp\b` so "expert" and "expense" do not anchor a requirement.
_EXPERIENCE_WIDE = r"(?:experience|expertise|exp\b)"
# A gap of 46-80 characters, the third pass's reach to "2+ years of server hardware troubleshooting and repair
# experience". No full stop at all, so the gap cannot run into the next sentence (and is cheaper than a lookahead per
# character); a description whose gap holds "(M.S.)" or "e.g." is left to the first two passes.
_GAP_LONG = r"[\w\s'\":/()&,·•+#-]{46,80}?"
# Eighty characters also reach benefits and company prose: "5 years of paid parental leave benefits, flexible
# schedules, and an amazing employee experience", "20 years and we take pride in our people and culture, and our
# experience". A match whose words are first person, pay, leave, tenure or culture, or whose anchor is HR's
# "employee experience" / "candidate experience", is not a requirement; nor are years of education, schooling or
# training, which stand beside experience ("1-2 years of education or training in a security field, or equivalent
# work experience", "4 years of total combined higher education and related work experience"), nor a company's
# "For over 15 years, ArcTouch has created ...".
_GAP_PROSE = re.compile(
    r"\b(?:we|our|ours|benefits?|leave|paid|pay|salary|compensation|perks?|vacation|insurance|bonus|tenure|history|"
    r"founded|since|culture|pride|proud)\b|\b(?:employee|candidate)s?\s+\w+$"
    r"|\bhigher\s+education\b|\b(?:education|schooling|training|apprenticeship)\s+(?:and|or)\b"
    r"|(?-i:\b(?:years?|yrs?),\s+[A-Z]\w+\s+(?:has|have|is|are|was|were|had)\b)",
    re.IGNORECASE,
)
# "more than 15 years of expertise" is a company's own, never a requirement: nobody asks for 15 years of expertise
# and not of experience. Read on the third pass's "expertise" anchor only ("Exp: 15+ Years" is a requirement).
_MAX_EXPERTISE_YEARS = 15
# A number the posting marks as preferred is not a requirement, and a false floor hides the job from someone who
# qualifies. The third pass leaves it unread ("Preferred Qualifications - 2+ years of ...", "5+ years heavy industrial
# experience preferred"); the first two passes keep reading it as they always did (ADR-0066).
_PREFERRED_BEFORE = re.compile(
    r"\b(?:preferred|preferable|preferably|desired|desirable|nice\s+to\s+have|bonus|ideally|even\s+better)\b"
    r"[^.;]{0,25}$",
    re.IGNORECASE,
)
_PREFERRED_WITHIN = re.compile(
    r"\b(?:preferred|preferable|preferably|desired|desirable)\b", re.IGNORECASE
)
_PREFERRED_AFTER = re.compile(
    r"^\W{0,3}(?:(?:is|are)\s+)?(?:preferred|desired|desirable|a\s+plus|an?\s+asset|nice\s+to\s+have)\b",
    re.IGNORECASE,
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
# A number is a whole token. It is not the tail of a longer one (the "000" of "$160,000 yearly depending on
# experience" and of "7.000 year training budget" read as 0 years) and not the fraction of a decimal (the 5 of
# "0.5 years"): `_scan` skips a match with a digit, or a digit and a point or comma, right before it. The fraction is
# consumed, so group 1 is the whole years and the floor rounds down the way `from_field` does for months: "2.5 years"
# is 2, "1.5 - 3.5 years" is 1-4 (`_scan` rounds a ceiling up). The boundary is checked after the match rather than by a
# lookbehind in front of the number, which defeats the literal-prefix scan `re` uses on `\d` (+1.0 s per 3,000
# descriptions, measured; the check after the match costs nothing).
_DIGITS = r"(\d{1,3})(?:[.,]\d{1,2}(?!\d))?"
# Hand-factored rather than `"|".join(_WORD_NUM)`: `re` does not build a trie out of an alternation,
# so sharing each first letter across its branches is what keeps the second pass affordable
# (measured 0.75s -> 0.47s per 3,000 descriptions on the pattern this appears in, when the
# digit branch was `\d{1,2}`; re-measured at `\d{1,3}` the pass is 1.193s -> 1.201s, unchanged).
_DIGITS_OR_WORDS = r"(\d{1,3}|t(?:hree|welve|wo|en)|f(?:our|ive)|s(?:ix|even)|e(?:ight|leven)|nine|one)(?:[.,]\d{1,2}(?!\d))?"


# `_scan` reads a range's ceiling off the digits the pattern captured, so it needs the fraction the pattern consumed.
_FRACTION = re.compile(r"[.,](\d{1,2})(?!\d)")
# A mixed fraction is another tail: the 2 of "additional 3 1/2 years (42 mos.) exp" is half a year, not two. Only with
# the space ("3 1/2"): "5/7 years" is a range, read as main reads it.
_NOT_A_WHOLE_NUMBER = re.compile(r"\d$|\d[.,]$|\d\s\d/$")


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
# `#`/`+` (not just `+`, already needed for "C++") so "C#" doesn't break a filler word it sits
# inside of — "3+ years with C# .Net Software Development" read as None because "C#" couldn't
# match `[\w'/&.-]+` as one skippable token, stranding "Software" out of reach. Live-confirmed on
# a Zoho posting stating exactly that. Measured full-corpus, old vs new, per ADR-0066's own
# discipline (bucket every record old-tier/value -> new-tier/value, not just coverage): zero
# regressions, 39 new answers, 60 corrected ones (the same bug was silently pushing them to a
# generic seniority-tier guess) — see docs/experience-extraction/
# 2026-09-16_symbol-gap-full-corpus-measurement.md.
_WORDS = (
    r"(?:[\w'/&.#+-]+[\s,]+){0,4}?"  # filler between the connector and the work word
)


class _Tier2Pattern(NamedTuple):
    """A Tier-2 pattern and whether it needs the narrative guards.

    `guarded` is False only for a pattern that cannot fire unless the literal word "experience" is
    nearby, which is what makes it unable to reach corporate narrative in the first place.
    """

    regex: re.Pattern[str]
    guarded: bool


def _tier2_patterns(num: str, third: bool = False) -> list[_Tier2Pattern]:
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

    `third` builds the recall widenings of the third pass (`_THIRD_PATTERNS`): "expertise" and "exp" for the
    anchor word, reversed only for "experience" and "exp" ("expertise of 20 years" is how a company describes
    itself), the forward pattern with a 46-80 character gap, and every pattern guarded, since the wider anchor
    can reach prose the literal word "experience" could not.
    """
    experience = _EXPERIENCE_WIDE if third else "experience"
    reversed_experience = r"(?:experience|exp\b)" if third else "experience"
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
                + experience,
                re.IGNORECASE,
            ),
            guarded=third,
        ),
        # "experience" then a range (reversed): "Experience: 8 – 12 Years"
        _Tier2Pattern(
            re.compile(
                reversed_experience
                + _GAP
                + num
                + r"\s*(?:to|-)\s*"
                + num
                + r"\s*\+?\s*"
                + _YEARS,
                re.IGNORECASE,
            ),
            guarded=third,
        ),
        # "7+ years of proven experience", "5 plus years … experience", "minimum 3 years of experience"
        _Tier2Pattern(
            re.compile(
                num + r"\s*(?:\+|plus)?\s*" + _YEARS + _GAP + experience,
                re.IGNORECASE,
            ),
            guarded=third,
        ),
        # reversed single: "experience of 5+ years", "Experience: 5 years"
        _Tier2Pattern(
            re.compile(
                reversed_experience + _GAP + num + r"\s*(?:\+|plus)?\s*" + _YEARS,
                re.IGNORECASE,
            ),
            guarded=third,
        ),
        *(
            [
                # "2+ years of hardware troubleshooting and repair experience" with a gap of 46-80 characters.
                # Forward only (the reversed pattern reads "experience … we've spent 10 years"), guarded, and
                # screened for benefits and company prose, since eighty characters can span it.
                _Tier2Pattern(
                    re.compile(
                        num + r"\s*(?:\+|plus)?\s*" + _YEARS + _GAP_LONG + experience,
                        re.IGNORECASE,
                    ),
                    guarded=True,
                )
            ]
            if third
            else []
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
#: Third pass, tried only when both passes above find nothing (see `from_description`). Digits only: the spelled
#: numbers with their digits are collapsed to digits first, and the alternation of number words costs +1.1 s per
#: 3,000 descriptions where the digit patterns cost +0.3.
_THIRD_PATTERNS = _tier2_patterns(_DIGITS, third=True)

# Company age, founder tenure, benefits: "N years" that is never a requirement. These read as
# requirements to a work-word pattern ("spent the last 15 years building …") and were previously
# excluded only as a side effect of demanding an of/in/as connector.
_NARRATIVE_BEFORE = re.compile(
    r"\b(?:spent|combined|celebrat\w*|founded|established|history|anniversar\w*|"
    r"vest\w*|sabbatical|tenure|runway|(?:re)?paid\s+over|spread\s+over)\b[\w\s,'-]{0,25}$",
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
#
# Three company-history idioms joined them (ADR-0337), each allowed no filler, because a filler
# reaches requirement prose ("3+ years in a role where we …"). "For over 30 years, we have
# helped …": a comma and a lower-case "we" or "our", since a description flattened to one line
# loses its full stops and "Experience Level: 8+ years We are seeking" is a requirement. "25+ years
# of history" with nothing between "of" and "history", since "8+ years of work history" and
# "3 years of driving history" are requirements. "25 years of growth," where "growth" ends the
# phrase, since "3+ years of growth marketing" is a requirement.
_NARRATIVE_SPAN = re.compile(
    r"\S{1,8}(?:\s*(?:to|-|or)\s*\S{1,8})?\s*\+?\s*(?:years?|yrs?)\b(?:"
    r"[\s\w'()-]{0,18}?\b(?:in\s+a\s+row(?![\w-])|vest\w*|of\s+graduat\w*)\b"
    r"|(?-i:,\s+(?:we|our)\b)"
    r"|\s+of\s+(?:history|heritage)\b"
    r"|\s+of\s+(?:\w+\s+)?growth\b(?=\s*[,.;&]|\s+and\b)"
    # An education, an age, a contract length: "4 year degree", "2 years of post-secondary study", "18 years of age",
    # "(1 year contract)". Singular "year" before degree/diploma ("3 Years Diploma" and "5+ years Degree in X" are a
    # requirement followed by the next list item); "or above" is not here ("3 years or above" is a floor).
    r"|(?<![sS])\s+(?:(?:technical|engineering|bachelor'?s?|associate'?s?|master'?s?|accredited|college|university)[\s/-]+){0,2}"
    r"(?:degree|diploma)\b"
    # Only an education noun that ends the phrase: "2 years of college education in a technical discipline", not
    # "2 years of college-level Java programming" or "3+ years of study design and data analysis experience".
    r"|(?:(?<![sS])\s+|\s+of\s+(?:[a-z-]+\s+){0,2}?)(?:college|university|undergraduate|post-?secondary)(?:[\s-]+level)?\b"
    r"(?=\s*(?:education|coursework|course\s*work|study|studies|programs?|degrees?|credits?|classes|courses?|training"
    r"|schooling)\b|\s*(?:or|and|in)\b|\s*[,.;:)]|(?:\s+[a-z]+){1,2}\s+(?:program|degree|coursework)\b)"
    r"|\s+of\s+(?:schooling|studies|study)\s*(?:[,.;:)]|(?:in|at|or|and|beyond|after|program)\b)"
    r"|\s+(?:of\s+age\b|old\b|(?:or|and)\s+older\b)"
    r"|(?<![sS])\s+(?:fixed[\s-]term\s+)?(?:contract|term)\b"
    r"(?:\s*[),.;:(]|\s+-\s|\s*$|\s+(?:basis|position|role|renewable|extension|appointment|duration|hybrid|with|"
    r"starting|beginning|from|opportunity|only|and|maternity|cover))"
    r"|\s+(?:fixed[\s-]term\s+)?(?:contract|term)\s*\))",
    re.IGNORECASE,
)

# The number is the years that stand in for a degree, not what the job asks: "4 years of additional experience may be
# substituted for a bachelor's degree", "(Additional 4 years of experience may substitute degree)". Read from the number
# to the verb with no comma, bracket, bullet, "or" or "and" between them, so a clause about another number ("5+ years,
# additional years may be considered in lieu of a degree") does not reach this one. A description flattened to one line
# has no full stops, so the stretch must also hold no degree word and at most one "experience" (`_substitutes`):
# "Master's Degree with 3 years of related experience Equivalent experience can be substituted for the degree" states
# the 3 as an alternative path, and the clause belongs to the second "experience".
_SUBST_AFTER = re.compile(
    r"(?:(?!\b(?:or|and)\b)[^.;,•·(]){0,100}?\b(?P<verb>may|can|could|will)\s+(?:also\s+)?(?:"
    r"be\s+(?:substituted|used|accepted|considered)\s+(?:for|in\s+lieu\s+of|instead\s+of)|substitute(?:\s+for)?)\s+"
    r"(?:a|an|the|your)?\s*(?:bachelor|master|degree|associate|high\s+school|college|university|education|B\.?S\b|M\.?S\b)",
    re.IGNORECASE,
)
_SUBST_LEAD_OTHER = re.compile(
    r"\b(?:degree|master'?s?|bachelor'?s?|ph\.?\s?d|doctorate|advanced)\b",
    re.IGNORECASE,
)
_EXPERIENCE_WORD = re.compile(r"\bexperience", re.IGNORECASE)


def _substitutes(text: str, pos: int) -> bool:
    """Whether the number ending at ``pos`` is the years that may stand in for a degree."""
    found = _SUBST_AFTER.match(text, pos)
    if found is None:
        return False
    lead = text[pos : found.start("verb")]
    return (
        not _SUBST_LEAD_OTHER.search(lead) and len(_EXPERIENCE_WORD.findall(lead)) <= 1
    )


# What a degree stands for, read by the third pass only: "a master's degree can be substituted for two years of
# experience", "an associate degree is equivalent to two (2) years", "in lieu of a degree, an additional four years".
# Its numbers are a degree's worth of years, and the third pass has no other answer to protect (the guard is not
# run in the first two passes, where an ambiguous "N years" beside a substitution is at least as often the requirement).
_SUBST_BEFORE = re.compile(
    r"(?:\b(?:degree|master'?s?|ph\.?\s?d|doctorate|bachelor'?s?|education|diploma|certification|training|coursework)\b"
    r"[^.;]{0,60}?\b(?:substitut\w*|credit\w*|counts?|equals?|equivalent\s+to|in\s+lieu\s+of)\s+(?:for|as|to)?\s*"
    r"(?:(?:up\s+to|an?\s+additional|additional)\s+|~\s*)*"
    r"|\bin\s+lieu\s+of\s+(?:a|an|the|your)?\s*(?:[\w'.-]+\s+){0,3}?(?:degree|bachelor'?s?|master'?s?|diploma|education)\b"
    r"[^.;]{0,20}?(?:an?\s+)?(?:additional\s+)?)$",
    re.IGNORECASE,
)

# "in the last 10 years", "over the past 15 years": a window of time, never a requirement ("our
# product offering has grown a lot in the last 10 years", Monzo's intern posting, read 10+).
# Checked immediately before the number, for every pattern, like `_CEILING_BEFORE`. The
# preposition is what separates it from requirement prose that uses the same words: "Minimum of
# the past 2 years working with M365" states a requirement, and keeps it.
#
# Also "3 out of the past 5 years", "within last 3 years" without the "the", and a window that is a range ("in the
# last 1-2 years": the match is the range's ceiling). "for the past 5 years" is a window only after a residency or
# record cue ("resident in the UK for the past 5 years"); elsewhere it is a requirement ("Experience with Python for
# the last 3 years is required", "Proven track record for the past 4 years in backend development"); the same
# cue makes "lived in the UK for 10 years" a window.
_WINDOW_BEFORE = re.compile(
    r"\b(?:\d+\s+(?:out\s+)?of|in|over|during|within|throughout)\s+(?:the\s+)?(?:last|past|previous|preceding)\s*"
    r"(?:\d{1,3}\s*(?:-|to)\s*)?$",
    re.IGNORECASE,
)
_RESIDENCY_WINDOW_BEFORE = re.compile(
    r"\b(?:resident|resided|resides?|residing|lived|living|citizen\w*|domicile\w*|violations?|convictions?)\b"
    r"[^.;]{0,60}\bfor\s+(?:the\s+)?(?:(?:last|past|previous|preceding)\s*)?(?:\d{1,3}\s*(?:-|to)\s*)?$",
    re.IGNORECASE,
)


# "up to N years" states a *ceiling*, so reading it as `min_years` inverts the posting — a job open
# to "candidates with up to 3 years of experience" was being served as requiring 3, hiding it from
# the juniors it addresses. The faithful reading is a floor of 0 with N as the top, which is what
# `_scan` now records (ADR-0079); withdrawing the number instead served the posting as stating no
# requirement at all, and left the scan hunting for a later occurrence — on one row, the company
# boilerplate "more than 50 years of experience". Checked for every pattern, guarded or not,
# because the inversion does not depend on which pattern found the number: the example above fires
# an experience-anchored one.
#
# It must sit *immediately* before the number, not merely nearby: a 25-character window turns
# "Bonus up to 20 percent and 6+ years in backend systems" and "up to date knowledge and 5+ years
# building services" into rejections of a real requirement.
_CEILING_BEFORE = re.compile(r"\bup\s+to\s*$", re.IGNORECASE)

# The other spellings of a ceiling: "less than 2 years", "fewer than", "under", "below", "maximum (of)", "no more than",
# "at most", "upto", "not exceeding". Never "no less than" / "not less than", which are floors. Only a single value
# ("Maximum 8-12 years" labels a range and keeps it).
_CEILING_WORDS_BEFORE = re.compile(
    r"(?<!\bno\s)(?<!\bnot\s)\b(?:upto|(?:less|fewer|lesser)\s+than|under|below|"
    r"(?:a\s+)?max(?:imum)?\.?(?:\s+of)?|(?:no|not)\s+more\s+than|at\s+most|not\s+exceeding)\s*$",
    re.IGNORECASE,
)
# "Candidates with less than 5 years of experience are not eligible": what is turned away is the ceiling, so the number
# is the floor. Read from the number to the end of its sentence.
_CEILING_NEGATED = re.compile(
    r"[^.;]{0,120}?\b(?:not\s+(?:be\s+)?(?:eligible|considered|accepted|qualified|suitable)|do\s+not\s+apply|"
    r"don'?t\s+apply|should\s+not\s+apply|ineligible|cannot\s+apply|can'?t\s+apply)\b",
    re.IGNORECASE,
)
# A ceiling that closes a range the sentence already opened is not a requirement of its own: "minimum of 6 years ...
# with a maximum of 10 years", "three to five years (no more than 10 years)", "minimum 5+ years and up to 20 years".
# A comma starts another clause, so it ends the reach ("BA/BS with 2+ years, MS with up to 2 years" is two cohorts).
_FLOOR_BEFORE = re.compile(
    r"(?:\d\s*\+?\s*(?:years?|yrs?)|\bminimum\b|\bmin\b|\bat\s+least\b|"
    r"\b(?:three|four|five|six|seven|eight|nine|ten)\s+(?:to\s+\w+\s+)?years?)[^.;,]{0,50}$",
    re.IGNORECASE,
)


def _is_narrative(text: str, match: re.Match) -> bool:
    """Whether this match sits in corporate history rather than in a requirement."""
    return bool(
        _NARRATIVE_BEFORE.search(text[max(0, match.start() - 40) : match.start()])
        or _NARRATIVE_AFTER.match(text[match.end() : match.end() + 14])
    )


# "Twenty (20) years", "five (5) years", "5 (five) years", "Six (6)+ years": a requirement written as a word and its
# digits, which neither number pattern can read. Replaced by the digits alone, padded with spaces to the original
# length so every offset (and so every guard window) is unchanged. "ten (5)" is left as written.
_NUMBER_NAMES = {
    "zero": 0,
    **_WORD_NUM,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "twenty-five": 25,
    "thirty": 30,
}
_PAREN_NUMBER = re.compile(
    r"\b(?:(?P<w1>" + "|".join(_NUMBER_NAMES) + r")\s*\(\s*(?P<d1>\d{1,2})\s*\)"
    r"|(?P<d2>\d{1,2})\s*\(\s*(?P<w2>" + "|".join(_NUMBER_NAMES) + r")\s*\))",
    re.IGNORECASE,
)


def _collapse_paren_number(match: re.Match) -> str:
    word, digits = (
        (match.group("w1"), match.group("d1"))
        if match.group("w1")
        else (match.group("w2"), match.group("d2"))
    )
    if _NUMBER_NAMES[word.lower()] != int(digits):
        return match.group(0)
    return digits.ljust(len(match.group(0)))


def _passes(text: str):
    """The three Tier-2 passes in the order they are tried, each as (text, patterns, third). Lazy, so the third
    pass's collapse of "five (5)" is paid only by a description the first two leave without an answer."""
    folded = text.translate(_FOLD)
    yield folded, _DESC_PATTERNS, False
    yield folded, _NUM_WORD_PATTERNS, False
    yield _PAREN_NUMBER.sub(_collapse_paren_number, folded), _THIRD_PATTERNS, True


def from_description(text: str | None) -> ExperienceSpan | None:
    """Tier 2 — mine the description with experience-anchored regex (for sources without a field).

    Two passes answer as before; only a description they leave without an answer gets the third, so the
    recall widenings in it can add a reading and cannot change one (ADR-0066)."""
    if not text:
        return None
    for found in (_scan(*one_pass) for one_pass in _passes(text)):
        if found:
            return found
    return None


def stated_floors(text: str | None) -> list[int]:
    """Every distinct floor the description states, in the pass Tier 2 answers from, smallest
    first; "up to N" ceilings left out. The answer is the first of them (ADR-0079), and `get_job`
    says so when there are several (ADR-0357)."""
    if not text:
        return []
    for stated in (_stated(*one_pass) for one_pass in _passes(text)):
        if stated:
            return sorted({span.min_years for span, ceiling in stated if not ceiling})
    return []


def _scan(
    text: str, patterns: list[_Tier2Pattern], third: bool = False
) -> ExperienceSpan | None:
    """One pass of the Tier-2 patterns over already-folded text, answered by its smallest floor.

    Every match that survives the guards is collected and the **smallest** `min_years` among them
    wins (ADR-0079), rather than whichever the leftmost pattern reached first. Its own `max_years`
    travels with it: a floor from one sentence paired with a ceiling from another describes nothing
    anybody wrote. Selecting rather than returning early is what lets `_GAP` be as wide as recall
    wants, since position no longer decides the answer.
    """
    return min(
        (span for span, _ in _stated(text, patterns, third)),
        key=lambda span: span.min_years,
        default=None,
    )


def _stated(
    text: str, patterns: list[_Tier2Pattern], third: bool = False
) -> list[tuple[ExperienceSpan, bool]]:
    """Every match of one pass that survives the guards, each with whether it is a ceiling ("up to N",
    "less than N") rather than a stated floor.

    A ceiling word ("less than", "maximum", ...) reads as a 0..N span only when the pass states no floor at
    all. Where it does, the clause keeps the reading main gave its number, a plain floor N, so the answer
    never rises above main's and ADR-0079's smallest floor is what chooses among them."""
    spans: list[tuple[ExperienceSpan, bool]] = []
    soft_ceilings: list[
        tuple[ExperienceSpan, bool]
    ] = []  # "less than N": a 0..N span if no floor is stated
    soft_floors: list[
        tuple[ExperienceSpan, bool]
    ] = []  # the same numbers, read as main read them
    for pattern, guarded in patterns:
        screened = (
            third and _GAP_LONG in pattern.pattern
        )  # the long gap reaches benefits and company prose
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
            if _NOT_A_WHOLE_NUMBER.search(
                text[max(0, match.start(1) - 4) : match.start(1)]
            ):
                continue  # the tail of a longer number ("160,000") or a decimal's fraction ("0.5")
            lo = _years_from_token(match.group(1))
            hi = (
                _years_from_token(match.group(2))
                if match.lastindex and match.lastindex >= 2 and match.group(2)
                else None
            )
            if hi is not None:
                fraction = _FRACTION.match(text, match.end(2))
                if fraction and int(fraction.group(1)):
                    hi += 1  # "3.6 years" is up to 4, the way from_field rounds a ceiling up
            if screened and _GAP_PROSE.search(match.group(0)):
                continue
            if third and (
                _PREFERRED_WITHIN.search(match.group(0))
                or _PREFERRED_BEFORE.search(
                    text[max(0, match.start(1) - 40) : match.start(1)]
                )
                or _PREFERRED_AFTER.match(text[match.end() : match.end() + 30])
            ):
                continue  # marked preferred
            if (
                third
                and lo >= _MAX_EXPERTISE_YEARS
                and text[max(0, match.end() - 9) : match.end()].lower() == "expertise"
            ):
                continue  # a company's own years of expertise
            if _NARRATIVE_SPAN.match(text[match.start(1) : match.end() + 20]):
                continue
            if _substitutes(text, match.end(1)):
                continue
            if third and _SUBST_BEFORE.search(
                text[max(0, match.start(1) - 90) : match.start(1)]
            ):
                continue
            before = text[max(0, match.start(1) - 90) : match.start(1)]
            if _WINDOW_BEFORE.search(before[-30:]) or _RESIDENCY_WINDOW_BEFORE.search(
                before
            ):
                continue
            if lo > _MAX_PLAUSIBLE_REQUIREMENT:
                continue
            if guarded and _is_narrative(text, match):
                continue
            # Where the wording of a ceiling starts, if the number sits right after one.
            ceiling_at = None
            ceiling_word = False
            closes_range = False
            if hi is None:
                window_from = max(0, match.start(1) - 24)
                word = _CEILING_WORDS_BEFORE.search(text[window_from : match.start(1)])
                if word and not _CEILING_NEGATED.match(text, match.end()):
                    ceiling_at = window_from + word.start()
                    ceiling_word = True
            if ceiling_at is None:
                up_to_from = max(0, match.start(1) - 10)
                up_to = _CEILING_BEFORE.search(text[up_to_from : match.start(1)])
                if up_to:
                    ceiling_at = up_to_from + up_to.start()
            if ceiling_at is not None and _FLOOR_BEFORE.search(
                text[max(0, ceiling_at - 70) : ceiling_at]
            ):
                if not ceiling_word:
                    continue
                # The clause closes a range a floor opened ("minimum of 6 years ... a maximum of 10"): it is no
                # 0..N span, and its number keeps main's reading, a plain floor, which a smaller cohort's
                # ("BS and 3 to 5 years or MS and less than 2 years") never lifts the answer past.
                closes_range = True
            if ceiling_at is not None:
                # "up to N years": the number is the top of the range, and the posting states no
                # floor at all. The top faces the requirement ceiling the floor just faced — this
                # branch skips the span rules below, so without it "up to 8 to 150 years" would
                # write a 150 no other path can produce (ADR-0072).
                top = hi if hi is not None else lo
                if not ceiling_word:
                    if top <= _MAX_PLAUSIBLE_REQUIREMENT:
                        spans.append((ExperienceSpan(0, top, "regex"), True))
                    continue
                if top <= _MAX_PLAUSIBLE_REQUIREMENT and not closes_range:
                    soft_ceilings.append((ExperienceSpan(0, top, "regex"), True))
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
            (soft_floors if ceiling_word else spans).append(
                (ExperienceSpan(lo, hi, "regex"), False)
            )
    if any(not ceiling for _, ceiling in spans):
        return spans + soft_floors
    return spans + soft_ceilings


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
    # The entry tier is tried before the associate tier (ADR-0337): a title carrying both is an
    # entry role ("Associate Software Engineer - Intern", "Associate Software Engineer (College
    # Grad 2027)", "Software Developer (Junior to Intermediate)"). The 759 served titles holding
    # one word of each and no higher tier (2026-09-29) state a median of 1 year (n=313), the
    # entry tier's own median, not the associate tier's 3.
    (
        re.compile(
            r"\b(intern|internship|trainee|graduate|\bgrad\b|student\w*|entry[\s_-]?level|junior|\bjr\b|apprentice|fresher|early[\s-]?career)\b",
            re.IGNORECASE,
        ),
        0,
    ),
    (
        re.compile(
            r"\b(associate|mid[\s_-]?level|intermediate|medior|middle(?![\s-]*east))\b",
            re.IGNORECASE,
        ),
        3,
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
#
# "Engineering" joined the role nouns (ADR-0337) for ladders named after the discipline: Netflix's
# "Software Engineering 5" and "Software Engineering L5", L3Harris's "Software Engineering 1".
# Of the served titles it reaches that state a number (2026-09-29), levels 1-3 state medians of
# 3, 5 and 6 years (n=25, 40, 3), at or above the 0, 3 and 5 this mapping gives: a low floor,
# which is the safe side for `min_years <= N`. Netflix's level-5 postings state no number at all;
# bare "L5" titles elsewhere state a median of 8 (n=31) against the 7 given here.
_LEVEL = re.compile(
    r"\b(?:engineer(?:ing)?|developer|programmer|analyst|scientist|architect|sde|swe)\s*"
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


# One employer's own level ladder, where it disagrees with `_LEVEL_YEARS` (ADR-0357). Netflix titles
# every role by level, "Software Engineer (L5)" or "Software Engineer 5", including role nouns
# `_LEVEL` does not hold ("Business Security Partner (L5)", "Creative Tech Researcher 5"). Its
# postings that state a number state medians of 3, 5 and 9 years at L4, L5 and L6 (n=11, 35, 21;
# served table, 2026-09-29), where `_LEVEL_YEARS` gives 7, 7 and nothing, and "(LN)" titles at
# other employers state 6, 5 and 10. A ladder is the employer's, so it is keyed by company and
# holds only levels its postings measure. Tier 3, so it answers only a posting that states no
# number: a stated one always wins (ADR-0018).
_COMPANY_LADDERS = {"netflix": {"4": 3, "5": 5, "6": 9}}
_LADDER_LEVEL = re.compile(
    r"\(\s*L\s*([1-9])\s*\)|\b[A-Za-z]+\s+([1-9])(?=\s*(?:$|[,(\u2013\u2014-]))"
)


def _company_level(title: str | None, company: str | None) -> ExperienceSpan | None:
    """The floor ``company``'s own ladder gives the level in ``title``, when it has one."""
    ladder = _COMPANY_LADDERS.get((company or "").strip().lower())
    match = _LADDER_LEVEL.search(title or "") if ladder else None
    years = ladder.get(match.group(1) or match.group(2)) if match else None
    return None if years is None else ExperienceSpan(years, None, "seniority")


def from_seniority(
    field: str | None, title: str | None = None, company: str | None = None
) -> ExperienceSpan | None:
    """Tier 3 (fallback) — map a seniority label to a floor-years estimate, from the source's field
    (else the title). The employer's own ladder first (Netflix's "(L5)"), then word labels
    ("Senior"), then a numeric/roman level suffix ("Engineer II")."""
    text = f"{field or ''} {title or ''}"
    if not text.strip():
        return None
    if (own := _company_level(title, company)) is not None:
        return own
    for pattern, years in _SENIORITY:
        if pattern.search(text):
            return ExperienceSpan(years, None, "seniority")
    match = _LEVEL.search(title or "") or _LEVEL_WORD.search(title or "")
    if match:
        return ExperienceSpan(_LEVEL_YEARS[match.group(1).lower()], None, "seniority")
    return None


def extract(
    field: str | None,
    description: str | None,
    title: str | None = None,
    company: str | None = None,
) -> ExperienceSpan | None:
    """Run the cascade: a concrete number from the structured field, then from the description, and
    only if neither yields one, a floor estimate from the seniority label (field or title, read on
    ``company``'s own ladder where it has one). Concrete numbers always win over the seniority
    fallback (per ADR-0018). None if nothing matches.
    """
    return (
        from_field(field)
        or from_description(description)
        or from_seniority(field, title, company)
    )
