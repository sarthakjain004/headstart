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

import headstart.ingest.embed_plan as pe
from headstart.ingest.doc_prep import STALE_DOC_HASH, doc_hash, to_meta

# The gate is per-test, not module-level. It used to be module-level, on the premise that
# importing this module needs langdetect — measurably false: `doc_prep` imports langdetect lazily
# inside `is_english`, and `_load_tokenizer` imports transformers lazily, so `embed_plan` imports
# cleanly with langdetect, torch, sentence_transformers, transformers, lancedb, numpy and pyarrow
# all blocked. That premise skipped the whole file on CI's base-deps-only install, so the sizing,
# packing, `_prior_rows` and empty-plan tests below — which need none of it — never ran there.
# Only the tests that push real job text through the English gate need the dependency, and they
# say so themselves. Note `test_main_empty_plan_when_nothing_new` calls `main()` ungated: that is
# safe only because its corpus is empty, so `is_english` is never reached. Give that fixture a job
# and it needs the gate too — without one it would *error* on CI rather than skip.
_NEEDS_LANGDETECT = "embed_plan.main() runs doc_prep.is_english over the corpus"


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


def test_target_seconds_fans_a_steady_state_run_out_across_lanes():
    """Guards the *constant*, which the test above does not: it passes 1200 as a literal, so it
    stays green if `_TARGET_SECONDS` regresses to the 20 min that made `ceil(cost / target)`
    exactly 1 on every run and left 14 of 15 lanes idle.

    Six of the seven 2026-09-25/26 runs (36200233818-36215851608), re-costed with the
    recalibrated `_S_PER_DOC`, are pinned to the shard counts those runs actually planned, rather
    than merely asserting `> 1` — `> 1` is satisfied by a target that restores almost none of the
    fan-out. Retuning the constant should fail here so the comment gets updated with it.
    `n_items` is only `shard_count`'s has-work guard.
    """
    planned = {558.0: 4, 484.0: 3, 336.0: 3, 232.0: 2, 113.0: 1, 129.0: 1}
    got = {
        cost: pe.shard_count(cost, 300, pe._MAX_SHARDS, pe._TARGET_SECONDS)
        for cost in planned
    }
    assert got == planned


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
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
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
            "--non-english-out",
            str(tmp_path / "pending_non_english.txt"),
            "--regate",
            str(tmp_path / "regate.txt"),
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
            "--non-english-out",
            str(tmp_path / "pending_non_english.txt"),
            "--regate",
            str(tmp_path / "regate.txt"),
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
    embedded, degraded, _ = pe._prior_rows(meta)
    assert embedded == {"eightfold:acme:1", "eightfold:acme:2"}
    assert degraded == {"eightfold:acme:2"}


def test_prior_rows_no_longer_guesses_from_the_ats(tmp_path):
    """An absent flag used to mean 'assume degraded on a detail-pass ATS'. That guess conflated
    "this ATS fetches descriptions separately" with "that fetch failed": it condemned 152,383
    rows, of which `experience_source == "regex"` proves 66,296 were built from a description,
    against ~16,771 genuinely title-only index-wide. `update_meta` backfills the real flag
    (ADR-0062), so only an explicit False is degraded now."""
    meta = tmp_path / "meta.jsonl"
    meta.write_text(
        _meta_row("eightfold:acme:1", "eightfold")  # detail pass, but no flag
        + _meta_row("greenhouse:acme:2", "greenhouse"),
        encoding="utf-8",
    )
    _, degraded, _ = pe._prior_rows(meta)
    assert degraded == set(), "an absent flag is not evidence of a title-only vector"


def test_a_degraded_row_is_re_embedded_once_its_description_arrives(
    tmp_path, monkeypatch
):
    """The upgrade the store makes possible. `embed_plan` skips by id, so without this the
    title-only vector survives every future run no matter how good the scrape gets."""
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
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
            "--non-english-out",
            str(tmp_path / "pending_non_english.txt"),
            "--regate",
            str(tmp_path / "regate.txt"),
        ],
    )
    assert pe.main() == 0

    assert json.loads((out / "plan.json").read_text())["count"] == 1
    # The merge stage evicts these before `index sync`, or add = fresh - index never re-adds them
    assert upgrades.read_text().split() == ["eightfold:acme:1"]


