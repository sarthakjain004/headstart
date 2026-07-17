"""Prune planning for the search index: dead-Board sweep + case-variant dedup (ADR-0023).

The board-scoped incremental sync (:mod:`index_sync`) only evicts within Boards scraped this run, so
it can't reach rows on Boards that left the scrape list, nor case-variant duplicates of one job
(Workday sites like ``.../External`` vs ``.../external``). These pure planners compute what to drop;
``scripts/embed/prune_index.py`` executes it against a LanceDB table.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from headstart.config import load_active_companies
from headstart.corpus import board_of
from headstart.scrapers.registry import get_scraper


def live_keep_set(ledger_dir: str | Path) -> set[str]:
    """Canonical (lowercased) Board keys that should survive: every live ledger Board on an enabled
    ATS, in the index's ``board_of`` key space (via ``board_key()``). ``load_active_companies``
    already drops dead Boards and ``DISABLED_ATS``; ``min_jobs=0`` keeps currently-empty live Boards."""
    keep: set[str] = set()
    for company in load_active_companies(ledger_dir, min_jobs=0):
        try:
            keep.add(
                get_scraper(company.ats, company.slug, company.name).board_key().lower()
            )
        except Exception:  # noqa: BLE001 - a malformed ledger row shouldn't sink the whole set
            continue
    return keep


def plan_prune(index_ids: Iterable[str], keep: set[str]) -> tuple[list[str], list[str]]:
    """Split index ids into ``(evict_off_board, evict_duplicate)``.

    ``evict_off_board``: Board not in ``keep`` (dead / dropped from the ledger / disabled ATS).
    ``evict_duplicate``: among the survivors, every id but the lexicographically-smallest per
    ``(lowercased Board, native id)`` group — the case-variant dupes of one job (the same
    representative :func:`headstart.config._dedupe_boards` keeps, so a future scrape re-sees the kept
    row instead of re-embedding it)."""
    off_board: list[str] = []
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for jid in index_ids:
        canon = board_of(jid).lower()
        if canon not in keep:
            off_board.append(jid)
            continue
        native = jid.rsplit(":", 1)[1]
        groups[(canon, native)].append(jid)
    duplicate: list[str] = []
    for ids in groups.values():
        if len(ids) > 1:
            duplicate.extend(
                sorted(ids)[1:]
            )  # keep the lex-min Board casing, drop the rest
    return off_board, duplicate
