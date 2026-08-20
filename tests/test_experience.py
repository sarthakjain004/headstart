"""Tests for the experience-extraction cascade (ADR-0009, guards in ADR-0013).

Covers each tier's happy path, the plausibility/range guards, and the cascade ordering. The one
known-open defect — the Tier-2 anchor also matching "N years ago ... experience" — is pinned as a
*strict* xfail, so whoever tightens the anchor is told by a failing suite to delete the marker.
"""

from __future__ import annotations

import pytest

from headstart.experience import (
    ExperienceSpan,
    extract,
    from_description,
    from_field,
    from_seniority,
)

# --- Tier 1: from_field ---------------------------------------------------------------------------


def test_field_open_ended():
    assert from_field("5+") == ExperienceSpan(5, None, "field")


def test_field_range():
    assert from_field("3 to 5") == ExperienceSpan(3, 5, "field")
    assert from_field("3-5") == ExperienceSpan(3, 5, "field")
    assert from_field("3–5") == ExperienceSpan(3, 5, "field")  # en dash


def test_field_malformed_range_drops_ceiling():
    # "3-1": hi < lo is a parse artifact, not a real range -> keep the floor, drop the ceiling
    assert from_field("3-1") == ExperienceSpan(3, None, "field")


def test_field_implausible_floor_rejected():
    # "100" must not silently truncate to 10 (\d{1,3} capture + plausibility guard, ADR-0013)
    assert from_field("100") is None
    assert from_field("51") is None  # just over the 50-year ceiling
    assert from_field("50") == ExperienceSpan(50, None, "field")  # boundary is kept


def test_field_implausible_ceiling_dropped():
    # Tier 1 range-checks its ceiling the same way Tier 2 does (ADR-0013)
    assert from_field("3 to 99") == ExperienceSpan(3, None, "field")


def test_field_empty_or_unparseable():
    assert from_field(None) is None
    assert from_field("") is None
    assert from_field("senior") is None


# --- Tier 2: from_description ----------------------------------------------------------------------


def test_description_range():
    assert from_description("7 to 12 years of experience") == ExperienceSpan(
        7, 12, "regex"
    )


def test_description_open_ended_tolerates_adjective():
    # the {0,25} gap lets "of proven" sit between the number and "experience"
    assert from_description("7+ years of proven experience") == ExperienceSpan(
        7, None, "regex"
    )


def test_description_reversed_order():
    assert from_description("Experience: 5 years") == ExperienceSpan(5, None, "regex")


def test_description_anchor_rejects_unrelated_number():
    # no "experience" near the number -> not a requirement
    assert from_description("Maintain a 40-year old C++ codebase") is None


def test_description_implausible_ceiling_dropped():
    assert from_description("3 to 99 years experience") == ExperienceSpan(
        3, None, "regex"
    )


def test_description_implausible_floor_rejected():
    # an over-ceiling number in the description is skipped, not returned (symmetry with Tier 1)
    assert from_description("99 years of experience") is None


def test_description_empty_or_signal_free():
    assert from_description(None) is None
    assert from_description("") is None
    assert from_description("great team, no numbers here") is None


@pytest.mark.xfail(
    strict=True,
    reason="Tier-2 anchor is direction-agnostic: 'N years ago ... experience' reads as a "
    "requirement. Tightening it risks the measured 18.1% description recall (ADR-0009), which "
    "can't be re-verified without the gitignored corpus. Deferred — see ADR-0013.",
)
def test_description_years_ago_is_not_a_requirement():
    assert from_description("5 years ago I gained experience") is None


# --- extract: the cascade -------------------------------------------------------------------------


def test_extract_field_beats_description():
    # Tier 1 wins when both could match
    assert extract("2+", "10+ years of experience") == ExperienceSpan(2, None, "field")


def test_extract_falls_through_to_description():
    assert extract(None, "10+ years of experience") == ExperienceSpan(10, None, "regex")


def test_extract_none_when_nothing_matches():
    assert extract(None, None) is None
    assert extract("", "no signal here") is None


