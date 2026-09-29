"""Where a set of served jobs are: the `location` values they carry most (ADR-0275), and the
countries those name, each with its top cities (ADR-0323, ADR-0331) — for one company's Boards
(:func:`top`) or for every job a search's filters match (:func:`places`, ADR-0355).

The Search rail has no location facet, and :mod:`headstart.serving.facets` cannot give one: it
counts a filter's fixed options, and `location` is free text that each employer writes, with no
option list. So both answers are one single-column scan of the rows, rolled up here. A company
profile's is scoped to the company's Boards: Amazon's 9,651 rows in 28 ms (2026-09-29, local).
A search's is every row it matches, up to the whole served table: 500,167 rows read in 0.06 s.

A location is the served string, with its whitespace collapsed, so "Dublin" and "Dublin, Ireland"
are two places, as the employers wrote them. What merges them is the country each names, read by
the ``country`` filter's own rule, so a count under Ireland is exactly the jobs ``country=IE``
would count with the same other filters: each distinct location is read by
`country_gazetteer.countries_outside_india` (the rule the filter compiles to SQL, run a column at a
time, ADR-0355), and India by the materialized ``country`` column that ``country=IN`` reads, or,
on a table without it, by `india_gazetteer.classify`. Within a country, a place is the city it
names first: "Dublin" and "Dublin, Ireland" are one "Dublin" under Ireland. A job naming two
countries counts in both; a place naming none ("N/A", "Remote") is counted apart.

Reading a location's countries costs about 20 µs a distinct place in bulk and 330 µs one at a
time, so each is read once per process (:data:`_OUTSIDE_INDIA`): the served table never changes
under a process, so that holds at most its distinct locations and heads (85,828 locations on
2026-09-29), and `JobSearch.warm` reads them all before the first request.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from functools import lru_cache
from threading import Lock
from typing import Any

from headstart.search_filters import (
    country_filter,
    country_gazetteer,
    india_filter,
    india_gazetteer,
)
from headstart.serving.count_ranking import most_first

#: The most rows one company's answer reads. A company's Boards are bounded
#: (`job_search.MAX_SCOPED_BOARDS`) and the largest measured is Amazon's 9,651 rows, so this is
#: five of those; past it the counts cover the first rows read, and ``capped`` says so. A search's
#: answer reads every row it matches.
MAX_ROWS = 50_000

#: How many of a country's cities, and of the places naming no country, each lists.
PLACES_PER_COUNTRY = 3

#: Each location string's countries but India, as this process has read them.
_OUTSIDE_INDIA: dict[str, frozenset[str]] = {}
_OUTSIDE_INDIA_LOCK = Lock()

#: Distinct places whose India this process remembers where no column says it.
_INDIA_CACHED = 200_000


def _outside_india(places: Iterable[str]) -> dict[str, frozenset[str]]:
    """Each of ``places``' countries but India, reading only those this process has not read."""
    wanted = set(places)
    with _OUTSIDE_INDIA_LOCK:
        unread = [place for place in wanted if place not in _OUTSIDE_INDIA]
    if unread:
        read = country_gazetteer.countries_outside_india(unread)
        with _OUTSIDE_INDIA_LOCK:
            _OUTSIDE_INDIA.update(zip(unread, read, strict=True))
    with _OUTSIDE_INDIA_LOCK:
        return {place: _OUTSIDE_INDIA[place] for place in wanted}


@lru_cache(maxsize=_INDIA_CACHED)
def _in_india(place: str) -> bool:
    return india_gazetteer.classify(place) == country_filter.INDIA


def scoped_rows(table: Any, where: str, columns: list[str]) -> list[dict[str, Any]]:
    """The ``columns`` of the rows ``where`` selects, at most :data:`MAX_ROWS` of them: the one
    scan a company's levels (`level_counts`) make."""
    return (
        table.search()
        .where(where, prefilter=True)
        .select(columns)
        .limit(MAX_ROWS)
        .to_list()
    )


def _located(
    table: Any, where: str | None, india_materialized: bool, limit: int
) -> tuple[int, Counter[tuple[str, bool]]]:
    """How many rows ``where`` selects, at most ``limit``, and how many carry each location as
    served with whether the ``country`` column puts it in India (False without the column)."""
    columns = ["location", india_filter.COLUMN] if india_materialized else ["location"]
    if where and "_rowid" in where:
        # A description keyword's rows named by row id (ADR-0320): LanceDB 0.36 cannot plan
        # that read unless a column its clause names is projected.
        columns.append("id")
    search = table.search()
    if where:
        search = search.where(where, prefilter=True)
    rows = search.select(columns).limit(limit).to_list()
    return len(rows), Counter(
        (
            row.get("location"),
            india_materialized and row.get(india_filter.COLUMN) == country_filter.INDIA,
        )
        for row in rows
    )


