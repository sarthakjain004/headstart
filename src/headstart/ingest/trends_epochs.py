"""Mark when a trends-affecting *definition* changed, not just when the data did (ADR-0164).

``role_trends`` already segments its series by centroid ``version`` when a refit re-bases the
whole taxonomy (ADR-0040). Three more things silently change what a family/band count means,
with nothing marking when they did: :mod:`config/role_families.json` can be re-curated without a
refit (:func:`headstart.roles.family_list_fingerprint` detects this); :mod:`headstart.tech_filter`
can widen or narrow which jobs enter the served index at all
(:data:`headstart.tech_filter.TECH_FILTER_VERSION`); and
:data:`headstart.ingest.doc_prep.DERIVATIONS_VERSION` can reshuffle seniority bands when
``update_meta`` re-derives already-indexed rows. Any one of these produces a step in the chart
that looks exactly like a hiring trend, and today nothing tells a reader "we changed how we
count" from "conditions changed".

A fifth, :data:`headstart.ingest.index_plan.DEDUP_VERSION` (ADR-0188), marks a change to which
served rows count as duplicates: that removes rows that were served before, in one tick. A sixth
column marks what decides a row's family. It held the title rules' fingerprint
while they decided (ADR-0215) and holds the classifier head's version since ADR-0220, which
renamed it ``family_classifier_version`` in place. Since then ``centroid_version`` reads
``none``: no centroid fit decides anything.

This module stamps those values each tick and appends a row to
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
    "dedup_version",
    "family_classifier_version",
)
# What an old row takes for a column added after it was written. A fixed value, never the live
# one: a change that lands before the first upgrading tick must still read as a boundary.
# The dedup rules' version when their column was added (ADR-0188); nothing decided a family
# from the title before the sixth column was added (ADR-0215).
_DEDUP_VERSION_AT_ADDITION = "1"
# What a column holds when nothing it names exists: no title decider before ADR-0215, and no
# centroid fit since ADR-0220.
ABSENT = "none"
# Headers from before a column existed, or under its old name, each with the values its rows take
# for the columns it lacks. Their rows are real boundaries the Space still marks, so a file in one
# of these shapes is upgraded in place rather than rebuilt as corrupt. The last one is the rename
# (ADR-0220): its rows keep the title rules' fingerprint under the new name.
_OLDER_HEADERS = {
    _COLUMNS[:-2]: (_DEDUP_VERSION_AT_ADDITION, ABSENT),
    _COLUMNS[:-1]: (ABSENT,),
    (*_COLUMNS[:-1], "family_rules_fingerprint"): (),
}


def upgrade_older_header(path: Path) -> None:
    """Rewrite a file in an older shape (a column missing or under its old name) in the current one;
    leave any other alone.

    Written beside it and renamed over it, so a crash mid-write leaves the old file whole rather
    than a truncated one the merge stage's upload would publish — and the staged file is removed
    on failure, because that upload takes all of ``data/state`` and would publish it too."""
    if not path.exists():
        return
    with path.open(encoding="utf-8", newline="") as fh:
        rows = [row for row in csv.reader(fh) if row]
    fill = _OLDER_HEADERS.get(tuple(rows[0])) if rows else None
    if fill is None:
        return
    staged = path.with_suffix(path.suffix + ".tmp")
    try:
        with staged.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(_COLUMNS)
            writer.writerows([*row, *fill] for row in rows[1:])
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


def _read_state(path: Path) -> tuple[tuple[str, ...] | None, bool]:
    """``(last recorded stamp, needs a rebuild)``.

    A missing or empty file has nothing recorded and needs no rebuild — the ordinary first-run
    case. A file whose header doesn't match the current shape is corrupt, or predates this
    module's shape (except an older header, which :func:`upgrade_older_header` upgrades before
    this reads it):
    :func:`append_if_changed` truncates and starts over rather than appending
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
    centroid_version: int | str,
    family_map_fingerprint: str,
    tech_filter_version: int,
    derivations_version: int,
    dedup_version: int,
    family_classifier_version: int | str,
) -> bool:
    """Append one row when this tick's stamp differs from the last recorded one.

    Returns whether it wrote. The comparison is over the definition values only, never
    ``ts`` — an unchanged run writes nothing, keeping the file at one row per real boundary
    rather than one row per tick.
    """
    current = (
        str(centroid_version),
        family_map_fingerprint,
        str(tech_filter_version),
        str(derivations_version),
        str(dedup_version),
        str(family_classifier_version),
    )
    upgrade_older_header(path)
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
