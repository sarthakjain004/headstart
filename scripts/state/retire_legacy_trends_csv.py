#!/usr/bin/env python3
"""Retire `data/state/role_trends.csv`, the pre-ADR-0120 trends ledger, from the HF dataset.

ADR-0120 moved the trends ledger from CSV to Parquet and prescribed the cleanup in as many words
(`docs/adr/0120-the-trends-ledger-is-parquet-not-csv.md:87`): a one-time
`HfApi().delete_file("data/state/role_trends.csv", ...)` **after** the first Parquet has landed.
The Parquet landed on 2026-09-09; the delete did not happen until **2026-09-21**, twelve days and
~300 runs later, at which point the CSV had cost ~11.7 GB/day of needless egress — `scrape_plan`
and `join` both fetch `data/state/*` with a wildcard, so it rode the wire twice per run.

Measured against the live dataset: the CSV was **174.89 MB** — 82% of `data/state/`'s 212.8 MB —
while `role_trends.parquet` carries the same ledger in **7.29 MB**. Nothing reads the CSV in steady
state: `role_trends.py`'s `_LEDGER` points at the Parquet and `deploy/hf-space/app.py` serves
`/trends` from it. Its one reader is `role_trends.py`'s migration fold-in, on the branch taken only
when the **Parquet is absent** — which is exactly the recovery net `MIN_ROWS` below protects.

The Parquet is a superset, verified before the delete rather than taken from the ADR: its
row-group `ts` statistics span `2026-08-11T12:57:28+00:00 -> 2026-09-21T16:55:09+00:00`, the lower
bound being exactly the CSV's first row; 2,400,903 of its 5,405,929 rows predate the migration; and
the CSV's literal first row (`stock,ai-ml,entry,all`) reads back with its identical count of 819.

**Kept after running, because it is idempotent and the file can come back.** `join` packs the whole
of `data/state/` into the `corpus-state` artifact and `merge` re-uploads it *without* `--delete`, so
any run sitting between `join`'s `state_fetch` and `merge`'s upload when the delete lands will
resurrect the CSV. Re-run this then; on a clean dataset it reports `already gone` and exits 0.

**Dry run by default.** `--apply` performs the delete, which is not reversible: the merge job
super-squashes the dataset every run (measured: `list_repo_commits` returns exactly 1), so there is
no prior revision to restore from.

Usage:
    python scripts/state/retire_legacy_trends_csv.py            # report only
    python scripts/state/retire_legacy_trends_csv.py --apply    # delete it
"""

from __future__ import annotations

import argparse
import sys

from huggingface_hub import HfApi, hf_hub_download

REPO = "imPoseidon/headstart-index"
LEGACY = "data/state/role_trends.csv"
REPLACEMENT = "data/state/role_trends.parquet"
#: The landed Parquet must carry the whole history, not merely exist. `merge` running without
#: the `corpus-state` artifact writes a *fresh* ledger of one tick and the upload publishes it
#: over the real one (ADR-0120's own "pre-existing hazard"), and `role_trends.py` folds the CSV
#: back in only when the Parquet is **absent** — so a short-but-present Parquet plus a deleted
#: CSV is silent, permanent loss of every historical row. The floor is the **2,400,903**
#: pre-cutover rows measured on 2026-09-21, rounded down: a Parquet holding fewer cannot be
#: carrying the folded-in history. It sits far above the ~10,700 of a single tick, which is the
#: shape a failed fold-in actually takes. `docs/agents/deployment.md` quotes the same floor, but
#: the number's authority is that measurement, not the doc.
MIN_ROWS = 2_400_000


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true", help="actually delete (default: report only)"
    )
    args = parser.parse_args()

    api = HfApi()
    info = api.repo_info(REPO, repo_type="dataset", files_metadata=True)
    sizes = {s.rfilename: (s.size or 0) for s in info.siblings}

    if LEGACY not in sizes:
        print(f"{LEGACY} is already gone — nothing to do", flush=True)
        return 0

    # Refuse to delete the old ledger unless the new one is actually there: a delete with no
    # replacement loses the ledger outright. This is the cheap half of the precondition — the
    # half ADR-0120 actually states is the row floor below.
    if REPLACEMENT not in sizes:
        print(
            f"REFUSING: {REPLACEMENT} is absent, so {LEGACY} is still the only trends ledger",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print(f"legacy      {sizes[LEGACY] / 1e6:8.2f} MB  {LEGACY}", flush=True)
    print(f"replacement {sizes[REPLACEMENT] / 1e6:8.2f} MB  {REPLACEMENT}", flush=True)

    # ADR-0120's precondition, enforced rather than assumed: presence is not enough, the landed
    # Parquet must carry >2.4M rows (see MIN_ROWS). Read from the file's own Parquet metadata
    # rather than its size: `zstd` means bytes do not bound row count in either direction.
    import pyarrow.parquet as pq

    rows = pq.ParquetFile(
        hf_hub_download(REPO, REPLACEMENT, repo_type="dataset")
    ).metadata.num_rows
    print(f"            {rows:,} rows (floor {MIN_ROWS:,})", flush=True)

    if rows <= MIN_ROWS:
        print(
            f"REFUSING: {REPLACEMENT} carries only {rows:,} rows, not over the {MIN_ROWS:,} floor — "
            f"the fold-in did not happen, so {LEGACY} is still the truth. Re-run the migration "
            "first; this delete is not reversible.",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print(
        f"reclaims    {sizes[LEGACY] / 1e6:8.2f} MB from the head revision", flush=True
    )

    if not args.apply:
        print("\ndry run — pass --apply to delete", flush=True)
        return 0

    api.delete_file(
        path_in_repo=LEGACY,
        repo_id=REPO,
        repo_type="dataset",
        commit_message="Retire the pre-ADR-0120 CSV trends ledger",
    )
    print(f"\ndeleted {LEGACY}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
