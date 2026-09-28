#!/usr/bin/env python3
"""Plan the embed fan-out — the embed-planner half of ADR-0025 Phase 1.

Runs once, after the tech filter, before the embed matrix. It:

1. diffs the tech corpus (``data/jobs/tech``) against the prior store's ``meta.jsonl`` to find
   the **new** ids (the ones an embed run would encode this time);
2. applies the *same* prep as ``embed_run`` — English gate, doc build, typed metadata — via
   the shared ``headstart.ingest.doc_prep`` (so a sharded Doc is byte-identical to the monolith's);
3. tokenizes each Doc with the model's tokenizer and sorts it into a token-length **Bucket**;
4. **LPT bin-packs** the Docs across a dynamic number of shards (≤ ``--max-shards``) by their
   measured per-Bucket cost, so each shard's makespan is balanced (cost is heavy-tailed — a
   cost-blind split straggles on the 4096-token Docs);
5. writes one ``shard-{k}.jsonl`` assignment per shard (``{doc, bucket, tokens, meta}`` lines,
   ordered cheap-first then board-priority-desc so a time-boxed shard banks the best Docs first —
   ADR-0022; ``tokens`` is the exact count the shard length-sorts batches on, ADR-0029)
   and a ``plan.json`` (``shards`` matrix + ``count`` + predicted makespan) the workflow reads.

The planner touches only ``meta.jsonl`` (ids, to diff) — never the vectors or the LanceDB — so it
stays a light, single job. The embed shards are stateless: everything they need is in their file.

Run: python -m headstart.ingest.embed_plan [--max-shards 15] [--limit N]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Iterator
from itertools import zip_longest
from pathlib import Path
from typing import NamedTuple

from headstart import log
from headstart.boards.board_identity import ats_of, board_of
from headstart.boards.priority_ledger import load_scores
from headstart.embedding_conventions import MODEL, MODEL_CODE_REVISION, MODEL_REVISION
from headstart.ingest import (
    PENDING_NON_ENGLISH_PATH,
    PENDING_UPGRADES_PATH,
    REPO_ROOT,
    observability,
    read_id_list,
    shard_plan,
)
from headstart.ingest.binpack import (
    lpt_pack,
    shard_count,
)
from headstart.ingest.corpus import iter_jobs
from headstart.ingest.doc_prep import (
    MAX_SEQ_TOKENS,
    bucket_for,
    build_doc,
    doc_hash,
    is_english,
    to_meta,
)

_log = log.get(__name__, __spec__)

_SOURCE = REPO_ROOT / "data" / "jobs" / "tech"
_PRIOR_META = REPO_ROOT / "data" / "embeddings" / "jobs" / "meta.jsonl"
_PRIORITY = REPO_ROOT / "data" / "state" / "board_priority.csv"
# Rides to the merge stage inside the corpus-state artifact it already downloads (ADR-0050).
_UPGRADES = PENDING_UPGRADES_PATH
_OUT = REPO_ROOT / "data" / "embeddings" / "assignments"
#: Served rows that failed the English gate on 2026-09-29, re-gated once (ADR-0286): most were
#: embedded before the gate existed, so no re-evaluation would otherwise reach them.
_REGATE = REPO_ROOT / "config" / "regate_english.txt"

# Measured CPU seconds-per-Doc per Bucket. Hardcoded (not derived from live CI logs) for Phase 1
# (ADR-0025): deterministic, one dict to edit. Refresh with the recipe in
# docs/AI_Integration/embedding-throughput.md (`gh run view <id> --log | grep '[embed_run]'`) when
# runner performance drifts. Last refreshed from all 14 embed shards of the eight runs
# 36200233818-36221241950 (2026-09-25/26), timing each `[embed_run] bucket` line to the next:
# 211/433/559/30 Docs at 0.60/1.46/2.22/4.88 s. The 2026-07-24 values (0.8/1.7/4.4/18.0) had
# every shard encoding in 0.32-0.71 of its predicted time.
_S_PER_DOC = {512: 0.6, 1024: 1.5, 2048: 2.2, 4096: 4.9}
_MAX_SHARDS = 15  # == pipeline.yml `max-parallel`; Phase 1 runs one shard per lane
# Per-shard makespan target: `binpack.shard_count` spins `ceil(total_cost / this)` shards, clamped
# to [1, _MAX_SHARDS] when there is work. This was 20 min, "sized so a big backlog saturates the
# lanes" — right for the backlog era, wrong for a steady state three orders of magnitude smaller.
# Measured over the four full runs of 2026-09-09, all on SHA fd15455 (34312743097, 34316866965,
# 34321068300, 34327339789): 257-450 new Docs, 714-1,146 s of planned cost, so `ceil(cost / 1200)`
# was **always exactly 1** and the matrix took one of the 15 lanes every run — 6.2-13.5 min of
# serial CPU on the critical path. At 300 s those four runs plan 3, 3, 3 and 4 shards.
#
# Plans above ~18,000 s are unchanged (both values clamp at _MAX_SHARDS); everything from ~300 s
# up to that moves onto more lanes, which is the point. Fan-out is cheap but not free — each added
# lane still pays the ~2.4 min job setup — and the 6-9 min/run saving is a projection, not a
# measurement of a multi-shard run: docs/pipeline/2026-09-09_five-run-log-review.md §3 has the
# per-lane costs and the workings.
#
# Lowered to 165 s with the 2026-09-26 recalibration above, which cut a plan's cost to ~0.55 of
# what the old table said: 300 x 0.55. Replayed on the seven runs 36200233818-36218633315, it
# plans the shards they ran on six of the seven (36218633315 would take 2, not 1). Keeping 300 s
# would have halved the fan-out on the same work.
_TARGET_SECONDS = 165.0
# The most edited Jobs re-embedded per run (ADR-0285). ADR-0207's change ledger averaged about
# 285 replaced descriptions a run from 2026-09-24 to 2026-09-29, bursts included; the bound is for
# a scraper change that rewrites every description of an ATS at once. It is spent across ATSes in
# turn (`_edits_in_turn`). The rest are re-embedded when their Board is next in a Slice, since
# their stored fingerprint still differs.
_MAX_EDIT_REEMBEDS = 2000


class PriorRows(NamedTuple):
    """What the prior store's ``meta.jsonl`` says about the vectors it holds."""

    #: Every embedded id.
    ids: set[str]
    #: The ids whose vector was built without a description (ADR-0050).
    degraded: set[str]
    #: ``{id: doc_hash}``, the fingerprint of the raw title and description each vector was built
    #: from (ADR-0285); a row embedded before it existed has none until ``update_meta`` stamps it.
    hashes: dict[str, str]