def test_a_degraded_row_with_still_no_description_is_left_alone(tmp_path, monkeypatch):
    """Re-embedding it would produce the same title-only vector and spend the budget twice."""
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
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
            "--non-english-out",
            str(tmp_path / "pending_non_english.txt"),
            "--regate",
            str(tmp_path / "regate.txt"),
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
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
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
            "--non-english-out",
            str(tmp_path / "pending_non_english.txt"),
            "--regate",
            str(tmp_path / "regate.txt"),
        ],
    )
    assert pe.main() == 0

    # dropped by the English gate, so it must not be promised to the merge as an incoming replacement
    assert upgrades.read_text().strip() == ""


def test_the_tokenizer_loads_at_the_pinned_revisions(monkeypatch):
    """`trust_remote_code` runs the model config's Python from `nomic-bert-2048`, so the tokenizer
    load needs both pins, like the encoder's."""
    import types

    from headstart import embedding_conventions as ec

    seen = {}

    def fake(model, **kwargs):
        seen.update(kwargs, model=model)
        return "tok"

    monkeypatch.setitem(
        sys.modules,
        "transformers",
        types.SimpleNamespace(
            AutoTokenizer=types.SimpleNamespace(from_pretrained=fake)
        ),
    )

    assert pe._load_tokenizer() == "tok"
    assert seen["model"] == ec.MODEL
    assert seen["revision"] == ec.MODEL_REVISION
    assert seen["code_revision"] == ec.MODEL_CODE_REVISION


# --- ADR-0285: a vector whose text changed is re-embedded ----------------------------------------


def _plan_upgrades(tmp_path, monkeypatch, jobs: list[dict], meta: str) -> list[str]:
    """Run the planner over ``jobs`` against the prior store ``meta``; return the upgrade list."""
    monkeypatch.setattr(pe, "_load_tokenizer", lambda: _FakeTok())
    tech = tmp_path / "tech"
    tech.mkdir(exist_ok=True)
    (tech / "greenhouse.jsonl").write_text(
        "".join(json.dumps(j) + "\n" for j in jobs), encoding="utf-8"
    )
    (tmp_path / "meta.jsonl").write_text(meta, encoding="utf-8")
    upgrades = tmp_path / "pending_upgrades.txt"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "embed_plan",
            "--source",
            str(tech),
            "--prior-meta",
            str(tmp_path / "meta.jsonl"),
            "--priority",
            str(tmp_path / "none.csv"),
            "--out-dir",
            str(tmp_path / "assignments"),
            "--upgrades-out",
            str(upgrades),
            "--non-english-out",
            str(tmp_path / "pending_non_english.txt"),
            "--regate",
            str(tmp_path / "regate.txt"),
        ],
    )
    assert pe.main() == 0
    return upgrades.read_text().split()


_JOB = {
    "id": "greenhouse:acme:1",
    "ats": "greenhouse",
    "title": "Backend Engineer",
    "description": "We are hiring a backend engineer to build distributed systems.",
}


def test_prior_rows_reads_each_rows_doc_hash(tmp_path):
    meta = tmp_path / "meta.jsonl"
    meta.write_text(
        _meta_row("greenhouse:a:1", "greenhouse", doc_hash="abc")
        + _meta_row("greenhouse:a:2", "greenhouse"),
        encoding="utf-8",
    )
    assert pe._prior_rows(meta)[2] == {"greenhouse:a:1": "abc"}


def test_doc_hash_follows_the_title_and_description_not_their_padding():
    edited = {**_JOB, "description": _JOB["description"] + " Remote is fine."}
    assert doc_hash(_JOB) == doc_hash({**_JOB, "title": " Backend Engineer "})
    assert doc_hash(_JOB) != doc_hash(edited)
    assert doc_hash(_JOB) != doc_hash({**_JOB, "title": "Staff Engineer"})
    assert to_meta(_JOB)["doc_hash"] == doc_hash(_JOB)


