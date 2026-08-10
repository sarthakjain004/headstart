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
