#!/usr/bin/env python3
"""Write iCIMS's alias ledger: portals that redirect to, or list only what, another portal lists.

An iCIMS Board is one portal host, `{portal}-{customer}.icims.com`, and a customer often runs
several. iCIMS job ids belong to the customer, so every portal lists a shared posting under the same
id and `index_plan.evict_duplicate`, which groups only within a Board, serves it once per portal.
Two shapes of the same fact (ADR-0254):

- **redirect** (ADR-0222): the portal's `/sitemap.xml` redirects to another Live portal, so the
  scraper reads that portal's list under this host. Buried onto the redirect's target.
- **subset-reqs**: the portal answers its own sitemap, but every posting it lists another portal
  of the same customer already lists. `careers-redlobster` and `hourly-spanish-redlobster` list the
  same 2,399 postings with no redirect (2026-09-28). The election is `alias_ledger.bury_contained`,
  as for Taleo Enterprise sections (ADR-0186) and ADP career sites (ADR-0202).

A posting is its `(job id, title slug)` pair off the sitemap URL `/jobs/{id}/{slug}/job`, and a
portal's customer is its host label's last hyphen-separated word (`redlobster`). The customer key
is a heuristic, so the title slug is what makes a match evidence: two customers sharing a last word
would also need to list the same ids under the same titles.

Reads every `live` row of the liveness ledger, including the portals the last run buried, and
replaces the alias file, so each run re-derives every verdict. Re-run it after every refresh of
`data/validate/liveness/icims.csv`. It replaces `dedupe_boards.py --ats icims --apply`, which now
refuses this file because it holds rows it did not write.

    PYTHONPATH=src python scripts/validate/icims_subset_portals.py
"""

from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from headstart.boards import alias_ledger, liveness_ledger
from headstart.boards.excluded_and_parked import EXCLUDED_BOARDS
from headstart.network import http
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.icims import ICIMSScraper, sitemap_rows

ATS = "icims"
#: Portals read at once: the width `probe_icims.py` reads the same sitemaps at.
_WORKERS = 8
_JOB_SLUG = re.compile(r"/jobs/(\d+)/([^/]+)/job")


def customer_of(portal: str) -> str:
    """``careers-redlobster.icims.com`` -> ``redlobster``."""
    return portal.split(".", 1)[0].rsplit("-", 1)[-1]


def burials(
    landed_on: dict[str, str], postings: dict[str, frozenset[tuple[str, str]]]
) -> dict[str, tuple[str, str]]:
    """``{buried portal: (kept portal, signal)}`` over the portals that were read.

    A portal whose sitemap landed on another portal that was read is a ``redirect``; the rest are
    compared by containment within one customer (``subset-reqs``). A redirecting portal lists its
    target's set, so it is kept out of the comparison, where it would only mirror its target."""
    redirects = {
        portal: target
        for portal, target in landed_on.items()
        if target != portal and target in postings
    }
    contained = alias_ledger.bury_contained(
        {p: ids for p, ids in postings.items() if p not in redirects}, customer_of
    )
    return {
        **{dup: (keep, "subset-reqs") for dup, keep in contained.items()},
        # onto the Board that survives, when the target is itself buried by containment
        **{
            dup: (contained.get(keep, keep), "redirect")
            for dup, keep in redirects.items()
        },
    }


def _portal(host: str) -> tuple[str, frozenset[tuple[str, str]]]:
    """``(the host its sitemap landed on, every (id, title slug) it lists)``."""
    response = http.fetch(
        "GET",
        ICIMSScraper(host).url(),
        headers={"User-Agent": USER_AGENT},
        timeout=60,
        allow_redirects=True,
    )
    response.raise_for_status()
    landed = (urlsplit(str(response.url)).hostname or host).lower()
    postings = set()
    for _job_id, url, _lastmod in sitemap_rows(response.text):
        match = _JOB_SLUG.search(url)
        if match:
            postings.add((match.group(1), match.group(2).lower()))
    return landed, frozenset(postings)


def main() -> None:
    liveness_dir = liveness_ledger.dir_for(ROOT)
    portals = sorted(
        {
            ICIMSScraper.slug_from(v.tenant, v.url)
            for v in liveness_ledger.load(liveness_dir / f"{ATS}.csv").values()
            if v.status == liveness_ledger.LIVE
        }
        - {key.split(":", 1)[1] for key in EXCLUDED_BOARDS if key.startswith(f"{ATS}:")}
    )
    print(
        f"{len(portals)} portals to read (live rows, less EXCLUDED_BOARDS)", flush=True
    )
    landed_on: dict[str, str] = {}
    postings: dict[str, frozenset[tuple[str, str]]] = {}
    with ThreadPoolExecutor(_WORKERS) as pool:
        futures = {pool.submit(_portal, p): p for p in portals}
        for n, future in enumerate(as_completed(futures), 1):
            portal = futures[future]
            try:
                landed_on[portal], postings[portal] = future.result()
            except (http.RequestsError, ValueError) as exc:
                print(
                    f"  [{n}/{len(portals)}] {portal}: unreadable ({exc})", flush=True
                )
                continue
            if n % 200 == 0:
                print(f"  [{n}/{len(portals)}] read", flush=True)
    today = datetime.now(UTC).date().isoformat()
    aliases = [
        alias_ledger.Alias(ATS, dup, keep, signal, keep, today)
        for dup, (keep, signal) in sorted(burials(landed_on, postings).items())
    ]
    alias_ledger.write(alias_ledger.path_for(liveness_dir, ATS), aliases)
    for a in aliases:
        print(f"  bury {a.duplicate} -> {a.canonical} ({a.signal})", flush=True)
    print(
        f"read {len(postings)} of {len(portals)} portals; buried "
        f"{sum(a.signal == 'redirect' for a in aliases)} by redirect and "
        f"{sum(a.signal == 'subset-reqs' for a in aliases)} by subset-reqs",
        flush=True,
    )


if __name__ == "__main__":
    main()
