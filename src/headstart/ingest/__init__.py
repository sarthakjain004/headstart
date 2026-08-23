"""The back-to-back ingest pipeline — every stage `.github/workflows/pipeline.yml` runs (ADR-0028).

The run is two symmetric halves — **plan → run → gather** — so each module is named
``{half}_{role}`` and the two triples group together. In run order::

    scrape_plan     stage 1  select this run's board slice, bin-pack it into scrape shards
    scrape_run      stage 2  (matrix) scrape one shard's boards into a fragment
    scrape_join     stage 3  union the scrape fragments into one snapshot
    filter_tech     stage 3  keep the tech subset (ADR-0017)
    update_descriptions
                    stage 3  persist fetched descriptions, repair the ones the scrape lost
    update_ledgers  stage 3  blend this run's measurements into the four ledgers, in the
                             order the run invokes them: priority, cost, failures, gap
    embed_plan      stage 3  diff, tokenize, bin-pack the new Docs into embed shards
    embed_run       stage 4  (matrix) embed one shard's Docs into a fragment
    embed_merge     stage 5  concatenate the embed fragments onto the store
    update_meta     stage 5  re-observe the stored facts, re-derive on a version bump (ADR-0061)
    index           stage 5  sync -> prune the LanceDB table (`compact` is NOT in this run —
                             it moved to the `cleanup-index` workflow)
    role_trends     stage 5  count the served stock into role families (ADR-0040)

One more entry point is not a stage but opens three of them (and ``cleanup-index``)::

    state_fetch     stages 1/3/5  pull this stage's slice of HF state, or abort (ADR-0030)

Each is run as ``python -m headstart.ingest.<module>``. They live here rather than under
``scripts/`` because they are the product's pipeline, not one-off tooling: being importable
makes them unit-testable without ``importlib`` path-loading, and keeps the run's thirteen entry
points from being scattered across five ``scripts/`` subdirs mixed in with R&D scripts.

Alongside them, the helper modules with no consumer outside this package::

    binpack        LPT packing + shard sizing, shared by both planners
    board_failures The consecutive-gone quarantine ledger (ADR-0058), written in the join
                   and read by scrape_plan
    doc_prep       Doc build / English gate / typed metadata, shared by embed_run and embed_plan
    index_plan     Pure add-evict and prune planners for the jobs table (no LanceDB import)
    observability  Run context, step summaries, and the shard-report round trip
    role_assignments  The id->family snapshot and the transitions between them (ADR-0057)
    shard_speedup  The measured fan-out speedup the makespan divides by (ADR-0054)

Genuinely shared logic stays in ``headstart`` proper — ``harvest`` (the scrape engine),
``board_cost``, ``board_priority``, ``corpus`` — because ``python -m headstart``'s curated-feed
path reaches them too, and the pipeline must not become a dependency of that.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

# src/headstart/ingest/__init__.py -> the repo root. Every stage reads and writes the repo's
# data/ tree, so they assume a source checkout — which `pip install -e .` (what all three
# workflows do) guarantees. Defined once here because the depth is easy to get wrong: each
# stage previously carried its own `Path(__file__).resolve().parents[2]`.
REPO_ROOT = Path(__file__).resolve().parents[3]

# The detail skip-list (ADR-0048, re-keyed by ADR-0050): Job ids whose detail the description
# store has settled — we hold the text, or we know the posting has none — so the scrape stage can
# skip their per-job detail fetch. It was keyed on *being embedded*, which is a different set: a
# Job embedded without a description was skipped forever and could never be repaired.
# Lives here rather than in either stage because three modules across two packages move this one
# file — `update_descriptions` writes it, `scrape_plan` ships it to the shards, `scrape_run` reads
# it — and a stage-owned constant would put it inside one of them.
HELD_DETAILS_PATH = REPO_ROOT / "data" / "state" / "held_details.txt.gz"

# The ADR-0050 upgrade list: Job ids whose vector must be replaced because their description
# arrived after they were first embedded. Here for the same reason as HELD_DETAILS_PATH — three
# modules move this one file (`embed_plan` writes it, `embed_merge` holds ids back until their
# replacement lands, `index` re-adds the rows) and each previously declared the path itself.
PENDING_UPGRADES_PATH = REPO_ROOT / "data" / "state" / "pending_upgrades.txt"

# The ADR-0062 re-derivation queue: Job ids whose description the store settled *this run*, whose
# stored metadata therefore still carries numbers derived without that text. `update_descriptions`
# appends, `update_meta` re-derives them and clears the file. It lives under data/state rather than
# riding the corpus artifact alone so a lost artifact or a failed merge retries next run instead of
# stranding those rows until the next DERIVATIONS_VERSION bump — the marking is the only signal
# that they need repair, and nothing regenerates it (once settled, a description is never "newly
# settled" again).
PENDING_REDERIVE_PATH = REPO_ROOT / "data" / "state" / "pending_rederive.txt"

# The ADR-0083 eviction grace period: Job ids that were absent from their Board's most recent
# scrape but have not yet been absent from a *second consecutive* one, so `index sync` withheld
# their eviction pending another look. Written and read only by `index sync` — unlike the paths
# above it has one owner — but it belongs here beside them because it is the same kind of thing:
# a small newline-delimited id list under data/state that round-trips through the HF dataset, and
# putting it anywhere else would leave the state directory's contents documented in two places.
#
# Rewritten in full each run rather than appended, which is what keeps it from accreting: the set
# is derived fresh from the run's own scrape plus whatever it carries forward, so an id that has
# reappeared, been pruned, or belongs to a Board that left the ledger is simply not written again.
UNCONFIRMED_PATH = REPO_ROOT / "data" / "state" / "unconfirmed_ids.txt"


def read_id_list(path: Path) -> set[str]:
    """The non-blank lines of a newline-delimited id file; empty when it does not exist.

    Both readers of PENDING_UPGRADES_PATH parsed this identically; the shape is the file format,
    so it lives with the path rather than being spelled out at each end.
    """
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def append_id_list(path: Path, ids: list[str]) -> None:
    """Append ids to a newline-delimited id file, creating it and its parents.

    Append rather than rewrite because the queue accumulates across runs until its consumer
    clears it: a run that settles descriptions must not discard what an earlier run settled and
    `update_meta` has not repaired yet.
    """
    if not ids:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("".join(f"{i}\n" for i in ids))


def write_id_list(path: Path, ids: Iterable[str]) -> None:
    """Replace a newline-delimited id file with exactly ``ids``, sorted.

    The counterpart to :func:`append_id_list`, for a set that is *derived* fresh each run rather
    than accumulated: rewriting is what stops it growing without bound. Writes the file even when
    ``ids`` is empty — an empty set is a real state ("nothing is awaiting a second look"), and
    skipping the write would leave the previous run's ids in place to be read back as current,
    which is the failure `write_unauthoritative_boards` documents for the same reason. Sorted so a
    set's arbitrary iteration order cannot produce a spurious diff in the dataset.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{i}\n" for i in sorted(ids)), encoding="utf-8")
