#!/usr/bin/env python3
"""Keep one current public label per Comeet company UID in the pool and ledger.

Run after discovery merges and every liveness refresh. A renamed account answers on old
and new labels, but the company's immutable UID owns its jobs. Retaining a dead old label
would let its later verdict shadow the current live label (ADR-0219). Only a successfully
read canonical page may replace its UID's rows; failures preserve every previous verdict.
--captures replays saved HTTP metadata/body pairs from this build's public probe.
Default is a dry run; --apply writes the two CSVs.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from headstart.scrapers.comeet import ComeetScraper, hosted_board

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "data/validate/liveness/comeet.csv"
POOL = ROOT / "data/ats-tenants-merged/comeet.csv"


def canonicalize(rows: list[dict], proven: dict[str, tuple[str, int]]) -> list[dict]:
    """Replace only UIDs whose canonical public Board was read successfully."""
    output = [
        row for row in rows if row["tenant"].rsplit("/", 1)[-1].lower() not in proven
    ]
    for uid, (slug, count) in sorted(proven.items()):
        group = [r for r in rows if r["tenant"].rsplit("/", 1)[-1].lower() == uid]
        if not group:
            continue
        row = dict(group[0], tenant=slug, url=f"https://www.comeet.com/jobs/{slug}")
        if "status" in row:
            row.update(
                status="live",
                jobs=str(count),
                checked_at=datetime.now(UTC).date().isoformat(),
            )
        if "source" in row:
            row["source"] = "+".join(
                sorted(
                    {s for r in group for s in r.get("source", "").split("+") if s}
                    | {"public-canonical"}
                )
            )
        output.append(row)
    return sorted(output, key=lambda row: row["tenant"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    rows = list(csv.DictReader(LEDGER.open()))
    candidates = {r["tenant"] for r in rows if r["status"] == "live"}
    proven: dict[str, tuple[str, int]] = {}
    renamed = set()

    def read(slug: str, page: str) -> None:
        company, jobs = hosted_board(page)
        canonical = f"{company['slug']}/{company['company_uid']}".lower()
        uid = company["company_uid"].lower()
        if slug.lower() == canonical:
            proven[uid] = (canonical, sum(not row.get("is_internal") for row in jobs))
        else:
            renamed.add(canonical)

    if args.captures:
        for path in sorted(args.captures.glob("*.json")):
            meta = json.loads(path.read_text())
            if meta.get("status") != 200:
                continue
            slug = ComeetScraper.slug_from("", meta["url"])
            try:
                read(slug, path.with_suffix(".body").read_text())
            except (ValueError, KeyError, TypeError):
                continue
    else:
        for slug in sorted(candidates):
            try:
                read(slug, ComeetScraper(slug).fetch_raw())
            except Exception as exc:  # noqa: BLE001 - an unresolved UID keeps its old rows
                print(f"preserve {slug}: {type(exc).__name__}")
    for slug in sorted(renamed):
        if slug.rsplit("/", 1)[-1] in proven:
            continue
        try:
            read(slug, ComeetScraper(slug).fetch_raw())
        except Exception as exc:  # noqa: BLE001 - never erase live evidence on a failed read
            print(f"preserve {slug}: {type(exc).__name__}")
    for path in (LEDGER, POOL):
        old = list(csv.DictReader(path.open()))
        new = canonicalize(old, proven)
        print(
            f"{path.name}: {len(old)} -> {len(new)} rows; {len(proven)} verified canonical UIDs"
        )
        if args.apply:
            with path.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(old[0]))
                writer.writeheader()
                writer.writerows(new)


if __name__ == "__main__":
    main()
