"""A checkpoint retains unread incumbents and historical edits without full rewrites."""

from pathlib import Path

import lancedb
import pyarrow.parquet as pq
import pytest

from headstart.ingest import trend_reference as tr
from headstart.ingest.role_assignments import Placement


def source(tmp_path, rows):
    return lancedb.connect(tmp_path / "db").create_table("jobs", rows, mode="overwrite")


def job(job_id="lever:acme:1", description="Old description", vector=None):
    return {
        "id": job_id,
        "ats": "lever",
        "title": "Engineer",
        "first_seen": "2026-09-01",
        "description": description,
        "min_years": 3,
        "vector": vector or [0.1, 0.2],
    }


def test_baseline_retains_a_job_not_seen_in_new_scrapes_and_quiet_ticks_are_empty(
    tmp_path,
):
    table = source(tmp_path, [job()])
    placed = {
        "lever:acme:1": Placement("lever:acme", "software-engineering", "mid", "lever")
    }
    first = tr.capture(
        table, placed, tmp_path / "facts", "2026-10-02T00:00:00+00:00", {}
    )
    assert pq.read_schema(first).metadata[b"baseline"] == b"true"
    assert pq.read_table(first)["first_seen"].to_pylist() == ["2026-09-01"]
    second = tr.capture(
        table, placed, tmp_path / "facts", "2026-10-02T01:00:00+00:00", {}
    )
    assert pq.read_table(second).num_rows == 0
    assert pq.read_schema(second).metadata[b"baseline"] == b"false"


def test_edit_and_removal_preserve_the_original_inputs(tmp_path):
    facts = tmp_path / "facts"
    first = tr.capture(
        source(tmp_path, [job(), job("lever:acme:2")]),
        {},
        facts,
        "2026-10-02T00:00:00+00:00",
        {},
    )
    second = tr.capture(
        source(tmp_path, [job(description="Edited", vector=[0.3, 0.4])]),
        {},
        facts,
        "2026-10-02T01:00:00+00:00",
        {},
    )
    assert pq.read_table(first)["description"].to_pylist() == [
        "Old description",
        "Old description",
    ]
    rows = pq.read_table(second).to_pylist()
    assert {(r["id"], r["kind"]) for r in rows} == {
        ("lever:acme:1", "present"),
        ("lever:acme:2", "removed"),
    }
    assert rows[0]["description"] == "Edited"


def test_failed_checkpoint_does_not_advance_the_state(tmp_path, monkeypatch):
    table = source(tmp_path, [job()])
    facts = tmp_path / "facts"
    tr.capture(table, {}, facts, "2026-10-02T00:00:00+00:00", {})
    before = (facts / tr.STATE).read_bytes()
    original = Path.replace

    def fail_state(self, target):
        if target == facts / tr.STATE:
            raise OSError("disk full")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", fail_state)
    with pytest.raises(OSError):
        tr.capture(table, {}, facts, "2026-10-02T01:00:00+00:00", {})
    assert (facts / tr.STATE).read_bytes() == before
    assert not (facts / tr.DIRECTORY / "2026-10-02T01-00-00+00-00.parquet").exists()


def test_fingerprint_changes_with_company_list_and_retains_exact_inputs(tmp_path):
    import zipfile

    root = tmp_path / "repo"
    (root / "data/validate").mkdir(parents=True)
    (root / "pyproject.toml").write_text("dependencies = []")
    ledger = root / "data/validate/boards.csv"
    ledger.write_text("acme")
    first = tr.freeze_rules(root, tmp_path / "facts")
    ledger.write_text("acme,globex")
    second = tr.freeze_rules(root, tmp_path / "facts")
    assert first != second
    with zipfile.ZipFile(
        tmp_path / "facts/reference_rules" / f"{first}.zip"
    ) as archive:
        assert archive.read("data/validate/boards.csv") == b"acme"


def test_replay_requires_its_baseline_and_preserves_each_edit(tmp_path):
    facts = tmp_path / "facts"
    first = tr.capture(
        source(tmp_path, [job()]), {}, facts, "2026-10-02T00:00:00+00:00", {}
    )
    tr.capture(
        source(tmp_path, [job(description="Edited")]),
        {},
        facts,
        "2026-10-02T01:00:00+00:00",
        {},
    )
    ticks = list(tr.checkpoints(facts))
    assert ticks[0][1]["lever:acme:1"]["description"] == "Old description"
    assert ticks[1][1]["lever:acme:1"]["description"] == "Edited"
    first.unlink()
    with pytest.raises(ValueError, match="parent chain"):
        list(tr.checkpoints(facts))