def _prior_rows(path: Path) -> PriorRows:
    """The prior store's embedded ids, degraded ids and fingerprints — all empty on a first run
    (no meta.jsonl yet).

    ``degraded`` is what makes a title-only vector repairable (ADR-0050), and it is now read
    straight from ``has_description`` on every row. It used to be **inferred** where the flag was
    absent — every row written before ADR-0050 — by assuming degraded on any ATS with a detail
    pass. That guess conflated "this ATS fetches descriptions separately" with "that fetch
    failed", and measurement killed it: of the 152,383 rows it condemned, ``experience_source ==
    "regex"`` proves 66,296 were built from a description after all, while ADR-0050 measured the
    genuinely title-only population at ~16,771. ``update_meta`` now backfills the real flag
    (ADR-0062), so there is nothing left to guess about.
    """
    if not path.exists():
        return PriorRows(set(), set(), {})
    ids: set[str] = set()
    degraded: set[str] = set()
    hashes: dict[str, str] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            ids.add(row["id"])
            if row.get("has_description") is False:
                degraded.add(row["id"])
            if row.get("doc_hash"):
                hashes[row["id"]] = row["doc_hash"]
    return PriorRows(ids, degraded, hashes)


def _edits_in_turn(edits: list[dict], scores: dict[str, float]) -> Iterator[dict]:
    """Edited Jobs in the order the re-embed cap is spent on them (ADR-0285's amendment): one ATS
    at a time in turn, and within an ATS its highest-priority Boards first.

    Corpus order is ATS-file order, so a backlog on one ATS used to spend the whole cap before a
    later file was read: on 2026-09-29, SuccessFactors' 9,070 pending re-embeds left Workday's
    1,911, WP Job Openings' 1,511 and Zoho's 929 with none. Board priority alone would not have
    helped, because SuccessFactors also holds the highest-scored Boards among them."""
    by_ats: dict[str, list[dict]] = defaultdict(list)
    for job in sorted(edits, key=lambda j: -scores.get(board_of(j["id"]), 0.0)):
        by_ats[ats_of(job["id"])].append(job)
    for turn in zip_longest(*by_ats.values()):
        yield from (job for job in turn if job is not None)


