"""Input changes are independent of raw listing versions and never come from the future."""

import numpy as np
import pyarrow as pa

from headstart.ingest.restate_inputs import VersionSources, bind_intervals


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
