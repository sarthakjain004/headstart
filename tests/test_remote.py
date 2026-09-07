"""Tests for the JD-supersedes-field remote overlay (ADR-0061 v8).

Every phrasing below is either a literal string observed in a real job description while
building this (see ``experiment/jd-remote-detection/LOG.md``) or a minimal paraphrase of one,
not an invented edge case — the point of this module is that plausible-looking hand-written
patterns are exactly what a bare ``"remote" in text`` check gets wrong in practice.
"""

from __future__ import annotations

from headstart.remote import extract

# --- structured tags: highest precision, checked first -------------------------------------


def test_li_remote_tag_wins_regardless_of_field():
    assert extract(False, "great team, apply now #LI-Remote") is True
    assert extract(None, "#LI-Remote") is True


def test_li_onsite_and_hybrid_tags_never_touch_the_field():
    # onsite/hybrid signals are collected but this cascade is one-directional (remote only)
    assert extract(True, "#LI-Onsite") is True
    assert extract(None, "#LI-Onsite") is None
    assert extract(False, "#LI-Hybrid") is False
    assert extract(None, "#LI-Hybrid") is None


def test_workday_position_role_type_tag():
    assert (
        extract(None, "Position Role Type: Remote. U.S. Citizen requirements apply.")
        is True
    )
    assert extract(False, "Position Role Type: Onsite. Must relocate.") is False
    assert extract(None, "Position Role Type: Hybrid — 3 days per week onsite") is None


# --- real positive phrasings ----------------------------------------------------------------


def test_this_is_a_remote_position():
    assert (
        extract(
            None, "This is a remote position. A remote position does not require..."
        )
        is True
    )


def test_fully_100_percent_remote():
    assert extract(False, "This is a fully remote US opportunity.") is True
    assert (
        extract(None, "work 100% remotely with flexible hours") is True
    )  # "remotely" suffix


def test_remote_first_and_remote_friendly():
    assert (
        extract(
            None, "ClickHouse is a globally distributed company and remote-friendly."
        )
        is True
    )
    assert (
        extract(
            False, "This role is remote-first, executed from anywhere in Switzerland."
        )
        is True
    )


def test_remote_work_opportunity_phrase():
    assert (
        extract(None, "Benefits: Remote work opportunity in the United States.") is True
    )


def test_location_field_style_mention():
    assert (
        extract(None, "Role Location: Remote-US Compensation Range: $62,000-$93,000")
        is True
    )


# --- jargon: "remote"/"hybrid" meaning something else entirely -----------------------------


def test_technical_jargon_is_not_a_work_location_signal():
    assert (
        extract(False, "FastRPC (Fast Remote Procedure Call) is a high-performance API")
        is False
    )
    assert (
        extract(
            None, "Experience with Firebase services (Auth, Firestore, Remote Config)"
        )
        is None
    )
    assert (
        extract(False, "ensures seamless remote access for world-class engineers")
        is False
    )
    assert (
        extract(None, "You work across hybrid retrieval, re-ranking, query rewriting")
        is None
    )
    assert (
        extract(False, "supporting enterprise hybrid infrastructure comprising on-prem")
        is False
    )


def test_onsite_amenity_is_not_a_work_location_signal():
    # "on-site parking/gym/etc" describes an office perk, not the role's own location
    assert (
        extract(True, "Benefits: on-site parking, on-site Fitness Center, dental")
        is True
    )
    assert (
        extract(
            None,
            "medical, prescription drug, dental and vision, on-site health centers",
        )
        is None
    )


# --- negation: the word appears right next to its own denial -------------------------------


def test_negation_flips_a_bare_remote_mention_to_no_effect():
    # negated -> not a positive remote signal, so a None/False field is left alone
    assert (
        extract(
            None, "On-site in New York. Remote work is not available for this position."
        )
        is None
    )
    assert (
        extract(False, "Onsite (No remote positions available). Relocation: Mandatory.")
        is False
    )
    assert (
        extract(True, "We do not offer telecommuting or remote for this role.") is True
    )  # unchanged


# --- the "work from anywhere" perk-vs-policy ambiguity --------------------------------------


def test_work_from_anywhere_as_bounded_perk_does_not_trigger():
    # a real miss found by reading: title said "(Hybrid)", JD listed this as an annual perk
    assert extract(None, "Work From Anywhere Month + meeting-free weeks yearly") is None
    assert (
        extract(
            False,
            "you'll have the opportunity to work from anywhere, up to 10 days per year",
        )
        is False
    )


def test_work_from_anywhere_as_genuine_policy_still_triggers():
    assert (
        extract(None, "we maintain a remote-first work culture. #WorkFromAnywhere")
        is True
    )
    assert (
        extract(None, "flexible working hours - you can work from anywhere you choose")
        is True
    )


# --- cascade shape: one-directional, safe to re-run ------------------------------------------


def test_no_signal_leaves_field_exactly_as_it_was():
    assert extract(True, "just a normal job description with no location talk") is True
    assert (
        extract(False, "just a normal job description with no location talk") is False
    )
    assert extract(None, "just a normal job description with no location talk") is None


def test_missing_or_empty_description_is_a_no_op():
    assert extract(True, None) is True
    assert extract(False, None) is False
    assert extract(None, None) is None
    assert extract(False, "") is False


def test_idempotent_on_an_already_superseded_value():
    # re-running extract() on its own prior output must never regress or double-apply
    text = "This is a remote position."
    once = extract(False, text)
    assert once is True
    assert extract(once, text) is True
