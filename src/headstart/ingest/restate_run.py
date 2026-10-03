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
import gc
import json
import os
import shutil
import subprocess
from collections import Counter
from contextlib import ExitStack
from pathlib import Path

from headstart import log
from headstart.boards import eightfold_backing
from headstart.ingest import (
    REPO_ROOT,
    board_failures,
    job_facts,
    restate_baseline,
    restate_count,
    restate_inputs,
    restate_place,
    restate_replay,
    restate_served,
    role_family_classifier,
    trend_reference,
)
from headstart.ingest.doc_prep import DERIVATIONS_VERSION, is_english
from headstart.ingest.index_plan import (
    DEDUP_VERSION,
    boards_by_canon,
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
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    vectors: dict[str, object] = {}
    if not wanted:
        return vectors
    selected = pa.array(sorted(wanted), pa.string())
    for path in sorted((facts_dir / job_facts.JOB_VECTORS).glob("*.parquet")):
        for batch in pq.ParquetFile(path).iter_batches(
            batch_size=4096, columns=["id", "vector"]
        ):
            table = pa.Table.from_batches([batch])
            table = table.filter(pc.is_in(table["id"], value_set=selected))
            if not len(table):
                continue
            flat = table["vector"].combine_chunks()
            matrix = (
                flat.flatten().to_numpy().reshape(len(table), -1).astype(np.float32)
            )
            for job_id, vector in zip(table["id"].to_pylist(), matrix, strict=True):
                vectors[job_id] = vector.copy()
    if (db / "jobs.lance").exists():
        import lancedb

        from headstart.embedding_conventions import PROD_TABLE

        table = lancedb.connect(db).open_table(PROD_TABLE)
        for ids, batch in role_family_classifier.served_vector_batches(table):
            for job_id, vector in zip(ids, batch, strict=True):
                if job_id in wanted:
                    vectors[job_id] = vector.copy()
    return vectors


def _descriptions(store: Path, wanted: set[str]) -> dict[str, str]:
    """Each wanted id's description, from the description store (ADR-0050)."""
    from headstart.ingest.update_descriptions import _entries

    texts: dict[str, str] = {}
    if not wanted or not store.exists():
        return texts
    for ats_dir in sorted(p for p in store.iterdir() if p.is_dir()):
        for job_id, text in _entries(ats_dir):
            if job_id in wanted:
                if text is None:
                    texts.pop(job_id, None)
                else:
                    texts[job_id] = text
    return texts


def main() -> int:
    log.setup()
    log.context("restate_run")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--facts", type=Path, default=job_facts.FACTS_DIR)
    ap.add_argument(
        "--baseline",
        type=Path,
        help="complete served reference baseline; exact replay begins here",
    )
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
        "--watchlist", type=Path, help="defaults beside the family taxonomy"
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
        "--require-reference-chain",
        action="store_true",
        help="fail without a committed reference window (also required with a publisher inventory)",
    )
    ap.add_argument(
        "--encode-budget-seconds",
        type=float,
        default=0.0,
        help="time to spend encoding titles the classifier cache lacks (needs the title model)",
    )
    args = ap.parse_args()

    with ExitStack() as resources:
        return _run(args, resources)


