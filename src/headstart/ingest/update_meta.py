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
description the ADR-0050 store *holds* is re-derived through the full cascade. Rows whose text the
store does not hold are left alone — recomputing without the text a value came from could only
downgrade it, and #162 measured 127,501 such rows (all pre-ADR-0050, so they carry no
``has_description``).

**``remote`` is both** — a Fact (each scraper's own ATS-native field, re-observed above like any
other) with a Derivation overlaid on top of it (``headstart.remote.extract``, ADR-0061 v8): if
the JD confidently reads as remote, that wins over whatever the just-refreshed fact says. That
overlay needs no "text the store doesn't hold" guard the way the cascade above does — it is
one-directional (can only turn False/None into True), so recomputing it without text simply
returns the fact unchanged rather than risking a downgrade.

**The re-derivation queue** (ADR-0062) is the other half of that: when a run finally supplies one of
those descriptions, the row is still carrying numbers derived without it, and no version has moved.
``update_descriptions`` appends the ids to ``data/state/pending_rederive.txt``; this module runs the
cascade for exactly those rows and clears the file. Without it, closing the description gap would
repair the *text* and leave every number behind it stale until the next version bump.

**What is deliberately never rewritten:** ``vector`` (a fact about the embedded doc — only the
ADR-0050 upgrade path replaces it), ``has_description`` where a row already carries one (a fact
about *that vector*: the upgrade planner keys on it, so refreshing it from the store would hide
every title-only vector from the path meant to repair it), and ``id`` / ``ats``.

**``has_description`` is, however, backfilled where it is absent** (ADR-0062). Every pre-ADR-0050
row lacks it, which is why ``embed_plan`` has had to guess from the ATS — over-approximating the
degraded set by ~9x. See :func:`has_description_for`: written once, from evidence where there is
any and from the same inference where there is none, so the guess stops being re-made every run.

Version sweeps have a ten-minute soft budget (ADR-0176). Each processed row carries its own
``_derivations_version`` so a later run can finish the sweep even after rows move during an
embedding upgrade. Facts and queued descriptions continue to refresh after the budget expires;
the global watermark advances only when no row remains unswept. Progress survives once the
normal publication uploads the rewritten metadata. A runner lost before publication still
retries from the last published checkpoint.

The rewrite preserves **row order and count**, because ``meta.jsonl`` is row-aligned with
``embeddings.f32`` and ``index._load_store`` hard-errors on drift. It is written to a temp file and
renamed, so a kill mid-write leaves the previous store intact.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import nullcontext
from pathlib import Path
from time import monotonic
from typing import Any, NamedTuple

from headstart import log
from headstart.experience import from_field, from_seniority
from headstart.ingest import (
    PENDING_REDERIVE_PATH,
    REPO_ROOT,
    read_id_list,
)
from headstart.ingest.derived_meta import (
    country_meta,
    experience_fields,
    experience_meta,
    remote_meta,
    salary_fields,
    salary_meta,
)
from headstart.ingest.doc_prep import DERIVATIONS_VERSION, META_FIELDS
from headstart.ingest.update_descriptions import read_store
from headstart.salary import from_field as salary_from_field
from headstart.scrapers import registry

_log = log.get(__name__, __spec__)

_STORE = REPO_ROOT / "data" / "embeddings" / "jobs"
_JOBS = REPO_ROOT / "data" / "jobs" / "tech"
_DESCRIPTIONS = REPO_ROOT / "data" / "descriptions"
_WATERMARK = REPO_ROOT / "data" / "state" / "derivations.json"

#: Small batches bound process-transfer memory and spread expensive descriptions across workers.
#: Progress remains every 50,000 written rows, independently of dispatch size.
_SWEEP_CHUNK_ROWS = 1_000
_PROGRESS_ROWS = 50_000
_ROW_VERSION = "_derivations_version"
_SWEEP_BUDGET_SECONDS = 600

