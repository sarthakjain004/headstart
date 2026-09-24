#!/usr/bin/env python3
"""Write Eightfold's alias ledger: career sites whose backing ATS Board already serves them (ADR-0205).

An Eightfold career site is often a front over the company's real ATS — NVIDIA's `jobs.nvidia.com`
lists the requisitions of its Workday site, Arcadis's lists its Oracle ones — and both Boards are
scraped, so each posting is served twice under two ATS labels. The two share no native id, and
`index_plan.evict_duplicate` groups within one Board, so nothing else can see the pair.

The signal is the requisition id Eightfold states for every posting (`atsJobId`, and
`displayJobId` where Oracle keys on it), matched against the id each backing Board's own listing
walk reads — the walk the pipeline scrapes, never a targeted search, because what the walk misses
is not served. An Eightfold Board is buried onto its backing Board(s), with signal `backing-reqs`,
when, in `aliases`:

- **Every tech posting has a backing copy the tech gate keeps.** Listed is not served: the backing
  copy carries its own department and `tech_filter` reads it, so a tech posting whose backing copy
  is non-tech would leave search. No tolerance.
- **At most 1% of all its postings have no backing copy at all**, and none of those is tech. The
  two listings are read minutes apart and postings propagate between them, so a strict bar flaps.
- **Every backing Board is Scrapable** and was read; the Eightfold listing was read whole and is
  not empty.
- **A second Eightfold site of one company follows its winner.** `nvidia.eightfold.ai` serves
  `jobs.nvidia.com`'s postings; it is decided after the winner and buried onto the winner's
  backing Board when the winner is itself buried, else onto the winner. These are
  `check_liveness`'s hand-frozen `_EIGHTFOLD_ALIAS_LOSERS`, which stays beside them (ADR-0205).

The candidates are `BACKING`, the committed pairs file `data/validate/eightfold_backing.csv`
(`headstart.eightfold_backing`, ADR-0210), found by content on served index v654 (2026-09-23):
pairs of Boards on two ATSes sharing exact descriptions. A new front enters by adding a row there.
Lumen is left out by the user's decision (its backing site is an internal careers site), and so is
International SOS (postings of its own). Every verdict is re-derived live on each run, including
for the Boards the last run buried, so a Board whose backing Board drops out comes back when the
script next runs.

Reads each candidate and backing Board once, every read sequential within its Board and 16 Boards
at a time, so at most 16 requests are in flight. Replaces the alias file, so re-run it after every
refresh of the liveness ledger of eightfold or of any ATS in `BACKING`.

    PYTHONPATH=src python scripts/validate/eightfold_backing_boards.py
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from headstart import (
    board_aliases,
    eightfold_backing,
    http,
    liveness,
    scrapable_boards,
)
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.eightfold import EightfoldScraper, _department_of
from headstart.scrapers.greenhouse import GreenhouseScraper
from headstart.scrapers.oracle import OracleScraper
from headstart.scrapers.registry import company_from_row
from headstart.scrapers.successfactors import (
    SuccessFactorsScraper,
    _job_urls_from,
    _titled_fields,
)
from headstart.scrapers.taleo_enterprise import TaleoEnterpriseScraper
from headstart.scrapers.workday import (
    _FIXED_FACETS_BY_SLUG,
    WorkdayScraper,
    _posting_key,
)
from headstart.tech_filter import is_tech

ATS = "eightfold"
SIGNAL = "backing-reqs"
#: The most of a Board's postings its backing Boards may not list, all of them non-tech. Measured
#: 2026-09-24: 14 of 35 candidates were short by 1 to 32 postings (at most 1% of each), and
#: micron's shortfall read 2, 20 and 0 on three reads that day.
RESIDUAL_SHARE = 0.01
#: Boards read at once. Every read below is sequential within its Board, so this is also the
#: most requests in flight.
_WORKERS = 16

#: Eightfold Board -> the Board keys (lowercased `board_key`) that list its postings: the
#: committed pairs file, which `index sync`/`prune` read too (ADR-0210).
BACKING: dict[str, tuple[str, ...]] = eightfold_backing.load()


@dataclass(frozen=True, slots=True)
class Posting:
    """One listed posting: the requisition ids it is known by, and the tech gate's verdict on the
    fields its own Board's listing carries."""

    keys: frozenset[str]
    tech: bool


