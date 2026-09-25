#!/usr/bin/env python3
"""Retire the four Trends files ADR-0230's one Board-delta history replaced, from the HF dataset.

- `data/state/role_trends.parquet`, the aggregate ledger. The history replays it at every tick.
- `data/state/role_trend_board_counts.parquet`, the Board-count snapshot. The replay's last tick.
- `data/state/trends_epochs.csv`, the methodology boundaries. Every tick now carries its own.
- `data/state/hot_boards.json`, the `hot_boards` stage's ranking. The Space ranks Hot at boot
  from the same history (ADR-0230 step 5).

Run it after `migrate_trends_to_one_delta_history.py --apply` has landed and the Space from
step 6 is live, then run `reclaim_storage`: a delete removes the file from the head revision, not
its stored bytes (ADR-0168).

**It refuses, and deletes nothing, unless all three hold:**
1. **The pipeline's chain is paused.** The `merge` upload adds and never deletes, so a run that
   fetched `data/state` before the delete and uploads it after would put all four files back.
   `retire_legacy_trends_csv.py` met exactly that resurrection.
2. **The live Space reads the new layout.** Its deployed `headstart/trend_history.py` names the
   archive, which only step 6's reader does. An older Space would read the aggregate at its next
   boot and find nothing.
3. **The history on HF reproduces what is deleted.** It fetches the tick files, the archive and
   the three files this retires that hold counts or methodology, and runs the migration's own
   verification on them: the Board-count snapshot at its `as_of` with 0 differing keys, the
   aggregate at every tick, and the epoch boundaries exactly. Nothing else holds the 615 ticks
   before per-Board counting, so this is the last chance to notice that the archive does not.

**Dry run by default.** `--apply` deletes every file still present in one commit. It is not
reversible: the merge job super-squashes the dataset every run, so there is no prior revision to
restore from. It is not idempotent: check 3 needs every retired file present, so a run that
finds some already gone refuses rather than deleting the rest.

Usage:
    python scripts/state/retire_superseded_trends_files.py            # report only
    python scripts/state/retire_superseded_trends_files.py --apply    # delete them
    HF_DATASET=imPoseidon/headstart-index python -m headstart.ingest.reclaim_storage
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

# Never over Xet: it dies silently mid-transfer (see CLAUDE.md). Set before huggingface_hub loads.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# A sibling script, importable because running a script puts its own directory on sys.path.
from migrate_trends_to_one_delta_history import (
    AGGREGATE,
    ARCHIVE,
    BOARD_COUNTS,
    DELTAS,
    EPOCHS,
    REPO,
    STATE,
    chain_refusal,
    verify,
)

SPACE = "imPoseidon/headstart-search"
HOT = "hot_boards.json"
RETIRED = tuple(f"{STATE}/{name}" for name in (AGGREGATE, BOARD_COUNTS, EPOCHS, HOT))
# The Space's reader of the history, as deploy-space.yml copies it into the Space repo.
SPACE_READER = "headstart/trend_history.py"


def space_refusal() -> str | None:
    """Why the live Space cannot do without the retired files, or None when it can."""
    from huggingface_hub import hf_hub_download
    from huggingface_hub.utils import EntryNotFoundError

    try:
        reader = Path(hf_hub_download(SPACE, SPACE_READER, repo_type="space"))
    except EntryNotFoundError:
        return f"{SPACE} has no {SPACE_READER}; deploy step 6 first"
    if ARCHIVE not in reader.read_text(encoding="utf-8"):
        return f"{SPACE}'s {SPACE_READER} does not read {ARCHIVE}; deploy step 6 first"
    return None


def history_refusal(work: Path) -> str | None:
    """Why the history on HF does not reproduce the files to be retired, or None when it does."""
    from huggingface_hub import snapshot_download

    snapshot_download(
        REPO,
        repo_type="dataset",
        local_dir=work,
        allow_patterns=[
            f"{STATE}/{DELTAS}/*",
            *(f"{STATE}/{name}" for name in (ARCHIVE, AGGREGATE, BOARD_COUNTS, EPOCHS)),
        ],
    )
    state = work / STATE
    # snapshot_download returns quietly when the Hub is unreachable, so ask what arrived.
    missing = [
        name
        for name in (DELTAS, ARCHIVE, AGGREGATE, BOARD_COUNTS, EPOCHS)
        if not (state / name).exists()
    ]
    if missing:
        return f"not on {REPO} (or not fetched): {', '.join(missing)}"
    try:
        print("verifying the history on the dataset", flush=True)
        check = verify(state, state)
    except ValueError as exc:
        return f"the history on {REPO} is not migrated: {exc}"
    print(
        f"history     {check.history_ticks} tick(s); board counts @ {check.board_counts_as_of}: "
        f"{check.board_count_keys_differing} of {check.board_count_keys:,} keys differ; "
        f"aggregate {check.aggregate_ticks_compared} of {check.aggregate_ticks} tick(s) "
        f"compared, {len(check.aggregate_ticks_differing)} differ; counting changes "
        f"{'match' if check.counting_changes == check.epoch_boundaries else 'DIFFER from'} "
        "the epoch boundaries",
        flush=True,
    )
    return (
        None
        if check.clean
        else "the history does not reproduce the files to be retired"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true", help="actually delete (default: report only)"
    )
    args = parser.parse_args()

    from huggingface_hub import CommitOperationDelete, HfApi

    api = HfApi()
    sizes = {
        s.rfilename: (s.size or 0)
        for s in api.repo_info(REPO, repo_type="dataset", files_metadata=True).siblings
    }
    present = [path for path in RETIRED if path in sizes]
    for path in RETIRED:
        size = f"{sizes[path] / 1e6:8.2f} MB" if path in sizes else "already gone"
        print(f"{size:>14}  {path}", flush=True)
    if not present:
        print("nothing to retire", flush=True)
        return 0

    with tempfile.TemporaryDirectory(prefix="trends-retirement-") as work:
        # In order, each only once the one before it passed: the last fetches the history.
        for check in (
            chain_refusal,
            space_refusal,
            lambda: history_refusal(Path(work)),
        ):
            refusal = check()
            if refusal:
                print(f"REFUSING: {refusal}", file=sys.stderr, flush=True)
                return 1

    print(
        f"reclaims {sum(sizes[p] for p in present) / 1e6:.2f} MB from the head revision",
        flush=True,
    )
    if not args.apply:
        print("\ndry run: pass --apply to delete", flush=True)
        return 0
    api.create_commit(
        REPO,
        [CommitOperationDelete(path_in_repo=path) for path in present],
        repo_type="dataset",
        commit_message="Retire the Trends files the Board-delta history replaced (ADR-0230)",
    )
    print(f"\ndeleted {len(present)} file(s); now run reclaim_storage", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
