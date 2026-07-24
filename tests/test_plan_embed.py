"""Tests for the embed planner (scripts/pipeline/plan_embed.py, ADR-0025 Phase 1).

The cost-model bin-packing (LPT) and the dynamic shard sizing are the new logic worth locking
down; the end-to-end ``main`` is exercised with a fake tokenizer (no model download, no embedding)
over a tiny corpus, asserting the diff/English-gate/partition invariants — every new English Doc
lands in exactly one shard, and prior/non-English Docs are excluded.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip(
    "langdetect"
)  # plan_embed imports headstart.embed_prep (langdetect gate)

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "plan_embed", _ROOT / "scripts" / "pipeline" / "plan_embed.py"
)
pe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pe)


class _FakeTok:
    """Stand-in for the model tokenizer: token count ≈ word count (so 'a a a' ~ n tokens)."""

    def __call__(self, text, truncation=True, max_length=4096):
        if isinstance(text, str):
            return {"input_ids": list(range(min(len(text.split()), max_length)))}
        return {
            "input_ids": [list(range(min(len(t.split()), max_length))) for t in text]
        }


def test_lpt_pack_balances_better_than_round_robin():
    costs = [10.0, 1.0, 1.0, 1.0, 1.0]
    assign, loads = pe.lpt_pack(costs, 2)
    assert len(assign) == len(costs)
    assert all(0 <= k < 2 for k in assign)
    # every item counted once, per-shard load == sum of its items
    recomputed = [0.0, 0.0]
    for i, k in enumerate(assign):
        recomputed[k] += costs[i]
    assert recomputed == loads
    assert sum(loads) == sum(costs)
    # LPT keeps the big item alone; makespan 10, not round-robin's 12
    assert max(loads) == 10.0


def test_lpt_pack_is_deterministic():
    costs = [3.0, 3.0, 2.0, 2.0, 1.0]
    assert pe.lpt_pack(costs, 3) == pe.lpt_pack(costs, 3)


def test_shard_count_clamps_and_scales():
    assert pe.shard_count(0.0, 0, 15, 1200) == 0  # no work -> no shards
    assert pe.shard_count(100.0, 50, 15, 1200) == 1  # small day-run collapses to one
    assert (
        pe.shard_count(40_000.0, 5000, 15, 1200) == 15
    )  # big backlog saturates the cap
    assert pe.shard_count(2400.0, 10, 15, 1200) == 2


def _write_corpus(tech: Path) -> None:
    tech.mkdir(parents=True, exist_ok=True)
    jobs = [
        {
            "id": "lever:acme:1",
            "ats": "lever",
            "company": "Acme",
            "title": "Backend Engineer",
            "description": "Build and ship reliable backend services in Python and Go every day.",
        },
        {
            "id": "lever:acme:2",
            "ats": "lever",
            "company": "Acme",
            "title": "Frontend Engineer",
            "description": "Craft delightful React interfaces and ship them to millions of users.",
        },
        {
            "id": "lever:acme:3",
            "ats": "lever",
            "company": "Acme",
            "title": "Entwickler",
            "description": "Wir suchen einen erfahrenen Entwickler für unser Team in Berlin heute.",
        },
        {
            "id": "lever:acme:4",
            "ats": "lever",
            "company": "Acme",
            "title": "Data Engineer",
            "description": "Own the data platform and its pipelines end to end for the company.",
        },
    ]
    with (tech / "lever.jsonl").open("w", encoding="utf-8") as fh:
        for j in jobs:
            fh.write(json.dumps(j) + "\n")


def test_main_partitions_new_english_docs(tmp_path, monkeypatch):
    tech = tmp_path / "tech"
    _write_corpus(tech)
    prior = tmp_path / "meta.jsonl"
    prior.write_text(
        json.dumps({"id": "lever:acme:4"}) + "\n", encoding="utf-8"
    )  # already embedded
    out = tmp_path / "assignments"

    monkeypatch.setattr(pe, "_load_tokenizer", lambda: _FakeTok())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "plan_embed",
            "--source",
            str(tech),
            "--prior-meta",
            str(prior),
            "--priority",
            str(tmp_path / "none.csv"),
            "--out-dir",
            str(out),
            "--max-shards",
            "3",
            "--target-seconds",
            "1",
        ],
    )
    assert pe.main() == 0

    plan = json.loads((out / "plan.json").read_text())
    # ids 1 & 2 are new + English; 3 is German (dropped), 4 is already in prior meta (skipped)
    assert plan["count"] == 2
    assert plan["shards"] == list(range(len(plan["shards"])))

    seen = []
    for k in plan["shards"]:
        for line in (out / f"shard-{k}.jsonl").read_text().splitlines():
            rec = json.loads(line)
            assert set(rec) == {"doc", "bucket", "meta"}
            assert rec["doc"].startswith("search_document: ")
            seen.append(rec["meta"]["id"])
    assert sorted(seen) == ["lever:acme:1", "lever:acme:2"]  # each new Doc exactly once


def test_main_empty_plan_when_nothing_new(tmp_path, monkeypatch):
    tech = tmp_path / "tech"
    tech.mkdir()
    (tech / "lever.jsonl").write_text("", encoding="utf-8")  # no jobs
    out = tmp_path / "assignments"

    monkeypatch.setattr(pe, "_load_tokenizer", lambda: _FakeTok())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "plan_embed",
            "--source",
            str(tech),
            "--prior-meta",
            str(tmp_path / "absent.jsonl"),
            "--priority",
            str(tmp_path / "none.csv"),
            "--out-dir",
            str(out),
        ],
    )
    assert pe.main() == 0
    plan = json.loads((out / "plan.json").read_text())
    assert plan == {"shards": [], "count": 0, "makespan_s": 0.0, "per_shard_s": []}
