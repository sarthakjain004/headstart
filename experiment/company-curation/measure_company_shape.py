"""Measure the shape of the *company* population behind the served index.

Answers the questions a "trim the companies down" feature has to settle before any
of it is designed: how many companies are there, how concentrated are they, how many
are even nameable, how many are the same employer under two identities, and how many
are not employers at all.

Two sources, deliberately different in freshness (CLAUDE.md's freshest-data rule):
  * ``data/state/board_priority.csv`` — refresh from HF before running; it is ~1.8 MB.
  * ``data/lancedb`` — the served table. A local snapshot is fine for *shape* claims
    (concentration, identity, nameability) but its absolute row count is whatever this
    machine last pulled. The header printed below states which snapshot was read.

Run:  python -u experiment/company-curation/measure_company_shape.py [priority_csv]
"""

from __future__ import annotations

import collections
import csv
import re
import sys
from pathlib import Path

import lancedb

# ATSes whose scraper resolves a real company name (headstart.company_name.PATTERNS, ADR-0114).
NAMED = {
    "ashby",
    "eightfold",
    "jobvite",
    "keka",
    "lever",
    "ripplehire",
    "phenom",
    "taleo_enterprise",
}

HOST = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$", re.IGNORECASE)
VENDOR_HOST = re.compile(
    r"(icims|myworkdayjobs|oraclecloud|zohorecruit|eightfold|darwinbox|freshteam|phenom"
    r"|taleo|openings\.co|zwayam|sensehq|smartrecruiters|greenhouse|lever)\.",
    re.IGNORECASE,
)
# Deliberately crude: a *recall-biased* vocabulary probe, not a classifier. It over-fires on
# real employers whose board host carries "hr"/"recruiting" (Lockheed, RTX), so read its output
# as an upper bound on staffing/services presence, never as a count of agencies.
AGENCY_WORDS = re.compile(
    r"(staffing|recruit|consult|manpower|talent|hiring|placement|resourc|outsourc"
    r"|solutions|services|technolog|infotech|systems)",
    re.IGNORECASE,
)


def board_of(job_id: str) -> str:
    return job_id.rsplit(":", 1)[0]


def supply_side(path: Path) -> None:
    rows = list(csv.DictReader(path.open()))
    counts = sorted((int(r["last_tech_jobs"]) for r in rows), reverse=True)
    total = sum(counts)
    hiring = [c for c in counts if c >= 1]
    print(
        f"\n== board_priority ledger ({path}, newest row {max(r['updated_at'] for r in rows)}) =="
    )
    print(f"scored boards: {len(rows)}  hiring: {len(hiring)}  tech jobs: {total}")
    print(f"median tech jobs on a hiring board: {hiring[len(hiring) // 2]}")
    for k in (10, 100, 1000, 5000, 10000):
        print(
            f"  top {k:>5} boards hold {100 * sum(counts[:k]) / total:5.1f}% of the jobs"
        )


def served_side(db_path: Path) -> None:
    table = lancedb.connect(str(db_path)).open_table("jobs")
    arrow = table.to_lance().to_table(columns=["id", "ats", "title"])
    ids = arrow.column("id").to_pylist()
    atses = arrow.column("ats").to_pylist()
    titles = arrow.column("title").to_pylist()
    total = len(ids)
    boards = collections.Counter(board_of(i) for i in ids)
    print(f"\n== served table ({db_path}) ==")
    print(f"rows: {total}   distinct boards: {len(boards)}")
    per = sorted(boards.values(), reverse=True)
    for k in (10, 100, 1000, 5000):
        print(
            f"  top {k:>4} boards hold {100 * sum(per[:k]) / total:5.1f}% of served rows"
        )
    print(
        f"  boards serving <=3 rows: {sum(1 for v in per if v <= 3)} of {len(boards)}"
    )

    named = sum(1 for a in atses if a in NAMED)
    print(
        f"\nnameable: rows on an ATS that resolves a real company name: {named} ({100 * named / total:.1f}%)"
    )

    own = vendor = label = 0
    for job_id in ids:
        head = board_of(job_id).split(":", 1)[1].split("/")[0]
        if not HOST.match(head):
            label += 1
        elif VENDOR_HOST.search(head):
            vendor += 1
        else:
            own += 1
    print(
        "domain join feasibility (can we reach an employer domain from the board key?)"
    )
    print(f"  employer's own host : {own:7d} ({100 * own / total:4.1f}%)")
    print(f"  vendor-hosted tenant: {vendor:7d} ({100 * vendor / total:4.1f}%)")
    print(f"  bare label, no host : {label:7d} ({100 * label / total:4.1f}%)")

    # Split identity: the same board under two casings.
    folded = collections.defaultdict(list)
    for key, n in boards.items():
        folded[key.lower()].append((key, n))
    dups = {k: v for k, v in folded.items() if len(v) > 1}
    dup_rows = sum(n for v in dups.values() for _, n in v)
    print(
        f"\nboard keys colliding case-insensitively: {len(dups)} groups, {dup_rows} rows ({100 * dup_rows / total:.1f}%)"
    )
    for _, v in sorted(dups.items(), key=lambda kv: -sum(n for _, n in kv[1]))[:5]:
        print("   ", v)

    # Not-an-employer signals.
    by_board = collections.defaultdict(list)
    for job_id, title in zip(ids, titles):
        by_board[board_of(job_id)].append((title or "").strip().lower())
    repeaters = [
        (len(ts) / len(set(ts)), len(ts), b)
        for b, ts in by_board.items()
        if len(ts) >= 20
    ]
    repeat_rows = sum(n for r, n, _ in repeaters if r >= 3)
    print(
        f"\nrows on boards repeating a title >=3x on average: {repeat_rows} ({100 * repeat_rows / total:.1f}%)"
    )
    for r, n, b in sorted(repeaters, reverse=True)[:5]:
        print(f"    {r:5.1f}x  {n:5d} rows  {b}")
    agency = sum(
        n for b, n in boards.items() if AGENCY_WORDS.search(b.split(":", 1)[1])
    )
    print(
        f"upper bound on staffing/services vocabulary in the slug: {agency} rows ({100 * agency / total:.1f}%)"
    )

    # Query fragmentation: how many companies one ordinary query spans.
    print(
        "\nfragmentation per keyword (title match, unranked — a floor on what a query spans)"
    )
    for kw in ("backend", "data engineer", "frontend", "machine learning", "devops"):
        hits = [job_id for job_id, t in zip(ids, titles) if kw in (t or "").lower()]
        hb = collections.Counter(board_of(i) for i in hits)
        ordered = sorted(hb.values(), reverse=True)
        half, seen = 0, 0
        for n, v in enumerate(ordered, 1):
            seen += v
            if seen >= len(hits) / 2:
                half = n
                break
        print(
            f"  {kw:>16}: {len(hits):6d} jobs / {len(hb):5d} companies"
            f" | median 1 company = {sorted(hb.values())[len(hb) // 2]} jobs"
            f" | half the jobs sit on {half} companies"
        )


if __name__ == "__main__":
    priority = Path(
        sys.argv[1] if len(sys.argv) > 1 else "data/state/board_priority.csv"
    )
    if priority.exists():
        supply_side(priority)
    else:
        print(f"skipping supply side: {priority} not found (pull it from HF first)")
    served_side(Path("data/lancedb"))