def _run(args, resources) -> int:
    import pyarrow.parquet as pq

    window = restate_inputs.committed_reference_window(
        args.facts,
        args.board_failures.parent,
        baseline=args.baseline,
        require_chain=args.require_reference_chain
        or bool(
            os.environ.get("HEADSTART_RESTATE_INPUT_INVENTORY")
            or os.environ.get("HEADSTART_RESTATE_INPUT_REVISION")
        ),
    )
    through = window.through if window is not None and window.committed else None
    runs = window.ticks if through is not None else restate_replay.runs(args.facts)
    if window is not None:
        args.baseline = window.baseline
        if through is None:
            base = window.ticks[0]
            runs = [base] + [r for r in runs if r > base]
    if not runs:
        _log.info(f"no Job facts under {args.facts} yet — nothing to restate")
        return 0
    reads = restate_replay.board_reads(args.facts, through=through)
    baseline_sources = {}
    inherited = {}
    future = []
    baseline_stamp = None
    if args.baseline is not None:
        baseline_file = pq.ParquetFile(args.baseline)
        metadata = baseline_file.schema_arrow.metadata or {}
        if metadata.get(b"baseline") != b"true":
            raise ValueError("--baseline must name a complete reference baseline")
        baseline_stamp = metadata[b"ts"].decode()
        columns = [
            name
            for name in baseline_file.schema_arrow.names
            if name not in {"vector", "row_logits", "title_logits", "description"}
        ]
        for batch in baseline_file.iter_batches(batch_size=4096, columns=columns):
            for row in batch.to_pylist():
                if row["kind"] == "present":
                    inherited[row["id"]] = row
        for path in sorted((args.facts / job_facts.JOB_FACTS).glob("*.parquet")):
            stamp = pq.read_schema(path).metadata[b"stamp"].decode()
            if stamp > baseline_stamp and (through is None or stamp <= through):
                for batch in pq.ParquetFile(path).iter_batches(
                    batch_size=8192, columns=["id", "kind"]
                ):
                    future.extend(
                        r | {"run": stamp}
                        for r in batch.to_pylist()
                        if r["id"] in inherited
                    )
        _log.info(f"exact starting coverage from reference baseline {baseline_stamp}")
    ledger_boards = live_keep_set(args.ledger)
    live = boards_by_canon(ledger_boards)
    keep_set = job_facts.RunScope.of(
        set(), set(), live, board_failures.load(args.board_failures)
    ).keep_set
    _log.info("loading narrow all-Job versions for Dormancy")
    dormant_versions = restate_replay.job_versions(
        args.facts, columns=["id", "kind", "posted_at", "board"], through=through
    )
    if dormant_versions is None and baseline_stamp is None:
        _log.info("no Job facts — nothing to restate")
        return 0
    # Dormancy depends on the actual earlier observations. Rebasing these dates
    # would revive already-Dormant Boards until their next read.
    periods = (
        {}
        if dormant_versions is None
        else restate_served.dormant_periods(dormant_versions, reads, live)
    )
    del dormant_versions
    gc.collect()
    import pyarrow as pa

    pa.default_memory_pool().release_unused()
    _log.info("selecting IDs ever admitted by today's tech gate")
    wanted = (
        restate_replay.eligible_ids(args.facts, tech_filter.is_tech, through=through)
        | inherited.keys()
    )
    _log.info(f"loading wide Job versions for {len(wanted)} eligible IDs")
    versions = restate_replay.job_versions(args.facts, wanted=wanted, through=through)
    if baseline_stamp is not None:
        if versions is None:
            versions = job_facts._schema().empty_table()
            for name in restate_replay.VERSION_COLUMNS:
                versions = versions.append_column(name, pa.array([], pa.string()))
        versions = restate_baseline.seed_versions(
            versions, inherited, baseline_stamp, future
        )
    del inherited, future, wanted
    _log.info(f"{versions.num_rows} Job versions over {len(runs)} runs")
    served = restate_served.served_intervals(
        versions, reads, is_tech=tech_filter.is_tech, live=live, keep_set=keep_set
    )
    del versions
    served = restate_served.clip_dormant(served, periods)
    del periods
    gc.collect()
    pa.default_memory_pool().release_unused()
    head = role_family_classifier.Head(args.classifier)
    if baseline_stamp is not None:
        _log.info("loading immutable observed input versions")
        baseline_sources = resources.enter_context(
            restate_inputs.VersionSources(head.row_vector_dim)
        )
        restate_inputs.load_reference_sources(
            baseline_sources,
            args.facts,
            args.baseline,
            args.board_failures.parent,
            window=window,
            through=through,
        )
        restate_inputs.load_description_sources(
            baseline_sources, args.facts, args.descriptions, through=through
        )
        served = restate_inputs.bind_intervals(
            served, baseline_sources, through=through
        )
    latest_ids = {
        job_id
        for job_id, start in zip(
            served["id"].to_pylist(),
            served[
                "input_from" if "input_from" in served.schema.names else "valid_from"
            ].to_pylist(),
            strict=True,
        )
        if (job_id, start) not in baseline_sources
    }
    _log.info(
        f"loading latest descriptions for {len(latest_ids)} IDs without baseline sources"
    )
    descriptions = _descriptions(args.descriptions, latest_ids)
    _log.info(f"loaded {len(descriptions)} latest descriptions; applying English gate")
    served = restate_served.english_only(
        served, descriptions, is_english, version_sources=baseline_sources
    )
    ids = served["id"].to_pylist()
    served = restate_served.fold_duplicates(
        served,
        ledger_boards,
        site_jobs=workday_site_jobs(args.ledger),
        backing=eightfold_backing.load(),
    )
    _log.info(f"{served.num_rows} served intervals under today's rules")

    families = role_taxonomy.load_families(args.families)
    watchlist = role_taxonomy.load_watchlist(
        args.watchlist or args.families.with_name("role_watchlist.json"), set(families)
    )
    head.check_families(families)
    # The publisher inventories this input's bytes: loading must not migrate its metadata.
    loaded_cache = role_family_classifier.load_cache(args.title_cache, head.version)
    fingerprint = head.inputs_fingerprint
    logits = loaded_cache.title_logits
    if (
        loaded_cache.inputs_fingerprint is not None
        and loaded_cache.inputs_fingerprint != fingerprint
    ):
        _log.info(
            f"title cache {args.title_cache} has different mathematical inputs: starting empty"
        )
        logits = {}
    elif loaded_cache.inputs_fingerprint is None and logits:
        _log.info(
            f"adopting legacy title cache {args.title_cache} in-memory at head version "
            f"{head.version}; assuming weights and encoder unchanged under the matching "
            "version contract; inventoried input remains read-only"
        )
    cache = role_family_classifier.Cache(head.version, dict(logits), fingerprint)
    titles = served["title"].to_pylist()
    if args.encode_budget_seconds > 0:
        # A sibling survives output replacement and stays outside inventoried state.
        runner_cache = args.out.with_name(args.out.name + "-title-cache.parquet")
        if runner_cache.resolve() == args.title_cache.resolve():
            raise ValueError("runner title cache would overwrite the inventoried input")
        added = role_family_classifier.fill(
            cache,
            head,
            titles,
            args.encode_budget_seconds,
            lambda c: role_family_classifier.save_cache(runner_cache, c),
        )
        _log.info(f"encoded {added} title(s) the classifier cache lacked")
    wanted = set(ids) & latest_ids
    served = restate_place.placements(
        served,
        head,
        cache,
        _vectors(args.db, args.facts, wanted),
        descriptions,
        version_sources=baseline_sources,
    )
    _log.info(f"placed {served.num_rows} served intervals in a family and band")
    del descriptions, baseline_sources, ids, titles, wanted, latest_ids
    gc.collect()
    pa.default_memory_pool().release_unused()

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
    count_columns = [
        "id",
        "board",
        "dedup_group",
        "served_from",
        "first_seen",
        "served_to",
        "starts_as",
        "ended_as",
        "family",
        "band",
    ]
    ticks = [
        restate_count.tick_counts(
            served.select(count_columns),
            runs,
            restate_replay.first_reads(reads),
            restate_place.place_of,
        )
    ]
    for watch in watchlist:
        selected = served.filter(
            pa.array(
                [
                    family is not None and watch.matches(title)
                    for family, title in zip(
                        served["family"].to_pylist(),
                        served["title"].to_pylist(),
                        strict=True,
                    )
                ],
                pa.bool_(),
            )
        )
        name = role_taxonomy.WATCH_PREFIX + watch.name
        ticks.append(
            restate_count.tick_counts(
                selected.select(count_columns),
                runs,
                restate_replay.first_reads(reads),
                lambda row, name=name: (name, row["band"]),
            )
        )
    inventory_path = os.environ.get("HEADSTART_RESTATE_INPUT_INVENTORY")
    inventory = json.loads(Path(inventory_path).read_text()) if inventory_path else {}
    input_revision = (
        os.environ.get("HEADSTART_RESTATE_INPUT_REVISION")
        or inventory.get("input_revision")
        or inventory.get("revision")
    )
    rules_fingerprint = trend_reference.freeze_rules(REPO_ROOT, args.facts)
    for n, grouped in enumerate(zip(*ticks, strict=True), 1):
        run = grouped[0][0]
        levels, turnover = Counter(), Counter()
        for stamp, values, events in grouped:
            if stamp != run:
                raise ValueError(
                    "watched-role tick alignment differs from primary history"
                )
            levels.update(values)
            turnover.update(events)
        if broken := restate_count.unbalanced(previous[1], levels, turnover):
            raise ValueError(
                f"tick {run}: stock did not move by its turnover for {len(broken)} "
                f"(board, family, band), e.g. {broken[:3]}"
            )
        trend_history.record_tick(
            args.out, run, levels, turnover, methodology, replayed=previous
        )
        tick_path = args.out / trend_history.DELTAS / job_facts.file_name(run)
        table = pq.read_table(tick_path)
        bound = table.schema.metadata | {
            b"rules_fingerprint": rules_fingerprint.encode()
        }
        if input_revision:
            bound[b"input_revision"] = input_revision.encode()
        pq.write_table(
            table.replace_schema_metadata(bound), tick_path, compression="zstd"
        )
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        active = pc.and_(
            pc.less_equal(served["served_from"], run),
            pc.or_kleene(
                pc.is_null(served["served_to"]), pc.greater(served["served_to"], run)
            ),
        )
        active = pc.and_(active, pc.is_valid(served["family"]))
        placements = served.filter(active).select(
            [
                "id",
                "board",
                "family",
                "band",
                "first_seen",
                "restated_min_years",
                "input_quality",
            ]
        )
        directory = args.out / "placements"
        directory.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            placements.replace_schema_metadata(
                {
                    b"ts": run.encode(),
                    **{
                        key: value
                        for key, value in bound.items()
                        if key in {b"rules_fingerprint", b"input_revision"}
                    },
                }
            ),
            directory / job_facts.file_name(run),
            compression="zstd",
        )
        previous = (run, levels)
        _log.info(f"tick {n}/{len(runs)} {run}: {sum(turnover.values())} moves")
    _log.info(f"restated {len(runs)} ticks under today's rules -> {args.out}")
    code_sha = (
        os.environ.get("HEADSTART_RESTATE_CODE_SHA")
        or subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    )
    quality = Counter(
        flag for flags in served["input_quality"].to_pylist() for flag in flags
    )
    replay = {
        "schema_version": 1,
        "rules_fingerprint": rules_fingerprint,
        "rules_code_sha": code_sha,
        "input_revision": input_revision,
        "first_covered_tick": runs[0],
        "last_covered_tick": runs[-1],
        "inputs": inventory.get("inputs", []),
        "files": [],
        "quality": {"input_counts": dict(quality), "watched_roles": bool(watchlist)},
        "limitations": [
            "History before the complete reference baseline is retained separately, not recomputed."
        ],
    }
    (args.out / "replay.json").write_text(json.dumps(replay, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    log.run_logging_crash(
        _log, main, "restate_run failed — the live Trends are untouched"
    )
