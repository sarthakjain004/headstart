#!/usr/bin/env python3
"""Filter the scraped jobs down to the tech subset (ADR-0017).

``data/jobs/{ats}.jsonl`` (every scraped job) -> ``data/jobs/tech/{ats}.jsonl`` (software/tech only).

The scrapers keep writing the full set; this stage keeps only software/tech roles
(``headstart.jobs.tech_filter``, recall-biased — a non-tech job creeping in is fine, dropping a tech job
is not) into ``data/jobs/tech/``, which is what the embedding / index / UI consume. Dropping the
non-tech ~83% means the embedding model only ever works on the jobs the product actually serves.

It also leaves out every row on a Board ``scrape_join`` judged Dormant, one whose newest posting is
more than two years old (``headstart.ingest.board_dormancy``, ADR-0250). With no verdict to read, it
leaves nothing out.

Run from repo root:
    python -m headstart.ingest.filter_tech
Verify recall afterwards with ``scripts/filter/verify_tech.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from headstart import log
from headstart.ingest import DORMANT_BOARDS_PATH, REPO_ROOT, board_dormancy
from headstart.jobs.tech_filter import filter_jobs_and_report

_log = log.get(__name__, __spec__)


def main() -> int:
    log.setup()
    log.context("filter_tech")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=REPO_ROOT / "data" / "jobs")
    ap.add_argument("--dst", type=Path, default=REPO_ROOT / "data" / "jobs" / "tech")
    ap.add_argument(
        "--dormant-boards",
        type=Path,
        default=DORMANT_BOARDS_PATH,
        help="the Boards scrape_join judged Dormant, whose rows are left out (ADR-0250)",
    )
    args = ap.parse_args()
    if not args.src.is_dir():
        log.fail(_log, f"no source dir at {args.src}")
    dormant = board_dormancy.read(args.dormant_boards)
    if dormant is None:
        _log.warning(
            f"no readable Dormant-Board verdict at {args.dormant_boards} — leaving no Board out "
            "this run (ADR-0250)"
        )

    try:
        filter_jobs_and_report(
            args.src, args.dst, _log, dormant_boards=dormant or frozenset()
        )
    except ValueError as exc:
        # a torn line raises with its file:line; say so as an abort, not a bare traceback
        log.fail(_log, f"tech filter aborted: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
