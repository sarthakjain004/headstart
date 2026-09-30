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
