"""Tests for the source-agnostic corpus reader (ADR-0014)."""

from __future__ import annotations

import json

import pytest

from headstart.corpus import iter_jobs


def _write(path, jobs):
    path.write_text("".join(json.dumps(j) + "\n" for j in jobs), encoding="utf-8")


def test_iter_jobs_jsonl_dir_dedups_by_id(tmp_path):
    # greenhouse: a blank line and a re-emitted g1 (resumed scrape) are both tolerated -> g1 once
    (tmp_path / "greenhouse.jsonl").write_text(
        '{"id": "greenhouse:a:1", "title": "Eng"}\n'
        "\n"
        '{"id": "greenhouse:a:1", "title": "Eng"}\n',
        encoding="utf-8",
    )
    _write(tmp_path / "lever.jsonl", [{"id": "lever:b:9", "title": "SRE"}])
    got = list(iter_jobs(tmp_path))
    assert [j["id"] for j in got] == ["greenhouse:a:1", "lever:b:9"]


def test_iter_jobs_sidecorpus_csv_maps_columns(tmp_path):
    csv_path = tmp_path / "sidecorpus.csv"
    csv_path.write_text(
        "id,ats,company,title,remote,years_experience,job_type,compensation,description\n"
        "sidecorpus:x:1,sidecorpus,Acme,Backend Eng,true,5+,full-time,$120k,Build things\n",
        encoding="utf-8",
    )
    (job,) = list(iter_jobs(csv_path))
    assert job["id"] == "sidecorpus:x:1"
    assert job["remote"] is True  # "true" -> canonical bool
    assert job["experience"] == "5+"  # years_experience -> experience
    assert job["employment_type"] == "full-time"  # job_type -> employment_type
    assert job["salary"] == "$120k"  # compensation -> salary
    assert job["description"] == "Build things"


def test_iter_jobs_rejects_unknown_source(tmp_path):
    bad = tmp_path / "corpus.txt"
    bad.write_text("nope", encoding="utf-8")
    with pytest.raises(ValueError):
        list(iter_jobs(bad))