#: Identity: what a row *is*, never re-observed, so it can never be rewritten onto another Job.
_IDENTITY = ("id", "ats")

#: `remote`'s served value is a **derivation** wearing a fact's column (ADR-0061 v8 / ADR-0118):
#: the JD overlay overwrites it in place rather than living in a column of its own. If it stayed
#: in FACT_FIELDS, this blind resync would overwrite an already-JD-derived `True` with the raw
#: scrape's current value on every ordinary run — the overlay only gets a chance to reinstate it
#: on a sweep, so between sweeps a Board's mere re-scrape would silently discard the override.
#: Excluded here instead; `remote`'s own block below reads the fresh raw fact directly from
#: `facts` and only runs on `sweep or rederive`, the same cadence every other derivation gets.
_FACT_WITH_OVERLAY = ("remote",)

#: Columns re-observed from the scrape every run — **derived** from the canonical metadata list, so
#: a new served column is refreshed automatically instead of needing a second edit here that whoever
#: adds it has no reason to know about. `title` is included for display: the vector keeps encoding
#: the title it was built from until a doc-drift upgrade exists (ADR-0021), and a current title over
#: a slightly stale vector beats a stale title.
FACT_FIELDS = tuple(
    f for f in META_FIELDS if f not in _IDENTITY and f not in _FACT_WITH_OVERLAY
)

#: Recomputed from facts whenever the extractor's version moves.
DERIVED_FIELDS = ("min_years", "max_years", "experience_source")

#: Salary's derived columns (ADR-0082) — no "seniority" tier exists, unlike experience's.
SALARY_DERIVED_FIELDS = (
    "min_salary_annual",
    "max_salary_annual",
    "salary_currency",
    "salary_source",
)

# `country` (ADR-0138) has no `DERIVED_FIELDS`-style tuple: it is one field, compared directly in
# `refresh_row` rather than looped, and `derivation_delta`'s tier-bucketing doesn't apply to a
# binary IN/null value — so unlike `DERIVED_FIELDS`/`SALARY_DERIVED_FIELDS` there is nothing here
# that would actually read the tuple.