# --- Tier 2 additions (ADR-0018): phrasings the earlier patterns missed --------------------------


def test_description_years_of_in_without_the_word_experience():
    # the big gap: "N years of/in/as <work word>" with no "experience" nearby
    assert from_description("5+ years in software testing") == ExperienceSpan(
        5, None, "regex"
    )
    assert from_description(
        "7 years of professional software engineering"
    ) == ExperienceSpan(7, None, "regex")
    assert from_description("10+ years in software development") == ExperienceSpan(
        10, None, "regex"
    )


def test_description_reversed_range_plus_and_period():
    assert from_description("Experience: 8 – 12 Years") == ExperienceSpan(
        8, 12, "regex"
    )
    assert from_description("5 plus years of proven experience") == ExperienceSpan(
        5, None, "regex"
    )
    assert from_description("Experience Required: Min. 10 Years") == ExperienceSpan(
        10, None, "regex"
    )


def test_description_ignores_non_experience_year_mentions():
    # "per year" / salary / not-experience must stay misses
    assert from_description("16 hours of paid volunteer time per year") is None
    assert from_description("2 Extra Salaries Per Year") is None
    assert from_description("$60,000-70,000/year") is None


# --- Tier 2: a range's floor is the floor -------------------------------------------------------
# Where "experience" sits further from the number than `_GAP`, the range patterns cannot anchor, and
# a single-value pattern matched at the range's TOP — serving the CEILING as the FLOOR. That both
# mislabels the job and hides it from the `min_years <= your_years` filter from exactly the
# candidate who qualifies. Measured at 2,672 jobs across the description store before the fix.
# `_RANGE_TAIL` is the whole cure: it works for every pattern, and for separators none enumerate.


def test_description_range_without_the_word_experience():
    # the reported bug: "2-4 years" must not be served as 4+
    assert from_description(
        "2-4 years of hands-on software development"
    ) == ExperienceSpan(2, 4, "regex")
    assert from_description("1-3 years in a Field Engineering role") == ExperienceSpan(
        1, 3, "regex"
    )


def test_description_range_with_trailing_plus():
    # "5-8+ years" — the ceiling carries the "+", and the gap to "experience" exceeds the anchor
    assert from_description(
        "- 5-8+ years of all-source investigative or targeting experience"
    ) == ExperienceSpan(5, 8, "regex")


def test_description_recovers_floor_for_unenumerated_separator():
    # the backward look is the safety net: no range pattern lists "~" or "and"
    assert from_description("2 ~ 4 years of experience") == ExperienceSpan(
        2, 4, "regex"
    )
    assert from_description("between 2 and 4 years of experience") == ExperienceSpan(
        2, 4, "regex"
    )
    assert from_description(
        "10 to 12 years of embedded switching software"
    ) == ExperienceSpan(10, 12, "regex")


def test_range_tail_and_does_not_swallow_an_anniversary_list():
    # "and" is only safe because a digit must sit immediately before it — "3 year and 10 year"
    # has "year" in between, so it must not read as a range (nor as a requirement at all)
    assert from_description("PTO at 3 year and 10 year anniversary plus bonus") is None


def test_description_accepts_yrs_abbreviation():
    assert from_description("2-4 yrs of experience") == ExperienceSpan(2, 4, "regex")
    assert from_description("5 + yrs building production APIs") == ExperienceSpan(
        5, None, "regex"
    )


def test_description_optional_connector_captures_bare_gerund():
    # "N years building/working/hands-on …" with no of/in/as connector
    assert from_description(
        "4+ years building distributed & scalable systems"
    ) == ExperienceSpan(4, None, "regex")
    assert from_description(
        "2+ years data center or IT infrastructure experience"
    ) == ExperienceSpan(2, None, "regex")


