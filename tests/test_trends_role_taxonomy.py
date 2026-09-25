"""Tests for the role-trend taxonomy seam (headstart.trends.role_taxonomy, ADR-0040, ADR-0220).

Pinned here: band edges (incl. the intern override and the honest "unspecified"), the family list
refusing what would corrupt the ledger, and the family-list fingerprint moving only with the
families themselves.
"""

import json
from pathlib import Path

import pytest

from headstart.trends import role_taxonomy


def _families(tmp_path, families, name="families.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"families": families}), encoding="utf-8")
    return path


def test_band_edges():
    assert role_taxonomy.band(None, "Backend Engineer", None) == "unspecified"
    assert role_taxonomy.band(0, "Engineer", "full_time") == "entry"
    assert role_taxonomy.band(1, None, None) == "entry"
    assert role_taxonomy.band(2, None, None) == "mid"
    assert role_taxonomy.band(4, None, None) == "mid"
    assert role_taxonomy.band(5, None, None) == "senior"
    assert role_taxonomy.band(7, None, None) == "senior"
    assert role_taxonomy.band(8, None, None) == "staff"


def test_band_intern_overrides_years_and_title_wins():
    # interns rarely carry a years figure; the title/employment_type signal wins over both
    # a missing and a present min_years
    assert role_taxonomy.band(None, "Software Engineering Intern", None) == "intern"
    assert role_taxonomy.band(3, "SDE Internship", None) == "intern"
    assert role_taxonomy.band(None, "Engineer", "Internship") == "intern"
    assert (
        role_taxonomy.band(None, "Internal Tools Engineer", None) == "unspecified"
    )  # not intern


def test_load_families_keeps_the_curated_order(tmp_path):
    path = _families(tmp_path, [{"name": "qa-test"}, {"name": "software-engineering"}])
    assert role_taxonomy.load_families(path) == ["qa-test", "software-engineering"]


def test_load_families_rejects_a_family_listed_twice(tmp_path):
    path = _families(tmp_path, [{"name": "qa-test"}, {"name": "qa-test"}])
    with pytest.raises(ValueError, match="twice"):
        role_taxonomy.load_families(path)


def test_a_malformed_taxonomy_file_is_a_value_error_naming_it(tmp_path):
    # `role_trends` catches ValueError into a named ERROR; a bare KeyError or decode error
    # escaped it with neither the file nor the role.
    broken = tmp_path / "families.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="families.json: unreadable family list"):
        role_taxonomy.load_families(broken)
    no_name = _families(tmp_path, [{"label": "QA"}])
    with pytest.raises(ValueError, match="unreadable family list"):
        role_taxonomy.load_families(no_name)
    watchlist = tmp_path / "watch.json"
    watchlist.write_text(
        json.dumps({"roles": [{"name": "rust", "parent": "qa-test"}]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match=r"watch.json: watch role 'rust' is missing"):
        role_taxonomy.load_watchlist(watchlist, {"qa-test"})
    watchlist.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable watchlist"):
        role_taxonomy.load_watchlist(watchlist, {"qa-test"})


def test_load_families_rejects_the_reserved_non_tech_name(tmp_path):
    path = _families(tmp_path, [{"name": role_taxonomy.NON_TECH}])
    with pytest.raises(ValueError, match="reserved"):
        role_taxonomy.load_families(path)


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
    assert role_taxonomy.family_list_fingerprint(
        base
    ) == role_taxonomy.family_list_fingerprint(reworded)
    assert role_taxonomy.family_list_fingerprint(
        base
    ) != role_taxonomy.family_list_fingerprint(added)


def test_the_curated_family_list_loads():
    """The shipped config itself: a broken list skips a pipeline tick, so it fails here first."""
    names = role_taxonomy.load_families(
        Path(__file__).resolve().parent.parent / "config" / "role_families.json"
    )
    assert "unclassified-tech" in names and "software-engineering" in names
