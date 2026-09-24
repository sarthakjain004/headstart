"""Name every Board that has tech openings, grouped into the companies a person would type.

The Trends tab can be narrowed to one or more companies (ADR-0185). Its counts come from the
ADR-0143 Board-delta ledger, which is keyed by **board_key**, while a person types a company
name, so something has to join the two. This stage writes that join as a small static file
the Space serves, `data/state/company_directory.json`:

    {"companies": [{"name": "Lockheed Martin",
                    "boards": ["eightfold:lockheedmartin.eightfold.ai",
                               "successfactors:lockheed.jobs.hr.cloud.sap"]}, ...]}

It runs in the pipeline rather than the Space because the naming rules (`display_name`, the
curated aliases) live in `hot_boards`, and the Space never imports from `ingest`.

## It carries names, not counts, on purpose

Openings change every run. Names almost never do. With no count and no timestamp inside it,
the file comes out byte-identical run after run until a Board gains or loses its last tech
opening or its name changes, so republishing it with `data/state` costs no new storage. The
Space already holds the delta ledger and reads each Board's current openings from it.

## When two Boards are one company

Two Boards are one company when they are **the same tenant** or share a **curated alias**,
and never merely because their names match. Measured on the 2026-09-24 snapshot, 32,829
Boards with tech openings:

- **Same tenant** is structural: one ATS account split into several Boards. Workday splits a
  tenant into sites (`workday:hpe/ACJobSite`, `workday:hpe/Jobsathpe`: 684 tenants), Taleo
  Enterprise into career sections under one host (HDR's fifteen: 36 hosts), Taleo Business
  Edition into `cws` sites under one `org` (12 orgs). Case is ignored everywhere, which also
  folds the stale casing duplicates ADR-0023 describes (`smartrecruiters:AbhiBus` and
  `smartrecruiters:abhibus`: 54 pairs).
- **A curated alias** (`hot_boards.DISPLAY_ALIASES`) is the one cross-ATS identity anyone has
  asserted, so Lockheed Martin's Eightfold and SuccessFactors Boards are one company.

A matching name is not identity, even a stated one. The first draft of this stage merged
cased names across ATSes and slugs within one: it made one "Pearl" of four ATSes' Boards, one
"Arlo" of a New York startup and Netgear's spin-off, and one "Clarity" of `ashby:clarity` and
`ashby:hiive`. A tidied slug collides even more easily: `trakstar:amazon` is one
Salesforce-admin posting, and `workday:google/GOCJobs` is Google Operations Center. The cost
of refusing is that a real company on two ATSes (Schonfeld on Greenhouse and SmartRecruiters)
shows twice under one name, and the user picks both. That is a choice the user can see, where
a wrong merge would add another employer to their chart without telling them.

**Grouping is not deduplication.** Where one tenant's Boards list the same requisitions
(Taleo sections serve the whole tenant set; Workday sites overlap), their counts overlap too,
and a sum over the group counts those postings more than once. This file says which Boards
belong to a company. Whether their counts can be added is the index's problem, not this one.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from headstart import log, roles
from headstart.ingest.hot_boards import (
    DISPLAY_ALIASES,
    board_names,
    display_name,
    stated_name,
)

# `__spec__` as well as `__name__`, like every other module that doubles as a `python -m`
# entry point: run that way `__name__` is "__main__", outside the root `setup()` configures.
_log = log.get(__name__, __spec__)

REPO_ROOT = Path(__file__).resolve().parents[3]
_BOARD_COUNTS = REPO_ROOT / "data" / "state" / "role_trend_board_counts.parquet"
_OUT = REPO_ROOT / "data" / "state" / "company_directory.json"
_DB = REPO_ROOT / "data" / "lancedb"


def tech_boards(path: Path) -> set[str]:
    """Boards with at least one tech opening in role_trends' current Board-count snapshot.

    A Board with only `non-tech` rows has no series the Trends tab would chart, and `watch:`
    rows re-count Jobs already counted in their family (ADR-0051).
    """
    import pyarrow.parquet as pq

    table = pq.read_table(
        path, columns=["board", "metric", "family", "count"]
    ).to_pydict()
    return {
        board
        for board, metric, family, count in zip(
            table["board"],
            table["metric"],
            table["family"],
            table["count"],
            strict=True,
        )
        if metric == "stock"
        and count > 0
        and family != roles.NON_TECH
        and not family.startswith(roles.WATCH_PREFIX)
    }


def tenant(board: str) -> str:
    """The ATS account a Board belongs to, however many Boards that account is split into."""
    ats, slug = board.split(":", 1)
    if ats == "workday":  # {tenant}/{site}
        slug = slug.split("/", 1)[0]
    elif ats == "taleo_enterprise":  # https://{host}/careersection/{section}
        slug = urlsplit(slug).netloc or slug
    elif ats == "taleo_be":  # https://{pod}/.../searchResults?org={tenant}&cws={site}
        slug = parse_qs(urlsplit(slug).query).get("org", [slug])[0]
    return f"{ats}:{slug.casefold()}"


def group(boards: set[str], names: dict[str, str]) -> list[dict]:
    """One entry per company, each naming its Boards. See the module docstring for the rule."""
    parent = {board: board for board in boards}

    def root(board: str) -> str:
        while parent[board] != board:
            parent[board] = parent[parent[board]]
            board = parent[board]
        return board

    # A union over two keys, not a grouping by one: RTX's aliased site and its lowercase
    # casing duplicate share only a tenant, while Lockheed's two Boards share only an alias.
    first: dict[str, str] = {}
    for board in sorted(boards):
        alias = DISPLAY_ALIASES.get(board)
        for key in (tenant(board), f"alias:{alias.casefold()}" if alias else None):
            if key is None:
                continue
            if key in first:
                parent[root(board)] = root(first[key])
            else:
                first[key] = board
    clusters: dict[str, list[str]] = collections.defaultdict(list)
    for board in boards:
        clusters[root(board)].append(board)
    companies = [
        {"name": _name(cluster, names), "boards": sorted(cluster)}
        for cluster in clusters.values()
    ]
    # Sorted so an unchanged set of Boards writes an unchanged file (module docstring).
    companies.sort(key=lambda c: (c["name"].casefold(), c["boards"][0]))
    return companies


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
    ap.add_argument("--board-counts", type=Path, default=_BOARD_COUNTS)
    ap.add_argument("--db", type=Path, default=_DB)
    ap.add_argument("--out", type=Path, default=_OUT)
    args = ap.parse_args()

    # role_trends writes the snapshot and is `continue-on-error`, so a run where it skipped
    # leaves it absent: a missing prerequisite, not a defect here.
    if not args.board_counts.exists():
        _log.warning(
            f"skipping the company directory — {args.board_counts} is missing "
            "(role_trends writes it; it may have skipped this run)"
        )
        return 0

    from headstart.search import PROD_TABLE

    names = board_names(args.db, PROD_TABLE)
    if not names:
        # Every Board would fall back to its slug, which rewrites the whole file for one run
        # and names every company worse. Keeping the previous directory is better on both.
        _log.warning(
            "no company names readable; keeping the previous directory unchanged"
        )
        return 0
    companies = group(tech_boards(args.board_counts), names)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"companies": companies}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    boards = sum(len(c["boards"]) for c in companies)
    multi = sum(1 for c in companies if len(c["boards"]) > 1)
    _log.info(
        f"company directory: {len(companies):,} companies over {boards:,} Boards, "
        f"{multi:,} with more than one Board, {args.out.stat().st_size:,} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
