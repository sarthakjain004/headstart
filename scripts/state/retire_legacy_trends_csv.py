#!/usr/bin/env python3
"""Retire `data/state/role_trends.csv`, the pre-ADR-0120 trends ledger, from the HF dataset.

ADR-0120 moved the trends ledger from CSV to Parquet and prescribed the cleanup in as many words
(`docs/adr/0120-the-trends-ledger-is-parquet-not-csv.md:87`): a one-time
`HfApi().delete_file("data/state/role_trends.csv", ...)` **after** the first Parquet has landed.
The Parquet landed; the delete never happened.

Measured 2026-09-16 against the live dataset: the CSV is **174.89 MB** — the fifth-largest file in
the repo and 2.7% of the 6.50 GB head revision — while `role_trends.parquet` carries the same
ledger in **5.22 MB**. Nothing reads the CSV: `role_trends.py`'s `_LEDGER` points at the Parquet,
and a grep of `src/` and `scripts/` finds no other reference.

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

from huggingface_hub import HfApi

REPO = "imPoseidon/headstart-index"
LEGACY = "data/state/role_trends.csv"
REPLACEMENT = "data/state/role_trends.parquet"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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

    # Refuse to delete the old ledger unless the new one is actually there. The ADR's precondition,
    # enforced rather than assumed: a delete with no replacement loses the ledger outright.
    if REPLACEMENT not in sizes:
        print(
            f"REFUSING: {REPLACEMENT} is absent, so {LEGACY} is still the only trends ledger",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print(f"legacy      {sizes[LEGACY] / 1e6:8.2f} MB  {LEGACY}", flush=True)
    print(f"replacement {sizes[REPLACEMENT] / 1e6:8.2f} MB  {REPLACEMENT}", flush=True)
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
