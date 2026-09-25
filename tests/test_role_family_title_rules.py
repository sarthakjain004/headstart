"""Tests for the title rules that label training titles for the role-family classifier
(``scripts/embed/role_family_title_rules.py``, ADR-0220).

Contracts: a specific cue decides its taxonomy-v3 family; precedence settles a title naming two;
a negative rule makes a row non-tech unless a strong cue also matched; a title naming nothing is
no training example; and every family a rule names is on the curated list.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from headstart.trends import role_taxonomy

_REPO = Path(__file__).resolve().parent.parent


def _load_rules():
    path = _REPO / "scripts" / "embed" / "role_family_title_rules.py"
    spec = importlib.util.spec_from_file_location("role_family_title_rules", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = (
        module  # a dataclass resolves its module through sys.modules
    )
    spec.loader.exec_module(module)
    return module


rules = _load_rules()


@pytest.mark.parametrize(
    ("title", "family", "tier"),
    [
        ("Senior Data Engineer", "data-engineering", "sole-cue"),
        ("Machine Learning Engineer", "ai-ml-data-science", "sole-cue"),
        ("Data Scientist", "ai-ml-data-science", "sole-cue"),
        ("Frontend Engineer", "frontend-web", "sole-cue"),
        ("Senior DBA", "database-administration", "sole-cue"),
        ("Firmware Engineer", "embedded-firmware", "sole-cue"),
        ("Technical Program Manager", "program-project-management", "sole-cue"),
        ("Senior Software Engineer", "software-engineering", "generic-software"),
        # a language is no family: Java and Full Stack developers are software engineers
        ("Java Developer", "software-engineering", "sole-cue"),
        ("Full Stack Developer", "software-engineering", "sole-cue"),
        ("React Developer", "frontend-web", "precedence"),
        # a specialty outranks a language and a level
        ("QA Tech Lead (Python)", "qa-test", "precedence"),
        # a vendor platform is a strong cue, so the construction word does not veto it
        ("SAP Consultant - Construction", "enterprise-applications", "sole-cue"),
    ],
)
def test_a_title_cue_decides_its_family(title, family, tier):
    decision = rules.classify(title)
    assert (decision.family, decision.tier) == (family, tier)


@pytest.mark.parametrize(
    ("title", "rule"),
    [
        ("Front End Clerk", "retail-front-end"),
        ("Bridge Engineer", "civil-building-utilities"),
        # a weak cue (engineering manager) does not survive a discipline word
        ("Engineering Manager - Substation", "civil-building-utilities"),
        ("Hotel Chief Engineer", "facilities-hospitality"),
        ("Account Executive", "sales-marketing-business"),
    ],
)
def test_a_negative_rule_decides_non_tech(title, rule):
    assert rules.classify(title) == (role_taxonomy.NON_TECH, "negative", rule)


@pytest.mark.parametrize("title", ["Engineer II", "", None])
def test_a_title_naming_nothing_is_no_training_example(title):
    assert rules.classify(title) == (None, None, None)


def test_every_family_a_rule_names_is_on_the_curated_list():
    """The trainer's own check, held in CI too, so a rule naming a new family fails here."""
    rules.check_families(
        set(role_taxonomy.load_families(_REPO / "config" / "role_families.json"))
    )


def test_a_family_missing_from_the_list_is_refused():
    with pytest.raises(ValueError, match="data-engineering"):
        rules.check_families({"software-engineering"})