def test_description_rejects_company_narrative():
    # Loosening the connector reopened this class, so the exclusion is now explicit. Company age,
    # founder tenure and benefits are never requirements.
    assert (
        from_description("has spent the last 15 years building modern infrastructure")
        is None
    )
    assert (
        from_description("Founded in New Zealand 12 years ago, we are working with")
        is None
    )
    assert (
        from_description(
            "The founding team spent a combined 40+ years at Palantir building"
        )
        is None
    )
    assert from_description("1-month sabbatical after 3 years of service") is None


def test_narrative_at_guard_is_case_sensitive():
    # "at Palantir" is tenure; "at a startup" / "at the company" is ordinary requirement prose.
    # Under re.I the `[A-Z]` matched both and silently discarded a real number.
    assert from_description(
        "3+ years of engineering at a fast-growing startup"
    ) == ExperienceSpan(3, None, "regex")
    assert from_description(
        "4+ years of software development at the company"
    ) == ExperienceSpan(4, None, "regex")


def test_unguarded_patterns_cannot_match_without_the_word_experience():
    # The guard flag is the only thing between a pattern and corporate narrative. A pattern may
    # therefore go unguarded only if it cannot fire at all unless "experience" is literally there.
    # Asserted behaviourally rather than by inspecting the pattern string: the work-word pattern
    # contains "experience" inside an alternation while still needing guards, so any structural
    # check reports it as safe.
    from headstart.experience import _DESC_PATTERNS, _NUM_WORD_PATTERNS

    narrative = [
        "Founded 12 years ago by a team of engineers",
        "spent 15 years building great products",
        "shipping software for 9 years (10 years including beta)",
    ]
    # both passes, because the factory's whole point is that they cannot drift apart
    for pattern, guarded in [*_DESC_PATTERNS, *_NUM_WORD_PATTERNS]:
        if guarded:
            continue
        for line in narrative:
            assert not pattern.search(line), (
                f"{pattern.pattern} matched {line!r} unguarded"
            )


def test_both_passes_carry_the_same_guard_flags():
    # `_desc_patterns` is a factory so the digits pass and the words pass stay in lockstep; if one
    # gained a pattern the other did not, or the flags diverged, the guards would apply unevenly.
    from headstart.experience import _DESC_PATTERNS, _NUM_WORD_PATTERNS

    assert [p.guarded for p in _DESC_PATTERNS] == [
        p.guarded for p in _NUM_WORD_PATTERNS
    ]
    assert any(p.guarded for p in _DESC_PATTERNS), "guards would be inert if empty"
    assert not all(p.guarded for p in _DESC_PATTERNS), (
        "an unguarded anchored pattern is what lets '25 years of experience' through"
    )


def test_fold_covers_every_character_the_patterns_stopped_handling():
    # The Tier-2 patterns dropped their typographic variants once folding was introduced, so they
    # are correct only while `_FOLD` maps each one. Pin that: this is a silent failure otherwise.
    from headstart.experience import _FOLD

    for ch, expected in [
        ("\u2011", "-"),
        ("\u2012", "-"),
        ("\u2013", "-"),
        ("\u2014", "-"),
        ("\u2015", "-"),
        ("\u2212", "-"),
        ("\u2018", "'"),
        ("\u2019", "'"),
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\uf0b7", "\u2022"),
        ("\u30fb", "\u2022"),
    ]:
        assert ch.translate(_FOLD) == expected


def test_fold_is_offset_preserving():
    # The narrative guards slice `text` by `match.start()`, so a mapping that changed length would
    # silently move every guard window. Every replacement must be exactly one character.
    from headstart.experience import _FOLD

    for src, dst in _FOLD.items():
        assert isinstance(dst, str) and len(dst) == 1, (chr(src), dst)


def test_description_narrative_does_not_mask_a_real_requirement():
    # a guarded rejection must fall through to the next occurrence, not abandon the description
    assert from_description(
        "Founded 12 years ago. Requirements: 5+ years building production systems"
    ) == ExperienceSpan(5, None, "regex")


# --- Tier 3: seniority fallback, calibrated to data (ADR-0018) ------------------------------------