def _load_tokenizer():
    """The model's tokenizer — the same one ``SentenceTransformer(MODEL)`` wraps, loaded standalone
    so the planner never pulls the encoder weights (it only needs token counts). Pinned like
    ``open_model``: the load reads the model config, whose code comes from ``nomic-bert-2048``."""
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(
        MODEL,
        revision=MODEL_REVISION,
        trust_remote_code=True,
        code_revision=MODEL_CODE_REVISION,
    )


def _token_lengths(tok, docs: list[str]) -> list[int]:
    """Exact token counts (same truncation as embed_run), batched with a progress stream."""
    lengths: list[int] = []
    for s in range(0, len(docs), 1024):
        enc = tok(docs[s : s + 1024], truncation=True, max_length=MAX_SEQ_TOKENS)
        lengths.extend(len(ids) for ids in enc["input_ids"])
        _log.info(f"tokenized {len(lengths)}/{len(docs)}")
    return lengths


def _write_plan(out_dir: Path, plan: shard_plan.EmbedPlan) -> None:
    """Persist plan.json (the workflow reads ``shards`` + ``count``) and echo the matrix to stdout."""
    (out_dir / "plan.json").write_text(plan.to_json(), encoding="utf-8")
    print(json.dumps({"shards": plan.shards, "count": plan.count}), flush=True)


