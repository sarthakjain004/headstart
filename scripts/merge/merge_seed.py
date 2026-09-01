#!/usr/bin/env python3
"""Merge the harvested company-seed shards into one global seed for the fingerprinter.

Each discovery door writes its own shard to data/discover/seed/{door}.csv with columns
name,domain,sector,source. This unions them, normalizes the domain (the only field the
fingerprinter actually dereferences), drops what the existing seed already holds, and
orders the result so that `python scripts/resolve/fingerprint.py [n]` spends its first n
fetches on the rows most likely to yield a new Board.

Ordering matters because the fingerprinter's only knob is a prefix count. Rows sort
uncovered-first, then by door: hiring signals (a company posting jobs today has a live
Board to find), then repo-local unresolved (already carries an ATS provider hint), then
the firmographic/regional/registry bulk.

`covered` marks a row whose normalized name matches a live slug already in the liveness
ledger. It is a *hint that sorts*, never a filter — the match is name-to-slug, which is
approximate, so dropping on it would lose Boards. Companies genuinely already covered
just sort last.

Output: config/seed_global.csv  (name,domain,sector,source,covered)

Run:  python scripts/merge/merge_seed.py
"""

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SEED_DIR = ROOT / "data" / "discover" / "seed"
KNOWN_SLUGS = SEED_DIR / "known_live_slugs.txt"
EXISTING = ROOT / "config" / "seed_india.csv"
OUT = ROOT / "config" / "seed_global.csv"
MISMATCH_OUT = SEED_DIR / "_dropped_name_domain_mismatch.csv"

# Door priority, MEASURED not assumed: each door's first 40 rows were fingerprinted on identical
# code and scored by how many resolved to an ATS.
#   repo_unresolved     17/40  42.5%   (already carries an ATS provider hint)
#   hiring_signals      12/40  30.0%   (HN/WWR — real but micro-startups, often no ATS at all)
#   lusha_firmographic   3/40   7.5%   (large established firms; mostly enterprise ATSes we
#                                       don't support, or none)
#   open_registries      1/40   2.5%   (yc/wikidata bulk)
#   global_regions       0/40   0.0%   (wikidata/ambitionbox regional — nothing, and it is the
#                                       LARGEST shard: 1,809 rows of near-zero yield)
# The two registry-shaped doors are ~2,760 of the 5,189 rows and between them scored 1 hit in 80.
# That is the whole argument for measuring order rather than assuming it: the top two doors
# (~1,600 rows) hold essentially all the recoverable Boards in the seed.
DOOR_RANK = {
    "repo_unresolved": 0,
    "hiring_signals": 1,
    "lusha_firmographic": 2,
    "open_registries": 3,
    "global_regions": 4,
}


def norm_domain(raw: str) -> str:
    """Bare lowercase apex-ish host, or '' if the value can't be a domain."""
    d = raw.strip().lower()
    d = re.sub(r"^[a-z]+://", "", d)
    d = d.split("/")[0].split("?")[0].split("@")[-1].split(":")[0]
    d = d.removeprefix("www.").rstrip(".")
    return d if "." in d and re.fullmatch(r"[a-z0-9.-]+", d) else ""


def norm_name(raw: str) -> str:
    return re.sub(r"[^a-z0-9]", "", raw.lower())


def name_matches_domain(name: str, domain: str) -> bool:
    """Does the company name plausibly belong to this domain?

    Registry and derived-domain doors produce rows whose name and host are unrelated
    (`Virtusa` -> westentou.com, `Reliance Jio` -> ramjaju.in). Each one costs the
    fingerprinter a fetch against a host that cannot hold that company's Board. Prefix
    agreement either way catches rebrands and truncations without demanding equality.
    """
    nm, lab = norm_name(name), norm_name(domain.split(".")[0])
    if not nm or not lab:
        return False
    return (
        lab in nm
        or nm in lab
        or (len(lab) >= 4 and lab[:5] in nm)
        or (len(nm) >= 4 and nm[:5] in lab)
    )


def main() -> int:
    if not SEED_DIR.is_dir():
        sys.exit(f"no seed directory: {SEED_DIR}")

    known = set()
    if KNOWN_SLUGS.exists():
        known = {
            ln.strip() for ln in KNOWN_SLUGS.read_text().splitlines() if ln.strip()
        }
    print(f"known live slugs: {len(known)}", flush=True)

    already = set()
    if EXISTING.exists():
        already = {
            norm_domain(r["domain"])
            for r in csv.DictReader(EXISTING.open(encoding="utf-8"))
        }
        already.discard("")
    print(f"already seeded (config/seed_india.csv): {len(already)}", flush=True)

    rows: dict[str, dict] = {}
    mismatches: list[list[str]] = []
    dropped_bad, dropped_existing, dropped_mismatch, dupes = 0, 0, 0, 0

    for shard in sorted(SEED_DIR.glob("*.csv")):
        # "_"-prefixed files are this script's own outputs, not doors — same convention as
        # merge_harvest_into_tenants.py. Without this the dropped-mismatch dump written below
        # would be re-ingested as a shard on the next run, re-adding exactly what it dropped.
        if shard.stem.startswith("_"):
            continue
        door = shard.stem
        kept = 0
        for r in csv.DictReader(shard.open(encoding="utf-8")):
            domain = norm_domain(r.get("domain", ""))
            name = (r.get("name") or "").strip()
            if not domain or not name:
                dropped_bad += 1
                continue
            if domain in already:
                dropped_existing += 1
                continue
            if not name_matches_domain(name, domain):
                dropped_mismatch += 1
                mismatches.append([name, domain, r.get("source") or door])
                continue
            if domain in rows:
                dupes += 1
                continue
            rows[domain] = {
                "name": name,
                "domain": domain,
                "sector": (r.get("sector") or "").strip(),
                "source": (r.get("source") or door).strip(),
                "covered": "yes" if norm_name(name) in known else "no",
                "_rank": DOOR_RANK.get(door, 9),
            }
            kept += 1
        print(f"  {door:22s} +{kept}", flush=True)

    out = sorted(
        rows.values(), key=lambda r: (r["covered"] == "yes", r["_rank"], r["domain"])
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["name", "domain", "sector", "source", "covered"]
        )
        w.writeheader()
        for r in out:
            w.writerow({k: r[k] for k in w.fieldnames})

    # A dropped row is not a deleted row: the heuristic has real false positives (a rebrand, a
    # holding-company domain), so write them where they can be read back and re-added by hand.
    if mismatches:
        with MISMATCH_OUT.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["name", "domain", "source"])
            w.writerows(sorted(mismatches))

    uncovered = sum(1 for r in out if r["covered"] == "no")
    print(
        f"\n{len(out)} rows -> {OUT.relative_to(ROOT)}"
        f"  ({uncovered} uncovered first, {len(out) - uncovered} already-covered last)",
        flush=True,
    )
    print(
        f"dropped: {dropped_bad} malformed, {dropped_existing} already in seed_india, "
        f"{dropped_mismatch} name/domain mismatch, {dupes} cross-shard dupes",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
