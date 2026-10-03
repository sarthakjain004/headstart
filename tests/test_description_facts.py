"""Immutable identities and superseded Tech-subset description text."""

import hashlib

import pyarrow.parquet as pq
import pytest

from headstart.ingest import description_facts as df


def test_hash_is_exact_utf8_content():
    assert (
        df.description_hash("It’s text.")
        == hashlib.sha256("It’s text.".encode()).hexdigest()
    )
    assert df.description_hash(" text ") != df.description_hash("text")


def test_new_text_has_only_identity_facts_and_retry_is_immutable(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_SHA", "abc")
    records = [{"id": "eightfold:a:1", "description": "New."}]
    df.record(tmp_path, "eightfold", records, [], "2026-10-03T00:00:00+00:00")
    paths = list(tmp_path.rglob("*.parquet"))
    assert len(paths) == 1
    before = paths[0].read_bytes()
    df.record(tmp_path, "eightfold", records, [], "2026-10-03T00:00:00+00:00")
    assert paths[0].read_bytes() == before
    assert list(tmp_path.rglob("*.parquet")) == paths
    assert pq.read_table(paths[0]).to_pylist() == [
        {
            "id": "eightfold:a:1",
            "description_hash": df.description_hash("New."),
            "observed_at": "2026-10-03T00:00:00+00:00",
            "run_id": "123",
            "run_attempt": "2",
            "code_sha": "abc",
        }
    ]
    assert not (tmp_path / "description_archive").exists()


def test_lookup_checks_current_or_archive_and_never_guesses(tmp_path):
    job_id = "eightfold:a:1"
    df.record(
        tmp_path,
        "eightfold",
        [{"id": job_id, "description": "New."}],
        [{"id": job_id, "description": "Old."}],
        "now",
    )
    current = {job_id: "New."}
    assert (
        df.read_description(tmp_path, current, job_id, df.description_hash("New."))
        == "New."
    )
    assert (
        df.read_description(tmp_path, current, job_id, df.description_hash("Old."))
        == "Old."
    )
    assert (
        df.read_description(tmp_path, current, job_id, df.description_hash("Other."))
        is None
    )
    assert (
        df.read_description(tmp_path, {}, job_id, df.description_hash("New.")) is None
    )
    assert (
        df.read_description(
            tmp_path, current, "eightfold:b:1", df.description_hash("Old.")
        )
        is None
    )


def test_empty_record_writes_nothing(tmp_path):
    df.record(tmp_path, "eightfold", [], [], "now")
    assert list(tmp_path.iterdir()) == []


def test_corrupt_archive_is_not_returned_or_overwritten(tmp_path):
    import pyarrow as pa

    old = [{"id": "eightfold:a:1", "description": "Old."}]
    df.record(tmp_path, "eightfold", [], old, "now")
    (path,) = tmp_path.rglob("*.parquet")
    damaged = [
        {
            "id": "eightfold:a:1",
            "description": "Wrong.",
            "description_hash": df.description_hash("Old."),
        }
    ]
    pq.write_table(pa.Table.from_pylist(damaged), path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="hash mismatch"):
        df.read_description(tmp_path, {}, "eightfold:a:1", df.description_hash("Old."))
    with pytest.raises(ValueError, match="immutable description file differs"):
        df.record(tmp_path, "eightfold", [], old, "now")
    assert path.read_bytes() == before
    assert list(tmp_path.rglob("*.tmp")) == []


def test_failed_atomic_archive_leaves_no_partial_file(tmp_path, monkeypatch):
    def fail(table, path, **kwargs):
        path.write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(pq, "write_table", fail)
    with pytest.raises(OSError, match="disk full"):
        df.record(
            tmp_path,
            "eightfold",
            [{"id": "eightfold:a:1", "description": "New."}],
            [{"id": "eightfold:a:1", "description": "Old."}],
            "now",
        )
    assert list(tmp_path.rglob("*.parquet")) == []
    assert list(tmp_path.rglob("*.tmp")) == []