def main() -> int:
    log.setup()
    log.context("embed_plan")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--source",
        default=str(_SOURCE),
        help="tech corpus dir (default: data/jobs/tech)",
    )
    ap.add_argument(
        "--prior-meta",
        default=str(_PRIOR_META),
        help="prior store meta.jsonl to diff against",
    )
    ap.add_argument(
        "--priority",
        default=str(_PRIORITY),
        help="board_priority.csv for within-shard ordering",
    )
    ap.add_argument(
        "--out-dir", default=str(_OUT), help="where to write shard-*.jsonl + plan.json"
    )
    ap.add_argument(
        "--upgrades-out",
        default=str(_UPGRADES),
        help="where to list ids whose stale title-only row the merge stage must evict "
        "before re-adding (ADR-0050)",
    )
    ap.add_argument(
        "--non-english-out",
        default=str(PENDING_NON_ENGLISH_PATH),
        help="where to list held ids whose text no longer passes the English gate, for the "
        "merge stage to drop (ADR-0286)",
    )
    ap.add_argument(
        "--regate",
        default=str(_REGATE),
        help="held ids to put through the English gate once more this run (ADR-0286)",
    )
    ap.add_argument(
        "--max-shards",
        type=int,
        default=_MAX_SHARDS,
        help="fan-out cap (== workflow max-parallel)",
    )
    ap.add_argument(
        "--target-seconds",
        type=float,
        default=_TARGET_SECONDS,
        help="per-shard makespan target",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="admission control: keep only the top-priority N new Docs (0 = all)",
    )
    args = ap.parse_args()

    prior, degraded, hashes = _prior_rows(Path(args.prior_meta))
    regate = read_id_list(Path(args.regate))
    scores = load_scores(Path(args.priority))
    # An empty ledger is not an error — ordering just degrades to corpus order — so say it here.
    _log.info(f"priority: {len(scores)} Board scores from {args.priority}")
    _log.info(
        f"prior store: {len(prior)} embedded ids ({len(degraded)} without a description)"
    )

    # Collect the new English Docs — same gate/build/meta as embed_run, via the shared module.
    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []
    boards: list[str] = []
    upgrades: list[str] = []
    non_english: list[str] = []
    scanned = already = dropped = 0
    edited = deferred = 0
    progress = observability.PreparationProgress(_log)
    edits: list[dict] = []  # planned after the scan, by `_edits_in_turn`

    def add_doc(job: dict) -> None:
        ids.append(job["id"])
        docs.append(build_doc(job))
        metas.append(to_meta(job))
        boards.append(board_of(job["id"]))

    for job in iter_jobs(args.source):
        scanned += 1
        jid = job.get("id") or ""
        upgrading = False
        is_edit = False
        if jid in prior:
            # A Job already in the store is normally done. Two exceptions are re-embedded, their
            # ids recorded so the merge stage evicts the stale row first. A vector built without
            # a description whose description we now have (ADR-0050). And a vector whose text
            # has changed since, which `doc_hash` shows (ADR-0285): an edited posting, or a
            # clone that was rewritten. `embed_plan` skips by id, so nothing else reaches them.
            # Each goes through the English gate again, as does a Job on the one-off re-gate
            # list, which is only checked, never re-embedded (ADR-0286).
            described = jid in degraded and (job.get("description") or "").strip()
            # No description over a vector built from one is a failed fetch, not an edit.
            # Re-embedding it would build a title-only vector that ADR-0050 rebuilds once the
            # text is back (ADR-0285's amendment).
            stored = (
                hashes.get(jid)
                if jid in degraded or (job.get("description") or "").strip()
                else None
            )
            is_edit = not described and stored is not None and stored != doc_hash(job)
            if not (described or is_edit or jid in regate):
                already += 1
                progress.report(scanned, len(docs), already, dropped)
                continue
            if is_edit:
                edits.append(job)
                progress.report(scanned, len(docs), already, dropped)
                continue
            upgrading = bool(described or is_edit)
        if not is_english(job.get("title") or "", job.get("description") or ""):
            # A held Job re-evaluated here is served, on an English vector, while its text
            # now fails the gate a new Job must pass. List it for the merge to drop from the
            # store; sync then evicts its row like any Job that left (ADR-0286).
            if jid in prior:
                non_english.append(jid)
            dropped += 1
            progress.report(scanned, len(docs), already, dropped)
            continue
        if jid in prior and not upgrading:
            # On the re-gate list only, and still English: nothing to re-embed.
            already += 1
            progress.report(scanned, len(docs), already, dropped)
            continue
        # Listed only now that the Doc is actually planned. Listing before the English gate put
        # ids on the upgrade list that no shard would ever embed — an English title over a German
        # body is common — and `embed_merge` holds any id whose replacement never arrives, so
        # those would be held on every run forever while `index sync` churned their rows.
        if upgrading:
            upgrades.append(jid)
        add_doc(job)
        progress.report(scanned, len(docs), already, dropped)
    for job in _edits_in_turn(edits, scores):
        if edited >= _MAX_EDIT_REEMBEDS:
            deferred += 1
        elif not is_english(job.get("title") or "", job.get("description") or ""):
            dropped += 1
        else:
            upgrades.append(job["id"])
            edited += 1
            add_doc(job)
    _log.info(
        f"new Docs: {len(docs)} (scanned {scanned}, already {already}, non-English {dropped}, "
        f"upgraded {len(upgrades)})"
    )
    if edited:
        # Its own line: `scripts/runlog/fanout_plan.py` parses the one above to its closing paren.
        _log.info(
            f"edited Jobs: {edited} of the upgrades re-embed changed text (ADR-0285)"
        )
    if deferred:
        _log.info(
            f"edited Jobs: {deferred} more deferred past the cap of {_MAX_EDIT_REEMBEDS} a run "
            "(ADR-0285) — re-embedded when their Board is next read"
        )
    # Always rewritten, empty included: a stale list from a prior run would have the merge stage
    # evict rows nothing is re-embedding this time.
    upgrades_path = Path(args.upgrades_out)
    upgrades_path.parent.mkdir(parents=True, exist_ok=True)
    upgrades_path.write_text("".join(f"{jid}\n" for jid in upgrades), encoding="utf-8")
    # Rewritten every run for the same reason: a stale list would drop Jobs nothing re-gated.
    non_english_path = Path(args.non_english_out)
    non_english_path.parent.mkdir(parents=True, exist_ok=True)
    non_english_path.write_text(
        "".join(f"{jid}\n" for jid in non_english), encoding="utf-8"
    )
    if non_english:
        _log.info(
            f"held Jobs no longer English: {len(non_english)} listed for the merge to drop "
            "(ADR-0286)"
        )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("shard-*.jsonl"):
        stale.unlink()  # a shorter plan must not leave a prior run's extra shards behind

    if not docs:
        _write_plan(
            out_dir,
            shard_plan.EmbedPlan(shards=[], count=0, makespan_s=0.0, per_shard_s=[]),
        )
        _log.info("nothing new to embed — emitted empty plan")
        return 0

    tok = _load_tokenizer()
    lengths = _token_lengths(
        tok, docs
    )  # kept: the shard length-sorts on these (ADR-0029)
    buckets = [bucket_for(n) for n in lengths]
    costs = [_S_PER_DOC[b] for b in buckets]

    # Admission control (optional): keep the top-priority N that fit, bank the rest to next run's diff.
    keep = list(range(len(docs)))
    if args.limit and len(keep) > args.limit:
        keep.sort(key=lambda i: (scores.get(boards[i], 0.0), -costs[i]), reverse=True)
        keep = sorted(keep[: args.limit])
        _log.info(f"admission: capped {len(docs)} -> {len(keep)} top-priority Docs")

    sel_costs = [costs[i] for i in keep]
    total_cost = sum(sel_costs)
    m = shard_count(total_cost, len(keep), args.max_shards, args.target_seconds)
    assign, loads = lpt_pack(sel_costs, m)

    # Group each shard's Docs, ordered cheap-first then priority-desc (ADR-0022).
    shard_items: list[list[int]] = [[] for _ in range(m)]
    for local, i in enumerate(keep):
        shard_items[assign[local]].append(i)
    for k in range(m):
        shard_items[k].sort(key=lambda i: (buckets[i], -scores.get(boards[i], 0.0)))
        path = out_dir / f"shard-{k}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for i in shard_items[k]:
                fh.write(
                    json.dumps(
                        {
                            "doc": docs[i],
                            "bucket": buckets[i],
                            # exact count, so the shard can length-sort batches without
                            # re-tokenizing or guessing from characters (ADR-0029)
                            "tokens": lengths[i],
                            "meta": metas[i],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        _log.info(
            f"shard {k}: {len(shard_items[k])} docs, ~{loads[k] / 60:.1f} min -> {path}"
        )

    makespan = max(loads) if loads else 0.0
    _write_plan(
        out_dir,
        shard_plan.EmbedPlan(
            shards=list(range(m)),
            count=len(keep),
            makespan_s=makespan,
            per_shard_s=loads,
        ),
    )
    _log.info(
        f"{len(keep)} Docs across {m} shards; predicted makespan ~{makespan / 60:.1f} min "
        f"(total work Σ {total_cost / 60:.1f} min)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
