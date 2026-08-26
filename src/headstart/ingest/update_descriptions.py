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

**The store holds text, and membership means exactly that: we have this Job's description.** It
briefly recorded a second kind of entry — a ``null`` meaning "the detail answered and this posting
genuinely has none" — gated on ``Job.detail_fetched``. That was removed: measured 2026-08-26, the
category barely exists (0 of 713 live Jobs sampled across 12 ATSes carried no description, and the
store had accumulated **7** such entries in its lifetime, 0.002% of 328,930), while the flag it
needed was set by one scraper of the nine with a detail pass — so an empty description in the
corpus is, in practice, always a fetch that failed. ``read_store`` skips any legacy ``null`` so
"held" means one thing everywhere; ``--compact`` drops them on its next pass.

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
from typing import NamedTuple

from headstart import log
from headstart.ingest import (
    HELD_DETAILS_PATH,
    PENDING_REDERIVE_PATH,
    REPO_ROOT,
    append_id_list,
)

_log = log.get(__name__, __spec__)

_JOBS = REPO_ROOT / "data" / "jobs" / "tech"
_STORE = REPO_ROOT / "data" / "descriptions"
_PRIOR_META = REPO_ROOT / "data" / "embeddings" / "jobs" / "meta.jsonl"
_BASE = "base.jsonl.gz"


def _fragments(ats_dir: Path) -> list[Path]:
    """The store's files for one ATS in read order: the base, then fragments oldest-first.

    Order is the whole contract — a later entry overwrites an earlier one for the same id, so a
    re-fetched description supersedes what it replaces without anything being deleted.
    """
    if not ats_dir.is_dir():
        return []
    base = ats_dir / _BASE
    return ([base] if base.exists() else []) + sorted(_numbered(ats_dir), key=_sequence)


def _numbered(ats_dir: Path) -> list[Path]:
    """The ATS's fragment files. One filter for readers and writers alike — when only the writer
    screened for a numeric name, a stray file made every *read* raise while writes kept working."""
    return [
        p
        for p in ats_dir.glob("*.jsonl.gz")
        if p.name != _BASE and p.name.split(".", 1)[0].isdigit()
    ]


def _sequence(path: Path) -> int:
    """A fragment's ordinal. Sorted on numerically, not lexicographically: zero-padding only
    orders correctly while the width holds, and a stalled compaction (its workflow step is
    `continue-on-error`) is exactly the case where it would not."""
    return int(path.name.split(".", 1)[0])


def read_store(ats_dir: Path) -> dict[str, str]:
    """``{Job id: description}`` for one ATS. Membership means we hold this Job's text.

    A legacy ``null`` entry — the removed "authoritatively has none" state, 7 of them, all
    eightfold — is skipped rather than returned, so a Job it names is treated as unheld and
    fetched again like any other. Skipping on read (rather than migrating the files) is what
    makes the removal self-healing: ``compact`` rewrites the base from this function, so the
    entries disappear on its next pass with no migration step.
    """
    held: dict[str, str] = {}
    for path in _fragments(ats_dir):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    record = json.loads(line)
                    description = record.get("description")
                    if isinstance(description, str) and description:
                        held[record["id"]] = description
                    else:  # a legacy null, or a later entry blanking an earlier one
                        held.pop(record["id"], None)
    return held


def _write_fragment(ats_dir: Path, records: list[dict]) -> Path:
    """Append one run's changes as the next numbered fragment. Never touches existing files."""
    ats_dir.mkdir(parents=True, exist_ok=True)
    used = [_sequence(p) for p in _numbered(ats_dir)]
    out = ats_dir / f"{max(used, default=0) + 1:04d}.jsonl.gz"
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return out


class Reconciled(NamedTuple):
    """What one ATS's reconcile pass did. Named because it outgrew a positional tuple."""

    filled: int
    learned: int
    #: No fresh text and nothing stored. These Jobs come back unchanged every run: the backlog
    #: that does not shrink on its own, invisible until it was counted. Says nothing about *why*
    #: — see the comment at the branch that counts it.
    unrecorded: int
    rederive_ids: list[str]


def reconcile(jobs_path: Path, ats_dir: Path) -> Reconciled:
    """Fill this ATS's corpus from the store and persist what the run learned.

    Returns a :class:`Reconciled` — corpus rows repaired from the store, descriptions newly
    stored or changed, postings recorded as authoritatively having none, postings left with no
    description and no stored answer either way, and the ids behind the second and third.

    ``rederive_ids`` is the ADR-0062 marking. A Job whose description arrives *now* still carries
    metadata derived without that text, and nothing else would ever revisit it: ``embed_plan``
    skips ids it has embedded, and ``update_meta``'s version sweep only fires on a
    ``DERIVATIONS_VERSION`` bump.
    """
    held = read_store(ats_dir)
    learned: list[dict] = []
    filled = unrecorded = 0

    # The rewrite streams through a temp file rather than buffering the corpus a second time —
    # `held` above already holds this ATS's stored text, and doubling that on a CI box is what
    # this avoids. `.jsonl.tmp` is deliberately outside the `*.jsonl` glob every corpus reader
    # uses, so a crash mid-write leaves an orphan file rather than a half-written corpus.
    tmp = jobs_path.with_suffix(".jsonl.tmp")
    with jobs_path.open(encoding="utf-8") as fh, tmp.open("w", encoding="utf-8") as out:
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
                if held.get(job_id) != fresh:
                    learned.append({"id": job_id, "description": fresh})
            else:
                stored = held.get(job_id)
                if stored:
                    job["description"] = (
                        stored  # a failed fetch must not erase good text
                    )
                    filled += 1
                else:
                    # No text this run and none stored, so the next run starts here again.
                    #
                    # Deliberately NOT called "the detail never ran", and no longer split by
                    # whether it did: measured 2026-08-26, a Job that genuinely carries no
                    # description is vanishingly rare (0 of 713 sampled live across 12 ATSes),
                    # so in practice every Job here is a fetch that failed. The count is exact —
                    # these Jobs really are unrecorded and really do return every run.
                    unrecorded += 1
            out.write(json.dumps(job, ensure_ascii=False) + "\n")

    if learned:
        _write_fragment(ats_dir, learned)
    tmp.replace(jobs_path)
    return Reconciled(filled, len(learned), unrecorded, [r["id"] for r in learned])


