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
from pathlib import Path

from headstart import log
from headstart.board_priority import load_scores
from headstart.corpus import board_of, iter_jobs
from headstart.ingest import REPO_ROOT
from headstart.ingest.binpack import (  # noqa: F401 (lpt_pack re-exported for tests)
    lpt_pack,
    shard_count,
)
from headstart.ingest.doc_prep import (
    _MAX_SEQ_TOKENS,
    bucket_for,
    build_doc,
    is_english,
    to_meta,
)
from headstart.search import MODEL

_log = log.get(__name__, __spec__)

_SOURCE = REPO_ROOT / "data" / "jobs" / "tech"
_PRIOR_META = REPO_ROOT / "data" / "embeddings" / "jobs" / "meta.jsonl"
_PRIORITY = REPO_ROOT / "data" / "state" / "board_priority.csv"
# Rides to the merge stage inside the corpus-state artifact it already downloads (ADR-0050).
_UPGRADES = REPO_ROOT / "data" / "state" / "pending_upgrades.txt"
_OUT = REPO_ROOT / "data" / "embeddings" / "assignments"

# Measured CPU seconds-per-Doc per Bucket, from the 2026-07-24 ubuntu-latest run recorded in
# docs/AI_Integration/embedding-throughput.md. Hardcoded (not derived from live CI logs) for
# Phase 1 (ADR-0025): deterministic, one dict to edit. Refresh with the recipe in that doc
# (`gh run view <id> --log | grep '[embed_run]'`) when runner performance drifts.
_S_PER_DOC = {512: 0.8, 1024: 1.7, 2048: 4.4, 4096: 18.0}
_MAX_SHARDS = 15  # == pipeline.yml `max-parallel`; Phase 1 runs one shard per lane
_TARGET_SECONDS = (
    20 * 60
)  # per-shard makespan target; sized so a big backlog saturates the lanes


def _detail_pass_atses() -> set[str]:
    """ATSes whose ``description`` comes from a per-Job detail fetch, so it can go missing."""
    from headstart.scrapers.registry import SCRAPERS

    return {ats for ats, scraper in SCRAPERS.items() if scraper.has_detail_pass}


def _prior_rows(path: Path) -> tuple[set[str], set[str]]:
    """``(embedded ids, ids whose vector was built without a description)`` — both empty on a
    first run (no meta.jsonl yet).

    The second set is what makes a title-only vector repairable (ADR-0050). ``has_description``
    is read straight from the row where present. Where it is **absent** — every row written
    before ADR-0050 — it is inferred: assume degraded on an ATS with a detail pass, and assume
    fine on a listing-only one, whose description arrives with the listing and so cannot have
    been lost. That inference bounds the migration to ~22k re-embeds instead of ~186k; without
    it, repairing ~16,771 degraded vectors would re-encode the whole store.
    """
    if not path.exists():
        return set(), set()
    detail_pass = _detail_pass_atses()
    ids: set[str] = set()
    degraded: set[str] = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            ids.add(row["id"])
            flag = row.get("has_description")
            if flag is False or (flag is None and row.get("ats") in detail_pass):
                degraded.add(row["id"])
    return ids, degraded


def _load_tokenizer():
    """The model's tokenizer — the same one ``SentenceTransformer(MODEL)`` wraps, loaded standalone
    so the planner never pulls the encoder weights (it only needs token counts)."""
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)


def _token_lengths(tok, docs: list[str]) -> list[int]:
    """Exact token counts (same truncation as embed_run), batched with a progress stream."""
    lengths: list[int] = []
    for s in range(0, len(docs), 1024):
        enc = tok(docs[s : s + 1024], truncation=True, max_length=_MAX_SEQ_TOKENS)
        lengths.extend(len(ids) for ids in enc["input_ids"])
        _log.info(f"tokenized {len(lengths)}/{len(docs)}")
    return lengths


def _write_plan(
    out_dir: Path, *, shards: list[int], count: int, makespan: float, loads: list[float]
) -> None:
    """Persist plan.json (the workflow reads ``shards`` + ``count``) and echo the matrix to stdout."""
    plan = {
        "shards": shards,
        "count": count,
        "makespan_s": round(makespan, 1),
        "per_shard_s": [round(x, 1) for x in loads],
    }
    (out_dir / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(json.dumps({"shards": shards, "count": count}), flush=True)


def main() -> int:
    log.setup()
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

    prior, degraded = _prior_rows(Path(args.prior_meta))
    scores = load_scores(Path(args.priority))
    _log.info(
        f"prior store: {len(prior)} embedded ids ({len(degraded)} without a description)"
    )

    # Collect the new English Docs — same gate/build/meta as embed_run, via the shared module.
    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []
    boards: list[str] = []
    upgrades: list[str] = []
    scanned = already = dropped = 0
    for job in iter_jobs(args.source):
        scanned += 1
        jid = job.get("id") or ""
        upgrading = False
        if jid in prior:
            # A Job already in the store is normally done. The exception is a vector built
            # without a description whose description we now have — re-embed it, and record the
            # id so the merge stage evicts the stale row first (ADR-0050). Nothing else can
            # reach these: `embed_plan` skips by id, so they would stay title-only forever.
            if not (jid in degraded and (job.get("description") or "").strip()):
                already += 1
                continue
            upgrading = True
        if not is_english(job.get("title") or "", job.get("description") or ""):
            dropped += 1
            continue
        # Listed only now that the Doc is actually planned. Listing before the English gate put
        # ids on the upgrade list that no shard would ever embed — an English title over a German
        # body is common — and `embed_merge` holds any id whose replacement never arrives, so
        # those would be held on every run forever while `index sync` churned their rows.
        if upgrading:
            upgrades.append(jid)
        ids.append(jid)
        docs.append(build_doc(job))
        metas.append(to_meta(job))
        boards.append(board_of(jid))
    _log.info(
        f"new Docs: {len(docs)} (scanned {scanned}, already {already}, non-English {dropped}, "
        f"upgraded {len(upgrades)})"
    )
    # Always rewritten, empty included: a stale list from a prior run would have the merge stage
    # evict rows nothing is re-embedding this time.
    upgrades_path = Path(args.upgrades_out)
    upgrades_path.parent.mkdir(parents=True, exist_ok=True)
    upgrades_path.write_text("".join(f"{jid}\n" for jid in upgrades), encoding="utf-8")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("shard-*.jsonl"):
        stale.unlink()  # a shorter plan must not leave a prior run's extra shards behind

    if not docs:
        _write_plan(out_dir, shards=[], count=0, makespan=0.0, loads=[])
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
        out_dir, shards=list(range(m)), count=len(keep), makespan=makespan, loads=loads
    )
    _log.info(
        f"{len(keep)} Docs across {m} shards; predicted makespan ~{makespan / 60:.1f} min "
        f"(total work Σ {total_cost / 60:.1f} min)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
