#!/usr/bin/env python3
"""Write Radancy's alias ledger: career fronts whose every posting another front already lists
(ADR-0265).

A Radancy TalentBrew front is its host (ADR-0246), and one employer often runs a language or
country twin of the same front: `jobs.jabil.cn` lists the same 1,954 postings as `jobs.jabil.com`,
`empleos.greystar.com` 500 of `jobs.greystar.com`'s (2026-09-28). Both twins probe `live`, and
`index_plan.evict_duplicate` groups only within a Board, so each such posting is served once per
twin. This is not the Front
duplication the owner allows (a front over a Backing Board of another ATS): both copies are
Radancy, and CLAUDE.md's Radancy rule holds canonical front hosts only.

The signal is containment, as in Taleo Enterprise's `subset-reqs` (ADR-0186): a front whose
posting ids are all listed by another front is buried onto a maximal one, through
`alias_ledger.bury_contained`. TalentBrew's job id is platform-wide (the twins above serve one
posting under one id on both hosts), so every front is compared with every other. Two fronts
that overlap without either containing the other both stay: burying either would hide the
postings only it lists.

A front is its sitemap's own-host job URLs, read through the scraper's `sitemap_rows`. Reads every
`live` row of the liveness ledger, including the fronts the last run buried, and replaces the
alias file, so re-run it after every refresh of `data/validate/liveness/radancy.csv`.

    PYTHONPATH=src python scripts/validate/radancy_subset_fronts.py
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from headstart.boards import alias_ledger, liveness_ledger
from headstart.boards.excluded_and_parked import EXCLUDED_BOARDS, PARKED_BOARDS
from headstart.network import http
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.radancy import RadancyScraper, sitemap_rows

ATS = "radancy"
SIGNAL = "subset-reqs"
#: Fronts read at once: one sitemap GET each, every front on its own host.
_WORKERS = 8
#: The English front the employer's own site links to, kept over its translated twin and never
#: buried onto it. `bury_contained` keeps the lowest host, which for two twin pairs is the
#: translated one (`empleos.greystar.com`, `jobs.jabil.cn`), and `jobs.mt.com.cn` lists one
#: posting `jobs.mt.com` does not (533 against 532, 2026-09-28), which would bury the English front
#: onto the Chinese one. A twin one posting apart is left unburied instead.
PREFERRED_FRONTS = frozenset({"jobs.greystar.com", "jobs.jabil.com", "jobs.mt.com"})


def _front_ids(host: str) -> set[str]:
    """Every job id the front's sitemap lists on its own host."""
    response = http.fetch(
        "GET",
        RadancyScraper(host).url(),
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    xml = response.content.decode("utf-8-sig", "replace")
    return {job_id for job_id, _url in sitemap_rows(xml, host)}


def burials(ids_by_front: dict[str, set[str]]) -> dict[str, str]:
    """``{buried front: kept front}``: every front compared with every other, a front of
    :data:`PREFERRED_FRONTS` kept over a mirror of it and never buried."""
    buried = alias_ledger.bury_contained(ids_by_front, lambda _host: ATS)
    for front in PREFERRED_FRONTS & buried.keys():
        kept = buried[front]
        if ids_by_front[front] == ids_by_front[kept]:
            buried = {
                dup: (front if keep == kept else keep)
                for dup, keep in buried.items()
                if dup != front
            }
            buried[kept] = front
    return {dup: keep for dup, keep in buried.items() if dup not in PREFERRED_FRONTS}


def main() -> None:
    liveness_dir = liveness_ledger.dir_for(ROOT)
    withheld = {
        key.split(":", 1)[1]
        for key in EXCLUDED_BOARDS | PARKED_BOARDS
        if key.startswith(f"{ATS}:")
    }
    fronts = sorted(
        {
            RadancyScraper.slug_from(v.tenant, v.url)
            for v in liveness_ledger.load(liveness_dir / f"{ATS}.csv").values()
            if v.status == liveness_ledger.LIVE
        }
        - withheld
    )
    print(
        f"{len(fronts)} fronts to read (live rows, less excluded and parked)",
        flush=True,
    )
    ids_by_front: dict[str, set[str]] = {}
    with ThreadPoolExecutor(_WORKERS) as pool:
        futures = {pool.submit(_front_ids, f): f for f in fronts}
        for n, future in enumerate(as_completed(futures), 1):
            front = futures[future]
            try:
                ids_by_front[front] = future.result()
            except (http.RequestsError, ValueError) as exc:
                print(f"  [{n}/{len(fronts)}] {front}: unreadable ({exc})", flush=True)
                continue
            print(
                f"  [{n}/{len(fronts)}] {front}: {len(ids_by_front[front])} ids",
                flush=True,
            )
    today = datetime.now(UTC).date().isoformat()
    aliases = [
        alias_ledger.Alias(ATS, dup, keep, SIGNAL, keep, today)
        for dup, keep in sorted(burials(ids_by_front).items())
    ]
    alias_ledger.write(alias_ledger.path_for(liveness_dir, ATS), aliases)
    for a in aliases:
        print(
            f"  bury {a.duplicate} ({len(ids_by_front[a.duplicate])}) -> {a.canonical} "
            f"({len(ids_by_front[a.canonical])})",
            flush=True,
        )
    print(
        f"read {len(ids_by_front)} of {len(fronts)} fronts; buried {len(aliases)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
