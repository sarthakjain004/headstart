#!/usr/bin/env python3
"""Refresh the embedding store's **metadata** so a fix reaches rows already embedded (ADR-0061).

    python -m headstart.ingest.update_meta        # the pipeline step, in the merge job

Every column of ``data/embeddings/jobs/meta.jsonl`` used to be written exactly once, at embed time,
and ``embed_plan`` skips ids it has already embedded — so nothing ever re-read a Job after its first
embedding. A corrected extractor reached new Jobs only, and a Board that edited a posting's salary
served the old one forever. This is the ADR-0048 trap in general form; ADR-0050 solved it for
description *text*, and this module solves it for everything derived from or observed alongside it.

Two passes, over the same single rewrite:

**Facts** — ``salary``, ``location``, ``remote``, … are *observed*, so they are re-observed: for any
Job in this run's corpus that the store already holds, the scrape's values overwrite the stored
ones. Cheap, and it runs every time.

**Derivations** — ``min_years`` / ``max_years`` / ``experience_source`` are ``f(code, facts)``, so a
change in the code has to reach every row. :data:`doc_prep.DERIVATIONS_VERSION` is compared against
the watermark in ``data/state/derivations.json``; when the code is newer, every row whose
description the ADR-0050 store *settles* is re-derived through the full cascade. Rows the store has
never settled are left alone — recomputing without the text a value came from could only downgrade
it, and #162 measured 127,501 such rows (all pre-ADR-0050, they carry no ``has_description``).

**The re-derivation queue** (ADR-0062) is the other half of that: when a run finally settles one of
those descriptions, the row is still carrying numbers derived without it, and no version has moved.
``update_descriptions`` appends the ids to ``data/state/pending_rederive.txt``; this module runs the
cascade for exactly those rows and clears the file. Without it, closing the description gap would
repair the *text* and leave every number behind it stale until the next version bump.

**What is deliberately never rewritten:** ``vector`` (a fact about the embedded doc — only the
ADR-0050 upgrade path replaces it), ``has_description`` (a fact about *that vector*: the upgrade
planner keys on it, so refreshing it from the store would hide every title-only vector from the
path meant to repair it), and ``id`` / ``ats``.

The rewrite preserves **row order and count**, because ``meta.jsonl`` is row-aligned with
``embeddings.f32`` and ``index._load_store`` hard-errors on drift. It is written to a temp file and
renamed, so a kill mid-write leaves the previous store intact.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from headstart import log
from headstart.experience import extract, from_field, from_seniority
from headstart.ingest import PENDING_REDERIVE_PATH, REPO_ROOT, read_id_list
from headstart.ingest.doc_prep import DERIVATIONS_VERSION, META_FIELDS
from headstart.ingest.update_descriptions import read_store

_log = log.get(__name__, __spec__)

_STORE = REPO_ROOT / "data" / "embeddings" / "jobs"
_JOBS = REPO_ROOT / "data" / "jobs" / "tech"
_DESCRIPTIONS = REPO_ROOT / "data" / "descriptions"
_WATERMARK = REPO_ROOT / "data" / "state" / "derivations.json"

#: Identity: what a row *is*, never re-observed, so it can never be rewritten onto another Job.
_IDENTITY = ("id", "ats")

#: Columns re-observed from the scrape every run — **derived** from the canonical metadata list, so
#: a new served column is refreshed automatically instead of needing a second edit here that whoever
#: adds it has no reason to know about. `title` is included for display: the vector keeps encoding
#: the title it was built from until a doc-drift upgrade exists (ADR-0021), and a current title over
#: a slightly stale vector beats a stale title.
FACT_FIELDS = tuple(f for f in META_FIELDS if f not in _IDENTITY)

#: Recomputed from facts whenever the extractor's version moves.
DERIVED_FIELDS = ("min_years", "max_years", "experience_source")


def read_watermark(path: Path) -> int:
    """The derivations version the stored metadata was last written at (0 when never stamped)."""
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text(encoding="utf-8"))["version"])
    except (ValueError, KeyError, TypeError):
        # A truncated or hand-edited watermark must re-derive, not silently skip: claiming a
        # sweep already ran is the one failure that leaves wrong values served indefinitely.
        _log.warning(f"{path} is unreadable — treating the store as un-swept")
        return 0


def write_watermark(path: Path, version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": version}) + "\n", encoding="utf-8")


def corpus_facts(jobs_dir: Path) -> dict[str, dict]:
    """``{Job id: {fact field: value}}`` from this run's tech corpus."""
    facts: dict[str, dict] = {}
    for path in sorted(jobs_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                job = json.loads(line)
                facts[job["id"]] = {f: job.get(f) for f in FACT_FIELDS}
    return facts


def held_descriptions(
    store_dir: Path, keep: set[str] | None = None
) -> dict[str, str | None]:
    """Every settled description across the ADR-0050 store, keyed by Job id.

    ``keep`` narrows the result to those ids. A version sweep needs all of them, but the ADR-0062
    re-derivation only needs this run's newly-settled handful — and holding the whole store is
    ~1 GB of text on a runner that is already the pipeline's memory ceiling.
    """
    held: dict[str, str | None] = {}
    if store_dir.is_dir():
        for ats_dir in sorted(p for p in store_dir.iterdir() if p.is_dir()):
            rows = read_store(ats_dir)
            held.update(
                rows if keep is None else {k: v for k, v in rows.items() if k in keep}
            )
    return held


def refresh_row(
    meta: dict,
    facts: dict | None,
    descriptions: dict[str, str | None],
    sweep: bool,
    rederive: bool = False,
) -> tuple[dict, bool, bool]:
    """One row's refresh. Returns ``(row, facts_changed, derivations_changed)``.

    ``rederive`` marks a single row for the cascade at an unchanged version — the ADR-0062 case,
    where this run's scrape settled a description the row was never derived from. It is kept
    separate from ``sweep`` because the two mean different things: ``sweep`` is "the extractor
    changed, redo everything the store settles", ``rederive`` is "this row's third cascade input
    just arrived".

    Pure, so the whole policy is unit-testable without a store on disk.
    """
    row = dict(meta)
    facts_changed = False
    if facts:
        for field in FACT_FIELDS:
            if row.get(field) != facts[field]:
                row[field] = facts[field]
                facts_changed = True

    # Re-derive when the code moved, when this row was marked, or when its own inputs just did.
    # `experience` (the raw field) and `title` are two of the three cascade inputs, so a change in
    # either can change the answer even at an unchanged version.
    inputs_moved = facts_changed and (
        row.get("experience") != meta.get("experience")
        or row.get("title") != meta.get("title")
    )
    if not (sweep or rederive or inputs_moved):
        return row, facts_changed, False

    if (
        row["id"] in descriptions
    ):  # the full cascade, against the text this row was derived from
        span = extract(row.get("experience"), descriptions[row["id"]], row.get("title"))
    else:
        span = _rederive_without_text(row, meta)
    if span is _KEEP:
        return row, facts_changed, False

    derived = {
        "min_years": span.min_years if span else None,
        "max_years": span.max_years if span else None,
        "experience_source": span.source if span else None,
    }
    changed = any(row.get(f) != derived[f] for f in DERIVED_FIELDS)
    row.update(derived)
    return row, facts_changed, changed


#: Sentinel for "leave this row's derivations exactly as they are" — distinct from ``None``, which
#: is a real cascade result meaning "nothing matched, so serve no number".
_KEEP = object()


def _rederive_without_text(row: dict, meta: dict) -> Any:
    """The cascade for a row whose description we do **not** hold (:data:`_KEEP` to leave it).

    Descriptions are only loaded during a sweep, and even then 48% of served rows have no entry —
    so this runs on any ordinary run where a Board edited a title or the raw experience field.
    Running the full cascade here would pass ``description=None`` and *wipe* a floor that came from
    the description, turning a cosmetic title edit into a lost number and growing the very
    `experience_source: none` share this module exists to shrink.

    So only the tiers that do not need the text may speak: a parseable field wins outright
    (ADR-0018's ordering), a description-sourced value is kept because nothing here can improve on
    it, and otherwise the seniority floor is re-read from the new title.
    """
    field = from_field(row.get("experience"))
    if field is not None:
        return field
    if meta.get("experience_source") == "regex":
        return _KEEP
    return from_seniority(row.get("experience"), row.get("title"))


def refresh(
    store: Path,
    jobs_dir: Path,
    descriptions_dir: Path,
    watermark: Path,
    pending_rederive: Path | None = None,
) -> int:
    meta_path = store / "meta.jsonl"
    if not meta_path.exists():
        _log.info("no store yet — nothing to refresh")
        return 0

    stored_version = read_watermark(watermark)
    sweep = DERIVATIONS_VERSION > stored_version
    facts = corpus_facts(jobs_dir)
    pending = read_id_list(pending_rederive) if pending_rederive else set()
    if sweep:
        descriptions = held_descriptions(descriptions_dir)
    elif pending:
        descriptions = held_descriptions(descriptions_dir, keep=pending)
    else:
        descriptions = {}
    if pending and not descriptions:
        # Same failure the watermark guards against: the merge job takes the description store from
        # a `continue-on-error` artifact, so an empty one means it was lost. Re-deriving now would
        # run the cascade with no text and *wipe* description-sourced floors on exactly the rows
        # this queue exists to repair — and clearing the queue would make that permanent.
        _log.warning(
            f"{len(pending)} rows queued to re-derive but no descriptions loaded — the store is "
            "missing, not empty; leaving the queue for the next run"
        )
        pending = set()
    _log.info(
        f"derivations v{stored_version} stored, v{DERIVATIONS_VERSION} in code — "
        f"{'SWEEPING' if sweep else 'no sweep'}; corpus facts for {len(facts)} Jobs; "
        f"{len(pending)} queued to re-derive"
        + (f"; {len(descriptions)} settled descriptions" if descriptions else "")
    )

    tmp = meta_path.with_suffix(".jsonl.refresh")
    rows = fact_hits = derived_hits = 0
    try:
        with (
            meta_path.open(encoding="utf-8") as src,
            tmp.open("w", encoding="utf-8") as out,
        ):
            for line in src:
                line = line.strip()
                if not line:
                    continue
                meta = json.loads(line)
                row, fact_changed, derived_changed = refresh_row(
                    meta,
                    facts.get(meta["id"]),
                    descriptions,
                    sweep,
                    rederive=meta["id"] in pending,
                )
                rows += 1
                fact_hits += fact_changed
                derived_hits += derived_changed
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                if rows % 50_000 == 0:
                    _log.info(f"  {rows} rows refreshed")
        tmp.replace(meta_path)
    except BaseException:
        # The merge job uploads `data/embeddings/jobs` wholesale and without `--delete`, so a
        # half-written temp file left behind here would be published to HF and stay there.
        tmp.unlink(missing_ok=True)
        raise
    _log.info(
        f"refreshed {rows} rows: {fact_hits} with changed facts, "
        f"{derived_hits} with changed derivations"
    )
    if sweep and not descriptions:
        # The merge job downloads the description store on `continue-on-error`, so an empty one
        # here means the artifact was lost, not that nothing is held. Stamping now would record a
        # sweep that never read a single description and leave every row unswept for good.
        _log.warning(
            "sweep found no held descriptions — the store is missing, not empty; leaving the "
            f"watermark at v{stored_version} so the next run retries"
        )
    elif sweep:
        write_watermark(watermark, DERIVATIONS_VERSION)
        _log.info(f"watermark -> v{DERIVATIONS_VERSION}")

    if pending and pending_rederive is not None:
        # Cleared only now, after the rewrite landed. Safe against a later failure too: the merge
        # job uploads data/state and the embedding store in the same step sequence, so a failed
        # upload leaves HF holding the *old* queue beside the *old* meta.jsonl — consistent, and
        # the next run redoes both.
        pending_rederive.unlink(missing_ok=True)
        _log.info(f"re-derive queue: cleared {len(pending)} consumed id(s)")
    return 0


def main() -> int:
    log.setup()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=_STORE)
    parser.add_argument("--source", type=Path, default=_JOBS)
    parser.add_argument("--descriptions", type=Path, default=_DESCRIPTIONS)
    parser.add_argument("--watermark", type=Path, default=_WATERMARK)
    parser.add_argument(
        "--pending-rederive",
        type=Path,
        default=PENDING_REDERIVE_PATH,
        help="ids whose description settled since they were embedded (ADR-0062); "
        "re-derived at an unchanged version, then cleared",
    )
    args = parser.parse_args()
    return refresh(
        args.store,
        args.source,
        args.descriptions,
        args.watermark,
        args.pending_rederive,
    )


if __name__ == "__main__":
    raise SystemExit(main())