def test_seniority_maps_labels_to_calibrated_floors():
    assert from_seniority("entry_level") == ExperienceSpan(0, None, "seniority")
    assert from_seniority("Associate") == ExperienceSpan(3, None, "seniority")
    assert from_seniority("Mid-Senior Level") == ExperienceSpan(5, None, "seniority")
    assert from_seniority("experienced") == ExperienceSpan(
        5, None, "seniority"
    )  # data median 5
    assert from_seniority("Executive") == ExperienceSpan(
        5, None, "seniority"
    )  # a level, not C-suite
    assert from_seniority("Director") == ExperienceSpan(10, None, "seniority")
    assert from_seniority("Not Applicable") is None


def test_seniority_from_title_and_level_suffix():
    assert from_seniority(None, "Senior Software Engineer") == ExperienceSpan(
        5, None, "seniority"
    )
    assert from_seniority(None, "Staff Engineer") == ExperienceSpan(
        7, None, "seniority"
    )
    assert from_seniority(None, "Software Engineer 1") == ExperienceSpan(
        0, None, "seniority"
    )
    assert from_seniority(None, "Data Scientist III") == ExperienceSpan(
        5, None, "seniority"
    )
    assert from_seniority(None, "Backend Developer") is None  # no seniority signal


def test_number_always_beats_seniority():
    # seniority field, but the description states a number -> use the number
    assert extract(
        "Mid-Senior level", "we want 3+ years of experience"
    ) == ExperienceSpan(3, None, "regex")
    # seniority field, no number anywhere -> fall back to seniority
    assert extract("entry_level", "join our team") == ExperienceSpan(
        0, None, "seniority"
    )
    # a field number beats a senior title
    assert extract("2+", None, "Senior Engineer") == ExperienceSpan(2, None, "field")


# --- widened recall, measured against the served table (see the PR) -------------------------------


def test_description_reads_an_open_in_phrase_without_a_work_word():
    # `_WORK` can only enumerate domains someone thought of; the misses are a long tail.
    assert from_description("5-7+ years in IT operations including server support") == (
        ExperienceSpan(5, 7, "regex")
    )
    assert from_description(
        "3+ years in product marketing, delivering go-to-market"
    ) == (ExperienceSpan(3, None, "regex"))


def test_description_reads_a_bare_gerund():
    assert from_description("5+ years shipping production C++ in robotics") == (
        ExperienceSpan(5, None, "regex")
    )
    assert from_description("4+ years specializing in Flutter") == ExperienceSpan(
        4, None, "regex"
    )


def test_description_reads_a_trailing_parenthetical():
    assert from_description("In-depth knowledge of PHP (3+ years).") == ExperienceSpan(
        3, None, "regex"
    )
    assert from_description("M365 administration activities (3-5 years)") == (
        ExperienceSpan(3, 5, "regex")
    )
    assert from_description(
        "customer-facing work (typically 8+ years, but flexible)"
    ) == (ExperienceSpan(8, None, "regex"))


def test_widened_patterns_still_reject_narrative():
    assert from_description("Clay spent 18 years at Google, where he led Labs") is None
    assert from_description("Appen has led AI training data for over 30 years") is None
    assert from_description("In just two years, we achieved unicorn status") is None


def test_field_tolerates_a_stated_bound():
    assert from_field(">3 years") == ExperienceSpan(3, None, "field")
    assert from_field(">2yrs") == ExperienceSpan(2, None, "field")
    assert from_field("Minimum 3 years") == ExperienceSpan(3, None, "field")
    assert from_field("Not Applicable") is None


def test_seniority_reads_snake_case_labels():
    # `\b` cannot end a label whose next character is `_`, so these never matched before.
    assert from_seniority("student_college") == ExperienceSpan(0, None, "seniority")
    assert from_seniority("student_school") == ExperienceSpan(0, None, "seniority")
    assert from_seniority("senior_manager") == ExperienceSpan(5, None, "seniority")
    assert from_seniority("senior_executive") == ExperienceSpan(5, None, "seniority")