def has_description_for(row: dict, detail_pass: frozenset[str]) -> bool:
    """What ``has_description`` should be on a row written before ADR-0050 recorded it (ADR-0062).

    Every pre-ADR-0050 row carries no ``has_description``, so ``embed_plan`` has had to *infer*
    whether its vector was built from a description — assuming degraded on any detail-pass ATS.
    That inference conflates two very different rows: one embedded without a description (the
    vector is title-only and re-embedding repairs it) and one embedded *with* a description we
    simply never persisted, since pre-ADR-0050 the text was read once at embed time and discarded.
    The second is fine and re-embedding it changes nothing.

    ``experience_source == "regex"`` settles that for a row outright: the stored floor was read
    *out of a description*, so one existed when the Doc was built. Measured against the live store
    this proves 66,175 of 151,538 detail-pass gap rows — 43.7% — are not degraded at all, against
    an ADR-0050 measurement putting the genuinely title-only population at ~16,771 index-wide.

    Where there is no such proof, this returns exactly what the inference already concluded, so
    recording it changes no behaviour — it only stops the guess from being re-made every run.
    """
    if row.get("experience_source") == "regex":
        return True
    return row.get("ats") not in detail_pass


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
    """``{Job id: {fact field: value}}`` from this run's tech corpus.

    Carries ``_FACT_WITH_OVERLAY`` fields (``remote``) alongside ``FACT_FIELDS`` — the blind
    sync loop in :func:`refresh_row` only iterates ``FACT_FIELDS`` and so still correctly leaves
    them alone, but `remote`'s own derivation block needs this run's fresh raw value to read, and
    the fact dict is the only place that value exists once the field is out of `FACT_FIELDS`.
    """
    facts: dict[str, dict] = {}
    for path in sorted(jobs_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                job = json.loads(line)
                facts[job["id"]] = {
                    f: job.get(f) for f in (*FACT_FIELDS, *_FACT_WITH_OVERLAY)
                }
    return facts


def held_descriptions(store_dir: Path, keep: set[str] | None = None) -> dict[str, str]:
    """Every description the ADR-0050 store holds, keyed by Job id.

    ``keep`` narrows the result to those ids. A version sweep needs all of them, but the ADR-0062
    re-derivation only needs this run's newly-stored handful — and holding the whole store is
    ~1 GB of text on a runner that is already the pipeline's memory ceiling.
    """
    held: dict[str, str] = {}
    if store_dir.is_dir():
        for ats_dir in sorted(p for p in store_dir.iterdir() if p.is_dir()):
            rows = read_store(ats_dir)
            held.update(
                rows if keep is None else {k: v for k, v in rows.items() if k in keep}
            )
    return held


def derivation_delta(
    before: dict, after: dict, source_field: str, fields: tuple[str, ...]
) -> str | None:
    """Classify a row's derivation change: "gained", "lost", "retiered", "moved", or None.

    A bare "N rows with changed derivations" cannot answer the one question a DERIVATIONS_VERSION
    bump exists to ask — did the fix win coverage or lose it? Those are opposite outcomes behind
    one number, and a sweep that silently strips 4,000 answers reports the same count as one that
    adds 4,000. ADR-0066 already makes this split mandatory when a pattern change is verified
    locally; this reports it for the sweep that applies the change to production, where nobody is
    watching a diff.

    ADR-0066 asks for old-tier -> new-tier bucketing **and** same-tier value changes, and the
    source field *is* the tier (experience: "field" / "regex" / "seniority"; salary has no
    seniority tier), so those are two outcomes and not one. "retiered" is an answer that changed
    which tier produced it, and it is deliberately **direction-blind**: a regex pattern winning a
    row that fell through to a seniority guess and a regex pattern losing one to that guess both
    land here. Coverage is flat either way, so neither is gained/lost — read the bucket as "this
    many answers changed provenance, go look", not as a quality verdict. "moved" is the strict
    same-tier value change, the one a coverage total hides completely.

    Pure, like `refresh_row`, so the policy stays unit-testable with no store on disk.
    """
    if all(before.get(f) == after.get(f) for f in fields):
        return None
    old, new = before.get(source_field), after.get(source_field)
    if (old is None) != (new is None):
        return "gained" if new is not None else "lost"
    if old != new:
        return "retiered"
    # Same tier on both sides. `old is None` here means a row whose stored fields disagree with
    # its own source — a value with no tier, which this code never writes (a null span nulls
    # every field together) but a pre-ADR-0061 row can carry. Its fields differed, so the sweep
    # just cleared that value: an answer went away, which is "lost". Returning None instead would
    # drop it from every bucket and understate exactly the direction most worth seeing.
    return "moved" if old is not None else "lost"


def refresh_row(
    meta: dict,
    facts: dict | None,
    descriptions: dict[str, str],
    sweep: bool,
    rederive: bool = False,
) -> tuple[dict, bool, bool]:
    """One row's refresh. Returns ``(row, facts_changed, derivations_changed)``.

    ``rederive`` marks a single row for the cascade at an unchanged version — the ADR-0062 case,
    where this run's scrape supplied a description the row was never derived from. It is kept
    separate from ``sweep`` because the two mean different things: ``sweep`` is "the extractor
    changed, redo everything the store holds", ``rederive`` is "this row's third cascade input
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
    # Salary's own input drift — its cascade never reads `title`, so title-only edits don't
    # trigger it (unlike experience's).
    salary_inputs_moved = facts_changed and row.get("salary") != meta.get("salary")

    changed = False
    if sweep or rederive or inputs_moved:
        if (
            row["id"] in descriptions
        ):  # the full cascade, against the text this row was derived from
            derived = experience_meta(
                row.get("experience"), descriptions[row["id"]], row.get("title")
            )
        else:
            span = _rederive_without_text(row, meta)
            derived = None if span is _KEEP else experience_fields(span)
        if derived is not None:
            changed = changed or any(row.get(f) != derived[f] for f in DERIVED_FIELDS)
            row.update(derived)

    if sweep or rederive or salary_inputs_moved:
        if row["id"] in descriptions:
            derived_salary = salary_meta(
                row.get("salary"), descriptions[row["id"]], row.get("ats")
            )
        else:
            salary_span = _rederive_salary_without_text(row, meta)
            derived_salary = (
                None if salary_span is _KEEP else salary_fields(salary_span)
            )
        if derived_salary is not None:
            changed = changed or any(
                row.get(f) != derived_salary[f] for f in SALARY_DERIVED_FIELDS
            )
            row.update(derived_salary)

    # `country`'s derivation (ADR-0138) — a pure function of `location` alone, so unlike
    # experience/salary it needs no held-description branch at all: `location` is already a fact
    # `FACT_FIELDS` resynced above, so a sweep achieves full coverage in one pass.
    country_inputs_moved = facts_changed and row.get("location") != meta.get("location")
    if sweep or rederive or country_inputs_moved:
        new_country = country_meta(row.get("location"))["country"]
        changed = changed or (new_country != row.get("country"))
        row["country"] = new_country

    # `remote`'s overlay (headstart.remote, ADR-0061 v8/ADR-0118). `remote` is excluded from
    # FACT_FIELDS (see `_FACT_WITH_OVERLAY`), so unlike every fact above, nothing has already
    # refreshed `row["remote"]` to this run's raw field — that has to happen here, from `facts`,
    # the same place `experience`/`salary`'s own raw-field re-syncs come from. Only on `sweep or
    # rederive`: an ordinary run carries no held description (`descriptions` is `{}` unless
    # sweeping or draining the ADR-0062 queue — see `main`), so running this every run would
    # just be `remote_meta(raw_field, None)`, i.e. adopt the raw field with no chance to
    # reinstate a JD override — the raw field would win by default, silently discarding it.
    if sweep or rederive:
        raw_remote = facts.get("remote") if facts else row.get("remote")
        new_remote = remote_meta(raw_remote, descriptions.get(row["id"]))["remote"]
        if new_remote != row.get("remote"):
            changed = True
        row["remote"] = new_remote

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


def _rederive_salary_without_text(row: dict, meta: dict) -> Any:
    """Salary's version of :func:`_rederive_without_text` — same reasoning, one fewer branch.

    A parseable field wins outright, same as experience. A description-sourced value is kept
    because nothing here can improve on it without the text. But where experience falls through to
    a seniority floor that needs no text, salary has no such tier (see ``headstart.salary``'s
    module docstring) — "no field, no held description, no prior regex value" is honestly
    ``None`` here, never a guess.
    """
    field = salary_from_field(row.get("salary"), row.get("ats"))
    if field is not None:
        return field
    if meta.get("salary_source") == "regex":
        return _KEEP
    return None


class _ChunkArgs(NamedTuple):
    """One chunk's worth of `_refresh_chunk` inputs.

    Carries only the slice of `facts`/`descriptions` that chunk's own rows need, never the whole
    corpus — `descriptions` alone is ~1 GB during a sweep (`held_descriptions`), already this
    pipeline's memory ceiling on one process; handing the full dict to every worker would multiply
    that by the worker count instead of dividing the work across them.
    """

    rows: list[dict]
    facts: dict[str, dict]
    descriptions: dict[str, str]
    sweep: bool
    pending: set[str]
    detail_pass: frozenset[str]


def _row_batches(meta_path: Path, size: int) -> Iterator[list[dict]]:
    """`meta.jsonl`, parsed and grouped into fixed-size batches.

    Read lazily so the whole store is never resident as a single list — `refresh` fans these
    batches out across a process pool during a sweep, or runs them in-process otherwise.
    """
    batch: list[dict] = []
    with meta_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            batch.append(json.loads(line))
            if len(batch) >= size:
                yield batch
                batch = []
    if batch:
        yield batch


def _chunk_args(
    batches: Iterator[list[dict]],
    facts: dict[str, dict],
    descriptions: dict[str, str],
    sweep: bool,
    pending: set[str],
    detail_pass: frozenset[str],
    deadline: float | None = None,
) -> Iterator[_ChunkArgs]:
    """Pair each row batch with only the `facts`/`descriptions`/`pending` entries it needs."""
    for batch in batches:
        ids = {row["id"] for row in batch}
        yield _ChunkArgs(
            rows=batch,
            facts={i: facts[i] for i in ids if i in facts},
            descriptions=(
                {i: descriptions[i] for i in ids if i in descriptions}
                if descriptions
                else {}
            ),
            sweep=sweep and (deadline is None or monotonic() < deadline),
            pending=pending & ids if pending else set(),
            detail_pass=detail_pass,
        )


class _ChunkResult(NamedTuple):
    """One chunk's worth of `_refresh_chunk` output — the counterpart to `_ChunkArgs`, so
    `refresh`'s accumulation loop unpacks named fields instead of a positional tuple that a
    future reorder could silently mismap.
    """

    rows: list[dict]
    fact_hits: int
    derived_hits: int
    backfilled: int
    exp_delta: Counter[str]
    sal_delta: Counter[str]
    country_delta: Counter[str]
    unswept: int


def _refresh_chunk(args: _ChunkArgs) -> _ChunkResult:
    """One chunk's worth of `refresh_row` calls, plus the delta/backfill bookkeeping `refresh`
    used to do inline for every row — factored out so it runs identically whether dispatched to a
    process-pool worker (sweeping) or called directly in-process (everything else). Top-level and
    built from a picklable `_ChunkArgs` so it works under `spawn`, not just `fork`.
    """
    out_rows: list[dict] = []
    fact_hits = derived_hits = backfilled = 0
    exp_delta: Counter[str] = Counter()
    sal_delta: Counter[str] = Counter()
    country_delta: Counter[str] = Counter()
    unswept = 0
    for meta in args.rows:
        sweep_row = args.sweep and meta.get(_ROW_VERSION, 0) < DERIVATIONS_VERSION
        row, fact_changed, derived_changed = refresh_row(
            meta,
            args.facts.get(meta["id"]),
            args.descriptions,
            sweep_row,
            rederive=meta["id"] in args.pending,
        )
        if sweep_row:
            row[_ROW_VERSION] = DERIVATIONS_VERSION
        unswept += row.get(_ROW_VERSION, 0) < DERIVATIONS_VERSION
        fact_hits += fact_changed
        derived_hits += derived_changed
        # `meta` is untouched (refresh_row copies), so it is the genuine "before".
        if derived_changed:
            for counts, source, fields in (
                (exp_delta, "experience_source", DERIVED_FIELDS),
                (sal_delta, "salary_source", SALARY_DERIVED_FIELDS),
            ):
                move = derivation_delta(meta, row, source, fields)
                if move:
                    counts[move] += 1
            # `country` has no tier concept (`derivation_delta` doesn't apply) — just a direct
            # before/after compare, since the only two values are "IN" and null.
            if meta.get("country") != row.get("country"):
                country_delta["gained" if row.get("country") else "lost"] += 1
        # Written once, on the rows that never had it. A row that carries the flag keeps it: it
        # is a fact about the vector, and only a re-embed may change it.
        #
        # Read from `meta`, the row as it was BEFORE this refresh — never from `row`. The cascade
        # above may have just set `experience_source = "regex"` from a description that arrived
        # *this run*, which the vector was never built from. Reading that back as proof would mark
        # a genuinely title-only vector `has_description: True` and hide it from the upgrade path
        # forever — the exact failure ADR-0061 froze this field against.
        if row.get("has_description") is None:
            row["has_description"] = has_description_for(meta, args.detail_pass)
            backfilled += 1
        out_rows.append(row)
    return _ChunkResult(
        out_rows,
        fact_hits,
        derived_hits,
        backfilled,
        exp_delta,
        sal_delta,
        country_delta,
        unswept,
    )


def _parallel_chunks(
    pool: ProcessPoolExecutor, args: Iterator[_ChunkArgs], workers: int
) -> Iterator[_ChunkResult]:
    """Keep at most `workers` batches submitted but not yet written.

    Executor.map eagerly consumes its input on the pipeline's Python 3.12.
    Its pending arguments and completed results can retain the entire metadata
    store while a slow early batch blocks the writer. Observe completion order,
    but emit in source order because metadata is aligned with the vector file.
    Completed batches count toward the bound until the writer consumes them.
    """
    indexed = enumerate(args)
    pending = {}
    ready = {}
    next_write = 0

    def submit_one():
        item = next(indexed, None)
        if item is not None:
            index, chunk_args = item
            pending[pool.submit(_refresh_chunk, chunk_args)] = index

    try:
        for _ in range(workers):
            submit_one()
        while pending:
            future = next(as_completed(pending))
            ready[pending.pop(future)] = future.result()
            del future
            while next_write in ready:
                yield ready.pop(next_write)
                next_write += 1
                submit_one()
    finally:
        for future in pending:
            future.cancel()


def refresh(
    store: Path,
    jobs_dir: Path,
    descriptions_dir: Path,
    watermark: Path,
    pending_rederive: Path | None = None,
    sweep_budget_seconds: float = _SWEEP_BUDGET_SECONDS,
) -> int:
    if not 0 <= sweep_budget_seconds < float("inf"):
        raise ValueError("sweep budget must be finite and nonnegative")
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
        + (f"; {len(descriptions)} held descriptions" if descriptions else "")
    )

    detail_pass = registry.detail_pass_atses()
    # Outside the uploaded store: even SIGKILL must not leave a partial file
    # where the workflow's folder upload could publish it.
    tmp = store.parent / f".{store.name}-meta.jsonl.refresh"
    rows = fact_hits = derived_hits = backfilled = 0
    exp_delta: Counter[str] = Counter()
    sal_delta: Counter[str] = Counter()
    country_delta: Counter[str] = Counter()
    unswept = 0
    deadline = monotonic() + sweep_budget_seconds if sweep else None
    # A sweep runs the full cascade on every row instead of a cheap fact-sync — measured ~230x
    # slower per row on the 2026-09-15 nightly (805,160 rows: ~15s fact-only vs. a sweep still not
    # done at 800,000 rows after 59 minutes). `refresh_row` is pure, so a sweep fans the store out
    # across a process pool; an ordinary run is fast enough already that pool start-up would cost
    # more than it saves, so it stays sequential.
    workers = os.cpu_count() or 1
    pool_cm = (
        ProcessPoolExecutor(max_workers=workers)
        if (sweep and workers > 1)
        else nullcontext()
    )
    try:
        with tmp.open("w", encoding="utf-8") as out, pool_cm as pool:
            batches = _row_batches(meta_path, _SWEEP_CHUNK_ROWS)
            args_iter = _chunk_args(
                batches,
                facts,
                descriptions,
                sweep and bool(descriptions),
                pending,
                detail_pass,
                deadline,
            )
            chunk_results = (
                _parallel_chunks(pool, args_iter, workers)
                if pool
                else map(_refresh_chunk, args_iter)
            )
            for chunk in chunk_results:
                for row in chunk.rows:
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                rows += len(chunk.rows)
                fact_hits += chunk.fact_hits
                derived_hits += chunk.derived_hits
                backfilled += chunk.backfilled
                exp_delta.update(chunk.exp_delta)
                sal_delta.update(chunk.sal_delta)
                country_delta.update(chunk.country_delta)
                unswept += chunk.unswept
                if rows % _PROGRESS_ROWS == 0:
                    _log.info(f"  {rows} rows refreshed")
        tmp.replace(meta_path)
    except BaseException:
        # The merge job uploads `data/embeddings/jobs` wholesale and without `--delete`, so a
        # half-written temp file left behind here would be published to HF and stay there.
        tmp.unlink(missing_ok=True)
        raise
    _log.info(
        f"refreshed {rows} rows: {fact_hits} with changed facts, "
        f"{derived_hits} with changed derivations, "
        f"{backfilled} given a has_description they never had"
    )
    for label, counts in (("experience", exp_delta), ("salary", sal_delta)):
        if counts:
            _log.info(
                f"{label} derivations: {counts['gained']} gained, {counts['lost']} lost, "
                f"{counts['retiered']} retiered, {counts['moved']} moved (same tier, new "
                f"value) (ADR-0066)"
            )
    if country_delta:
        _log.info(
            f"country derivations: {country_delta['gained']} gained, "
            f"{country_delta['lost']} lost (ADR-0138)"
        )
    if sweep and not descriptions:
        # The merge job downloads the description store on `continue-on-error`, so an empty one
        # here means the artifact was lost, not that nothing is held. Stamping now would record a
        # sweep that never read a single description and leave every row unswept for good.
        _log.warning(
            "sweep found no held descriptions — the store is missing, not empty; leaving the "
            f"watermark at v{stored_version} so the next run retries"
        )
    elif sweep and unswept:
        _log.info(
            f"sweep checkpoint: {rows - unswept} of {rows} rows at v{DERIVATIONS_VERSION}; "
            f"{unswept} remain after the {sweep_budget_seconds:g}s soft budget — "
            f"leaving watermark at v{stored_version}; next run resumes"
        )
    elif sweep:
        write_watermark(watermark, DERIVATIONS_VERSION)
        _log.info(f"watermark -> v{DERIVATIONS_VERSION}")

    if pending and pending_rederive is not None:
        # Cleared only now, after the rewrite landed. Safe against a later failure too: the merge
        # job uploads data/state and the embedding store in the same step sequence, so a failed
        # upload leaves HF holding the *old* queue beside the *old* meta.jsonl — consistent, and
        # the next run redoes both.
        #
        # **Truncated, not unlinked.** The merge uploads `data/state` without `--delete`, so a
        # local deletion never reaches the dataset: `data/state/embedded_ids.txt.gz` is still on HF
        # although nothing in this repo has written it for months. An unlink here would leave the
        # remote queue intact, and every later join would re-fetch and re-append it forever.
        pending_rederive.write_text("", encoding="utf-8")
        _log.info(f"re-derive queue: cleared {len(pending)} consumed id(s)")
    return 0


def main() -> int:
    log.setup()
    log.context("update_meta")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=_STORE)
    parser.add_argument("--source", type=Path, default=_JOBS)
    parser.add_argument("--descriptions", type=Path, default=_DESCRIPTIONS)
    parser.add_argument("--watermark", type=Path, default=_WATERMARK)
    parser.add_argument(
        "--sweep-budget-seconds",
        type=float,
        default=_SWEEP_BUDGET_SECONDS,
        help="soft time budget for version sweeps; unfinished rows resume next run "
        "while facts and queued descriptions still refresh (default: 600)",
    )
    parser.add_argument(
        "--pending-rederive",
        type=Path,
        default=PENDING_REDERIVE_PATH,
        help="ids whose description arrived after they were embedded (ADR-0062); "
        "re-derived at an unchanged version, then cleared",
    )
    args = parser.parse_args()
    return refresh(
        args.store,
        args.source,
        args.descriptions,
        args.watermark,
        args.pending_rederive,
        args.sweep_budget_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
