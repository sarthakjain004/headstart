"""Mark when a trends-affecting *definition* changed, not just when the data did (ADR-0164).

``role_trends`` already segments its series by centroid ``version`` when a refit re-bases the
whole taxonomy (ADR-0040). Three more things silently change what a family/band count means,
with nothing marking when they did: :mod:`config/role_families.json` can be re-curated without a
refit (:func:`headstart.roles.family_map_fingerprint` detects this); :mod:`headstart.tech_filter`
can widen or narrow which jobs enter the served index at all
(:data:`headstart.tech_filter.TECH_FILTER_VERSION`); and
:data:`headstart.ingest.doc_prep.DERIVATIONS_VERSION` can reshuffle seniority bands when
``update_meta`` re-derives already-indexed rows. Any one of these produces a step in the chart
that looks exactly like a hiring trend, and today nothing tells a reader "we changed how we
count" from "conditions changed".

This module stamps those four values each tick and appends a row to
``data/state/trends_epochs.csv`` only when at least one differs from the last recorded row — so
every row in the file is already a real methodology boundary, not a per-tick sample, and the
Space can draw a marker at each one. ``family_map_fingerprint`` is a content hash rather than a
hand-maintained counter, so a curation edit is detected automatically instead of depending on
someone remembering to bump it — the exact failure class CLAUDE.md's ``DERIVATIONS_VERSION`` rule
already documents happening twice for a version a human has to remember to move.
"""

from __future__ import annotations

import csv
from pathlib import Path

_COLUMNS = (
    "ts",
    "centroid_version",
    "family_map_fingerprint",
    "tech_filter_version",
    "derivations_version",
)


def _read_state(path: Path) -> tuple[tuple[str, ...] | None, bool]:
    """``(last recorded stamp, needs a rebuild)``.

    A missing or empty file has nothing recorded and needs no rebuild — the ordinary first-run
    case. A file whose header doesn't match the current shape is corrupt, or predates this
    module's shape: :func:`append_if_changed` truncates and starts over rather than appending
    beneath it, so a corrupt file heals itself on the next tick instead of permanently reading as
    "nothing to compare against" and writing a row on every run forever.
    """
    if not path.exists() or path.stat().st_size == 0:
        return None, False
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header != list(_COLUMNS):
            return None, True
        last = None
        for row in reader:
            last = tuple(row)
        return last, False


def append_if_changed(
    path: Path,
    ts: str,
    centroid_version: int,
    family_map_fingerprint: str,
    tech_filter_version: int,
    derivations_version: int,
) -> bool:
    """Append one row when this tick's stamp differs from the last recorded one.

    Returns whether it wrote. The comparison is over the four definition values only, never
    ``ts`` — an unchanged run writes nothing, keeping the file at one row per real boundary
    rather than one row per tick.
    """
    current = (
        str(centroid_version),
        family_map_fingerprint,
        str(tech_filter_version),
        str(derivations_version),
    )
    previous, rebuild = _read_state(path)
    if previous is not None and previous[1:] == current:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = rebuild or not path.exists() or path.stat().st_size == 0
    with path.open("w" if rebuild else "a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(_COLUMNS)
        writer.writerow((ts, *current))
    return True
