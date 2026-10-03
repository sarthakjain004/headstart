"""Input changes are independent of raw listing versions and never come from the future."""

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from headstart.ingest import restate_inputs as ri
from headstart.ingest.restate_inputs import VersionSources, bind_intervals


def _window_file(path, metadata, rows=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    table = (
        pa.Table.from_pylist(rows)
        if rows
        else pa.table({"id": pa.array([], pa.string())})
    )
    pq.write_table(
        table.replace_schema_metadata(
            {k.encode(): v.encode() for k, v in metadata.items()}
        ),
        path,
    )
    return path


def _window_fixture(tmp_path):
    facts, state = tmp_path / "facts", tmp_path / "state"
    base = _window_file(
        facts / "trend_reference/base.parquet", {"ts": "1", "baseline": "true"}
    )
    ref = _window_file(
        facts / "trend_reference/end.parquet",
        {"ts": "4", "previous_tick": "1", "run_id": "run", "run_attempt": "2"},
    )
    _window_file(state / "reference_state.parquet", {"ts": "4"})
    for directory in ("job_facts", "board_reads"):
        for stamp, attempt in (("2", "1"), ("3", "2"), ("5", "3")):
            _window_file(
                facts / directory / f"{stamp}.parquet",
                {"stamp": stamp, "run_id": "run", "run_attempt": attempt},
            )
    _window_file(
        facts / "trend_reference/orphan.parquet",
        {"ts": "2.5", "previous_tick": "1", "run_id": "run", "run_attempt": "1"},
    )
    return facts, state, base, ref


def test_committed_window_ignores_orphans_pending_tail_and_joins_exact_attempt(
    tmp_path,
):
    facts, state, base, ref = _window_fixture(tmp_path)
    window = ri.committed_reference_window(
        facts, state, baseline=base, require_chain=True
    )
    assert window.baseline == base
    assert window.observations == ((base, "1"), (ref, "3"))
    assert window.ticks == ["1", "3"] and window.through == "3"


@pytest.mark.parametrize(
    "failure",
    [
        "missing_attempt",
        "duplicate_attempt",
        "read_disagrees",
        "broken_parent",
        "wrong_baseline",
    ],
)
def test_committed_window_fails_closed_on_invalid_joins_or_chain(tmp_path, failure):
    facts, state, base, ref = _window_fixture(tmp_path)
    if failure == "missing_attempt":
        _window_file(ref, {"ts": "4", "previous_tick": "1", "run_id": "run"})
    elif failure == "duplicate_attempt":
        _window_file(
            facts / "job_facts/duplicate.parquet",
            {"stamp": "2.8", "run_id": "run", "run_attempt": "2"},
        )
    elif failure == "read_disagrees":
        _window_file(
            facts / "board_reads/3.parquet",
            {"stamp": "2.8", "run_id": "run", "run_attempt": "2"},
        )
    elif failure == "broken_parent":
        _window_file(
            ref,
            {
                "ts": "4",
                "previous_tick": "missing",
                "run_id": "run",
                "run_attempt": "2",
            },
        )
    else:
        base = _window_file(tmp_path / "wrong.parquet", {"ts": "0", "baseline": "true"})
    with pytest.raises(ValueError):
        ri.committed_reference_window(facts, state, baseline=base, require_chain=True)


def test_production_requires_checkpoint_but_explicit_fixture_can_fallback(tmp_path):
    base = _window_file(tmp_path / "base.parquet", {"ts": "1", "baseline": "true"})
    with pytest.raises(ValueError, match="committed"):
        ri.committed_reference_window(
            tmp_path / "facts", tmp_path / "state", baseline=base, require_chain=True
        )
    assert not ri.committed_reference_window(
        tmp_path / "facts", tmp_path / "state", baseline=base
    ).committed


def test_reference_and_description_loads_share_window_and_exclude_future_bindings(
    tmp_path, monkeypatch
):
    from headstart.ingest import description_facts as df

    facts, state, base, ref = _window_fixture(tmp_path)
    job_id = "lever:acme:1"
    for path, text, stamp in (
        (base, "Base", "1"),
        (ref, "Committed", "4"),
        (facts / "trend_reference/orphan.parquet", "Orphan", "2.5"),
    ):
        metadata = {
            k.decode(): v.decode() for k, v in pq.read_schema(path).metadata.items()
        }
        _window_file(
            path,
            metadata,
            [
                {
                    "id": job_id,
                    "kind": "present",
                    "vector": [0.25, 0.5],
                    "description": text,
                }
            ],
        )
    monkeypatch.setenv("GITHUB_RUN_ID", "run")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "3")
    # Earlier wall time must not override this observation's future logical run.
    df.record(facts, "lever", [{"id": job_id, "description": "Future"}], [], "0")
    window = ri.committed_reference_window(facts, state, baseline=base)
    with VersionSources(2) as sources:
        ri.load_reference_sources(
            sources, facts, base, state, window=window, through=window.through
        )
        ri.load_description_sources(
            sources, facts, tmp_path / "store", through=window.through
        )
        assert list(sources.events()) == [(job_id, "1"), (job_id, "3")]
        for stamp, text in (("2", "Base"), ("3", "Committed")):
            sources.bind(job_id, stamp)
            assert sources.get((job_id, stamp))[1] == text
        sources.add_batch(
            "5", [{"id": job_id, "vector": [9, 9], "description": "Later"}]
        )
        row = {
            "id": job_id,
            "served_from": "1",
            "served_to": None,
            "ended_as": None,
            "starts_as": "listed",
        }
        pieces = bind_intervals(
            pa.Table.from_pylist(
                [row], schema=pa.schema([(n, pa.string()) for n in row])
            ),
            sources,
            through="3",
        )
        assert pieces["input_from"].to_pylist() == ["1", "3"]