def test_seniority_reads_mid_level_synonyms():
    assert from_seniority(None, "Middle Mobile Engineer") == ExperienceSpan(
        3, None, "seniority"
    )
    assert from_seniority(None, "Medior Developer") == ExperienceSpan(
        3, None, "seniority"
    )
    # "Middle East" is a region, not a level
    assert from_seniority(None, "Sales Engineer, Middle East") is None


def test_seniority_reads_l_prefixed_and_worded_levels():
    assert from_seniority(None, "DEVELOPER L3") == ExperienceSpan(5, None, "seniority")
    assert from_seniority(None, "TEST ENGINEER L4") == ExperienceSpan(
        7, None, "seniority"
    )
    assert from_seniority(None, "Managed Services Engineer (L1)") == ExperienceSpan(
        0, None, "seniority"
    )
    assert from_seniority(None, "Level 1 Support Engineer") == ExperienceSpan(
        0, None, "seniority"
    )
    # the bare-numeral and roman forms keep the mapping they had
    assert from_seniority(None, "Data Scientist III") == ExperienceSpan(
        5, None, "seniority"
    )
    assert from_seniority(None, "SDE II") == ExperienceSpan(3, None, "seniority")


def test_description_rejects_narrative_idioms_after_the_number():
    # These read as requirements to the widened patterns; only what FOLLOWS the number says
    # otherwise, which is what `_NARRATIVE_BEFORE` cannot see.
    assert (
        from_description("on the Cloud 100 for four years in a row and teaming up")
        is None
    )
    assert (
        from_description("$165K salary, competitive equity (4 year vest), NYC") is None
    )
    assert (
        from_description("Within ~1 year of graduating, or recently graduated") is None
    )


def test_ambiguous_idioms_are_not_guarded():
    # "running" and "in business" read as narrative often enough to be tempting, but each costs
    # more real requirements than it saves (see `_NARRATIVE_SPAN`).
    assert from_description("4+ years running distributed systems at scale") == (
        ExperienceSpan(4, None, "regex")
    )
    assert from_description("3+ years in business development or sales") == (
        ExperienceSpan(3, None, "regex")
    )


def test_narrative_idiom_guard_does_not_eat_real_requirements():
    assert from_description(
        "6+ years of shipping features for native Android apps"
    ) == (ExperienceSpan(6, None, "regex"))
    assert from_description("A minimum of four years of relevant experience") == (
        ExperienceSpan(4, None, "regex")
    )


def test_narrative_idiom_binds_to_the_number_its_pattern_matched():
    # Searching a window re-anchors on whatever "years" comes first in it, letting an idiom that
    # qualifies a different number two sentences away disqualify this one.
    assert from_description(
        "5+ years of experience. Equity (4 year vest) and 401k."
    ) == ExperienceSpan(5, None, "regex")


def test_in_a_row_does_not_swallow_a_hyphenated_noun():
    # `\b` is satisfied by the hyphen, so "a row-level security team" read as the award idiom.
    assert from_description(
        "10+ years of experience in a row-level security team"
    ) == ExperienceSpan(10, None, "regex")


def test_gerund_needs_an_object():
    # "three years running" is the streak idiom; "running <something>" is a real requirement.
    assert from_description("fastest-growing company three years running") is None
    assert from_description("4+ years running distributed systems") == ExperienceSpan(
        4, None, "regex"
    )


def test_up_to_is_a_ceiling_not_a_floor():
    # "up to N years" states a maximum — reading it as min_years is wrong in every case, and it is
    # how retention boilerplate and fixed-term contracts phrase a duration.
    assert from_description("Your data is kept for up to 2 years in our pool") is None
    assert (
        from_description("a training position of up to three years in duration") is None
    )


def test_range_tail_needs_a_word_boundary():
    # `_DIGITS_OR_WORDS` spells numbers out, so without a leading `\b` any word ENDING in one
    # supplies a bogus floor and silently turns a correct single value into a range.
    assert from_description(
        "GET THE JOB DONE - 5+ years of full stack engineering experience"
    ) == ExperienceSpan(5, None, "regex")
    assert from_description("Everyone - 6+ years of backend experience") == (
        ExperienceSpan(6, None, "regex")
    )
    assert from_description("on the phone - 8+ years of support experience") == (
        ExperienceSpan(8, None, "regex")
    )


