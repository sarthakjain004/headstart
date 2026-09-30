#!/usr/bin/env python3
"""Recompute the whole Trends history from the Job facts under today's rules: a Restatement
(ADR-0330).

Writes one tick file per run that recorded facts, in the Board-delta shape ``role_trends`` writes
(ADR-0230), into a directory of its own (``data/restated/`` by default), every tick under today's
Methodology. The steps, each its own module:

1. :mod:`~headstart.ingest.restate_replay`: the facts become Job versions;
2. :mod:`~headstart.ingest.restate_served`: today's keep-set, tech filter, English gate, grace
   period, Dormant Boards and duplicate groups decide when each version counted;
3. :mod:`~headstart.ingest.restate_place`: today's classifier and derivations give each its
   family and band;
4. :mod:`~headstart.ingest.restate_count`: every tick's levels and turnover.

Watched roles (``watch:`` groups) are not restated yet, so a comparison with ``role_trends`` leaves
them out. Titles the classifier's cache lacks are encoded within ``--encode-budget-seconds``, which
needs the title model and so runs on CI; a title left unencoded counts as unclassified tech.

Run: python -m headstart.ingest.restate_run [--out DIR] [--encode-budget-seconds N]
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from headstart import log
from headstart.boards import eightfold_backing
from headstart.boards.board_identity import lower_key
from headstart.ingest import (
    REPO_ROOT,
    board_failures,
    job_facts,
    restate_count,
    restate_place,
    restate_replay,
    restate_served,
    role_family_classifier,
)
from headstart.ingest.doc_prep import DERIVATIONS_VERSION, is_english
from headstart.ingest.index_plan import (
    DEDUP_VERSION,
    boards_by_canon,
    duplicate_ranks,
    live_keep_set,
    workday_site_jobs,
)
from headstart.jobs import tech_filter
from headstart.trends import role_taxonomy, trend_history

_log = log.get(__name__, __spec__)

_OUT = REPO_ROOT / "data" / "restated"


def _vectors(db: Path, facts_dir: Path, wanted: set[str]) -> dict[str, object]:
    """Each wanted id's description vector: the archived one, overridden by the served one."""
    import numpy as np
    import pyarrow.parquet as pq

    vectors: dict[str, object] = {}
    for path in sorted((facts_dir / job_facts.JOB_VECTORS).glob("*.parquet")):
        table = pq.read_table(path)
        flat = table["vector"].combine_chunks()
        matrix = flat.flatten().to_numpy().reshape(len(table), -1).astype(np.float32)
        for job_id, vector in zip(table["id"].to_pylist(), matrix, strict=True):
            if job_id in wanted:
                vectors[job_id] = vector
    if (db / "jobs.lance").exists():
        import lancedb

        from headstart.embedding_conventions import PROD_TABLE

        table = lancedb.connect(db).open_table(PROD_TABLE)
        for ids, batch in role_family_classifier.served_vector_batches(table):
            for job_id, vector in zip(ids, batch, strict=True):
                if job_id in wanted:
                    vectors[job_id] = vector
    return vectors


def _descriptions(store: Path, wanted: set[str]) -> dict[str, str]:
    """Each wanted id's description, from the description store (ADR-0050)."""
    from headstart.ingest.update_descriptions import read_store

    texts: dict[str, str] = {}
    if not store.exists():
        return texts
    for ats_dir in sorted(p for p in store.iterdir() if p.is_dir()):
        for job_id, text in read_store(ats_dir).items():
            if job_id in wanted:
                texts[job_id] = text
    return texts


