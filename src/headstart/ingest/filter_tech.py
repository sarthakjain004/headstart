#!/usr/bin/env python3
"""Filter the scraped jobs down to the tech subset (ADR-0017).

``data/jobs/{ats}.jsonl`` (every scraped job) -> ``data/jobs/tech/{ats}.jsonl`` (software/tech only).

The scrapers keep writing the full set; this stage keeps only software/tech roles
(``headstart.tech_filter``, recall-biased — a non-tech job creeping in is fine, dropping a tech job
is not) into ``data/jobs/tech/``, which is what the embedding / index / UI consume. Dropping the
non-tech ~83% means the embedding model only ever works on the jobs the product actually serves.

Run from repo root:
    python -m headstart.ingest.filter_tech
Verify recall afterwards with ``scripts/filter/verify_tech.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from headstart import log
from headstart.ingest import REPO_ROOT
from headstart.tech_filter import filter_jobs

_log = log.get(__name__, __spec__)


def main() -> int:
    log.setup()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=REPO_ROOT / "data" / "jobs")
    ap.add_argument("--dst", type=Path, default=REPO_ROOT / "data" / "jobs" / "tech")
    args = ap.parse_args()
    if not args.src.is_dir():
        log.fail(_log, f"no source dir at {args.src}")

    stats = filter_jobs(args.src, args.dst)
    _log.info(f"{'ATS':<16}{'kept':>9}{'total':>9}{'kept%':>8}")
    kept = total = 0
    empty = []
    for ats, (k, t) in sorted(stats.items()):
        kept += k
        total += t
        if t:
            _log.info(f"{ats:<16}{k:>9}{t:>9}{100 * k / t:>7.1f}%")
        else:
            # An ATS that scraped nothing used to be skipped here, leaving a wholly broken
            # scraper no trace in this table at all.
            #
            # Every ATS reaching `stats` was in this run's slice: `filter_jobs` keys off
            # `src_dir.glob("*.jsonl")`, and `harvest` opens one handle per ATS *in the shard's
            # list* precisely so a zero-yield ATS still leaves an empty file. An ATS outside the
            # slice has no file at all and never lands here — so "not in the slice" is not one of
            # the readings, and offering it would blunt the signal this line exists to give.
            #
            # Deferral IS one, though: `harvest` opens those handles before the resume filter, so
            # an ATS whose every Board was deferred by a budget kill also leaves an empty file and
            # arrives here having been neither attempted nor empty. `scrape_join`'s own
            # "deferred boards" line is where that is diagnosed.
            empty.append(ats)
    if empty:
        _log.warning(
            f"{len(empty)} ATS(es) were in this run's slice but contributed zero rows: "
            f"{', '.join(empty)} — their boards failed, were deferred, or are genuinely empty"
        )
    if total:
        _log.info(
            f"{'TOTAL':<16}{kept:>9}{total:>9}{100 * kept / total:>7.1f}%"
            f"  (dropped {total - kept} non-tech) -> {args.dst}"
        )
    else:
        # A zero-row run used to be near-silent: the table printed its header and stopped, which
        # is a hard shape to notice in a green log. Everything downstream reads this corpus, so
        # say it plainly. Not an abort — this stage does not own that call.
        _log.error(f"no rows at all reached the tech filter -> {args.dst} is empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
