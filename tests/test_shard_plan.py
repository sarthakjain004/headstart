"""Tests for headstart.ingest.shard_plan — the typed plan.json shapes and shard_index (ADR-0153).

``ScrapePlan``/``EmbedPlan`` are exercised for round-tripping (to_json -> from_json), for the
exact on-disk shape (byte-for-byte what the old hand-assembled dicts wrote — pipeline.yml's
heredocs and any artifact from a prior run must keep reading it), and for tolerance of an older
or corrupt plan. ``shard_index`` is exercised against the ``shard-{k}.jsonl`` convention both
``scrape_run`` and ``embed_run`` rely on.
"""

from __future__ import annotations

import json

from headstart.ingest import shard_plan


def test_shard_index_reads_the_trailing_number():
    assert shard_plan.shard_index("data/scrape/assignments/shard-3.jsonl") == "3"
    assert shard_plan.shard_index(None) is None
    assert shard_plan.shard_index("") is None


def test_scrape_plan_to_json_matches_the_hand_assembled_shape(tmp_path):
    """The on-disk shape must survive the move to a dataclass unchanged — pipeline.yml's
    heredocs, and any plan.json a prior run already wrote, read these exact keys."""
    plan = shard_plan.ScrapePlan(
        shards=[0, 1],
        count=5,
        per_shard_boards=[3, 2],
        per_shard_minutes=[10.001, 20.006],
        per_shard_serial_minutes=[40.0, 60.5],
    )
    path = tmp_path / "plan.json"
    path.write_text(plan.to_json(), encoding="utf-8")

    assert json.loads(path.read_text()) == {
        "shards": [0, 1],
        "count": 5,
        "per_shard_boards": [3, 2],
        "per_shard_minutes": [10.0, 20.01],  # rounded to 2 dp, as the old writer did
        "per_shard_serial_minutes": [40.0, 60.5],
    }


def test_scrape_plan_omits_minutes_on_a_cold_start():
    plan = shard_plan.ScrapePlan(shards=[], count=0, per_shard_boards=[])
    assert json.loads(plan.to_json()) == {
        "shards": [],
        "count": 0,
        "per_shard_boards": [],
    }


def test_scrape_plan_round_trips_and_reads_its_own_shard(tmp_path):
    plan = shard_plan.ScrapePlan(
        shards=[0, 1, 2],
        count=6,
        per_shard_boards=[2, 2, 2],
        per_shard_minutes=[10.0, 20.5, 30.0],
        per_shard_serial_minutes=[40.0, 60.5, 90.0],
    )
    path = tmp_path / "plan.json"
    path.write_text(plan.to_json(), encoding="utf-8")

    loaded = shard_plan.ScrapePlan.from_json(path)
    assert loaded.predicted_minutes("1") == 20.5
    # the serial sum is read from its own field: the join measures the fan-out's speedup against
    # it, and against the prediction the estimate would chase its own tail (ADR-0054)
    assert loaded.serial_minutes("1") == 60.5


def test_scrape_plan_from_json_tolerates_an_older_plan_or_a_missing_file(tmp_path):
    # an older plan, before per_shard_minutes/per_shard_serial_minutes existed
    old = tmp_path / "plan.json"
    old.write_text(json.dumps({"shards": [0], "count": 3, "per_shard_boards": [3]}))
    plan = shard_plan.ScrapePlan.from_json(old)
    assert plan is not None
    assert plan.predicted_minutes("0") is None
    assert plan.serial_minutes("0") is None

    assert shard_plan.ScrapePlan.from_json(tmp_path / "absent.json") is None

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    assert shard_plan.ScrapePlan.from_json(corrupt) is None


def test_scrape_plan_minutes_are_none_for_a_shard_index_past_the_end(tmp_path):
    """A shard asking for an entry the plan never sized for (a stale assignment against a
    freshly re-planned run) gets absence, not an IndexError."""
    plan = shard_plan.ScrapePlan(
        shards=[0], count=1, per_shard_boards=[1], per_shard_minutes=[5.0]
    )
    assert plan.predicted_minutes("7") is None


def test_embed_plan_to_json_matches_the_hand_assembled_shape():
    plan = shard_plan.EmbedPlan(
        shards=[0, 1], count=4, makespan_s=12.345, per_shard_s=[6.001, 12.345]
    )
    assert json.loads(plan.to_json()) == {
        "shards": [0, 1],
        "count": 4,
        "makespan_s": 12.3,  # rounded to 1 dp, as the old writer did
        "per_shard_s": [6.0, 12.3],
    }


def test_embed_plan_round_trips(tmp_path):
    plan = shard_plan.EmbedPlan(shards=[0], count=2, makespan_s=6.0, per_shard_s=[6.0])
    path = tmp_path / "plan.json"
    path.write_text(plan.to_json(), encoding="utf-8")

    loaded = shard_plan.EmbedPlan.from_json(path)
    assert loaded == plan


def test_embed_plan_from_json_tolerates_a_missing_file(tmp_path):
    assert shard_plan.EmbedPlan.from_json(tmp_path / "absent.json") is None