def test_an_edited_posting_is_re_embedded(tmp_path, monkeypatch):
    """#694: the clone-then-edit case. The store holds the fingerprint of the text the vector was
    built from; the posting now says something else, so its vector is rebuilt."""
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
    embedded = {**_JOB, "title": "Process Engineer IV"}
    meta = _meta_row(_JOB["id"], "greenhouse", doc_hash=doc_hash(embedded))
    assert _plan_upgrades(tmp_path, monkeypatch, [_JOB], meta) == [_JOB["id"]]


def test_an_unchanged_posting_is_not_re_embedded(tmp_path, monkeypatch):
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
    meta = _meta_row(_JOB["id"], "greenhouse", doc_hash=doc_hash(_JOB))
    assert _plan_upgrades(tmp_path, monkeypatch, [_JOB], meta) == []


def test_a_row_with_no_fingerprint_yet_is_left_alone(tmp_path, monkeypatch):
    """Embedded before ADR-0285: `update_meta` stamps it first, so nothing re-embeds on a guess."""
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
    meta = _meta_row(_JOB["id"], "greenhouse")
    assert _plan_upgrades(tmp_path, monkeypatch, [_JOB], meta) == []


def test_a_row_stamped_stale_is_re_embedded(tmp_path, monkeypatch):
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
    meta = _meta_row(_JOB["id"], "greenhouse", doc_hash=STALE_DOC_HASH)
    assert _plan_upgrades(tmp_path, monkeypatch, [_JOB], meta) == [_JOB["id"]]


def test_the_cap_is_spent_across_atses_in_turn(tmp_path, monkeypatch):
    """A backlog on the ATS whose file sorts first used to spend the whole cap: on 2026-09-29,
    SuccessFactors' 9,070 pending re-embeds left Workday, WP Job Openings and Zoho with none."""
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
    monkeypatch.setattr(pe, "_MAX_EDIT_REEMBEDS", 2)
    backlog = [{**_JOB, "id": f"ashby:acme:{n}", "ats": "ashby"} for n in range(3)]
    late = {**_JOB, "id": "zoho:acme:1", "ats": "zoho"}
    jobs = [*backlog, late]
    meta = "".join(_meta_row(j["id"], j["ats"], doc_hash="old") for j in jobs)
    upgrades = _plan_upgrades(tmp_path, monkeypatch, jobs, meta)
    assert late["id"] in upgrades
    assert len(upgrades) == 2


def test_within_an_ats_the_highest_priority_board_is_re_embedded_first(
    tmp_path, monkeypatch
):
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
    monkeypatch.setattr(pe, "_MAX_EDIT_REEMBEDS", 1)
    monkeypatch.setattr(
        pe,
        "load_scores",
        lambda _path: {"greenhouse:small": 1.0, "greenhouse:big": 9.0},
    )
    jobs = [
        {**_JOB, "id": "greenhouse:small:1"},
        {**_JOB, "id": "greenhouse:big:1"},
    ]
    meta = "".join(_meta_row(j["id"], "greenhouse", doc_hash="old") for j in jobs)
    assert _plan_upgrades(tmp_path, monkeypatch, jobs, meta) == ["greenhouse:big:1"]


def test_no_description_over_a_described_vector_is_not_an_edit(tmp_path, monkeypatch):
    """A fetch that failed with no held text to fill it. Re-embedding would build a title-only
    vector, and ADR-0050 would rebuild it again once the text came back."""
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
    # A title the English gate passes on its own, so only the edit rule can keep it off the list.
    job = {
        **_JOB,
        "title": "Senior Software Engineer, Payments Platform and Distributed Systems",
    }
    meta = _meta_row(job["id"], "greenhouse", doc_hash=doc_hash(job))
    unfetched = {**job, "description": ""}
    assert _plan_upgrades(tmp_path, monkeypatch, [unfetched], meta) == []


