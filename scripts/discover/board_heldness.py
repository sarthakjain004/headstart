"""Which Boards the liveness ledgers already hold, read the way the scrapers read them, and how a
miner stages the ones they do not.

CLAUDE.md's landing rule: decide "new" by `board_key`, never by parsing a URL, because
`check_liveness.py` keys a ledger on the raw `tenant` string and a Board held under another spelling
would land as a second row. So a candidate `(tenant, url)` is turned into a Company through the
same funnel `scrapable_boards.load` uses (`registry.company_from_row`, which runs the Scraper's own
`slug_from`) and identified by `board_identity`, then matched, case-folded, against **every** ledger
row of the ATS -- live, dead or unknown, because a Board a probe already read dead is not new.

`stage_unheld` is the one write a miner makes: it appends the unheld candidates, `ats,tenant,url`,
to `data/wayback-ats/{ats}.csv`, the gitignored pool `check_liveness.py --dir data/wayback-ats {ats}`
reads. It never touches a ledger.

Two more files record what each source found, so a landing can be credited to its source after the
pool has merged them (several sources find the same Board, and only the first stages it):
`.baseline_held/{ats}.txt`, the Board keys the ledger held the first time this checkout looked, and
`.sources/{source}-{ats}.tsv`, every candidate of that source the baseline did not hold.
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from headstart.boards.board_identity import board_identity
from headstart.scrapers.registry import company_from_row

LEDGERS = ROOT / "data" / "validate" / "liveness"
POOL = ROOT / "data" / "wayback-ats"
BASELINE = POOL / ".baseline_held"
SOURCES = POOL / ".sources"

_HELD: dict[str, dict[str, str]] = {}


def key_of(ats: str, tenant: str, url: str) -> str | None:
    """The Board this row names, case-folded, or None when the Scraper cannot read it."""
    try:
        return board_identity(company_from_row(ats, tenant, url)).lower()
    except Exception:  # noqa: BLE001 - a row no Scraper can read names no Board
        return None


def held_keys(ats: str) -> dict[str, str]:
    """`{board_key: status}` over every ledger row of `ats` now, live over unknown over dead."""
    if ats not in _HELD:
        rank = {"live": 3, "unknown": 2, "dead": 1}
        held: dict[str, str] = {}
        path = LEDGERS / f"{ats}.csv"
        for row in csv.DictReader(path.open(encoding="utf-8")):
            key = key_of(ats, row["tenant"], row["url"])
            if key and rank[row["status"]] > rank.get(held.get(key), 0):
                held[key] = row["status"]
        _HELD[ats] = held
    return _HELD[ats]


def baseline_keys(ats: str) -> set[str]:
    """The Board keys the ledger held before this checkout landed anything, saved on first use."""
    path = BASELINE / f"{ats}.txt"
    if not path.exists():
        BASELINE.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(f"{key}\n" for key in sorted(held_keys(ats))), encoding="utf-8"
        )
    return set(path.read_text(encoding="utf-8").split("\n")) - {""}


def stage_unheld(
    ats: str, candidates: Iterable[tuple[str, str]], source: str
) -> Counter:
    """Append the candidates no ledger row holds to the pool, and count what happened.

    `candidates` are `(tenant, url)` in the ledger's own spelling; `source` names the miner's
    surface (`common_crawl_host_graph`). Counted: `candidates`, `unreadable` (no Board key), `held`
    (a ledger row shares the key), `duplicate` (already staged, this run or an earlier one),
    `staged`, and `unheld_at_baseline` (what the source found that the ledger lacked before any
    landing here, staged or not). Safe to re-run: staged rows are skipped.
    """
    POOL.mkdir(parents=True, exist_ok=True)
    SOURCES.mkdir(parents=True, exist_ok=True)
    path = POOL / f"{ats}.csv"
    seen: set[str] = set()
    if path.exists():
        for row in csv.DictReader(path.open(encoding="utf-8")):
            key = key_of(ats, row["tenant"], row["url"])
            if key:
                seen.add(key)
    held = held_keys(ats)
    baseline = baseline_keys(ats)
    credited: set[str] = set()
    source_path = SOURCES / f"{source}-{ats}.tsv"
    counts: Counter = Counter()
    fresh = not path.exists()
    out = path.open("a", newline="", encoding="utf-8")
    credit = source_path.open("w", encoding="utf-8")
    try:
        writer = csv.writer(out)
        if fresh:
            writer.writerow(("ats", "tenant", "url"))
            out.flush()
        for tenant, url in candidates:
            counts["candidates"] += 1
            key = key_of(ats, tenant, url)
            if key is None:
                counts["unreadable"] += 1
                continue
            if key not in baseline and key not in credited:
                credited.add(key)
                credit.write(f"{ats}\t{tenant}\t{url}\n")
                credit.flush()  # every hit row reaches disk as found, so a crash loses none
                counts["unheld_at_baseline"] += 1
            if key in held:
                counts["held"] += 1
            elif key in seen:
                counts["duplicate"] += 1
            else:
                seen.add(key)
                writer.writerow((ats, tenant, url))
                out.flush()
                counts["staged"] += 1
    finally:
        out.close()
        credit.close()
    return counts
