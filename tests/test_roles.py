"""Tests for the role-trend taxonomy seam (headstart.roles, ADR-0040, ADR-0220).

Pinned here: band edges (incl. the intern override and the honest "unspecified"), the family list
refusing what would corrupt the ledger, and the family-list fingerprint moving only with the
families themselves.
"""

import json
from pathlib import Path

import pytest

from headstart import roles


def _families(tmp_path, families, name="families.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"families": families}), encoding="utf-8")
    return path


def test_band_edges():
    assert roles.band(None, "Backend Engineer", None) == "unspecified"
    assert roles.band(0, "Engineer", "full_time") == "entry"
    assert roles.band(1, None, None) == "entry"
    assert roles.band(2, None, None) == "mid"
    assert roles.band(4, None, None) == "mid"
    assert roles.band(5, None, None) == "senior"
    assert roles.band(7, None, None) == "senior"
    assert roles.band(8, None, None) == "staff"


def test_band_intern_overrides_years_and_title_wins():
    # interns rarely carry a years figure; the title/employment_type signal wins over both
    # a missing and a present min_years
    assert roles.band(None, "Software Engineering Intern", None) == "intern"
    assert roles.band(3, "SDE Internship", None) == "intern"
    assert roles.band(None, "Engineer", "Internship") == "intern"
    assert (
        roles.band(None, "Internal Tools Engineer", None) == "unspecified"
    )  # not intern


def test_load_families_keeps_the_curated_order(tmp_path):
    path = _families(tmp_path, [{"name": "qa-test"}, {"name": "software-engineering"}])
    assert roles.load_families(path) == ["qa-test", "software-engineering"]


def test_load_families_rejects_a_family_listed_twice(tmp_path):
    path = _families(tmp_path, [{"name": "qa-test"}, {"name": "qa-test"}])
    with pytest.raises(ValueError, match="twice"):
        roles.load_families(path)


def test_load_families_rejects_the_reserved_non_tech_name(tmp_path):
    path = _families(tmp_path, [{"name": roles.NON_TECH}])
    with pytest.raises(ValueError, match="reserved"):
        roles.load_families(path)


def test_family_list_fingerprint_moves_with_the_families_not_their_wording(tmp_path):
    base = _families(
        tmp_path, [{"name": "qa-test", "label": "QA"}, {"name": "devops"}], "a.json"
    )
    reworded = _families(
        tmp_path,
        [
            {"name": "devops", "definition": "delivery"},
            {"name": "qa-test", "label": "Test"},
        ],
        "b.json",
    )
    added = _families(
        tmp_path,
        [{"name": "qa-test"}, {"name": "devops"}, {"name": "mobile"}],
        "c.json",
    )
    assert roles.family_list_fingerprint(base) == roles.family_list_fingerprint(
        reworded
    )
    assert roles.family_list_fingerprint(base) != roles.family_list_fingerprint(added)


def test_the_curated_family_list_loads():
    """The shipped config itself: a broken list skips a pipeline tick, so it fails here first."""
    names = roles.load_families(
        Path(__file__).resolve().parent.parent / "config" / "role_families.json"
    )
    assert "unclassified-tech" in names and "software-engineering" in names