def _collapsed(place: str | None) -> str:
    return " ".join(str(place or "").split())


#: What separates two places in one location string: "Berlin, CT; Westwood, MA".
_PLACES_SEPARATOR = re.compile(r"[;|]")


def _head(place: str) -> str:
    """The city ``place`` names first: before its first comma or separator."""
    return _PLACES_SEPARATOR.split(place, maxsplit=1)[0].split(",", 1)[0].strip()


def _places(ranked: list[tuple[str, int]]) -> list[dict[str, Any]]:
    return [
        {"location": place, "count": count}
        for place, count in ranked[:PLACES_PER_COUNTRY]
    ]


def _by_country(
    located: Counter[tuple[str, bool]], india_materialized: bool
) -> dict[str, Any]:
    """``located`` rolled up by the countries each place names: each country's jobs and its top
    cities, most jobs first, ties by code, a city spelled as its commonest place writes it; and
    the jobs whose place names no country, with its top places.

    A city is a place's first part ("Dublin" of "Dublin, Ireland" under IE) when that part names
    no country but the one it is counted under; one naming another ("London" of "London,
    Dublin" under IE) leaves the whole place, as written."""
    written = {place for place, _ in located if _collapsed(place)}
    outside = _outside_india(written)

    def countries_of(place: str, in_india: bool) -> frozenset[str]:
        if india_materialized:
            india = in_india
        else:
            india = _in_india(place)
        return outside[place] | {"IN"} if india else outside[place]

    heads = {place: _head(_collapsed(place)) for place in written}
    head_outside = _outside_india(head for head in heads.values() if head)
    jobs: Counter[str] = Counter()
    cities: dict[str, Counter[str]] = {}
    spelled: dict[tuple[str, str], str] = {}
    no_country: Counter[str] = Counter()
    for (place, in_india), count in located.items():
        if place not in written:
            continue
        shown = _collapsed(place)
        codes = countries_of(place, in_india)
        head = heads[place]
        head_codes = (
            head_outside[head] | ({"IN"} if _in_india(head) else set())
            if head
            else None
        )
        for code in codes:
            jobs[code] += count
            city = head if head_codes is not None and head_codes <= {code} else shown
            key = spelled.setdefault((code, city.casefold()), city)
            cities.setdefault(code, Counter())[key] += count
        if not codes:
            no_country[shown] += count
    return {
        "countries": [
            {"code": code, "jobs": count, "places": _places(most_first(cities[code]))}
            for code, count in most_first(jobs)
        ],
        "no_country": {
            "jobs": sum(no_country.values()),
            "places": _places(most_first(no_country)),
        },
    }


def _unstated(located: Counter[tuple[str, bool]]) -> int:
    return sum(count for (place, _), count in located.items() if not _collapsed(place))


def top(table: Any, where: str, limit: int, india_materialized: bool) -> dict[str, Any]:
    """The ``limit`` locations the rows ``where`` selects carry most, most first, ties by name,
    and every location's country (:func:`_by_country`), over at most :data:`MAX_ROWS` rows.

    ``jobs`` is how many rows were counted, ``unstated`` how many of them name no location, and
    ``distinct`` how many different locations they name."""
    read, located = _located(table, where, india_materialized, MAX_ROWS)
    counted: Counter[str] = Counter()
    for (place, _), count in located.items():
        if shown := _collapsed(place):
            counted[shown] += count
    ranked = most_first(counted)
    return {
        "jobs": read,
        "unstated": _unstated(located),
        "distinct": len(counted),
        "capped": read >= MAX_ROWS,
        "locations": [
            {"location": place, "count": count} for place, count in ranked[:limit]
        ],
        **_by_country(located, india_materialized),
    }


def places(table: Any, where: str | None, india_materialized: bool) -> dict[str, Any]:
    """Where every row ``where`` selects is (ADR-0355): ``jobs`` read, ``unstated`` (no location)
    and the rollup by country (:func:`_by_country`). ``table`` holds at most the served table's
    rows, so reading all of them is bounded by its size."""
    read, located = _located(
        table, where, india_materialized, max(1, table.count_rows())
    )
    return {
        "jobs": read,
        "unstated": _unstated(located),
        **_by_country(located, india_materialized),
    }
