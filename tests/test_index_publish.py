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
    # ADR-0227: the evictions Trends books as Closed ride the table's commit too, so a failed
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


def test_a_missing_grace_set_warns_and_the_success_line_names_only_what_went_up(
    tmp_path, monkeypatch, caplog
):
    _capture(monkeypatch)
    (tmp_path / "data/lancedb").mkdir(parents=True)
    (tmp_path / "data/lancedb/_index_base.json").write_text("{}")
    (tmp_path / "data/state").mkdir(parents=True)
    (tmp_path / "data/state/eviction_queue.tsv").write_text("")

    with caplog.at_level("INFO", logger="headstart.ingest.index_publish"):
        index_publish.publish("owner/repo", None, tmp_path)

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "unconfirmed_ids.txt absent" in warnings[0]
    published = next(
        r.getMessage() for r in caplog.records if "published" in r.getMessage()
    )
    assert "eviction_queue.tsv" in published and "unconfirmed" not in published


def test_superseded_search_indexes_are_left_out_and_deleted(tmp_path, monkeypatch):
    """ADR-0244. `refresh-indexes` replaces every index each run, and the old ones stayed on the
    Hub: 374 of 391 index directories, 8.9 GB, that the latest version never reads. The commit
    leaves them out and deletes their remote files; the table still opens and serves each index
    from what remains."""
    lancedb = pytest.importorskip("lancedb")
    import shutil

    db = lancedb.connect(str(tmp_path / "data/lancedb"))
    table = db.create_table(
        "jobs", data=[{"id": f"x{i}", "ats": "a" if i % 2 else "b"} for i in range(300)]
    )
    table.create_scalar_index("ats")
    indices = tmp_path / "data/lancedb/jobs.lance/_indices"
    first = {d.name for d in indices.iterdir()}
    table.create_scalar_index("ats", replace=True)
    (old,) = first
    remote_old = f"data/lancedb/jobs.lance/_indices/{old}/page_data.lance"
    monkeypatch.setattr(
        HfApi,
        "list_repo_files",
        lambda self, repo, repo_type=None: [remote_old, "data/state/board_cost.csv"],
    )
    commits = _capture(monkeypatch)

    index_publish.publish("owner/repo", None, tmp_path)

    ops = commits[0]["operations"]
    deleted = [
        op.path_in_repo for op in ops if type(op).__name__ == "CommitOperationDelete"
    ]
    added = [op.path_in_repo for op in ops if type(op).__name__ == "CommitOperationAdd"]
    assert deleted == [remote_old]
    assert not [p for p in added if f"/_indices/{old}/" in p]
    assert [p for p in added if "/_indices/" in p], "the live index still goes up"

    shutil.rmtree(indices / old)
    reopened = lancedb.connect(str(tmp_path / "data/lancedb")).open_table("jobs")
    assert reopened.search().where("ats = 'a'").limit(500).to_arrow().num_rows == 150
    assert reopened.index_stats("ats_idx").num_unindexed_rows == 0


def test_an_unreadable_table_deletes_no_index(tmp_path, monkeypatch):
    """A table the manifest read fails on keeps every index: a publish never deletes on a read it
    could not make."""
    pytest.importorskip("lance")
    (tmp_path / "data/lancedb/jobs.lance/_indices/abc").mkdir(parents=True)
    (tmp_path / "data/lancedb/jobs.lance/_indices/abc/page_data.lance").write_bytes(
        b"x"
    )
    monkeypatch.setattr(
        HfApi,
        "list_repo_files",
        lambda self, repo, repo_type=None: pytest.fail("listed"),
    )
    commits = _capture(monkeypatch)

    index_publish.publish("owner/repo", None, tmp_path)

    assert [op.path_in_repo for op in commits[0]["operations"]] == [
        "data/lancedb/jobs.lance/_indices/abc/page_data.lance"
    ]
