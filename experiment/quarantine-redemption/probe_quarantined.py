#!/usr/bin/env python3
"""Re-probe every quarantined Board's own listing URL once, and record what it answers now.

Reads data/state/board_failures.csv (pulled from HF), builds each Board's listing URL with the
same scraper class the pipeline would use, and does one GET. Streams a JSONL row per Board.
"""

from __future__ import annotations

import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

sys.path.insert(0, "src")
from headstart.scrapers.registry import SCRAPERS

UA = "Mozilla/5.0 (compatible; HeadStart/1.0; +https://github.com/imPoseidon/HeadStart)"


def probe(board: str) -> dict:
    ats, _, slug = board.partition(":")
    out = {"board": board, "ats": ats}
    cls = SCRAPERS.get(ats)
    if cls is None:
        return out | {"error": "no scraper"}
    try:
        url = cls(slug).url()
    except Exception as exc:  # noqa: BLE001
        return out | {"error": f"url(): {type(exc).__name__}: {exc}"}
    out["url"] = url
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        out["status"] = r.status_code
        out["bytes"] = len(r.content)
        out["final"] = r.url
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return out


def main() -> int:
    with open("data/state/board_failures.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    boards = [r["board"] for r in rows if int(r["strikes"]) >= 5]
    print(f"probing {len(boards)} quarantined boards", file=sys.stderr, flush=True)
    with open(sys.argv[1], "w") as fh, ThreadPoolExecutor(max_workers=12) as pool:
        futs = {pool.submit(probe, b): b for b in boards}
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            if i % 50 == 0:
                print(f"  {i}/{len(boards)}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
