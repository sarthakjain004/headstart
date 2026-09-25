"""The dedup eviction ledger: which served rows a dedup rule took out, per run, Board and rule
(ADR-0210).

    ts,board,count,rule
    2026-09-25T06:00:00+00:00,eightfold:jobs.nvidia.com,41,backing-requisition

A dedup rule removes a row that was never a closure — the posting is still served, from another
Board — yet the Trends chart counts it as one. ADR-0188's epoch marker could absorb that only when
the removals land on the tick the rule changes, and ADR-0210's do not: they follow the
``requisition`` stamps, which arrive as each Board is re-scraped. This ledger records every such
removal where it happens, so a Trends reader can add them back exactly, whenever they land.

- ``ts`` is the run's :func:`headstart.ingest.run_ts`, the value ``role_trends`` stamps the same
  run's counts with, so the two ledgers join on it.
- ``board`` is the evicted row's Board as ``index prune`` resolves it (``resolve_board``), the
  key ``role_trends`` counts Boards by.
- ``rule`` is ``case-variant``, ``workday-tenant``, ``tenant-requisition`` (a Taleo or ADP
  Tenant's, ADR-0223) or ``backing-requisition`` (:mod:`headstart.ingest.index_plan`), or
  ``alias:{signal}`` for a row on a Board an alias ledger buries. Ordinary closures (``sync``'s
  evictions) and other off-Board evictions are never here.

Append-only, and written whole to a temp file then renamed, so a crash mid-write leaves the
previous ledger intact rather than truncated.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path

FIELDS = ("ts", "board", "count", "rule")


def append(
    path: str | Path,
    ts: str,
    rules: Mapping[str, str],
    board_of: Callable[[str], str],
) -> int:
    """Append this run's ``{evicted id: rule}`` to the ledger at ``path``, one row per Board and
    rule, sorted, and return how many rows. Writes nothing when nothing was evicted."""
    counts = Counter((board_of(job_id), rule) for job_id, rule in rules.items())
    if not counts:
        return 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    if not path.exists():
        writer.writerow(FIELDS)
    writer.writerows((ts, b, n, r) for (b, r), n in sorted(counts.items()))
    previous = path.read_bytes() if path.exists() else b""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(previous + buffer.getvalue().encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return len(counts)