def test_narrative_idiom_spans_a_range():
    # Anchoring the idiom check on the captured number puts it on a range's floor, so the check
    # has to be able to step over the rest of the range to reach the idiom.
    assert from_description("stock (2 to 4 year vest)") is None


def test_ceiling_applies_to_anchored_patterns_too():
    # This fires an experience-anchored (unguarded) pattern, so a guarded-only check misses it.
    assert from_description("Candidates with up to 3 years of experience") is None


def test_ceiling_must_sit_immediately_before_the_number():
    # A nearby-window check rejects real requirements that merely share a sentence with "up to".
    assert from_description(
        "Bonus up to 20 percent and 6+ years in backend systems"
    ) == ExperienceSpan(6, None, "regex")
    assert from_description(
        "up to date knowledge and 5+ years building services"
    ) == ExperienceSpan(5, None, "regex")


def test_requirement_ceiling_applies_to_anchored_patterns_too():
    # Measured: 1,066 descriptions produced a Tier-2 answer above 20 years and none was a real
    # requirement — company age, founder tenure, or a literal age. The anchor word says nothing
    # about genre; the magnitude does.
    assert (
        from_description("PayPal has been revolutionizing commerce for 25 years")
        is None
    )
    assert (
        from_description(
            "a federal contractor with more than 30 years of experience providing services"
        )
        is None
    )
    assert (
        from_description("Built on more than 50 years of experience, MacKay has")
        is None
    )
    # and the ordinary range is untouched
    assert from_description("8+ years of hands-on experience") == ExperienceSpan(
        8, None, "regex"
    )


def test_tier_two_captures_a_three_digit_year_whole():
    """A 3-digit number must reach the plausibility guard, not be truncated past it (ADR-0013).

    `_DIGITS` was `\\d{1,2}`, so "105 years" matched the trailing "05" and returned 5, and
    "100 years" returned 0 — a company-history sentence answering as a real requirement.
    ADR-0013 widened `_FIELD` for exactly this reason, "so a 3-digit value is captured whole
    and the plausibility guard can reject it", but scoped that widening to Tier 1. What it
    deferred was something else — the direction-agnostic Tier-2 *anchor*, still pinned by the
    strict xfail above. This widens Tier 2's digit capture; the deferred anchor stays deferred.
    """
    assert from_description("with 105 years of experience") is None
    assert from_description("over 100 years of experience") is None
    assert from_description("300 years of experience") is None
    # Measured over the whole description store — 328,923 descriptions, every ATS: 1,019 rows
    # change. 736 keep a Tier-2 answer but move its value, all but 5 of them off a bogus 0; those
    # 5 go non-zero to a different non-zero (10->5, 10->6, 10->4, 10->5, 20->1). 283 stop
    # answering, and 26 of those had a real prior value — mostly separator-less ranges, where
    # "810 years" is "8-10" with the hyphen lost upstream. An earlier note here said "~8"; that
    # came from bucketing by keyword rather than reading the rows, and undercounted.
    # The company-history sentence below is the shape behind the bulk of the 283.
    assert (
        from_description(
            "a team with more than 100 years of combined experience in payments"
        )
        is None
    )
    # Tier 1 already behaved; the point is that the two tiers now agree.
    assert from_field("105") is None


def test_two_digit_requirements_still_answer():
    """The widening must not disturb the ordinary case it sits next to."""
    assert from_description("5+ years of experience").min_years == 5
    assert from_description("12 years of experience").min_years == 12
    span = from_description("2-4 years of experience")
    assert (span.min_years, span.max_years) == (2, 4)


