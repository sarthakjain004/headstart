#!/usr/bin/env python3
"""Find eightfold tenants published under two live vanity hostnames, and bury the loser (#154).

Some eightfold customers run their board under more than one hostname — e.g. `jobs.nvidia.com`
and `nvidia.eightfold.ai` — and both can end up live in the ledger as if they were separate
companies. They are not: eightfold's `_EF_GROUP_ID` (the API's `domain=` param, read from each
tenant's `/careers` page) names the underlying tenant, and every pair sharing one that this
project has checked carries a byte-identical job-id set. Both get scraped, both get indexed under
different Board keys (`eightfold:{slug}:{native_id}`, so the same native id yields two distinct
composite ids), and nothing downstream catches it: `index_plan.plan_prune`'s case-variant dedup
groups by matching an id's prefix against the live keep-set, and these are two *different* live
keep-set entries, not a casing variant of one.

Measured 2026-08-16 across all 110 then-live tenants: 6 clusters, every one at 100% id-set
overlap — nvidia, qualcomm, micron, hsbc, vodafone, dsm — ~10,240 duplicate rows, ~9.5% of
eightfold's total advertised volume. Full evidence: docs/eightfold/sitemap-primary-evaluation.md.

**Nothing here is trusted from a prior run** — every cluster is re-verified live, by fetching
each candidate's current sitemap id set and requiring near-total overlap, right before a verdict
is written (the same "nothing written unproven" discipline as relocate_dead_boards.py). A cluster
that no longer overlaps (one side genuinely diverged since the last check) is left untouched and
reported, not guessed at.

**The winner within a cluster** is the hostname NOT ending in `.eightfold.ai` when exactly one
candidate qualifies (a company's own branded domain over eightfold's generic subdomain); ties
(both `.eightfold.ai`, as with dsm-firmenich/dsm) fall back to the lexicographically smaller
hostname — a deterministic tie-break, not a judgement about which name is more "current"; a
company that renamed and kept both subdomains live could easily sort the wrong way. Wrong for a
particular cluster? Override it with `--prefer` (bare `winner_slug` lines, one per cluster; this
ledger is eightfold-only, so no `ats:` prefix is needed).

**Known gap, not closed here**: marking the loser `status=dead` removes its Board key from
`index sync/prune`'s live keep-set, so its already-indexed rows are evicted by the next
`index prune` — via the plain off-board path (`plan_prune`'s `off_board`, not the case-variant
`duplicate` group; that dedup is what fails to catch this problem *before* the fix, not the
mechanism that fixes it after). But the marking is not durably protected against
`check_liveness.py`'s own dead-TTL re-probe (`DEAD_TTL_DAYS`, 90 days by default) — a plain
re-probe of a genuinely-live, 200-answering host would flip it back to `live` and silently
re-admit the duplicate. ADR-0034 solved the identical durability problem for non-prod boards with
a pre-probe skip (`_NONPROD_TENANTS`) inside `check_liveness.py` itself; the same shape belongs
there for these tenants too, but that file carries unrelated uncommitted work as of this script's
authorship and is deliberately not touched here. Re-run this script periodically (or after any
`check_liveness.py` refresh) until that durability fix lands.

    python scripts/validate/dedupe_eightfold_aliases.py
    python scripts/validate/dedupe_eightfold_aliases.py --apply
    python scripts/validate/dedupe_eightfold_aliases.py --prefer winners.txt --apply
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from headstart import liveness
from headstart.scrapers.eightfold import group_id_for, sitemap_ids_for

LEDGER = ROOT / "data" / "validate" / "liveness" / "eightfold.csv"

# Below this fraction of the smaller side's ids found in the larger side, the pair is treated as
# genuinely diverged (not the same tenant any more) rather than ordinary posting-turnover noise —
# every cluster measured 2026-08-16 sat at 100%, so this has wide margin either side.
_OVERLAP_THRESHOLD = 0.95


def read_prefer(path: Path) -> set[str]:
    """Curated winner slugs, one per line — overrides the automatic pick for whichever cluster
    that slug belongs to. A cluster with no curated winner falls back to the automatic rule."""
    prefer: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" in line or " " in line:
            raise SystemExit(f"bad prefer line (want a bare slug): {line!r}")
        prefer.add(line)
    return prefer


def pick_winner(slugs: list[str], prefer: set[str]) -> str:
    for slug in slugs:
        if slug in prefer:
            return slug
    non_eightfold = [s for s in slugs if not s.endswith(".eightfold.ai")]
    if len(non_eightfold) == 1:
        return non_eightfold[0]
    return min(slugs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--prefer", type=Path, help="curated winner slugs, one bare slug per line"
    )
    ap.add_argument("--apply", action="store_true", help="write the ledger correction")
    args = ap.parse_args()
    prefer = read_prefer(args.prefer) if args.prefer else set()

    ledger = liveness.load(LEDGER)
    live = [v for v in ledger.values() if v.status == liveness.LIVE]
    print(f"probing group_id for {len(live)} live eightfold tenants...", flush=True)

    by_group: dict[str, list[str]] = defaultdict(list)
    for i, v in enumerate(live, 1):
        gid = group_id_for(v.tenant)
        if gid:
            by_group[gid.lower()].append(v.tenant)
        if i % 20 == 0:
            print(f"  {i}/{len(live)}...", flush=True)

    clusters = {g: slugs for g, slugs in by_group.items() if len(slugs) > 1}
    print(
        f"\n{len(clusters)} alias cluster(s) found; re-verifying overlap...", flush=True
    )

    to_bury: list[tuple[str, str]] = []  # (winner, loser)
    for gid, slugs in sorted(clusters.items()):
        id_sets = {slug: sitemap_ids_for(slug) for slug in slugs}
        winner = pick_winner(slugs, prefer)
        for slug in slugs:
            if slug == winner:
                continue
            a, b = id_sets[winner], id_sets[slug]
            union = a | b
            overlap = len(a & b) / len(union) if union else 0.0
            if overlap < _OVERLAP_THRESHOLD:
                print(
                    f"  SKIP {gid}: {slug} vs {winner} only {overlap:.0%} overlap "
                    "(below threshold — no longer the same tenant, or a transient miss; "
                    "left untouched)",
                    flush=True,
                )
                continue
            print(
                f"  {gid}: keep {winner} ({len(a)} ids), bury {slug} ({len(b)} ids, "
                f"{overlap:.0%} overlap)",
                flush=True,
            )
            to_bury.append((winner, slug))

    print(f"\n{len(to_bury)} tenant(s) to bury as duplicates", flush=True)
    if not args.apply:
        print("dry run — pass --apply to write the ledger")
        return 0

    today = datetime.now(UTC).date().isoformat()
    for _winner, loser in to_bury:
        row = ledger[loser]
        ledger[loser] = liveness.Verdict(
            row.ats, row.tenant, row.url, liveness.DEAD, None, today
        )
    liveness.write(LEDGER, ledger.values())
    print(f"wrote {LEDGER}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
