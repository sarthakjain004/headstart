"""Tests for the role-trend taxonomy seam (headstart.roles, ADR-0040).

The banding and the centroid round-trip are the contract the one-off fit and the per-run
trends step must both honor, so they're pinned here: band edges (incl. the intern override
and the honest "unspecified"), cosine assignment, and save/load byte-fidelity.
"""

import pytest

np = pytest.importorskip("numpy")  # CI's quality job installs base deps only

from headstart import roles  # noqa: E402


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


def test_assign_picks_nearest_centroid():
    centroids = np.eye(3, dtype=np.float32)  # three orthogonal unit families
    vectors = np.array(
        [[0.9, 0.1, 0.0], [0.0, 0.2, 0.9], [0.1, 0.8, 0.1]], dtype=np.float32
    )
    assert roles.assign(vectors, centroids).tolist() == [0, 2, 1]


def test_save_load_round_trip(tmp_path):
    centroids = np.random.default_rng(0).random((4, 8)).astype(np.float32)
    manifest = {"version": 1, "k": 4, "dim": 8, "clusters": []}
    roles.save(tmp_path / "rc", centroids, manifest)
    loaded, m = roles.load(tmp_path / "rc")
    assert np.array_equal(loaded, centroids)
    assert m["version"] == 1 and m["k"] == 4


def _manifest(k=3, version=1):
    return {"version": version, "k": k, "dim": 4}


def _spec(tmp_path, spec):
    import json

    path = tmp_path / "families.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def test_load_families_maps_clusters_and_marks_non_tech(tmp_path):
    path = _spec(
        tmp_path,
        {
            "centroid_version": 1,
            "families": [{"name": "software-engineering", "clusters": [0, 1]}],
            "non_tech": {"clusters": [2]},
        },
    )
    assert roles.load_families(path, _manifest()) == {
        0: "software-engineering",
        1: "software-engineering",
        2: None,  # non-tech: counted as a diagnostic, never charted
    }


def test_load_families_rejects_an_unmapped_cluster(tmp_path):
    # the silent failure this guards: cluster 2's rows would vanish from every chart
    path = _spec(
        tmp_path,
        {
            "centroid_version": 1,
            "families": [{"name": "software-engineering", "clusters": [0, 1]}],
            "non_tech": {"clusters": []},
        },
    )
    with pytest.raises(ValueError, match=r"unmapped"):
        roles.load_families(path, _manifest())


def test_load_families_rejects_a_stale_centroid_version(tmp_path):
    # a map curated against another fit would label rows with the wrong fit's families
    path = _spec(
        tmp_path,
        {
            "centroid_version": 1,
            "families": [{"name": "x", "clusters": [0, 1, 2]}],
            "non_tech": {"clusters": []},
        },
    )
    with pytest.raises(ValueError, match=r"centroid version"):
        roles.load_families(path, _manifest(version=2))


def test_load_families_rejects_a_double_mapped_cluster(tmp_path):
    path = _spec(
        tmp_path,
        {
            "centroid_version": 1,
            "families": [
                {"name": "a", "clusters": [0, 1]},
                {"name": "b", "clusters": [1, 2]},
            ],
            "non_tech": {"clusters": []},
        },
    )
    with pytest.raises(ValueError, match=r"mapped twice"):
        roles.load_families(path, _manifest())


def test_load_families_rejects_the_reserved_non_tech_name(tmp_path):
    # a family so named would collide with the diagnostic series in the ledger
    path = _spec(
        tmp_path,
        {
            "centroid_version": 1,
            "families": [{"name": roles.NON_TECH, "clusters": [0, 1, 2]}],
            "non_tech": {"clusters": []},
        },
    )
    with pytest.raises(ValueError, match=r"reserved"):
        roles.load_families(path, _manifest())
