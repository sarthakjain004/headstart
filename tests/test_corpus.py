"""Tests for the source-agnostic corpus reader (ADR-0014)."""

from __future__ import annotations

import json
import logging

import pytest

from headstart import corpus
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


def test_iter_jobs_rejects_unknown_source(tmp_path):
    bad = tmp_path / "corpus.txt"
    bad.write_text("nope", encoding="utf-8")
    with pytest.raises(ValueError):
        list(iter_jobs(bad))


def test_duplicate_ids_are_counted_and_reported_per_file(tmp_path, caplog):
    """The drop must announce itself, so the next source of duplicates cannot hide.

    `harvest.scrape_all` letting a Board's own list carry an id twice was ~1% of the tech corpus
    across the five runs of 2026-09-16 and nothing said so — this dedupe absorbed it silently while
    its comment blamed a resumed scrape that never happened. Reported per file as each finishes,
    not as an end-of-generator total: a caller that stops early would lose the count entirely.
    """
    (tmp_path / "ats_a.jsonl").write_text(
        '{"id": "a:1"}\n{"id": "a:2"}\n{"id": "a:1"}\n', encoding="utf-8"
    )
    (tmp_path / "ats_b.jsonl").write_text('{"id": "b:1"}\n', encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="headstart.corpus"):
        rows = list(corpus._read_jsonl_dir(tmp_path))

    assert [r["id"] for r in rows] == ["a:1", "a:2", "b:1"]
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "ats_a.jsonl dropped 1 duplicate id(s)" in logged
    assert "ats_b.jsonl" not in logged, "a file with no duplicates must stay quiet"


def test_a_malformed_line_names_its_file_and_line(tmp_path):
    (tmp_path / "x.jsonl").write_text('{"id": "x:a:1"}\n{"id": "x:a:2"\n', "utf-8")
    with pytest.raises(ValueError, match=r"x\.jsonl:2: "):
        list(iter_jobs(tmp_path))
