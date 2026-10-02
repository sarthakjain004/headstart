#!/usr/bin/env python3
"""Write Taleo Enterprise's alias ledger: career sections another section already lists (ADR-0186).

A Taleo Enterprise Board is one career section, `{zone}.taleo.net/careersection/{section}`, and a
tenant's sections serve some or all of the tenant's requisitions under one tenant-wide req id. The
tenant is the section URL's host; a twin host is a second host of one Taleo customer (the last rule
below).
`index_plan.evict_duplicate` groups only within a Board, so a req listed on 15 sections is served
15 times. `dedupe_boards.py` cannot see it: no section redirects to another.

The signal is containment. A section whose full requisition set — every role, not only tech — is
non-empty and contained in the set of another section of the same tenant (the section URL's host)
is buried onto a maximal section, which lists every req the buried one does, so no req is lost and
the kept section's own job URLs are the ones served. The rules, all in `burials` (which delegates to
`alias_ledger.bury_contained`):

- **Chains collapse to the top.** A ⊂ B ⊂ C buries A and B onto C.
- **Mirrors keep one**, the lowest section URL, so the same sets always elect the same section.
- **A section under several maximal sections** goes to the largest, then the lowest URL.
- **Two maximal sections that overlap both stay.** Burying either would hide the reqs only it lists.
- **An empty section is never buried.** The empty set is a subset of everything, so it is no
  evidence, and an empty section can post a req nobody else lists tomorrow.
- **An unreadable section is never buried**, and nothing is buried onto it.
- **A non-public section is never the kept one** (`index_plan.is_non_public`: `internal`,
  `confidential`, ...), so it cannot win on one extra req at read time and serve employee-only
  links (MOL Group's `internal`, Hyatt's `wallstreet_internal`, #794). The public sections elect
  among themselves; a non-public section is then buried onto the largest kept public section
  that lists all its reqs, and left unburied when none does (ADR-0186's amendment).
- **A twin host's section goes to its linked host** (ADR-0307, #888). One Taleo customer can be
  served under two hosts with every section and req id the same (`pruitthealthcareers.taleo.net`
  serves `pruitthealth.taleo.net`'s), which a per-host comparison never sees. `TWIN_HOSTS` names
  each twin host and its linked host, the one the company's own careers site links to. After the
  per-host election, a twin section is buried onto the linked host's section at the same path,
  whatever the two walks read: the same path is the same section, and a walk of it can come back
  short. A twin section with no such row that the per-host election left unburied is buried onto
  the largest kept public section of the linked host that lists all its reqs. The twin sections
  buried onto either follow it. Nothing of the linked host is ever buried onto its twin.

Reads every `live` row of the liveness ledger, including the sections the last run buried (the alias
ledger leaves their liveness rows in place), so each run re-derives every verdict and a buried
section that has since gained a req of its own comes back. `excluded_and_parked.EXCLUDED_BOARDS` is skipped
(`scrapable_boards.is_excluded`). One listing walk per section, 16 sections at a time. Replaces the
alias file, so re-run it after every refresh of `data/validate/liveness/taleo_enterprise.csv`.

    PYTHONPATH=src python scripts/validate/taleo_enterprise_subset_sections.py
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Collection, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from headstart.boards import alias_ledger, liveness_ledger, scrapable_boards
from headstart.ingest.index_plan import is_non_public
from headstart.network import http
from headstart.scrapers.taleo_enterprise import TaleoEnterpriseScraper

ATS = "taleo_enterprise"
SIGNAL = "subset-reqs"
#: ``{twin host: linked host}``: one Taleo customer served under two hosts, every section and req id
#: the same on both (#888, measured 2026-09-29; ADR-0307 has each pair's evidence). The linked host
#: is the one the company's own careers site links to, else the lower name. Only sections on live
#: ledger rows are compared, so a twin section the linked host has no row for
#: (`percepta.taleo.net/careersection/10000`) is still scraped from the twin.
TWIN_HOSTS = {
    # Seven pairs a DNS-sieve landing of 2026-09-29 exposed (ADR-0307's amendment of 2026-09-30 has their
    # evidence), each read through the scraper's own listing walk: the
    # two sections list the same ids, and the same `contestNo` and title on every shared id (46 of 46, 8 of 8,
    # 100 of 100, 6 of 6, 104 of 104, 2 of 2, 697 of 697). The linked host is `vontier` (the Phenom front
    # careers.vontier.com applies through vontier.taleo.net/careersection/external/jobapply.ftl), `mlgw` (mlgw.com
    # links mlgw.taleo.net/careersection/ext), `ttec` (ADR-0307) and `tgh` (tgh.org/careers links
    # tgh.taleo.net/careersection/ex); for `westpac`, `golder` and `atkcareers` no careers page was read linking
    # either host, so the lower name stands.
    "aa246.taleo.net": "vontier.taleo.net",
    "aa333.taleo.net": "mlgw.taleo.net",
    "kearney.taleo.net": "atkcareers.taleo.net",
    "tas-tgh.taleo.net": "tgh.taleo.net",
    "teletech.taleo.net": "ttec.taleo.net",
    "westpacnz.taleo.net": "westpac.taleo.net",
    "wsp.taleo.net": "golder.taleo.net",
    "careerglobalhc.taleo.net": "hyundaicapital.taleo.net",
    "daimler.taleo.net": "tas-daimler.taleo.net",
    "elsewedyelectric.taleo.net": "aa010.taleo.net",
    "gb-corporation.taleo.net": "ghabbour.taleo.net",
    "manpower.taleo.net": "manpowergroup.taleo.net",
    "ouhk.taleo.net": "hkmu.taleo.net",
    "percepta.taleo.net": "ttec.taleo.net",
    "pruitthealthcareers.taleo.net": "pruitthealth.taleo.net",
}
#: Sections read at once. Each walks its own pages one after another, so this is also the most
#: requests in flight — the scraper's own detail width, measured clean at 16 (2026-09-13).
_WORKERS = 16


def burials(reqs_by_section: Mapping[str, Collection[str]]) -> dict[str, str]:
    """``{buried section: kept section}`` for every section another one of its tenant contains, and
    every twin host's section its linked host lists.

    ``reqs_by_section`` maps a section's canonical URL to its full requisition ids. Within a host
    the election is `alias_ledger.bury_contained_keeping_public`: ADR-0186's, shared with ADP
    Recruiting Management's (ADR-0202) and iCIMS's, where a non-public section is never the kept
    one. Then each twin host's sections go to its linked host (module docstring)."""

    def host(section: str) -> str:
        return urlsplit(section).hostname

    def non_public(section: str) -> bool:
        return is_non_public(TaleoEnterpriseScraper(section).board_key().lower())

    buried = alias_ledger.bury_contained_keeping_public(
        reqs_by_section, host, non_public
    )
    linked_public_kept = {
        s: frozenset(reqs)
        for s, reqs in reqs_by_section.items()
        if reqs
        and s not in buried
        and host(s) in TWIN_HOSTS.values()
        and not non_public(s)
    }
    for section, reqs in reqs_by_section.items():
        linked = TWIN_HOSTS.get(host(section))
        if linked is None:
            continue
        same_path = urlunsplit(urlsplit(section)._replace(netloc=linked))
        if same_path in reqs_by_section:
            buried[section] = same_path
        elif reqs and section not in buried:
            onto = alias_ledger.largest_containing(
                reqs,
                {s: own for s, own in linked_public_kept.items() if host(s) == linked},
            )
            if onto is not None:
                buried[section] = onto

    def survivor(section: str) -> str:
        # Every link leads from a twin host to its linked host or within one host, so this ends.
        while section in buried:
            section = buried[section]
        return section

    return {dup: survivor(keep) for dup, keep in buried.items()}


def write_aliases(
    liveness_dir: Path,
    reqs_of: Callable[[str], Collection[str]],
    checked_at: str,
) -> list[alias_ledger.Alias]:
    """Read the section of every live, non-excluded row through ``reqs_of``, bury the subsets,
    and replace the alias ledger beside ``liveness_dir`` with the result. A section whose read fails
    (a request error, or a page the listing cannot parse) is left out, so it is neither buried nor
    kept for anything else; any other exception is a bug and propagates."""
    live = {
        TaleoEnterpriseScraper.slug_from(v.tenant, v.url)
        for v in liveness_ledger.load(liveness_dir / f"{ATS}.csv").values()
        if v.status == liveness_ledger.LIVE
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
        alias_ledger.Alias(ATS, dup, keep, SIGNAL, keep, checked_at)
        for dup, keep in sorted(burials(reqs_by_section).items())
    ]
    alias_ledger.write(alias_ledger.path_for(liveness_dir, ATS), aliases)
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
    for a in write_aliases(liveness_ledger.dir_for(ROOT), _reqs, today):
        print(f"  bury {a.duplicate} -> {a.canonical}", flush=True)


if __name__ == "__main__":
    main()