def test_disk_backed_sources_are_exact_and_cache_is_bounded():
    vector = np.array([0.25, 0.5], dtype=np.float16)
    with VersionSources(2) as sources:
        sources.add_batch(
            "baseline",
            (
                {"id": str(i), "vector": vector, "description": f"Historical text {i}"}
                for i in range(300)
            ),
        )
        for i in range(300):
            sources.bind(str(i), "baseline")
            got, text = sources.get((str(i), "baseline"))
            assert got.tobytes() == vector.tobytes()
            assert text == f"Historical text {i}"
        assert len(sources._cache) <= 256
        assert sources.get(("0", "future"), "latest") == "latest"
        assert ("0", "baseline") in sources
        assert ("absent", "baseline") not in sources


def test_source_context_cleans_up_after_failure():
    from pathlib import Path

    import pytest

    sources = VersionSources(2)
    directory = Path(sources._temporary.name)
    with pytest.raises(RuntimeError), sources:
        raise RuntimeError("classifier failed")
    assert not directory.exists()


def test_a_future_description_does_not_replace_an_earlier_version():
    rows = [
        {
            "id": "lever:acme:1",
            "board": "lever:acme",
            "valid_from": "1",
            "served_from": "1",
            "served_to": None,
            "ended_as": None,
            "starts_as": "listed",
        }
    ]
    with VersionSources(2) as sources:
        sources.add_batch(
            "1",
            [
                {
                    "id": rows[0]["id"],
                    "description": "Old description",
                    "vector": [0.25, 0.5],
                }
            ],
        )
        sources.add_batch(
            "3",
            [
                {
                    "id": rows[0]["id"],
                    "description": "New description",
                    "vector": [0.5, 0.5],
                }
            ],
        )
        schema = pa.schema([(name, pa.string()) for name in rows[0]])
        pieces = bind_intervals(
            pa.Table.from_pylist(rows, schema=schema), sources
        ).to_pylist()
        assert [(r["served_from"], r["served_to"]) for r in pieces] == [
            ("1", "3"),
            ("3", None),
        ]
        assert sources.get((rows[0]["id"], "1"))[1] == "Old description"
        assert sources.get((rows[0]["id"], "3"))[1] == "New description"
        assert pieces[1]["starts_as"] == "changed"


def test_later_text_observations_preserve_already_observed_vectors():
    import hashlib

    with VersionSources(2) as sources:
        sources.add_batch(
            "1", [{"id": "lever:acme:1", "vector": [0.25, 0.5], "description": "Old"}]
        )
        sources.add_batch(
            "4",
            [{"id": "lever:acme:1", "vector": [0.75, 0.5], "description": "Future"}],
        )
        sources.add_description(
            "2", "lever:acme:1", hashlib.sha256(b"Old").hexdigest(), "Old"
        )
        sources.add_description(
            "3", "lever:acme:1", hashlib.sha256(b"Changed").hexdigest(), "Changed"
        )
        for stamp, expected in (("2", "Old"), ("3", "Changed")):
            sources.bind("lever:acme:1", stamp)
            vector, text = sources.get(("lever:acme:1", stamp))
            assert np.array_equal(vector, [0.25, 0.5])
            assert text == expected


def test_experience_only_observations_are_input_changes():
    with VersionSources(2) as sources:
        for stamp, years in (("1", 3), ("2", 8)):
            sources.add_batch(
                stamp,
                [
                    {
                        "id": "lever:acme:1",
                        "vector": [0.25, 0.5],
                        "description": None,
                        "min_years": years,
                        "experience_source": "field",
                    }
                ],
            )
        assert list(sources.events()) == [("lever:acme:1", "1"), ("lever:acme:1", "2")]
        sources.bind("lever:acme:1", "2")
        assert sources.observed(("lever:acme:1", "2"))["min_years"] == 8


def test_unknown_inputs_block_latest_store_fallback_and_score_noise_is_not_an_edit():
    job = "lever:acme:1"
    with VersionSources(2) as sources:
        for stamp, logits in (("2", [0.25, 0.5]), ("3", [0.250001, 0.5])):
            sources.add_batch(
                stamp,
                [
                    {
                        "id": job,
                        "title": "Engineer",
                        "description": "Known",
                        "vector": [0.25, 0.5],
                        "title_logits": [0.0, 1.0],
                        "row_logits": logits,
                    }
                ],
                "model",
            )
        assert list(sources.events()) == [(job, "2")]
        sources.bind(job, "1")
        assert sources.get((job, "1"), ([9.0, 9.0], "Future"))[1] is None
        assert np.array_equal(sources.get((job, "1"))[0], np.zeros(2))
        sources.bind(job, "2")
        assert np.array_equal(
            sources.logits((job, "2"), "model", "Engineer")[1], [0.25, 0.5]
        )
        assert sources.logits((job, "2"), "new-model", "Engineer") == (None, None)
