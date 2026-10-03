#!/usr/bin/env python3
"""Keep one current public label per Comeet company UID in pool and ledger.

Run after discovery merges and liveness refreshes. Only a successfully read canonical
page can replace its UID's rows, and never evidence older than a recorded verdict.
--captures replays dated HTTP metadata/body pairs; --resume continues saved proofs.
Progress is atomically checkpointed after every attempt. Default is dry-run; --apply
writes both CSVs. Failed/unknown reads preserve prior evidence (ADR-0386).
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from headstart.scrapers.comeet import ComeetScraper, hosted_board

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "data/validate/liveness/comeet.csv"
POOL = ROOT / "data/ats-tenants-merged/comeet.csv"
PROGRESS = ROOT / "experiment/comeet-public-api/canonical-progress.json"


def canonicalize(
    rows: list[dict], proven: dict[str, tuple[str, int, str]]
) -> list[dict]:
    """Replace only UIDs with canonical evidence at least as recent as their verdicts."""
    output = list(rows)
    for uid, (slug, count, observed_at) in sorted(proven.items()):
        group = [r for r in output if r["tenant"].rsplit("/", 1)[-1].lower() == uid]
        if not group:
            continue
        observed_day = datetime.fromisoformat(observed_at).date().isoformat()
        if any(r.get("checked_at", "")[:10] > observed_day for r in group):
            continue
        row = dict(group[0], tenant=slug, url=f"https://www.comeet.com/jobs/{slug}")
        if "status" in row:
            row.update(status="live", jobs=str(count), checked_at=observed_day)
        if "source" in row:
            row["source"] = "+".join(
                sorted(
                    {s for r in group for s in r.get("source", "").split("+") if s}
                    | {"public-canonical"}
                )
            )
        output = [r for r in output if r not in group]
        output.append(row)
    return sorted(output, key=lambda row: row["tenant"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path)
    parser.add_argument("--progress", type=Path, default=PROGRESS)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    rows = list(csv.DictReader(LEDGER.open()))
    candidates = {r["tenant"] for r in rows if r["status"] == "live"}
    proven = (
        json.loads(args.progress.read_text())
        if args.resume and args.progress.exists()
        else {}
    )
    renamed = set()

    def checkpoint(slug: str, outcome: str) -> None:
        args.progress.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.progress.with_suffix(".tmp")
        temporary.write_text(json.dumps(proven, indent=2))
        temporary.replace(args.progress)
        print(
            f"{slug}: {outcome}; {len(proven)} canonical proofs checkpointed",
            flush=True,
        )

    def read(slug: str, page: str, observed_at: str) -> None:
        company, jobs = hosted_board(page)
        canonical = f"{company['slug']}/{company['company_uid']}".lower()
        uid = company["company_uid"].lower()
        if slug.lower() == canonical:
            stamp = datetime.fromisoformat(observed_at)
            if stamp.tzinfo is None:
                raise ValueError("canonical proof needs a timezone")
            observed_at = stamp.astimezone(UTC).isoformat()
            previous = proven.get(uid)
            if previous is None or observed_at >= previous[2]:
                proven[uid] = (
                    canonical,
                    sum(not row.get("is_internal") for row in jobs),
                    observed_at,
                )
        else:
            renamed.add(canonical)

    if args.captures:
        for path in sorted(args.captures.glob("*.json")):
            meta = json.loads(path.read_text())
            if meta.get("status") != 200:
                continue
            slug = ComeetScraper.slug_from("", meta["url"])
            try:
                stamp = meta.get("captured_at")
                if not stamp:
                    headers = {k.lower(): v for k, v in meta.get("headers", {}).items()}
                    stamp = parsedate_to_datetime(headers["date"]).isoformat()
                read(slug, path.with_suffix(".body").read_text(), stamp)
                checkpoint(slug, "read dated capture")
            except (ValueError, KeyError, TypeError) as exc:
                checkpoint(slug, f"preserve: {type(exc).__name__}")
    else:
        for slug in sorted(candidates):
            uid = slug.rsplit("/", 1)[-1]
            proof = proven.get(uid)
            newest = max(
                r["checked_at"] for r in rows if r["tenant"].rsplit("/", 1)[-1] == uid
            )
            if args.resume and proof and proof[2][:10] >= newest[:10]:
                checkpoint(slug, "resume dated proof")
                continue
            try:
                read(
                    slug, ComeetScraper(slug).fetch_raw(), datetime.now(UTC).isoformat()
                )
                checkpoint(slug, "read canonical page")
            except Exception as exc:  # noqa: BLE001 - an unresolved UID keeps its old rows
                checkpoint(slug, f"preserve: {type(exc).__name__}")
    for slug in sorted(renamed):
        uid = slug.rsplit("/", 1)[-1]
        proof = proven.get(uid)
        newest = max(
            (r["checked_at"] for r in rows if r["tenant"].rsplit("/", 1)[-1] == uid),
            default="",
        )
        if proof and proof[2][:10] >= newest[:10]:
            continue
        try:
            read(slug, ComeetScraper(slug).fetch_raw(), datetime.now(UTC).isoformat())
            checkpoint(slug, "read renamed canonical page")
        except Exception as exc:  # noqa: BLE001 - never erase live evidence on a failed read
            checkpoint(slug, f"preserve: {type(exc).__name__}")
    # Stale capture evidence cannot alter the pool either. Filter it against the ledger first.
    usable = {
        uid: proof
        for uid, proof in proven.items()
        if not any(
            r["checked_at"][:10] > proof[2][:10]
            for r in rows
            if r["tenant"].rsplit("/", 1)[-1] == uid
        )
    }
    for path in (LEDGER, POOL):
        old = list(csv.DictReader(path.open()))
        new = canonicalize(old, usable)
        print(
            f"{path.name}: {len(old)} -> {len(new)} rows; {len(usable)} current canonical proofs",
            flush=True,
        )
        if args.apply and old:
            with path.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(old[0]))
                writer.writeheader()
                writer.writerows(new)


if __name__ == "__main__":
    main()
