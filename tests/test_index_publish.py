"""Tests for publishing the LanceDB table with its grace set (headstart.ingest.index_publish).

The property: the table and ``unconfirmed_ids.txt`` land in ONE commit, so the Hub can never
pair a new table with the previous run's grace set (ADR-0083). The commit is captured at
``HfApi.create_commit`` — what is tested is which operations go into it, not the transport.
"""

from __future__ import annotations

import pytest

pytest.importorskip("huggingface_hub")

from huggingface_hub import HfApi

from headstart.ingest import index_publish


def _capture(monkeypatch) -> list[dict]:
    commits: list[dict] = []
    monkeypatch.setattr(HfApi, "create_commit", lambda self, **kw: commits.append(kw))
    return commits


def test_table_and_grace_set_go_up_in_one_commit(tmp_path, monkeypatch):
    commits = _capture(monkeypatch)
    (tmp_path / "data/lancedb/jobs.lance/data").mkdir(parents=True)
    (tmp_path / "data/lancedb/jobs.lance/data/0.lance").write_bytes(b"rows")
    (tmp_path / "data/lancedb/_index_base.json").write_text("{}")
    (tmp_path / "data/state").mkdir(parents=True)
    (tmp_path / "data/state/unconfirmed_ids.txt").write_text("greenhouse:acme:1\n")
    # ADR-0222: the evictions Trends books as Closed ride the table's commit too, so a failed
    # `data/state` upload cannot lose them.
    (tmp_path / "data/state/eviction_queue.tsv").write_text(
        "2026-09-25T06:00:00+00:00\tx\n"
    )
    (tmp_path / "data/state/board_priority.csv").write_text("not this one")

    index_publish.publish("owner/repo", None, tmp_path)

    assert len(commits) == 1
    commit = commits[0]
    assert commit["repo_id"] == "owner/repo" and commit["repo_type"] == "dataset"
    assert sorted(op.path_in_repo for op in commit["operations"]) == [
        "data/lancedb/_index_base.json",
        "data/lancedb/jobs.lance/data/0.lance",
        "data/state/eviction_queue.tsv",
        "data/state/unconfirmed_ids.txt",
    ]


def test_a_missing_grace_set_publishes_the_table_alone(tmp_path, monkeypatch):
    commits = _capture(monkeypatch)
    (tmp_path / "data/lancedb").mkdir(parents=True)
    (tmp_path / "data/lancedb/_index_base.json").write_text("{}")

    index_publish.publish("owner/repo", None, tmp_path)

    assert [op.path_in_repo for op in commits[0]["operations"]] == [
        "data/lancedb/_index_base.json"
    ]
