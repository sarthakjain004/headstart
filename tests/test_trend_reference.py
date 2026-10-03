"""A checkpoint retains unread incumbents and historical edits without full rewrites."""

import json
from pathlib import Path

import lancedb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from headstart.ingest import trend_reference as tr
from headstart.ingest.role_assignments import Placement
from headstart.ingest.role_family_classifier import Cache


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
    assert "max_years" not in pq.read_schema(first).names
    assert "experience_source" not in pq.read_schema(first).names
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


@pytest.mark.parametrize("fingerprint", ["a" * 64, None])
def test_row_logit_roundoff_is_quiet_only_with_known_input_identity(
    tmp_path, fingerprint
):
    table = source(tmp_path, [job()])
    facts = tmp_path / "facts"
    methodology = {"classifier_inputs_fingerprint": fingerprint} if fingerprint else {}
    original = np.array([0.123456789, -0.25], dtype=np.float32)
    rounded = np.nextafter(original, np.float32(np.inf))
    first = tr.capture(
        table,
        {},
        facts,
        "2026-10-02T00:00:00+00:00",
        methodology,
        row_parts={"lever:acme:1": original},
    )
    second = tr.capture(
        table,
        {},
        facts,
        "2026-10-02T01:00:00+00:00",
        methodology,
        row_parts={"lever:acme:1": rounded},
    )
    assert pq.read_table(second).num_rows == (0 if fingerprint else 1)
    retained = pq.read_table(first)
    assert retained.schema.field("row_logits").type.value_type == pa.float32()
    assert retained["row_logits"].to_pylist() == [original.tolist()]
    assert retained["vector_fingerprint"].to_pylist() == [
        "10f189becc7cf227557e11f3999c4d6cbd844eb864a785d0468e6b112c85bc82"
    ]
    if fingerprint:
        third = tr.capture(
            table,
            {},
            facts,
            "2026-10-02T02:00:00+00:00",
            {"classifier_inputs_fingerprint": "b" * 64},
            row_parts={"lever:acme:1": rounded},
        )
        assert pq.read_table(third).num_rows == 1
        assert pq.read_table(third)["row_logits"].to_pylist() == [rounded.tolist()]


@pytest.mark.parametrize(
    "edit",
    [
        "vector",
        "description",
        "max_years",
        "experience_source",
        "title_logits",
        "placement",
    ],
)
def test_known_input_identity_still_records_input_and_placement_edits(tmp_path, edit):
    facts = tmp_path / "facts"
    row = job() | {"max_years": 5, "experience_source": "regex"}
    methodology = {"classifier_inputs_fingerprint": "a" * 64}
    placed = {
        "lever:acme:1": Placement("lever:acme", "software-engineering", "mid", "lever")
    }
    cache = Cache(1, {"engineer": np.array([0.2, 0.3], dtype=np.float32)})
    scores = {"lever:acme:1": np.array([0.1, 0.2], dtype=np.float32)}
    tr.capture(
        source(tmp_path, [row]),
        placed,
        facts,
        "2026-10-02T00:00:00+00:00",
        methodology,
        row_parts=scores,
        title_cache=cache,
    )
    if edit == "vector":
        # A real float32 edit that the archived float16 vector cannot distinguish.
        changed = float(np.nextafter(np.float32(0.1), np.float32(np.inf)))
        assert np.float16(changed) == np.float16(0.1)
        row["vector"] = [changed, 0.2]
    elif edit == "title_logits":
        cache = Cache(1, {"engineer": np.array([0.4, 0.3], dtype=np.float32)})
    elif edit == "placement":
        placed["lever:acme:1"] = Placement("lever:acme", "qa-test", "mid", "lever")
    else:
        row[edit] = {
            "description": "Edited",
            "max_years": 6,
            "experience_source": "field",
        }[edit]
    second = tr.capture(
        source(tmp_path, [row]),
        placed,
        facts,
        "2026-10-02T01:00:00+00:00",
        methodology,
        row_parts=scores,
        title_cache=cache,
    )
    assert pq.read_table(second).num_rows == 1
    assert pq.read_table(second)["row_logits"].to_pylist() == [
        scores["lever:acme:1"].tolist()
    ]


def test_vector_fingerprint_retains_float32_identity_through_half_precision_collision(
    tmp_path,
):
    facts = tmp_path / "facts"
    methodology = {"classifier_inputs_fingerprint": "a" * 64}
    first = tr.capture(
        source(tmp_path, [job(), job("lever:acme:2")]),
        {},
        facts,
        "2026-10-02T00:00:00+00:00",
        methodology,
    )
    before = pq.read_table(first)
    field = before.schema.field("vector_fingerprint")
    assert field.type == pa.string() and field.nullable
    assert json.loads(field.metadata[b"source_columns"]) == ["vector"]
    assert field.metadata[b"encoding"] == b"little-endian-float32-c-order"
    changed = float(np.nextafter(np.float32(0.1), np.float32(np.inf)))
    second = tr.capture(
        source(tmp_path, [job(vector=[changed, 0.2])]),
        {},
        facts,
        "2026-10-02T01:00:00+00:00",
        methodology,
    )
    after = {r["id"]: r for r in pq.read_table(second).to_pylist()}
    original = before.to_pylist()[0]
    assert original["vector_fingerprint"] == (
        "10f189becc7cf227557e11f3999c4d6cbd844eb864a785d0468e6b112c85bc82"
    )
    assert original["vector"] == after["lever:acme:1"]["vector"]
    assert original["vector_fingerprint"] != after["lever:acme:1"]["vector_fingerprint"]
    assert after["lever:acme:2"]["kind"] == "removed"
    assert after["lever:acme:2"]["vector_fingerprint"] is None


