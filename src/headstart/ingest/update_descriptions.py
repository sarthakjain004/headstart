#!/usr/bin/env python3
"""Reconcile this run's tech corpus with the persistent **description store** (ADR-0050).

Runs in the join stage, right after ``filter_tech``, and moves descriptions in both directions::

    python -m headstart.ingest.update_descriptions            # the pipeline step
    python -m headstart.ingest.update_descriptions --compact   # fold fragments into the base

**corpus -> store.** Every description this run actually fetched is persisted, so it survives a
later run whose fetch fails. Before this, a description was read exactly once — at embed time — and
then discarded, which is why ~16,771 Jobs sit in the index as title-only vectors that no clean run
could ever repair (ADR-0047, ADR-0048).

**store -> corpus.** Where the scrape left a Job's description empty, the stored text is written
back into ``data/jobs/tech/{ats}.jsonl``. This is what keeps every downstream reader unchanged:
``embed_plan``, ``doc_prep`` and ``experience.extract`` go on reading the corpus, and the corpus is
simply correct again. It also repairs ``scripts/enrich/experience_coverage.py``, which ADR-0048
left reading ``description: null`` for every already-embedded Eightfold Job and reporting it as no
coverage rather than as not measured.

**The store answers two questions, so it records two kinds of entry.** A text entry means we hold
this Job's description. A ``null`` entry means the detail pass *answered* and this posting has no
description — authoritative absence, recorded so the Job is not re-fetched every run forever. A Job
absent from the store entirely is one we have never settled, and it keeps being fetched. That
distinction rides in on ``Job.detail_fetched``; without it, a failed fetch and a genuinely
description-less posting are the same empty string in the corpus.

**Writes are append-only** (ADR-0050). Each run writes one small ``{seq}.jsonl.gz`` fragment per
ATS holding only what changed; the ``base.jsonl.gz`` is rewritten only by ``--compact``. Readers
take base-then-fragments in order, last write winning, which is also the update path an
organic-edit detector would need later (ADR-0021). Rewriting the whole store every run would mint
~174 MB of fresh blobs per run — the mistake ``data/lancedb`` was moved away from when it filled
the 100 GB quota in ~45 runs.

The skip-list falls out of the store rather than out of the embedding store: a Job is skipped when
we *hold its detail*, which is what CONTEXT.md's **Detail pass** entry has always claimed. That
also decouples eviction from the scrape — evicting a vector no longer discards the text behind it,
so ADR-0048's "eviction silently defeats itself" trap cannot arise.
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from headstart import log
from headstart.ingest import HELD_DETAILS_PATH, REPO_ROOT

_log = log.get(__name__, __spec__)

_JOBS = REPO_ROOT / "data" / "jobs" / "tech"
_STORE = REPO_ROOT / "data" / "descriptions"
_BASE = "base.jsonl.gz"
_MISSING = object()  # distinguishes "no entry" from an entry whose value is None


def _fragments(ats_dir: Path) -> list[Path]:
    """The store's files for one ATS in read order: the base, then fragments oldest-first.

    Order is the whole contract — a later entry overwrites an earlier one for the same id, so a
    re-fetched description supersedes what it replaces without anything being deleted.
    """
    if not ats_dir.is_dir():
        return []
    base = ats_dir / _BASE
    rest = sorted(p for p in ats_dir.glob("*.jsonl.gz") if p.name != _BASE)
    return ([base] if base.exists() else []) + rest


def read_store(ats_dir: Path) -> dict[str, str | None]:
    """``{Job id: description}`` for one ATS, where ``None`` means *authoritatively has none*.

    Membership answers "do we hold this Job's detail?"; the value answers "what is it?".
    """
    held: dict[str, str | None] = {}
    for path in _fragments(ats_dir):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    record = json.loads(line)
                    held[record["id"]] = record.get("description")
    return held


def _write_fragment(ats_dir: Path, records: list[dict]) -> Path:
    """Append one run's changes as the next numbered fragment. Never touches existing files."""
    ats_dir.mkdir(parents=True, exist_ok=True)
    used = [int(p.name[:4]) for p in ats_dir.glob("*.jsonl.gz") if p.name[:4].isdigit()]
    out = ats_dir / f"{max(used, default=0) + 1:04d}.jsonl.gz"
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return out


