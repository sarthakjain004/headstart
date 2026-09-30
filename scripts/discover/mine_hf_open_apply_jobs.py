#!/usr/bin/env python3
"""Hugging Face miner: the slugs a daily public crawl of Gem, Ashby and Rippling Boards carries.

`edwarddgao/open-apply-jobs` (MIT, ungated) is "a daily-refreshed open dataset of active job
postings sourced directly from public ATS APIs": one full snapshot per `data/date=YYYY-MM-DD/
source={ats}/` folder, with `source`, `source_slug` and `apply_url` columns. Its author found
Boards our ledgers lack on the three *small* ledgers, which is why popularity-biased lists (0% to
2% unheld on Greenhouse, Lever, Workday) still pay here. Measured 2026-09-29 against the ledgers:
Gem 123 unheld of 906 slugs, Ashby 165 of 3,858, Rippling 73 of 1,453.

Reads only the `source_slug` column of the newest snapshot that carries each source, through
`HfFileSystem` with Xet off (`HF_HUB_DISABLE_XET=1`, this repo's rule: a Xet transfer can die
silently). Spellings follow each ledger: Gem `https://jobs.gem.com/{slug}`, Ashby
`https://jobs.ashbyhq.com/{slug}` with a space written `%20` (the tenant keeps the space, as the 30
landed spaced rows do), Rippling `ats.rippling.com/{slug}` (the ledger's majority, no scheme).
Everything else goes through `board_heldness`; liveness is `check_liveness.py`'s job.

Run:   python -u scripts/discover/mine_hf_open_apply_jobs.py
Then:  python scripts/validate/check_liveness.py --dir data/wayback-ats gem ashby rippling
"""

from __future__ import annotations

import os
from urllib.parse import quote

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import pyarrow.parquet as pq
from board_heldness import stage_unheld
from huggingface_hub import HfFileSystem

DATA = "datasets/edwarddgao/open-apply-jobs/data"
URL_OF = {
    "gem": lambda slug: f"https://jobs.gem.com/{slug}",
    "ashby": lambda slug: f"https://jobs.ashbyhq.com/{quote(slug)}",
    "rippling": lambda slug: f"ats.rippling.com/{slug}",
}


def rows_for(ats: str, slugs: set[str]) -> list[tuple[str, str]]:
    """`(tenant, url)` for each slug in the ledger's own spelling, in slug order."""
    return [(slug, URL_OF[ats](slug)) for slug in sorted(slugs)]


def snapshot_slugs(fs: HfFileSystem, source: str) -> tuple[str, set[str]]:
    """The newest snapshot's date and its distinct `source_slug`s for `source`."""
    dates = sorted(
        (d.rsplit("/", 1)[-1] for d in fs.ls(DATA, detail=False)), reverse=True
    )
    for date in dates:
        folder = f"{DATA}/{date}/source={source}"
        if not fs.exists(folder):
            continue
        found: set[str] = set()
        for path in fs.ls(folder, detail=False):
            column = (
                pq.ParquetFile(path, filesystem=fs)
                .read(columns=["source_slug"])
                .column("source_slug")
            )
            found.update(str(s) for s in column.to_pylist() if s)
        return date, found
    raise SystemExit(f"no snapshot carries source={source}")


def main() -> None:
    fs = HfFileSystem()
    for ats in URL_OF:
        date, found = snapshot_slugs(fs, ats)
        print(f"{ats}: {len(found)} distinct slugs in {date}", flush=True)
        print(
            ats,
            dict(stage_unheld(ats, rows_for(ats, found), "hf_open_apply_jobs")),
            flush=True,
        )


if __name__ == "__main__":
    main()