def aliases(
    backing: Mapping[str, Sequence[str]],
    listings: Mapping[str, Sequence[Posting] | None],
    eligible: Collection[str],
    on_refused: Callable[[str, str], None] = lambda board, why: None,
) -> dict[str, tuple[str, ...]]:
    """``{buried Board: the Boards it is buried onto}``, keys as in ``backing``.

    ``listings`` maps each Board to its postings, or None where it could not be read. Pure, and
    deterministic in its input. ``on_refused`` hears why each Board that is not buried was not."""
    out: dict[str, tuple[str, ...]] = {}
    pending = dict(backing)
    while pending:
        # A Board backed by another candidate waits for it, then follows it if it was buried.
        ready = sorted(b for b, ps in pending.items() if not set(ps) & set(pending))
        if not ready:
            for board in sorted(pending):
                on_refused(board, "its backing Boards form a cycle")
            break
        for board in ready:
            partners = pending.pop(board)
            final = tuple(dict.fromkeys(f for p in partners for f in out.get(p, (p,))))
            why = _refusal(board, final, listings, eligible)
            if why is None:
                out[board] = final
            else:
                on_refused(board, why)
    return out


def _refusal(
    board: str,
    partners: Sequence[str],
    listings: Mapping[str, Sequence[Posting] | None],
    eligible: Collection[str],
) -> str | None:
    """Why ``board`` is not buried onto ``partners``, or None when it is."""
    if missing := [p for p in partners if p not in eligible]:
        return f"not Scrapable: {', '.join(missing)}"
    if unread := [p for p in partners if listings.get(p) is None]:
        return f"unread: {', '.join(unread)}"
    posts = listings.get(board)
    if not posts:
        return "its own listing is unread or empty"
    held = {k for p in partners for post in listings[p] for k in post.keys}
    served = {
        k for p in partners for post in listings[p] if post.tech for k in post.keys
    }
    unserved = sum(1 for post in posts if post.tech and not post.keys & served)
    if unserved:
        return f"{unserved} tech posting(s) of {len(posts)} have no backing copy the tech gate keeps"
    residual = sum(1 for post in posts if not post.keys & held)
    if residual > RESIDUAL_SHARE * len(posts):
        return f"{residual} of {len(posts)} postings are on no backing Board"
    return None


def write_aliases(
    liveness_dir: Path,
    read: Callable[[str, str], Sequence[Posting] | None],
    checked_at: str,
    backing: Mapping[str, Sequence[str]] = BACKING,
) -> list[board_aliases.Alias]:
    """Read every Board ``backing`` names through ``read(ats, slug)``, bury what ``aliases``
    elects, and replace the alias ledger beside ``liveness_dir``. A read that fails returns None
    and earns no verdict."""
    keyed = {f"{ATS}:{b}": tuple(ps) for b, ps in backing.items()}
    companies = {
        b.lowercase_identity: b for b in scrapable_boards.load(liveness_dir, min_jobs=0)
    }
    # A backing Board must be Scrapable; the ledger this run replaces does not count against it.
    eligible = set(companies) | _buried_by(liveness_dir)
    boards = sorted(set(keyed) | {p for ps in keyed.values() for p in ps})
    where = {}
    for key in boards:
        ats, slug = key.split(":", 1)
        if ats == ATS:
            # read even on a dead row: the former losers are dead
            where[key] = (ATS, slug)
        elif key in companies:
            where[key] = (ats, companies[key].slug)
    print(f"{len(where)} of {len(boards)} Boards to read", flush=True)
    listings: dict[str, Sequence[Posting] | None] = {}
    with ThreadPoolExecutor(_WORKERS) as pool:
        futures = {pool.submit(read, *where[k]): k for k in where}
        for n, future in enumerate(as_completed(futures), 1):
            key = futures[future]
            listings[key] = posts = future.result()
            said = "unread" if posts is None else f"{len(posts)} postings"
            print(f"  [{n}/{len(futures)}] {key}: {said}", flush=True)
    buried = aliases(
        keyed,
        listings,
        eligible,
        lambda board, why: print(f"  keep {board}: {why}", flush=True),
    )
    rows = [
        board_aliases.Alias(ATS, board.split(":", 1)[1], p, SIGNAL, p, checked_at)
        for board, partners in sorted(buried.items())
        for p in partners
    ]
    board_aliases.write(board_aliases.path_for(liveness_dir, ATS), rows)
    print(f"buried {len(buried)} of {len(keyed)} Eightfold Boards", flush=True)
    return rows


