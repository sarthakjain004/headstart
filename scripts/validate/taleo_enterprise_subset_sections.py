#!/usr/bin/env python3
"""Write Taleo Enterprise's alias ledger: career sections another section already lists (ADR-0186).

A Taleo Enterprise Board is one career section, `{zone}.taleo.net/careersection/{section}`, and a
tenant's sections serve some or all of the tenant's requisitions under one tenant-wide req id.
`index_plan.evict_duplicate` groups only within a Board, so a req listed on 15 sections is served
15 times. `dedupe_boards.py` cannot see it: no section redirects to another.

The signal is containment. A section whose full requisition set — every role, not only tech — is
non-empty and contained in the set of another section of the same tenant (the section URL's host)
is buried onto a maximal section, which lists every req the buried one does, so no req is lost and
the kept section's own job URLs are the ones served. The rules, all in `burials`:

- **Chains collapse to the top.** A ⊂ B ⊂ C buries A and B onto C.
- **Mirrors keep one**, the lowest section URL, so the same sets always elect the same section.
- **A section under several maximal sections** goes to the largest, then the lowest URL.
- **Two maximal sections that overlap both stay.** Burying either would hide the reqs only it lists.
- **An empty section is never buried.** The empty set is a subset of everything, so it is no
  evidence, and an empty section can post a req nobody else lists tomorrow.
- **An unreadable section is never buried**, and nothing is buried onto it.

Reads every `live` row of the liveness ledger, including the sections the last run buried (the alias
ledger leaves their liveness rows in place), so each run re-derives every verdict and a buried
section that has since gained a req of its own comes back. `config.EXCLUDED_BOARDS` is skipped
(`scrapable_boards.is_excluded`). One listing walk per section, 16 sections at a time. Replaces the
alias file, so re-run it after every refresh of `data/validate/liveness/taleo_enterprise.csv`.

    PYTHONPATH=src python scripts/validate/taleo_enterprise_subset_sections.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from collections.abc import Callable, Collection, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from headstart import board_aliases, http, liveness, scrapable_boards
from headstart.scrapers.taleo_enterprise import TaleoEnterpriseScraper

ATS = "taleo_enterprise"
SIGNAL = "subset-reqs"
#: Sections read at once. Each walks its own pages one after another, so this is also the most
#: requests in flight — the scraper's own detail width, measured clean at 16 (2026-09-13).
_WORKERS = 16


def burials(reqs_by_section: Mapping[str, Collection[str]]) -> dict[str, str]:
    """``{buried section: kept section}`` for every section another one of its tenant contains.

    ``reqs_by_section`` maps a section's canonical URL to its full requisition ids. Pure, and deterministic in
    its input alone: neither the mapping's order nor a previous run changes the answer."""
    tenants: dict[str, dict[str, frozenset[str]]] = defaultdict(dict)
    for section, ids in reqs_by_section.items():
        if ids:
            tenants[urlsplit(section).hostname][section] = frozenset(ids)
    buried = {}
    for sections in tenants.values():
        kept: dict[frozenset[str], str] = {}  # one elected section per maximal set
        for section in sorted(sections):
            if not any(sections[section] < other for other in sections.values()):
                kept.setdefault(sections[section], section)
        for section, own in sections.items():
            if kept.get(own) != section:
                buried[section] = min(
                    (keep for ids, keep in kept.items() if own <= ids),
                    key=lambda keep: (-len(sections[keep]), keep),
                )
    return buried


def write_aliases(
    liveness_dir: Path,
    reqs_of: Callable[[str], Collection[str]],
    checked_at: str,
) -> list[board_aliases.Alias]:
    """Read the section of every live, non-excluded row through ``reqs_of``, bury the subsets,
    and replace the alias ledger beside ``liveness_dir`` with the result. A section whose read fails
    (a request error, or a page the listing cannot parse) is left out, so it is neither buried nor
    kept for anything else; any other exception is a bug and propagates."""
    live = {
        TaleoEnterpriseScraper.slug_from(v.tenant, v.url)
        for v in liveness.load(liveness_dir / f"{ATS}.csv").values()
        if v.status == liveness.LIVE
    }
    sections = {s for s in live if not scrapable_boards.is_excluded(ATS, s)}
    print(
        f"{len(sections)} sections to read (live rows, less EXCLUDED_BOARDS)",
        flush=True,
    )
    reqs_by_section: dict[str, Collection[str]] = {}
    with ThreadPoolExecutor(_WORKERS) as pool:
        futures = {pool.submit(reqs_of, s): s for s in sorted(sections)}
        for n, future in enumerate(as_completed(futures), 1):
            section = futures[future]
            try:
                reqs_by_section[section] = future.result()
            except (http.RequestsError, ValueError) as exc:  # unreadable: never buried
                print(
                    f"  [{n}/{len(futures)}] {section}: unreadable ({exc})", flush=True
                )
                continue
            print(
                f"  [{n}/{len(futures)}] {section}: {len(reqs_by_section[section])} reqs",
                flush=True,
            )
    aliases = [
        board_aliases.Alias(ATS, dup, keep, SIGNAL, keep, checked_at)
        for dup, keep in sorted(burials(reqs_by_section).items())
    ]
    board_aliases.write(board_aliases.path_for(liveness_dir, ATS), aliases)
    print(
        f"read {len(reqs_by_section)} of {len(sections)} sections; buried {len(aliases)} onto "
        f"{len({a.canonical for a in aliases})} kept sections",
        flush=True,
    )
    return aliases


def _reqs(section: str) -> set[str]:
    """Every requisition id the section lists, through the scraper's own listing walk."""
    scraper = TaleoEnterpriseScraper(section)
    return {row["id"] for row in scraper._listing(scraper._get())}


def main() -> None:
    today = datetime.now(UTC).date().isoformat()
    for a in write_aliases(liveness.dir_for(ROOT), _reqs, today):
        print(f"  bury {a.duplicate} -> {a.canonical}", flush=True)


if __name__ == "__main__":
    main()
