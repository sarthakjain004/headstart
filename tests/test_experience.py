"""Tests for the experience-extraction cascade (ADR-0009, guards in ADR-0013).

Covers each tier's happy path, the plausibility/range guards, and the cascade ordering. The one
known-open defect — the Tier-2 anchor also matching "N years ago ... experience" — is pinned as a
*strict* xfail, so whoever tightens the anchor is told by a failing suite to delete the marker.
"""

from __future__ import annotations

import pytest

from headstart.experience import ExperienceSpan, extract, from_description, from_field

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