def _embedded_ids(meta_path: Path) -> set[str]:
    """Ids the embedding store already holds — empty when there is no store yet (first run)."""
    ids: set[str] = set()
    if not meta_path.exists():
        return ids
    with meta_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                ids.add(json.loads(line)["id"])
    return ids


def _ats_held_ids(ats_dir: Path) -> set[str]:
    """One ATS's held ids — the Jobs whose description text the store carries.

    Reading through :func:`read_store` instead would materialise every description (~1 GB of text)
    to look at the keys. Both callers below want only the keys, in different shapes.

    Applies :func:`read_store`'s own rule rather than counting every line: an entry with no text
    (a legacy ``null``) does not mean we hold anything, and a skip-list that said otherwise would
    tell the scrape not to fetch a description the store cannot supply.
    """
    ids: set[str] = set()
    for path in _fragments(ats_dir):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    record = json.loads(line)
                    description = record.get("description")
                    if isinstance(description, str) and description:
                        ids.add(record["id"])
                    else:
                        ids.discard(record["id"])
    return ids


def held_ids(store_root: Path) -> set[str]:
    """Every Job id the store holds text for, across every ATS — the ADR-0062 gap ledger's input.

    Same membership question :func:`write_held_details` publishes for the scrape, answered in
    memory for callers that need the set rather than the file.
    """
    ids: set[str] = set()
    if not store_root.is_dir():
        return ids
    for ats_dir in sorted(p for p in store_root.glob("*") if p.is_dir()):
        ids |= _ats_held_ids(ats_dir)
    return ids


def write_held_details(store_root: Path, out_path: Path) -> int:
    """Publish every id the store holds text for — the scrape's detail skip-list (ADR-0050).

    The detail pass has nothing left to learn for these Jobs, so the fetch is pure cost.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with gzip.open(out_path, "wt", encoding="utf-8") as dst:
        # Streamed per ATS rather than through `held_ids`: duplicates across fragments are
        # collapsed per ATS, which is all the dedupe this needs, and holding one ATS's keys at a
        # time keeps the whole store's id set off the heap.
        for ats_dir in sorted(p for p in store_root.glob("*") if p.is_dir()):
            seen = _ats_held_ids(ats_dir)
            for job_id in seen:
                dst.write(job_id + "\n")
            written += len(seen)
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
        "--pending-rederive",
        default=str(PENDING_REDERIVE_PATH),
        help="queue of ids whose description arrived this run, for update_meta (ADR-0062)",
    )
    ap.add_argument(
        "--prior-meta",
        default=str(_PRIOR_META),
        help="the embedding store's metadata; only Jobs it already holds are queued to "
        "re-derive (a Job first embedded this run needs no repair)",
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
    # Only Jobs the store *already holds* can need re-derivation. A Job first embedded this run gets
    # its metadata written by `doc_prep.to_meta` from this very description, so queueing it would
    # re-run the cascade on a value it just produced — and every new Job on every listing-only Board
    # is "learned", which is tens of thousands per run. Left unfiltered the queue is never small,
    # and a non-empty queue makes the merge load the whole ~1 GB description store every run rather
    # than only on a sweep.
    embedded = _embedded_ids(Path(args.prior_meta))
    _log.info(f"prior store: {len(embedded):,} already-embedded ids")

    queued = unrecorded = 0
    for path in sorted(jobs.glob("*.jsonl")):
        ats = path.stem
        done = reconcile(path, store / ats)
        rederive = [i for i in done.rederive_ids if i in embedded]
        # Appended per ATS rather than accumulated and written once: the queue is what stops these
        # Jobs from keeping embed-time numbers forever, so a crash halfway through the corpus must
        # not lose the ids of the ATSes already reconciled.
        append_id_list(Path(args.pending_rederive), rederive)
        queued += len(rederive)
        unrecorded += done.unrecorded
        _log.info(
            f"{ats}: filled {done.filled:,} from the store, learned {done.learned:,}, "
            f"queued {len(rederive):,} to re-derive"
            + (f", {done.unrecorded:,} still unrecorded" if done.unrecorded else "")
        )
    _log.info(
        f"skip-list: {write_held_details(store, Path(args.held_details)):,} Jobs held"
    )
    _log.info(f"re-derive queue: {queued:,} newly stored -> {args.pending_rederive}")
    if unrecorded:
        _log.info(
            f"{unrecorded:,} Job(s) carry no description and none is stored — nothing was "
            "learned for them this run, so they stay outside Tier-2 extraction until some later "
            "run's detail fetch supplies the text (ADR-0050)"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
