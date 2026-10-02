"""An unread Job must survive bootstrap; a later edit must replace its inherited fields."""

import pyarrow as pa

from headstart.ingest.restate_baseline import seed_versions


def test_disk_backed_sources_are_exact_and_cache_is_bounded():
    import numpy as np

    from headstart.ingest.restate_baseline import BaselineSources

    sources = BaselineSources("baseline")
    try:
        vector = np.array([0.25, 0.5], dtype=np.float16)
        sources.add_batch((str(i), vector, f"Historical text {i}") for i in range(300))
        for i in range(300):
            got, text = sources.get((str(i), "baseline"))
            assert got.tobytes() == vector.tobytes()
            assert text == f"Historical text {i}"
        assert len(sources._cache) <= 256
        assert sources.get(("0", "future"), "latest") == "latest"
        assert ("0", "baseline") in sources
        assert ("absent", "baseline") not in sources
        assert sources.get(("0", "baseline"))[1] == "Historical text 0"
    finally:
        sources.close()


def test_baseline_source_context_cleans_up_after_failure():
    from pathlib import Path

    import pytest

    from headstart.ingest.restate_baseline import BaselineSources

    sources = BaselineSources("baseline")
    directory = Path(sources._temporary.name)
    with pytest.raises(RuntimeError), sources:
        raise RuntimeError("classifier failed")
    assert not directory.exists()


def test_unread_baseline_job_is_not_lost_and_future_edit_replaces_it():
    schema = pa.schema(
        [
            (n, pa.string())
            for n in ("id", "board", "title", "valid_from", "valid_to", "ended_as")
        ]
    )
    versions = pa.Table.from_pylist(
        [
            {
                "id": "lever:acme:1",
                "board": "lever:acme",
                "title": "New title",
                "valid_from": "2026-10-03",
                "valid_to": None,
                "ended_as": None,
            }
        ],
        schema=schema,
    )
    baseline = {
        "lever:acme:1": {"reference_board": "lever:acme", "title": "Old title"},
        "lever:acme:2": {"reference_board": "lever:acme", "title": "Unread job"},
    }
    rows = seed_versions(versions, baseline, "2026-10-02", []).to_pylist()
    assert [(r["id"], r["title"], r["valid_to"]) for r in rows] == [
        ("lever:acme:1", "Old title", "2026-10-03"),
        ("lever:acme:1", "New title", None),
        ("lever:acme:2", "Unread job", None),
    ]


def test_first_future_absence_ends_a_job_that_has_no_listing_fact():
    schema = pa.schema(
        [
            (n, pa.string())
            for n in ("id", "board", "title", "valid_from", "valid_to", "ended_as")
        ]
    )
    versions = pa.Table.from_pylist([], schema=schema)
    baseline = {"lever:acme:1": {"reference_board": "lever:acme", "title": "Unread"}}
    rows = seed_versions(
        versions,
        baseline,
        "2026-10-02",
        [{"id": "lever:acme:1", "run": "2026-10-03", "kind": "unlisted"}],
    ).to_pylist()
    assert rows[0]["valid_to"] == "2026-10-03"
    assert rows[0]["ended_as"] == "unlisted"
