"""Name every Board the Trends tab has counted, grouped into the companies a person would pick.

The Trends tab can be narrowed to one or more companies (ADR-0185). Its counts come from the
ADR-0143 Board-delta ledger, which is keyed by **board_key**, while a person types a company
name, so something has to join the two. This stage writes that join as a static file the Space
serves, `data/state/company_directory.json`:

    {"companies": [{"name": "Lockheed Martin",
                    "boards": ["eightfold:lockheedmartin.eightfold.ai",
                               "successfactors:lockheed.jobs.hr.cloud.sap"]}, ...]}

It runs in the pipeline rather than the Space because the naming rules live in `board_naming`,
and the Space never imports from `ingest`.

## Every Board the ledger has counted, not only the ones hiring now

A company's line is the sum of its Boards' lines. A Board that has since closed its last tech
opening still has history in the ledger, and listing only the Boards hiring today would drop that
history from its company. On 2026-09-24, 1,087 of the ledger's 34,203 Boards had no tech opening
left, 59 of them under a company still hiring. So the directory lists every Board with a tech
`stock` row at the live centroid version, which also lets a user pick a company that has
stopped hiring.

## Names and Boards, no counts

A Board's openings are derivable from the delta ledger the Space already loads, and its ATS is
the prefix of its board_key, so the file carries neither. That keeps one source for counts. It
does not keep the file stable: 258 of the ledger's 284 post-baseline ticks added a Board never
seen before (median 11), so the ~2 MB file (~400 KB gzipped) is rewritten most runs, like the
trends ledger beside it, and `reclaim_storage` collects the superseded copies.

## When two Boards are one company

Two Boards are one company when `board_operator.tenant` names the same account for them on the
same ATS, or when they share a curated alias, and never merely because their names match.
Measured on the 2026-09-24 snapshot of 32,829 Boards with tech openings:

- **One account, several Boards** is structural. Workday splits an account into sites
  (`workday:hpe/ACJobSite`, `workday:hpe/Jobsathpe`: 684 accounts), Taleo Enterprise into career
  sections under one host (HDR's fifteen: 36 hosts), and Taleo Business Edition into `cws` sites
  under one `org` (12 orgs). Case is ignored, which also folds ADR-0023's stale casing
  duplicates (`smartrecruiters:AbhiBus` and `smartrecruiters:abhibus`: 54 pairs).
- **A curated alias** (`board_naming.DISPLAY_ALIASES`) is the one cross-ATS identity anyone
  has asserted, so Lockheed Martin's Eightfold and SuccessFactors Boards are one company.

A matching name is not identity, even a stated one. The first draft of this stage merged cased
names across ATSes and any names within one: it made one "Pearl" of four ATSes' Boards, one
"Arlo" of a New York startup and Netgear's spin-off, and one "Clarity" of `ashby:clarity` and
`ashby:hiive`. A tidied slug collides even more easily: `trakstar:amazon` is one Salesforce-admin
posting, and `workday:google/GOCJobs` is Google Operations Center. So an entry here is a
**Company** only as far as the data proves it. An employer on two ATSes with no alias
(Schonfeld on Greenhouse and SmartRecruiters) appears twice under one name, and the user picks
both. A wrong merge, by contrast, would add another employer to their chart without telling them.

**Grouping is not deduplication.** One company's Boards can list the same requisitions: Taleo
sections serve the account's whole set, Workday sites overlap, and Lockheed's Eightfold Board
mirrors its SuccessFactors one (1,248 of 1,249 distinct titles shared). A sum over an entry's
Boards counts those postings more than once. This file says which Boards belong to a company.
Whether their counts can be added is the index's problem, not this one.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from headstart import log, roles
from headstart.ingest.board_naming import (
    DISPLAY_ALIASES,
    board_names,
    display_name,
    stated_name,
)
from headstart.ingest.board_operator import tenant

# `__spec__` as well as `__name__`, like every other module that doubles as a `python -m`
# entry point: run that way `__name__` is "__main__", outside the root `setup()` configures.
_log = log.get(__name__, __spec__)

REPO_ROOT = Path(__file__).resolve().parents[3]
_BOARD_DELTAS = REPO_ROOT / "data" / "state" / "role_trend_board_deltas"
_OUT = REPO_ROOT / "data" / "state" / "company_directory.json"
_DB = REPO_ROOT / "data" / "lancedb"


def ledger_boards(delta_dir: Path) -> set[str]:
    """Every Board with a tech `stock` delta at the newest tick's centroid version.

    Older versions are skipped because a refit re-bases every series (ADR-0040) and the Space
    charts only the live one. `non-tech` has no series to chart, and `watch:` rows re-count Jobs
    already counted in their family (ADR-0051).
    """
    import pyarrow.parquet as pq

    tables = [
        (pq.read_schema(path).metadata or {}, path)
        for path in sorted(delta_dir.glob("*.parquet"))
    ]
    if not tables:
        return set()
    live = tables[-1][0].get(b"centroid_version")
    boards: set[str] = set()
    for metadata, path in tables:
        if metadata.get(b"centroid_version") != live:
            continue
        table = pq.read_table(path, columns=["board", "metric", "family"]).to_pydict()
        boards.update(
            board
            for board, metric, family in zip(
                table["board"], table["metric"], table["family"], strict=True
            )
            if metric == "stock"
            and family != roles.NON_TECH
            and not family.startswith(roles.WATCH_PREFIX)
        )
    return boards


def companies(boards: set[str], names: dict[str, str]) -> list[dict]:
    """One entry per company, each naming its Boards. See the module docstring for the rule."""
    parent = {board: board for board in boards}

    def root(board: str) -> str:
        while parent[board] != board:
            parent[board] = parent[parent[board]]
            board = parent[board]
        return board

    # A union over two keys, not a grouping by one: RTX's aliased site and its lowercase
    # casing duplicate share only an account, while Lockheed's two Boards share only an alias.
    first_board: dict[str, str] = {}  # key -> the first Board that carried it
    for board in sorted(boards):
        alias = DISPLAY_ALIASES.get(board)
        account = f"{board.split(':', 1)[0]}:{tenant(board).casefold()}"
        for key in (account, f"alias:{alias.casefold()}" if alias else None):
            if key is None:
                continue
            if key in first_board:
                parent[root(board)] = root(first_board[key])
            else:
                first_board[key] = board
    clusters: dict[str, list[str]] = collections.defaultdict(list)
    for board in boards:
        clusters[root(board)].append(board)
    entries = [
        {"name": _name(cluster, names), "boards": sorted(cluster)}
        for cluster in clusters.values()
    ]
    # Sorted so the same Boards always write the same file.
    entries.sort(key=lambda c: (c["name"].casefold(), c["boards"][0]))
    return entries


def _name(cluster: list[str], names: dict[str, str]) -> str:
    """The stated spelling if any Board has one ("NVIDIA", not "Nvidia"), else the tidied slug."""
    ordered = sorted(cluster)
    for board in ordered:
        named = stated_name(names.get(board, ""), board)
        if named:
            return named
    return display_name(names.get(ordered[0], ""), ordered[0])


def main() -> int:
    log.setup()
    log.context("company_directory")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--board-deltas", type=Path, default=_BOARD_DELTAS)
    ap.add_argument("--db", type=Path, default=_DB)
    ap.add_argument("--out", type=Path, default=_OUT)
    args = ap.parse_args()

    # role_trends writes the ledger and is `continue-on-error`, so a run where it never has
    # leaves it absent: a missing prerequisite, not a defect here.
    boards = ledger_boards(args.board_deltas)
    if not boards:
        _log.warning(
            f"skipping the company directory — no Board deltas in {args.board_deltas} "
            "(role_trends writes them; it may have skipped this run)"
        )
        return 0

    from headstart.search import PROD_TABLE

    names = board_names(args.db, PROD_TABLE)
    if not names:
        # Every Board would fall back to its slug and every company would be named worse for
        # a run. Keeping the previous directory is better.
        _log.warning("keeping the previous company directory unchanged")
        return 0
    entries = companies(boards, names)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"companies": entries}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    multi = sum(1 for c in entries if len(c["boards"]) > 1)
    _log.info(
        f"company directory: {len(entries):,} companies over {len(boards):,} Boards, "
        f"{multi:,} with more than one Board, {args.out.stat().st_size:,} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
