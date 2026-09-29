"""Write the ``is_confident_non_tech`` column of the served table from a Trends tick (ADR-0349).

:mod:`headstart.ingest.role_trends` decides every served row's role family on each tick. The rows
the head calls non-tech with a top probability of at least
:data:`~headstart.search_filters.confident_non_tech_filter.PROBABILITY` are the ones Search leaves
out unless asked; this stamps their ids on the table, in the same run and before the indexes are
refreshed and the table published, so Search can filter in the database.

**The column is written, never rewritten.** LanceDB has no update that touches one column: an
``update`` or a ``merge_insert`` re-writes every stamped row whole, vector and description with it,
338 MB for 59,523 rows on the 2026-09-29 table. ``add_columns`` with a SQL expression writes only
the new column: dropping the old column and adding ``id IN (…)`` in its place measured 0.2 s and
0.08 MB, on lancedb 0.33 and 0.39, and repeated ticks left no data files behind. So each tick
re-computes the whole column, which is also how a row stops being hidden: a retitled Job the head
no longer calls non-tech, or a Job that left the table, is simply not named. A tick that would
write the column it already holds writes nothing.

**Fails safe, in the direction of showing.** A stamp is refused, and the column left as the last
healthy tick wrote it, when it would hide more than :data:`MAX_STAMPED_SHARE` of the table
(a healthy head calls about a quarter of the rows non-tech at any probability, so more means the
head or its inputs are broken). A write that fails after the old column is dropped puts the column
back with every row visible, so the table is never left without it. The caller never reaches this
while the head, the family list or the title cache is missing or still warming: those degrade
before a family is decided, and the column is untouched.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any, NamedTuple

from headstart import log
from headstart.ingest.index_plan import in_predicate
from headstart.search_filters.confident_non_tech_filter import COLUMN, MIGRATION_SQL

_log = log.get(__name__, __spec__)

#: The most of the table one stamp may hide. The head calls 24.4% of the 2026-09-29 table non-tech
#: at any probability, and 11.9% at the threshold; a share above this one is not a head that
#: works.
MAX_STAMPED_SHARE = 0.30


class StampRefused(ValueError):
    """The stamp would hide an implausible share of the table; the column is left as it was."""


class Stamped(NamedTuple):
    #: Rows carrying the stamp after this call.
    stamped: int
    #: Rows this call stamped that were not, and rows it cleared that were.
    newly: int
    cleared: int
    #: False when the column already held exactly this and nothing was written.
    written: bool


def _held(table: Any) -> set[str] | None:
    """The ids the column stamps now, or None for a table without it."""
    if COLUMN not in table.schema.names:
        return None
    rows = (
        table.search()
        .where(f"{COLUMN} = true")
        .select(["id"])
        .limit(max(table.count_rows(), 1))
        .to_list()
    )
    return {row["id"] for row in rows}


def stamp(table: Any, confident_ids: Collection[str]) -> Stamped:
    """Make ``is_confident_non_tech`` true on exactly ``confident_ids`` and false elsewhere.

    Raises :class:`StampRefused` for an implausible share, and re-raises a failed write after
    putting the column back with every row visible."""
    wanted = set(confident_ids)
    rows = table.count_rows()
    if rows and len(wanted) / rows > MAX_STAMPED_SHARE:
        raise StampRefused(
            f"{len(wanted)} of {rows} rows ({len(wanted) / rows:.0%}) would be hidden, above the "
            f"{MAX_STAMPED_SHARE:.0%} a healthy head can give; the column is left as it was"
        )
    held = _held(table)
    if held == wanted:
        return Stamped(len(wanted), 0, 0, False)
    expression = in_predicate("id", wanted) if wanted else MIGRATION_SQL[COLUMN]
    try:
        if held is not None:
            table.drop_columns([COLUMN])
        table.add_columns({COLUMN: expression})
    except BaseException:
        # Between the drop and the add the table has no column: restore it, every row visible,
        # so `refresh-indexes` (which requires it) and Search find it either way.
        if COLUMN not in table.schema.names:
            table.add_columns(MIGRATION_SQL)
            _log.warning(
                f"the {COLUMN} write failed; the column was put back with every row visible"
            )
        raise
    before = held or set()
    return Stamped(len(wanted), len(wanted - before), len(before - wanted), True)