@pytest.mark.parametrize("edit", [{"max_years": 6}, {"experience_source": "field"}])
def test_null_description_keeps_provenance_and_provenance_only_edits(tmp_path, edit):
    facts = tmp_path / "facts"
    row = job(description=None) | {"max_years": 5, "experience_source": "regex"}
    placed = {
        "lever:acme:1": Placement("lever:acme", "software-engineering", "mid", "lever")
    }
    first = tr.capture(
        source(tmp_path, [row]), placed, facts, "2026-10-02T00:00:00+00:00", {}
    )
    second = tr.capture(
        source(tmp_path, [row | edit]),
        placed,
        facts,
        "2026-10-02T01:00:00+00:00",
        {},
    )
    assert pq.read_table(second).num_rows == 1
    ticks = list(
        tr.checkpoints(
            facts, ["2026-10-02T00:00:00+00:00", "2026-10-02T01:00:00+00:00"]
        )
    )
    original = ticks[0][1]["lever:acme:1"]
    changed = ticks[1][1]["lever:acme:1"]
    assert original["description"] is None
    assert original["min_years"] == 3
    assert original["max_years"] == 5
    assert original["experience_source"] == "regex"
    assert changed["description"] is None
    assert changed["min_years"] == 3
    assert changed["max_years"] == edit.get("max_years", 5)
    assert changed["experience_source"] == edit.get("experience_source", "regex")
    assert pq.read_table(first)["experience_source"].to_pylist() == ["regex"]


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
    live = ["2026-10-02T00:00:00+00:00", "2026-10-02T01:00:00+00:00"]
    ticks = list(tr.checkpoints(facts, live))
    assert ticks[0][1]["lever:acme:1"]["description"] == "Old description"
    assert ticks[1][1]["lever:acme:1"]["description"] == "Edited"
    first.unlink()
    with pytest.raises(ValueError, match="parent chain"):
        list(tr.checkpoints(facts, live))


def test_missing_middle_tick_is_not_a_quiet_tick(tmp_path):
    facts = tmp_path / "facts"
    tr.capture(source(tmp_path, [job()]), {}, facts, "2026-10-02T00:00:00+00:00", {})
    tr.capture(source(tmp_path, [job()]), {}, facts, "2026-10-02T02:00:00+00:00", {})
    with pytest.raises(ValueError, match="coverage"):
        list(tr.checkpoints(facts, [f"2026-10-02T0{i}:00:00+00:00" for i in range(3)]))


def test_unpublished_orphan_tail_does_not_advance_replay(tmp_path):
    facts = tmp_path / "facts"
    first = tr.capture(
        source(tmp_path, [job()]), {}, facts, "2026-10-02T00:00:00+00:00", {}
    )
    table = pq.read_table(first)
    metadata = table.schema.metadata | {
        b"ts": b"2026-10-02T01:00:00+00:00",
        b"previous_tick": b"2026-10-02T00:00:00+00:00",
        b"baseline": b"false",
    }
    pq.write_table(
        table.replace_schema_metadata(metadata), facts / tr.DIRECTORY / "orphan.parquet"
    )
    ticks = list(tr.checkpoints(facts, ["2026-10-02T00:00:00+00:00"]))
    assert len(ticks) == 1


def test_inherited_id_can_be_unlisted_by_a_future_complete_read(tmp_path):
    from headstart.ingest import job_facts

    facts = tmp_path / "facts"
    facts.mkdir()
    schema = pa.schema(
        [("id", pa.string()), ("board", pa.string()), ("fields_hash", pa.int64())]
    )
    pq.write_table(
        pa.Table.from_pylist([], schema=schema), facts / job_facts.LISTED_JOBS
    )
    baseline = tr.capture(
        source(tmp_path, [job()]), {}, facts, "2026-10-02T00:00:00+00:00", {}
    )
    live = {"lever:acme": "lever:acme"}
    assert tr.inherit_listed(facts, baseline, live) == 1
    scratch = job_facts.ScrapedLines(facts / job_facts.SCRAPED_LINES)
    scope = job_facts.RunScope(
        authoritative=frozenset({"lever:acme"}), keep_set=None, live=live
    )
    result = job_facts.record_run(
        scratch.close(), facts, "2026-10-02T01:00:00+00:00", [], scope
    )
    assert result.unlisted == 1
