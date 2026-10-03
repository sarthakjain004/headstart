"""Tests for placing served intervals in a family and band (headstart.ingest.restate_place,
ADR-0330).

The family is decided as `role_trends` decides it, from the cached title logits and the Job's
description vector; a Job with no vector is decided from its title alone. The band reads the
`min_years` the derivations find in the version's raw fields and description.
"""

from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")
pa = pytest.importorskip("pyarrow")

from headstart.ingest import restate_place as rp
from headstart.ingest import role_family_classifier as rfc

FAMILIES = ["software-engineering", "qa-test", "non-tech"]


def _head(tmp_path) -> rfc.Head:
    """Row part: the second coordinate pulls hard towards non-tech; the first is neutral."""
    directory = tmp_path / "head"
    directory.mkdir()
    row_weights = np.zeros((3, 2), np.float32)
    row_weights[2, 1] = 30.0
    np.savez(
        directory / "head.npz",
        title_weights=np.eye(3, 3, dtype=np.float32),
        row_weights=row_weights,
        bias=np.zeros(3, np.float32),
    )
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "version": 7,
                "model": "stub",
                "model_revision": "stub",
                "row_vector": {"model": "stub", "dim": 2},
                "families": FAMILIES,
                "cutoff": 0.6,
            }
        ),
        encoding="utf-8",
    )
    return rfc.Head(directory)


def _cache() -> rfc.Cache:
    return rfc.Cache(
        7,
        {
            rfc.normalise("Backend Engineer"): np.array([10.0, 0.0, 0.0], np.float32),
            rfc.normalise("QA Lead"): np.array([0.0, 10.0, 0.0], np.float32),
            rfc.normalise("Engineering Intern"): np.array([10.0, 0.0, 0.0], np.float32),
        },
    )


def _served(*rows: dict):
    base = {
        "board": "greenhouse:acme",
        "department": None,
        "location": None,
        "remote": None,
        "experience": None,
        "employment_type": None,
        "salary": None,
    }
    return pa.Table.from_pylist([base | row for row in rows])


def _place(tmp_path, served, vectors=None, descriptions=None):
    placed = rp.placements(
        served, _head(tmp_path), _cache(), vectors or {}, descriptions or {}
    )
    return {row["id"]: rp.place_of(row) for row in placed.to_pylist()}


def test_baseline_uses_its_own_vector_and_description_instead_of_latest_inputs(
    tmp_path,
):
    job_id = "greenhouse:acme:1"
    served = _served(
        {"id": job_id, "title": "Backend Engineer", "valid_from": "2026-10-02"}
    )
    placed = rp.placements(
        served,
        _head(tmp_path),
        _cache(),
        {job_id: np.array([0, 1], np.float32)},
        {job_id: "Requires 8 years of experience."},
        version_sources={
            (job_id, "2026-10-02"): (
                np.zeros(2, np.float16),
                "Requires 3 years of experience.",
            )
        },
    )
    assert rp.place_of(placed.to_pylist()[0]) == ("software-engineering", "mid")


@pytest.mark.parametrize("experience, expected", [(None, "mid"), ("1+ years", "entry")])
def test_missing_historical_text_retains_observed_years_but_real_raw_field_wins(
    tmp_path, experience, expected
):
    from headstart.ingest.restate_inputs import VersionSources

    job_id, stamp = "greenhouse:acme:1", "2026-10-02"
    served = _served(
        {
            "id": job_id,
            "title": "Backend Engineer",
            "valid_from": stamp,
            "input_from": stamp,
            "experience": experience,
        }
    )
    with VersionSources(2) as sources:
        sources.add_batch(
            stamp,
            [
                {
                    "id": job_id,
                    "vector": [0.0, 0.0],
                    "description": None,
                    "min_years": 4,
                    "experience_source": "regex",
                }
            ],
        )
        sources.bind(job_id, stamp)
        row = rp.placements(
            served, _head(tmp_path), _cache(), {}, {}, version_sources=sources
        ).to_pylist()[0]
        assert row["band"] == expected
        assert ("observed_experience_retained" in row["input_quality"]) == (
            experience is None
        )


def test_the_family_reads_the_title_and_the_description_vector(tmp_path):
    served = _served(
        {"id": "greenhouse:acme:1", "title": "Backend Engineer"},
        {"id": "greenhouse:acme:2", "title": "QA Lead"},
        {"id": "greenhouse:acme:3", "title": "Backend Engineer"},
    )
    vectors = {
        "greenhouse:acme:1": [1.0, 0.0],
        "greenhouse:acme:2": [1.0, 0.0],
        "greenhouse:acme:3": [0.0, 1.0],  # a description that is not tech work
    }

    placed = _place(tmp_path, served, vectors)

    assert placed["greenhouse:acme:1"][0] == "software-engineering"
    assert placed["greenhouse:acme:2"][0] == "qa-test"
    assert placed["greenhouse:acme:3"] is None


def test_a_job_with_no_vector_is_decided_from_its_title_alone(tmp_path):
    placed = _place(tmp_path, _served({"id": "greenhouse:acme:1", "title": "QA Lead"}))

    assert placed["greenhouse:acme:1"][0] == "qa-test"


def test_a_title_no_run_has_encoded_is_unclassified(tmp_path):
    placed = _place(
        tmp_path, _served({"id": "greenhouse:acme:1", "title": "Platypus Wrangler"})
    )

    assert placed["greenhouse:acme:1"][0] == rfc.UNCLASSIFIED


@pytest.mark.parametrize(
    ("row", "description", "band"),
    [
        ({"title": "Backend Engineer", "experience": "5+ years"}, None, "senior"),
        (
            {"title": "Backend Engineer"},
            "You bring 2 years of experience with Go.",
            "mid",
        ),
        ({"title": "Engineering Intern"}, None, "intern"),
        ({"title": "Backend Engineer"}, None, "unspecified"),
    ],
)
def test_the_band_reads_the_derived_years(tmp_path, row, description, band):
    job_id = "greenhouse:acme:1"
    placed = _place(
        tmp_path,
        _served({"id": job_id, **row}),
        {job_id: [1.0, 0.0]},
        {job_id: description} if description else {},
    )

    assert placed[job_id][1] == band


def test_placement_batches_equal_whole_matrix_with_version_sources(
    tmp_path, monkeypatch
):
    head = _head(tmp_path)
    served = _served(
        *[
            {
                "id": f"greenhouse:acme:{i}",
                "title": "Backend Engineer",
                "valid_from": "2026-10-02",
            }
            for i in range(7)
        ]
    )
    sources = {
        ("greenhouse:acme:2", "2026-10-02"): (
            np.array([0, 1], np.float16),
            "Requires 3 years of experience.",
        )
    }
    expected = rp.placements(served, head, _cache(), {}, {}, version_sources=sources)
    sizes = []
    original = head.row_logits

    def bounded(matrix):
        sizes.append(len(matrix))
        assert len(matrix) <= 2
        return original(matrix)

    monkeypatch.setattr(head, "row_logits", bounded)
    actual = rp.placements(
        served, head, _cache(), {}, {}, version_sources=sources, batch_size=2
    )
    assert actual.equals(expected)
    assert sizes == [2, 2, 2, 1]
