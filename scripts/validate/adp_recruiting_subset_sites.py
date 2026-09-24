#!/usr/bin/env python3
"""Write ADP Recruiting Management's alias ledger: career sites another site already lists (ADR-0202).

An ADP Recruiting Management Board is one career site, `myjobs.adp.com/{slug}/cx`, and one client
(`orgoid`) can run several. Their postings share one client-wide `reqId`, and some sites list
exactly what a sibling does: `gnc` and `generalnutritioncenter` list the same 751 postings
(2026-09-24).
`index_plan.evict_duplicate` groups only within a Board, so every such posting would be served
once per site. `dedupe_boards.py` cannot see it, because no site redirects to another.

The signal is containment, as in Taleo Enterprise's `subset-reqs` (ADR-0186). A site whose full
posting set is non-empty and contained in the set of another site of the same client is buried
onto a maximal site. The election is `board_aliases.bury_contained`. A site whose walk fails, or
reads fewer unique postings than the count it states (even by one, which the scraper itself
would tolerate), is left out, so it is neither buried nor kept for anything else.

Reads every `live` row of the liveness ledger, including the sites the last run buried (the alias
ledger leaves their liveness rows in place), so each run re-derives every verdict. It also
replaces the alias file, so re-run it after every refresh of
`data/validate/liveness/adp_recruiting.csv`.

    PYTHONPATH=src python scripts/validate/adp_recruiting_subset_sites.py
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from headstart import board_aliases, http, liveness
from headstart.config import EXCLUDED_BOARDS
from headstart.scrapers.adp_recruiting import ADPRecruitingScraper

ATS = "adp_recruiting"
SIGNAL = "subset-reqs"
#: Sites read at once. No rate limit was found up to 128-wide (ADR-0202), and this is the width
#: the census and dump ran clean at.
_WORKERS = 16


class _ShortWalk(Exception):
    """The walk read fewer unique postings than the site states, so its set is not the site's."""


def _site(slug: str) -> tuple[str, set[str]]:
    """``(orgoid, every reqId)`` for one site, through the scraper's own listing walk."""
    record, rows, stated = ADPRecruitingScraper(slug).read_site()
    ids = {row["reqId"] for row in rows}
    if len(ids) != stated:
        raise _ShortWalk(f"read {len(ids)} of {stated}")
    return record["orgoid"], ids


def main() -> None:
    liveness_dir = liveness.dir_for(ROOT)
    live = {
        ADPRecruitingScraper.slug_from(v.tenant, v.url)
        for v in liveness.load(liveness_dir / f"{ATS}.csv").values()
        if v.status == liveness.LIVE
    }
    sites = sorted(s for s in live if f"{ATS}:{s}".lower() not in EXCLUDED_BOARDS)
    print(f"{len(sites)} sites to read (live rows, less EXCLUDED_BOARDS)", flush=True)
    ids_by_site: dict[str, set[str]] = {}
    client_of: dict[str, str] = {}
    with ThreadPoolExecutor(_WORKERS) as pool:
        futures = {pool.submit(_site, s): s for s in sites}
        for n, future in enumerate(as_completed(futures), 1):
            site = futures[future]
            try:
                client_of[site], ids_by_site[site] = future.result()
            except (http.RequestsError, ValueError, KeyError, _ShortWalk) as exc:
                print(f"  [{n}/{len(sites)}] {site}: unreadable ({exc})", flush=True)
                continue
            print(
                f"  [{n}/{len(sites)}] {site}: {len(ids_by_site[site])} reqs",
                flush=True,
            )
    today = datetime.now(UTC).date().isoformat()
    aliases = [
        board_aliases.Alias(ATS, dup, keep, SIGNAL, keep, today)
        for dup, keep in sorted(
            board_aliases.bury_contained(ids_by_site, client_of.__getitem__).items()
        )
    ]
    board_aliases.write(board_aliases.path_for(liveness_dir, ATS), aliases)
    for a in aliases:
        print(f"  bury {a.duplicate} -> {a.canonical}", flush=True)
    print(
        f"read {len(ids_by_site)} of {len(sites)} sites; buried {len(aliases)} onto "
        f"{len({a.canonical for a in aliases})} kept sites",
        flush=True,
    )


if __name__ == "__main__":
    main()
