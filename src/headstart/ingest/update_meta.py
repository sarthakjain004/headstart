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
ADR-0050 upgrade path replaces it), ``has_description`` where a row already carries one (a fact
about *that vector*: the upgrade planner keys on it, so refreshing it from the store would hide
every title-only vector from the path meant to repair it), and ``id`` / ``ats``.

**``has_description`` is, however, backfilled where it is absent** (ADR-0062). Every pre-ADR-0050
row lacks it, which is why ``embed_plan`` has had to guess from the ATS — over-approximating the
degraded set by ~9x. See :func:`has_description_for`: written once, from evidence where there is
any and from the same inference where there is none, so the guess stops being re-made every run.

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
from headstart.salary import extract as extract_salary
from headstart.salary import from_field as salary_from_field
from headstart.scrapers import registry

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

#: Salary's derived columns (ADR-0082) — no "seniority" tier exists, unlike experience's.
SALARY_DERIVED_FIELDS = (
    "min_salary_annual",
    "max_salary_annual",
    "salary_currency",
    "salary_source",
)


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
    # Salary's own input drift — its cascade never reads `title`, so title-only edits don't
    # trigger it (unlike experience's).
    salary_inputs_moved = facts_changed and row.get("salary") != meta.get("salary")

    changed = False
    if sweep or rederive or inputs_moved:
        if (
            row["id"] in descriptions
        ):  # the full cascade, against the text this row was derived from
            span = extract(
                row.get("experience"), descriptions[row["id"]], row.get("title")
            )
        else:
            span = _rederive_without_text(row, meta)
        if span is not _KEEP:
            derived = {
                "min_years": span.min_years if span else None,
                "max_years": span.max_years if span else None,
                "experience_source": span.source if span else None,
            }
            changed = changed or any(row.get(f) != derived[f] for f in DERIVED_FIELDS)
            row.update(derived)

    if sweep or rederive or salary_inputs_moved:
        if row["id"] in descriptions:
            salary_span = extract_salary(
                row.get("salary"), descriptions[row["id"]], row.get("ats")
            )
        else:
            salary_span = _rederive_salary_without_text(row, meta)
        if salary_span is not _KEEP:
            derived_salary = {
                "min_salary_annual": salary_span.min_annual if salary_span else None,
                "max_salary_annual": salary_span.max_annual if salary_span else None,
                "salary_currency": salary_span.currency if salary_span else None,
                "salary_source": salary_span.source if salary_span else None,
            }
            changed = changed or any(
                row.get(f) != derived_salary[f] for f in SALARY_DERIVED_FIELDS
            )
            row.update(derived_salary)

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

    detail_pass = registry.detail_pass_atses()
    tmp = meta_path.with_suffix(".jsonl.refresh")
    rows = fact_hits = derived_hits = backfilled = 0
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
                # Written once, on the rows that never had it. A row that carries the flag keeps
                # it: it is a fact about the vector, and only a re-embed may change it.
                #
                # Read from `meta`, the row as it was BEFORE this refresh — never from `row`. The
                # cascade above may have just set `experience_source = "regex"` from a description
                # that settled *this run*, which the vector was never built from. Reading that back
                # as proof would mark a genuinely title-only vector `has_description: True` and hide
                # it from the upgrade path forever — the exact failure ADR-0061 froze this field
                # against.
                if row.get("has_description") is None:
                    row["has_description"] = has_description_for(meta, detail_pass)
                    backfilled += 1
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
        f"{derived_hits} with changed derivations, "
        f"{backfilled} given a has_description they never had"
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