def main() -> int:
    log.setup()
    log.context("restate_run")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--facts", type=Path, default=job_facts.FACTS_DIR)
    ap.add_argument(
        "--ledger", type=Path, default=REPO_ROOT / "data" / "validate" / "liveness"
    )
    ap.add_argument(
        "--board-failures",
        type=Path,
        default=REPO_ROOT / "data" / "state" / "board_failures.csv",
    )
    ap.add_argument(
        "--classifier",
        type=Path,
        default=REPO_ROOT / "config" / "role_family_classifier",
    )
    ap.add_argument(
        "--families", type=Path, default=REPO_ROOT / "config" / "role_families.json"
    )
    ap.add_argument(
        "--title-cache",
        type=Path,
        default=REPO_ROOT / "data" / "state" / "role_title_families.parquet",
    )
    ap.add_argument("--db", type=Path, default=REPO_ROOT / "data" / "lancedb")
    ap.add_argument(
        "--descriptions", type=Path, default=REPO_ROOT / "data" / "descriptions"
    )
    ap.add_argument("--out", type=Path, default=_OUT, help="default: data/restated")
    ap.add_argument(
        "--encode-budget-seconds",
        type=float,
        default=0.0,
        help="time to spend encoding titles the classifier cache lacks (needs the title model)",
    )
    args = ap.parse_args()

    versions = restate_replay.job_versions(args.facts)
    runs = restate_replay.runs(args.facts)
    if versions is None or not runs:
        _log.info(f"no Job facts under {args.facts} yet — nothing to restate")
        return 0
    reads = restate_replay.board_reads(args.facts)
    _log.info(f"{versions.num_rows} Job versions over {len(runs)} runs")

    keep = live_keep_set(args.ledger)
    live = boards_by_canon(keep)
    keep_set = job_facts.RunScope.of(
        set(), set(), live, board_failures.load(args.board_failures)
    ).keep_set
    served = restate_served.served_intervals(
        versions, reads, is_tech=tech_filter.is_tech, live=live, keep_set=keep_set
    )
    served = restate_served.clip_dormant(
        served, restate_served.dormant_periods(versions, runs, live)
    )
    descriptions = _descriptions(args.descriptions, set(served["id"].to_pylist()))
    served = restate_served.english_only(served, descriptions, is_english)
    ids = served["id"].to_pylist()
    requisitions = {
        job_id: req
        for job_id, req in zip(ids, served["requisition"].to_pylist(), strict=True)
        if req
    }
    served = restate_served.fold_duplicates(
        served,
        duplicate_ranks(
            ids,
            keep,
            site_jobs=workday_site_jobs(args.ledger),
            requisitions=requisitions,
            backing=eightfold_backing.load(),
        ),
    )
    _log.info(f"{served.num_rows} served intervals under today's rules")

    families = role_taxonomy.load_families(args.families)
    head = role_family_classifier.Head(args.classifier)
    head.check_families(families)
    cache = role_family_classifier.load_cache(args.title_cache, head.version)
    titles = served["title"].to_pylist()
    if args.encode_budget_seconds > 0:
        added = role_family_classifier.fill(
            cache,
            head,
            titles,
            args.encode_budget_seconds,
            lambda c: role_family_classifier.save_cache(args.title_cache, c),
        )
        _log.info(f"encoded {added} title(s) the classifier cache lacked")
    wanted = set(ids)
    served = restate_place.placements(
        served,
        head,
        cache,
        _vectors(args.db, args.facts, wanted),
        descriptions,
    )

    first_reads: dict[str, str] = {}
    if reads is not None:
        for board, run in zip(
            reads["board"].to_pylist(), reads["run"].to_pylist(), strict=True
        ):
            if board is not None:
                first_reads.setdefault(lower_key(board), run)
    methodology = trend_history.Methodology(
        family_list_fingerprint=role_taxonomy.family_list_fingerprint(args.families),
        family_classifier_version=role_family_classifier.classifier_version(head),
        tech_filter_version=tech_filter.TECH_FILTER_VERSION,
        derivations_version=DERIVATIONS_VERSION,
        dedup_version=DEDUP_VERSION,
    )
    # A Restatement is derived whole from the facts: the previous one is replaced, not extended.
    shutil.rmtree(args.out, ignore_errors=True)
    previous: tuple[str | None, dict] = (None, {})
    for run, levels, turnover in restate_count.tick_counts(
        served, runs, first_reads, restate_place.placed
    ):
        trend_history.record_tick(
            args.out, run, levels, turnover, methodology, replayed=previous
        )
        previous = (run, levels)
    _log.info(f"restated {len(runs)} ticks under today's rules -> {args.out}")
    return 0


if __name__ == "__main__":
    log.run_logging_crash(
        _log, main, "restate_run failed — the live Trends are untouched"
    )
