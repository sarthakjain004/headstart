"""The rows a description-keyword search keeps, found once and then named by row id (ADR-0320).

A keyword looked for in descriptions is the costliest Search filter: its regex reads every row's
description, most of the table's text, and a country or company filter does not shrink that read,
since the engine evaluates the whole where-clause over every row it scans. One concise
`search_jobs` call asks `/search` and `/facets?counts=total` for the same filters at once, and the
facet total's description coverage counts the other filters twice more; each paid its own scan.

:meth:`DescriptionMatches.where` answers a where-clause keeping the same rows as the one
:func:`~headstart.search_filters.compiler.build_filter` would, named by row id instead
(``_rowid IN (…)``), once it has found them:

1. the rows every filter but the keyword keeps, which the request's description coverage counts
   anyway (a country's regex over locations is the slow part of that);
2. among those, the rows the keyword *without* its word-start anchor matches: a bare literal,
   which Rust's regex engine finds about five times faster than one after ``(^|[^a-z0-9])``, and
   every row the keyword keeps is among them. LanceDB reads just the named rows for it;
3. among those candidates only, the rows the exact keyword keeps.

When the other filters keep too many rows to name (the United States) or there are none, the
literal is looked for over the whole table instead, once per keyword, and the other filters read
only its candidates. Every where-clause's rows are kept for the life of the process,
:data:`WHERES_KEPT` of them, so the other route, the next page and the next facet count read no
description at all. A request for a where-clause another request is finding waits for that one
instead of scanning again. A clause keeping more than :data:`ROW_LIST_MAX` rows is answered as it
was compiled.

A row id names one row only within one version of the table. The served table never changes under
a running process (the Space restarts on a new one, and nothing serving writes it), so a row id
found at one request names the same row at the next.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from headstart.search_filters.compiler import (
    KEYWORD_SCOPES,
    IndexCapabilities,
    SearchFilters,
    build_filter,
    keyword_clause,
    with_extra,
)

#: The most rows a where-clause names by id. On the served table a count by 50,000 ids took
#: 0.10 s and by 150,000 0.25 s, a ranked page 0.63 s and 2.2 s, against about 5 s to scan
#: descriptions for a word again (ADR-0320).
ROW_LIST_MAX = 100_000

#: How many where-clauses' rows are kept, the least recently used dropped first. Each holds at
#: most :data:`ROW_LIST_MAX` ids, about 1.5 MB of text (an id is `fragment << 32 | offset`).
WHERES_KEPT = 64


@dataclass
class _Found:
    """One where-clause's rows, once found: a clause naming them, or None for too many."""

    named: str | None = None
    done: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


def reads_descriptions(filters: SearchFilters, capabilities: IndexCapabilities) -> bool:
    """Whether ``filters`` look for their keyword in the description column. A keyword of quote
    marks alone compiles to no clause, and so reads nothing."""
    scope = KEYWORD_SCOPES.get(filters.kw_in or "")
    return bool(
        filters.kw
        and scope is not None
        and "description" in scope.columns
        and capabilities.has_description
        and keyword_clause(filters, capabilities)
    )


def _named(row_ids: list[int]) -> str:
    """A where-clause keeping exactly ``row_ids``. `id IS NOT NULL` is not decoration: LanceDB
    0.36, the Space's, fails `count_rows` on a filter naming only `_rowid` (measured)."""
    if not row_ids:
        return "false"
    return "id IS NOT NULL AND _rowid IN (" + ", ".join(map(str, row_ids)) + ")"


def _both(first: str | None, second: str | None) -> str:
    return " AND ".join(clause for clause in (first, second) if clause)


class DescriptionMatches:
    """Where-clauses for a description-keyword request, their rows found once (ADR-0320).

    Built once per process over the served ``table`` and its ``capabilities``.
    """

    def __init__(
        self,
        table: Any,
        capabilities: IndexCapabilities,
        *,
        row_list_max: int = ROW_LIST_MAX,
    ) -> None:
        self._table = table
        self._capabilities = capabilities
        self._row_list_max = row_list_max
        self._kept: OrderedDict[str, _Found] = OrderedDict()
        self._kept_lock = threading.Lock()

    def where(self, filters: SearchFilters, extra_where: str | None) -> str | None:
        """``build_filter(filters)`` narrowed by ``extra_where``: the same rows, named by row id
        when at most :data:`ROW_LIST_MAX` of them. Meant for every where-clause of a request that
        reads descriptions, the ones without the keyword too (its coverage counts them)."""
        compiled = with_extra(build_filter(filters, self._capabilities), extra_where)
        if compiled is None:
            return None
        if not reads_descriptions(filters, self._capabilities):
            return self._kept_rows(compiled, lambda: self._rows(compiled)) or compiled
        rest = with_extra(
            build_filter(replace(filters, kw=None, kw_in=None), self._capabilities),
            extra_where,
        )
        found = self._kept_rows(
            compiled, lambda: self._rows_with_keyword(filters, rest)
        )
        return found or compiled

    def _rows_with_keyword(
        self, filters: SearchFilters, rest: str | None
    ) -> list[int] | None:
        """The rows ``rest`` and the keyword keep, the keyword's literal looked for first:

        1. among the rows ``rest`` keeps, when few enough to name. The request's description
           coverage counts those same rows, so they are read once for both;
        2. otherwise over the whole table, kept per keyword; ``rest`` then reads only those
           candidates, so a country's regex never reads every location for it.

        The exact clause reads the candidates alone. None when neither narrows to the row list's
        bound ("ai" in descriptions: 475,290 candidates, 229,805 rows): the exact clause is then
        left to each route, not scanned here once more only to find as many."""
        exact = keyword_clause(filters, self._capabilities)
        loose = keyword_clause(filters, self._capabilities, word_start=False)
        # `where` sends only filters whose keyword reads descriptions, so both compile.
        assert exact and loose
        scope = self._kept_rows(rest, lambda: self._rows(rest)) if rest else None
        if scope is not None:
            rows = self._rows(_both(scope, loose))
            return None if rows is None else self._rows(_both(_named(rows), exact))
        # Its own key: without other filters the literal of "c++" is the whole where-clause.
        candidates = self._kept_rows(f"candidates {loose}", lambda: self._rows(loose))
        if candidates is None:
            return None
        return self._rows(_both(_both(candidates, rest), exact))

    def _kept_rows(self, key: str, find: Callable[[], list[int] | None]) -> str | None:
        """A clause naming the rows ``find`` finds, found at the first ask of ``key`` and kept;
        None when there are too many to name."""
        with self._kept_lock:
            found = self._kept.pop(key, None) or _Found()
            self._kept[key] = found
            while len(self._kept) > WHERES_KEPT:
                self._kept.popitem(last=False)
        with found.lock:
            if not found.done:
                rows = find()
                found.named = None if rows is None else _named(rows)
                found.done = True
        return found.named

    def _rows(self, where: str | None) -> list[int] | None:
        """The row ids ``where`` keeps, sorted, or None past the row list's bound. Reads only the
        columns ``where`` names. Every row is read, never a limit: LanceDB 0.33 answered a
        filtered read limited to 100,001 rows with 1,113 of the 3,754 it keeps (measured)."""
        query = self._table.search()
        if where:
            query = query.where(where)
        found = query.with_row_id(True).select(["_rowid"]).to_arrow()
        if found.num_rows > self._row_list_max:
            return None
        return sorted(found.column("_rowid").to_pylist())