def test_a_retitled_title_only_vector_is_still_an_edit(tmp_path, monkeypatch):
    """Built without a description and still without one: the title is all it encodes, so a new
    title is a real edit."""
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
    embedded = {**_JOB, "title": "Senior Backend Software Engineer", "description": ""}
    meta = _meta_row(
        _JOB["id"], "greenhouse", has_description=False, doc_hash=doc_hash(embedded)
    )
    retitled = {**embedded, "title": "Staff Backend Software Engineer for Payments"}
    assert _plan_upgrades(tmp_path, monkeypatch, [retitled], meta) == [_JOB["id"]]


def test_edit_re_embeds_stop_at_the_cap(tmp_path, monkeypatch, caplog):
    """A scraper change can rewrite every description of an ATS at once; the rest wait for their
    Board's next read, still carrying a fingerprint that differs."""
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
    monkeypatch.setattr(pe, "_MAX_EDIT_REEMBEDS", 1)
    jobs = [{**_JOB, "id": f"greenhouse:acme:{n}"} for n in range(3)]
    meta = "".join(_meta_row(j["id"], "greenhouse", doc_hash="old") for j in jobs)
    caplog.set_level("INFO")
    assert len(_plan_upgrades(tmp_path, monkeypatch, jobs, meta)) == 1
    assert any("2 more deferred" in r.getMessage() for r in caplog.records)


# --- ADR-0286: a held Job whose text fails the English gate is dropped -----------------------


def _non_english_listed(tmp_path) -> list[str]:
    return (tmp_path / "pending_non_english.txt").read_text().split()


_GERMAN = {
    **_JOB,
    "title": "Entwickler Backend",
    "description": "Wir suchen einen erfahrenen Entwickler für unser Team in Berlin heute.",
}


def test_an_edit_into_another_language_is_dropped_not_re_embedded(
    tmp_path, monkeypatch
):
    """#706: the served row keeps its English vector while its text no longer passes the gate a
    new Job must. Re-embedding would put a German vector in the English index; the row is
    dropped instead, and sync evicts it like any Job that left."""
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
    meta = _meta_row(_JOB["id"], "greenhouse", doc_hash=doc_hash(_JOB))
    assert _plan_upgrades(tmp_path, monkeypatch, [_GERMAN], meta) == []
    assert _non_english_listed(tmp_path) == [_JOB["id"]]


def test_a_regate_listed_job_that_fails_the_gate_is_dropped(tmp_path, monkeypatch):
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
    (tmp_path / "regate.txt").write_text(_JOB["id"] + "\n", encoding="utf-8")
    meta = _meta_row(_JOB["id"], "greenhouse", doc_hash=doc_hash(_GERMAN))
    assert _plan_upgrades(tmp_path, monkeypatch, [_GERMAN], meta) == []
    assert _non_english_listed(tmp_path) == [_JOB["id"]]


def test_a_regate_listed_job_that_passes_is_neither_dropped_nor_re_embedded(
    tmp_path, monkeypatch
):
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
    (tmp_path / "regate.txt").write_text(_JOB["id"] + "\n", encoding="utf-8")
    meta = _meta_row(_JOB["id"], "greenhouse", doc_hash=doc_hash(_JOB))
    assert _plan_upgrades(tmp_path, monkeypatch, [_JOB], meta) == []
    assert _non_english_listed(tmp_path) == []
    assert (
        json.loads((tmp_path / "assignments" / "plan.json").read_text())["count"] == 0
    )


def test_a_new_non_english_job_is_never_listed_for_a_drop(tmp_path, monkeypatch):
    """It was never embedded, so there is nothing to drop; it is held out as before."""
    pytest.importorskip("langdetect", reason=_NEEDS_LANGDETECT)
    assert _plan_upgrades(tmp_path, monkeypatch, [_GERMAN], "") == []
    assert _non_english_listed(tmp_path) == []
