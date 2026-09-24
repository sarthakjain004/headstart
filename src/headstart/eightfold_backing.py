"""The Eightfold career sites that front another ATS's Board, and which Boards (ADR-0205, ADR-0210).

One committed CSV, ``data/validate/eightfold_backing.csv``, one row per pair::

    eightfold,backing
    jobs.nvidia.com,workday:nvidia/nvidiaexternalcareersite

``eightfold`` is the Eightfold Board's slug (its host); ``backing`` is a lowercased ``board_key``
that lists its postings, and a site may have several. Three readers, one file:

- ``scripts/validate/eightfold_backing_boards.py`` takes them as its candidates, and buries a site
  whose backing Boards serve every tech posting it lists (ADR-0205).
- ``index sync``/``prune`` serve a posting once when a site that is not buried and its backing
  Board both list it, matched on the stored ``requisition`` (ADR-0210).
- ``EightfoldScraper`` reads which ATS backs its Board, because the posting states that ATS's
  requisition under a field that depends on it (Oracle's is ``displayJobId``).

Found by content on served index v654 (2026-09-23): pairs of Boards on two ATSes sharing exact
descriptions. A new front enters by adding a row. Lumen is left out by the user's decision (its
backing site is an internal careers site), and so is International SOS (postings of its own).
The rows whose backing Board is itself an Eightfold site are a company's second site (#154), the
hand-frozen ``check_liveness._EIGHTFOLD_ALIAS_LOSERS``.
"""

from __future__ import annotations

import csv
from functools import cache
from pathlib import Path

PATH = (
    Path(__file__).resolve().parents[2] / "data" / "validate" / "eightfold_backing.csv"
)


@cache
def load(path: Path = PATH) -> dict[str, tuple[str, ...]]:
    """``{Eightfold Board slug: its backing Board keys}``, in file order."""
    pairs: dict[str, tuple[str, ...]] = {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            slug = row["eightfold"].strip().lower()
            pairs[slug] = (*pairs.get(slug, ()), row["backing"].strip().lower())
    return pairs