def _buried_by(liveness_dir: Path) -> set[str]:
    """Eightfold Boards on live rows that the ledger this run replaces buries. Only that ledger
    keeps them off the Scrapable list, so a winner the last run buried must not count against its
    own second site."""
    previous = board_aliases.load_for(liveness_dir, ATS)
    return {
        f"{ATS}:{v.tenant}".lower()
        for v in liveness.load(liveness_dir / f"{ATS}.csv").values()
        if v.status == liveness.LIVE
        and company_from_row(ATS, v.tenant, v.url).slug.lower() in previous
    }


def read_board(ats: str, slug: str) -> list[Posting] | None:
    """One Board's postings through its scraper's own listing walk, or None when unreadable."""
    try:
        return _READERS[ats](slug)
    except (http.RequestsError, ValueError, RuntimeError) as exc:
        print(f"  {ats}:{slug}: unreadable ({exc})", flush=True)
        return None


def _eightfold(host: str) -> list[Posting] | None:
    """The PCSX (or SmartApply) listing, whole or not at all: an Eightfold posting nobody read
    cannot be checked against anything."""
    scraper = EightfoldScraper(host)
    group = scraper._group_id()
    positions = scraper._api_search(group) if group else None
    if positions is None or scraper.truncated:
        return None
    return [
        Posting(
            frozenset(str(k) for k in (p.get("atsJobId"), p.get("displayJobId")) if k),
            is_tech(p.get("name"), _department_of(p)),
        )
        for p in positions
    ]


def _workday(slug: str) -> list[Posting]:
    scraper = WorkdayScraper(slug)
    scraper._resolve_instance()
    items: list[dict] = []
    scraper._exhaust(_FIXED_FACETS_BY_SLUG.get(scraper.slug, {}), items.extend, depth=0)
    return [
        Posting(
            frozenset({_posting_key(i)}),
            is_tech(i.get("title"), (i.get("jobFamilyGroup") or "").strip() or None),
        )
        for i in items
    ]


def _oracle(slug: str) -> list[Posting]:
    # The listing carries no department (it is detail-only), so the verdict reads the title.
    return [
        Posting(frozenset({str(r["Id"])}), is_tech(r.get("Title"), None))
        for r in OracleScraper(slug)._listing()
        if r.get("Id")
    ]


def _greenhouse(slug: str) -> list[Posting]:
    jobs = GreenhouseScraper(slug).fetch_raw().get("jobs") or []
    return [
        Posting(
            frozenset({str(j.get("internal_job_id"))}),
            is_tech(
                (j.get("title") or "").strip(),
                (j.get("departments") or [{}])[0].get("name") or None,
            ),
        )
        for j in jobs
    ]


def _taleo_enterprise(slug: str) -> list[Posting]:
    scraper = TaleoEnterpriseScraper(slug)
    return [
        Posting(frozenset({r["contest_no"]}), is_tech(r["title"], r["department"]))
        for r in scraper._listing(scraper._get())
        if r.get("contest_no")
    ]


def _successfactors(slug: str) -> list[Posting]:
    """Every RMK job page, one after another: only the page states the requisition id. A page
    that fails is left out, which can only keep an Eightfold Board, never bury one."""
    scraper = SuccessFactorsScraper(slug)
    kind, text, _cut = scraper._fetch_sitemap()
    listed = _job_urls_from(text, scraper.slug) if kind == "urlset" else []
    if not listed:
        listed = scraper._search_job_urls()[0]
    posts = []
    for n, (url, _id) in enumerate(listed, 1):
        if n % 250 == 0:
            print(f"  successfactors:{slug}: {n}/{len(listed)} job pages", flush=True)
        try:
            r = scraper._fetch(
                "GET", url, headers={"User-Agent": USER_AGENT}, timeout=60
            )
        except http.RequestsError:
            continue
        fields = _titled_fields(r.text, url) if r.status_code == 200 else None
        if fields and fields["requisition"]:
            posts.append(
                Posting(
                    frozenset({fields["requisition"]}), is_tech(fields["title"], None)
                )
            )
    return posts


_READERS: dict[str, Callable[[str], list[Posting] | None]] = {
    "eightfold": _eightfold,
    "workday": _workday,
    "oracle": _oracle,
    "greenhouse": _greenhouse,
    "taleo_enterprise": _taleo_enterprise,
    "successfactors": _successfactors,
}


def main() -> None:
    # Sequential within a Board, so `_WORKERS` bounds the requests in flight (ADR-0016's switch).
    os.environ["HEADSTART_ASYNC_FANOUT"] = "0"
    today = datetime.now(UTC).date().isoformat()
    for a in write_aliases(liveness.dir_for(ROOT), read_board, today):
        print(f"  bury {a.duplicate} -> {a.canonical}", flush=True)


if __name__ == "__main__":
    main()
