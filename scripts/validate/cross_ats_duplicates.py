#!/usr/bin/env python3
"""Find Boards that two different ATSes both claim — the duplicate class `prune` cannot see.

`index_plan.evict_duplicate` groups by ``(lowercased Board, native id)``, i.e. *within* one
Board. So a company reachable on two providers at once — a Phenom career site in front of a
Workday requisition store, a SuccessFactors tenant that migrated to Phenom — is two Boards with
two native ids, and every posting it holds is served twice with nothing to collapse it. CLAUDE.md
§"Checking a liveness ledger for duplicate boards" names three *intra*-ATS mechanisms; this is the
fourth, and it crosses ATSes, so none of those diagnostics reach it.

**Join on the registrable domain, not on an apply link.** The first Phenom gate resolved each
tenant's backing board from the listing's ``applyUrl`` and missed every SuccessFactors-backed
tenant, because SF mostly states no ``applyUrl`` at all — four collisions reached a committed
ledger. ``jobs.kuehne-nagel.com`` and ``careers.kuehne-nagel.com`` share ``kuehne-nagel.com``;
that is the key that finds them.

**A collision is a question, not a verdict.** Of the four this found on Phenom, exactly one was a
real duplicate. The others were an internal-mobility board (4 of 120 titles shared with the
external one), a niche sub-board (0 of 115), and — the case worth remembering — a tenant that had
*migrated*: ``careers.ucb.com`` is in both the successfactors and phenom ledgers, and the
SuccessFactors **scraper** reads 0 jobs there while its **prober** still reports 311, because the
prober counts sitemap entries the scraper's own job-path regex cannot parse. Acting on the name
alone would have dropped 311 live postings. So this script reports; it never edits a ledger.

Usage:
    python scripts/validate/cross_ats_duplicates.py                 # every ATS against every other
    python scripts/validate/cross_ats_duplicates.py phenom          # one ATS against the rest
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGERS = ROOT / "data" / "validate" / "liveness"

#: Two-label public suffixes present in these ledgers. Not a full PSL: the cost of missing one is
#: a pair this does not surface, which is the same failure as not running it at all — whereas
#: pulling in a PSL dependency for a reporting script is not worth it.
_TWO_LABEL = frozenset({"co", "com", "org", "net", "gov", "ac", "edu"})


def registrable(host_or_url: str | None) -> str:
    """The registrable domain of a ledger row's tenant or url, or "" when it has none.

    A slug that is not a host at all (greenhouse's ``razorpaysoftwareprivatelimited``, lever's
    ``dreamsports``) has no domain and returns "" — those Boards are invisible to this join, which
    is the method's known blind spot and is stated in the report rather than hidden.
    """
    host = (host_or_url or "").strip().lower()
    host = host.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2:
        return ""
    if len(parts) >= 3 and parts[-2] in _TWO_LABEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def live_rows() -> dict[str, list[tuple[str, str, str]]]:
    """``{registrable domain: [(ats, tenant, jobs), …]}`` over every committed ledger's live rows."""
    by_domain: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for path in sorted(LEDGERS.glob("*.csv")):
        ats = path.stem
        with path.open(encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh):
                if (row.get("status") or "").strip() != "live":
                    continue
                # Both columns: discovery writes the host into whichever one it had.
                for field in (row.get("tenant"), row.get("url")):
                    domain = registrable(field)
                    if domain:
                        by_domain[domain].append(
                            (
                                ats,
                                (row.get("tenant") or "").strip(),
                                row.get("jobs") or "",
                            )
                        )
                        break
    return by_domain


def main(argv: list[str]) -> int:
    only = argv[1] if len(argv) > 1 else None
    by_domain = live_rows()
    hits = 0
    for domain, rows in sorted(by_domain.items()):
        atses = {ats for ats, _, _ in rows}
        if len(atses) < 2:
            continue
        if only and only not in atses:
            continue
        hits += 1
        print(f"\n{domain}", flush=True)
        for ats, tenant, jobs in sorted(rows):
            print(f"    {ats:18} {tenant:44} jobs={jobs or '-'}", flush=True)
    scope = f" involving {only}" if only else ""
    print(f"\n{hits} domain(s){scope} claimed by more than one ATS.", flush=True)
    print(
        "A collision is a question. Before dropping either row, scrape both and compare: the\n"
        "overlap may be ~0 (different boards), or one side may read 0 jobs (a migration, where\n"
        "the ledger that still says `live` is the stale one).",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
