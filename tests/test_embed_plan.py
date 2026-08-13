"""Tests for the embed planner (headstart.ingest.embed_plan, ADR-0025 Phase 1).

The cost-model bin-packing (LPT) and the dynamic shard sizing are the new logic worth locking
down; the end-to-end ``main`` is exercised with a fake tokenizer (no model download, no embedding)
over a tiny corpus, asserting the diff/English-gate/partition invariants — every new English Doc
lands in exactly one shard, and prior/non-English Docs are excluded.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip(
    "langdetect"
)  # embed_plan imports headstart.ingest.doc_prep (langdetect gate)

# Imported after the gate above, not at the top: on CI's base-deps-only install the module's
# langdetect dependency is absent, and this must skip rather than error.
import headstart.ingest.embed_plan as pe  # noqa: E402


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
            "embed_plan",
            "--source",
            str(tech),
            "--prior-meta",
            str(prior),
            "--priority",
            str(tmp_path / "none.csv"),
            "--out-dir",
            str(out),
            # Pinned into tmp_path: it defaults to the repo's real data/state/, and a test run
            # must never write there.
            "--upgrades-out",
            str(tmp_path / "pending_upgrades.txt"),
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
            assert set(rec) == {"doc", "bucket", "tokens", "meta"}
            assert rec["tokens"] <= rec["bucket"]  # exact count, within its Bucket
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
            "embed_plan",
            "--source",
            str(tech),
            "--prior-meta",
            str(tmp_path / "absent.jsonl"),
            "--priority",
            str(tmp_path / "none.csv"),
            "--out-dir",
            str(out),
            # Pinned into tmp_path: it defaults to the repo's real data/state/, and a test run
            # must never write there.
            "--upgrades-out",
            str(tmp_path / "pending_upgrades.txt"),
        ],
    )
    assert pe.main() == 0
    plan = json.loads((out / "plan.json").read_text())
    assert plan == {"shards": [], "count": 0, "makespan_s": 0.0, "per_shard_s": []}


def _meta_row(job_id: str, ats: str, **extra) -> str:
    return json.dumps({"id": job_id, "ats": ats, "title": "Engineer", **extra}) + "\n"


def test_prior_rows_reads_the_flag_where_it_exists(tmp_path):
    """ADR-0050: a vector recorded as built without a description is the repairable population."""
    meta = tmp_path / "meta.jsonl"
    meta.write_text(
        _meta_row("eightfold:acme:1", "eightfold", has_description=True)
        + _meta_row("eightfold:acme:2", "eightfold", has_description=False),
        encoding="utf-8",
    )
    embedded, degraded = pe._prior_rows(meta)
    assert embedded == {"eightfold:acme:1", "eightfold:acme:2"}
    assert degraded == {"eightfold:acme:2"}


def test_prior_rows_infers_the_flag_only_for_detail_pass_atses(tmp_path):
    """Rows written before ADR-0050 carry no flag. Inferring 'degraded' for all of them would
    re-embed ~186k Docs to repair ~16,771; a listing-only ATS re-supplies its description on
    every scrape and so cannot have lost one, which bounds the migration to ~22k."""
    meta = tmp_path / "meta.jsonl"
    meta.write_text(
        _meta_row("eightfold:acme:1", "eightfold")  # detail pass -> assume degraded
        + _meta_row("greenhouse:acme:2", "greenhouse"),  # listing-only -> assume fine
        encoding="utf-8",
    )
    _, degraded = pe._prior_rows(meta)
    assert degraded == {"eightfold:acme:1"}


def test_a_degraded_row_is_re_embedded_once_its_description_arrives(
    tmp_path, monkeypatch
):
    """The upgrade the store makes possible. `embed_plan` skips by id, so without this the
    title-only vector survives every future run no matter how good the scrape gets."""
    monkeypatch.setattr(pe, "_load_tokenizer", lambda: _FakeTok())
    tech = tmp_path / "tech"
    tech.mkdir()
    (tech / "eightfold.jsonl").write_text(
        json.dumps(
            {
                "id": "eightfold:acme:1",
                "ats": "eightfold",
                "title": "Backend Engineer",
                "description": "We are hiring a backend engineer to build systems.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    meta = tmp_path / "meta.jsonl"
    meta.write_text(
        _meta_row("eightfold:acme:1", "eightfold", has_description=False),
        encoding="utf-8",
    )
    out = tmp_path / "assignments"
    upgrades = tmp_path / "pending_upgrades.txt"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "embed_plan",
            "--source",
            str(tech),
            "--prior-meta",
            str(meta),
            "--priority",
            str(tmp_path / "none.csv"),
            "--out-dir",
            str(out),
            "--upgrades-out",
            str(upgrades),
        ],
    )
    assert pe.main() == 0

    assert json.loads((out / "plan.json").read_text())["count"] == 1
    # The merge stage evicts these before `index sync`, or add = fresh - index never re-adds them
    assert upgrades.read_text().split() == ["eightfold:acme:1"]


def test_a_degraded_row_with_still_no_description_is_left_alone(tmp_path, monkeypatch):
    """Re-embedding it would produce the same title-only vector and spend the budget twice."""
    monkeypatch.setattr(pe, "_load_tokenizer", lambda: _FakeTok())
    tech = tmp_path / "tech"
    tech.mkdir()
    (tech / "eightfold.jsonl").write_text(
        json.dumps(
            {
                "id": "eightfold:acme:1",
                "ats": "eightfold",
                "title": "Backend Engineer",
                "description": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    meta = tmp_path / "meta.jsonl"
    meta.write_text(
        _meta_row("eightfold:acme:1", "eightfold", has_description=False),
        encoding="utf-8",
    )
    out = tmp_path / "assignments"
    upgrades = tmp_path / "pending_upgrades.txt"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "embed_plan",
            "--source",
            str(tech),
            "--prior-meta",
            str(meta),
            "--priority",
            str(tmp_path / "none.csv"),
            "--out-dir",
            str(out),
            "--upgrades-out",
            str(upgrades),
        ],
    )
    assert pe.main() == 0

    assert json.loads((out / "plan.json").read_text())["count"] == 0
    assert (
        upgrades.read_text() == ""
    )  # always rewritten, so a prior run's list can't linger


def test_a_degraded_row_whose_new_description_is_not_english_is_not_listed(
    tmp_path, monkeypatch
):
    """An upgrade is only listed once its Doc is genuinely planned.

    The id is written for `embed_merge`, which drops the stale vector only when the replacement
    arrives — so listing a Job the English gate then drops means listing one that no shard will
    ever embed. It would be held on every run forever, and `index sync` would churn a
    delete-and-re-add of its row each time. An English title over a non-English body is the
    ordinary way to land here.
    """
    monkeypatch.setattr(pe, "_load_tokenizer", lambda: _FakeTok())
    tech = tmp_path / "tech"
    tech.mkdir()
    (tech / "eightfold.jsonl").write_text(
        json.dumps(
            {
                "id": "eightfold:acme:1",
                "ats": "eightfold",
                "title": "Backend Engineer",
                "description": "Wir suchen eine Entwicklerin für unsere Plattform in München.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    meta = tmp_path / "meta.jsonl"
    meta.write_text(
        _meta_row("eightfold:acme:1", "eightfold", has_description=False),
        encoding="utf-8",
    )
    upgrades = tmp_path / "pending_upgrades.txt"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "embed_plan",
            "--source",
            str(tech),
            "--prior-meta",
            str(meta),
            "--priority",
            str(tmp_path / "none.csv"),
            "--out-dir",
            str(tmp_path / "assignments"),
            "--upgrades-out",
            str(upgrades),
        ],
    )
    assert pe.main() == 0

    # dropped by the English gate, so it must not be promised to the merge as an incoming replacement
    assert upgrades.read_text().strip() == ""