def reconcile(jobs_path: Path, ats_dir: Path) -> tuple[int, int, int]:
    """Fill this ATS's corpus from the store and persist what the run learned.

    Returns ``(filled, learned, settled)`` — corpus rows repaired from the store, descriptions
    newly stored or changed, and postings recorded as authoritatively having none.
    """
    held = read_store(ats_dir)
    learned: list[dict] = []
    filled = settled = 0

    rows: list[str] = []
    with jobs_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            job = json.loads(line)
            job_id = job["id"]
            fresh = (job.get("description") or "").strip()
            if fresh:
                # Fresh text always wins: a re-fetch is more current than the store, and this is
                # the only path by which an edited posting reaches it.
                if held.get(job_id, _MISSING) != fresh:
                    learned.append({"id": job_id, "description": fresh})
            else:
                stored = held.get(job_id, _MISSING)
                if isinstance(stored, str) and stored:
                    job["description"] = (
                        stored  # a failed fetch must not erase good text
                    )
                    filled += 1
                elif stored is _MISSING and job.get("detail_fetched"):
                    # The detail answered and there is no description. Record that, or this Job is
                    # re-fetched on every run for the rest of its life.
                    learned.append({"id": job_id, "description": None})
                    settled += 1
            rows.append(json.dumps(job, ensure_ascii=False))

    if learned:
        _write_fragment(ats_dir, learned)
    jobs_path.write_text("\n".join(rows) + "\n" if rows else "", encoding="utf-8")
    return filled, len(learned) - settled, settled


def write_held_details(store_root: Path, out_path: Path) -> int:
    """Publish every id the store has settled — the scrape's detail skip-list (ADR-0050).

    Both entry kinds belong here: we hold the text, or we know there is none. Either way the
    detail pass has nothing left to learn, so the fetch is pure cost.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with gzip.open(out_path, "wt", encoding="utf-8") as dst:
        for ats_dir in sorted(p for p in store_root.glob("*") if p.is_dir()):
            for job_id in read_store(ats_dir):
                dst.write(job_id + "\n")
                written += 1
    return written


def compact(ats_dir: Path) -> int:
    """Fold an ATS's fragments into one base file and delete them. Returns the rows kept.

    Runs in ``cleanup-index``, not in the pipeline: it is the only step that rewrites the big file,
    which is exactly the cost append-only writes exist to avoid paying every run.
    """
    held = read_store(ats_dir)
    if not held:
        return 0
    tmp = ats_dir / (_BASE + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for job_id, description in held.items():
            fh.write(
                json.dumps(
                    {"id": job_id, "description": description}, ensure_ascii=False
                )
                + "\n"
            )
    tmp.replace(ats_dir / _BASE)
    for path in ats_dir.glob("*.jsonl.gz"):
        if path.name != _BASE:
            path.unlink()
    return len(held)


def main() -> int:
    log.setup()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jobs", default=str(_JOBS), help="tech corpus dir")
    ap.add_argument("--store", default=str(_STORE), help="description store dir")
    ap.add_argument(
        "--held-details", default=str(HELD_DETAILS_PATH), help="skip-list to publish"
    )
    ap.add_argument(
        "--compact",
        action="store_true",
        help="fold each ATS's fragments into its base file and stop",
    )
    args = ap.parse_args()
    store = Path(args.store)

    if args.compact:
        for ats_dir in sorted(p for p in store.glob("*") if p.is_dir()):
            _log.info(f"{ats_dir.name}: compacted to {compact(ats_dir):,} rows")
        return 0

    jobs = Path(args.jobs)
    if not jobs.is_dir():
        _log.warning(f"no tech corpus at {jobs} — nothing to reconcile")
        return 0
    for path in sorted(jobs.glob("*.jsonl")):
        ats = path.stem
        filled, learned, settled = reconcile(path, store / ats)
        _log.info(
            f"{ats}: filled {filled:,} from the store, learned {learned:,}, "
            f"settled {settled:,} as having none"
        )
    _log.info(
        f"skip-list: {write_held_details(store, Path(args.held_details)):,} Jobs held"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