def test_the_requirement_ceiling_still_refuses_a_narrative_range():
    """The ceiling runs *before* `_RANGE_TAIL` recovery, deliberately — this pins that order.

    Moving it after recovery restores a floor first and then waves the pair through, re-opening
    the hole ADR-0066 closed: "The anchor word says nothing about genre; the magnitude does."
    Measured both ways over 328,923 descriptions: moving it changes 6 rows, and every one is a
    narrative sentence gaining a bogus span (15-25, 18-22, 18-25, 18-28, 20-25, and one 8 -> 16-21).
    So it buys no recall, costs exactly these sentences, and an earlier note here claiming it
    "changed nothing at all" was measured against a scan that raced the edit it was testing.
    """
    assert (
        from_description(
            "the founding team brings 10 and 30 years of experience building payments"
        )
        is None
    )
    assert (
        from_description("a combined 8 and 40 years at Palantir of experience") is None
    )
    assert (
        from_description("we pair 4 and 30 years of cloud-operating experience") is None
    )


def test_narrative_above_the_requirement_ceiling_is_still_refused():
    """Moving the guard must not open the genre hole it exists to close (ADR-0066)."""
    assert from_description("more than 25 years of experience") is None
    assert (
        from_description(
            "PayPal has been revolutionizing commerce for more than 25 years"
        )
        is None
    )


def test_an_absurd_ceiling_condemns_the_whole_span():
    """A range whose top is implausible is narrative, floor included (ADR-0072 has the argument).

    The `_DIGITS` widening let a range pattern capture a 3-digit ceiling; the requirement guard
    tests only `lo`, so the code nulled the implausible `hi` and kept a narrative floor. Before
    the widening these sentences answered nothing at all — except the third, which answered 0.
    """
    assert (
        from_description("Our team brings 8 to 150 years of combined experience")
        is None
    )
    assert (
        from_description(
            "a leadership team with 10 to 175 years of collective experience"
        )
        is None
    )
    assert (
        from_description("the founding team brings 10 to 300 years of experience")
        is None
    )


def test_an_ordinary_range_is_untouched_by_the_ceiling_rule():
    """Only an absurd top is condemned — real ranges must keep both ends."""
    span = from_description("3 to 5 years of experience")
    assert (span.min_years, span.max_years) == (3, 5)
    span = from_description("2-4 years of experience")
    assert (span.min_years, span.max_years) == (2, 4)
    assert from_description("5+ years of experience").min_years == 5


def test_the_ceiling_rule_turns_on_exactly_at_one_hundred():
    """99 keeps ADR-0013's behaviour, 100 is the new rule — pin the boundary itself."""
    span = from_description("3 to 99 years experience")
    assert (span.min_years, span.max_years) == (3, None)
    assert from_description("3 to 100 years experience") is None


def test_seniority_reads_a_tech_discipline_manager():
    """Calibrated like every other Tier-3 label (ADR-0018), not assigned by rung.

    Of the 1,094 corpus titles holding "engineering manager", the 753 that also state a number
    have a median `min_years` of 5 — the senior tier, not the lead/staff 7 the ladder suggests.
    """
    assert from_seniority(None, "Engineering Manager") == ExperienceSpan(
        5, None, "seniority"
    )
    assert from_seniority(None, "Software Engineering Manager") == ExperienceSpan(
        5, None, "seniority"
    )
    assert from_seniority(None, "Software Development Manager") == ExperienceSpan(
        5, None, "seniority"
    )


def test_seniority_leaves_the_rest_of_the_manager_vocabulary_alone():
    # The bulk of the uncovered "manager" titles, none of which states a floor (#189). The last
    # two are what the wider "<tech discipline> manager" list would have reached: ops and
    # facilities roles, plus "development manager" swallowing the business kind.
    assert from_seniority(None, "Program Manager Non Tech") is None
    assert from_seniority(None, "Project Manager") is None
    assert from_seniority(None, "Business Development Manager") is None
    assert from_seniority(None, "HR and Admin Assistant / Manager") is None
    assert from_seniority(None, "IT Infrastructure Manager") is None
    assert from_seniority(None, "Facilities Technical Manager") is None
