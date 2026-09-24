"""Tests for the title rules that decide a row's role family first (ADR-0215).

Contracts: a specific cue decides its family; precedence settles a title naming two; a negative
rule makes a row non-tech unless a strong cue also matched; a title naming nothing is left to the
centroid; every family a rule names exists in the curated map; and the fingerprint moves with what
the rules decide, not with how a rule is named.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest

from headstart import roles
from headstart.ingest import role_family_rules

_REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("title", "family", "tier"),
    [
        ("Senior Data Engineer", "data-engineering", "sole-cue"),
        ("Machine Learning Engineer", "ai-ml", "sole-cue"),
        ("Frontend Engineer", "web-development", "sole-cue"),
        ("Senior Software Engineer", "software-engineering", "generic-software"),
        # a language outranks the generic "developer"
        ("Java Developer", "java-development", "precedence"),
        # a specialty outranks a language and a level
        ("QA Tech Lead (Python)", "qa-test", "precedence"),
        # a vendor platform is a strong cue, so the construction word does not veto it
        ("SAP Consultant - Construction", "enterprise-platform", "sole-cue"),
        (
            "Software Engineer - Manufacturing Systems",
            "software-engineering",
            "generic-software",
        ),
    ],
)
def test_a_title_cue_decides_its_family(title, family, tier):
    decision = role_family_rules.classify(title)
    assert (decision.family, decision.tier) == (family, tier)


@pytest.mark.parametrize(
    ("title", "rule"),
    [
        # the grocery clerks that filled the Frontend watch line
        ("Front End Clerk", "retail-front-end"),
        ("Bridge Engineer", "civil-building-utilities"),
        # a weak cue (engineering manager) does not survive a discipline word
        ("Engineering Manager - Substation", "civil-building-utilities"),
        ("Hotel Chief Engineer", "facilities-hospitality"),
        ("Account Executive", "sales-marketing-business"),
    ],
)
def test_a_negative_rule_decides_non_tech(title, rule):
    assert role_family_rules.classify(title) == (roles.NON_TECH, "negative", rule)


@pytest.mark.parametrize("title", ["Engineer II", "", None])
def test_a_title_naming_nothing_is_left_to_the_centroid(title):
    assert role_family_rules.classify(title) == (None, None, None)


def test_every_family_a_rule_names_is_in_the_curated_map():
    """The production contract `role_trends` checks at startup, held in CI too so a rule edit
    that names a new family fails here rather than skipping a pipeline tick."""
    spec = json.loads((_REPO / "config" / "role_families.json").read_text("utf-8"))
    role_family_rules.check_families({family["name"] for family in spec["families"]})


def test_a_family_missing_from_the_map_is_refused():
    with pytest.raises(ValueError, match="data-engineering"):
        role_family_rules.check_families({"software-engineering"})


def test_the_fingerprint_moves_with_a_pattern_but_not_with_a_rule_name(monkeypatch):
    before = role_family_rules.fingerprint()
    first, *rest = role_family_rules._CUES

    renamed = dataclasses.replace(first, name="renamed")
    monkeypatch.setattr(role_family_rules, "_CUES", (renamed, *rest))
    assert role_family_rules.fingerprint() == before

    widened = dataclasses.replace(
        first, pattern=re.compile(first.pattern.pattern + "|x")
    )
    monkeypatch.setattr(role_family_rules, "_CUES", (widened, *rest))
    assert role_family_rules.fingerprint() != before
