"""Where a set of Boards' served jobs are: the `location` values they carry most (ADR-0275), and
the countries those name (ADR-0323, ADR-0331).

The Search rail has no location facet, and :mod:`headstart.serving.facets` cannot give one: it
counts a filter's fixed options, and `location` is free text that each employer writes, with no
option list. A company profile asks something narrower — which few places one company's own jobs
name most often — and that is cheap because it is scoped to the company's Boards: one
single-column scan of their rows. Measured through :func:`top` on a local 514,163-row snapshot of
the served table (2026-09-29, third of three runs): Amazon's 9,651 rows in 28 ms, Deloitte South
Asia's 884 in 21 ms, Stripe's 221 in 21 ms.

A location is the served string, with its whitespace collapsed, so "Dublin" and "Dublin, Ireland"
are two places, as the employers wrote them. What merges them is the country each names, read by
the ``country`` filter's own gazetteer (`search_filters.country_gazetteer.classify`, ADR-0273), so
a profile's "Ireland 19" counts exactly the jobs ``country=IE`` would, and, within a country, the
city each names first: "Dublin" and "Dublin, Ireland" are one "Dublin" under Ireland. A job naming
two countries counts in both; a place naming none ("N/A", "Remote") is counted apart. The
gazetteer costs about 1.6 ms a distinct place (Amazon's 1,257 in 2.0 s, measured 2026-09-29), so
each place is read once per process (:data:`_COUNTRIES_CACHED`) and at most
:data:`MAX_PLACES_READ` of a scan's places, most jobs first, are read at all.
"""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from typing import Any

from headstart.search_filters import country_gazetteer
from headstart.serving.count_ranking import most_first

#: The most rows one answer reads. A company's Boards are bounded (`job_search.MAX_SCOPED_BOARDS`)
#: and the largest measured is Amazon's 9,651 rows, so this is five of those; past it the counts
#: cover the first rows read, and ``capped`` says so.
MAX_ROWS = 50_000

#: The most distinct places one answer reads a country from, most jobs first. Amazon names 1,257
#: and no other Board over 816 (2026-09-29); past this, ``places_unread`` counts the jobs left out.
MAX_PLACES_READ = 2_000

#: How many of a country's cities, and of the places naming no country, each lists.
PLACES_PER_COUNTRY = 3

#: Distinct places whose countries this process remembers. The served table names 85,712.
_COUNTRIES_CACHED = 100_000


@lru_cache(maxsize=_COUNTRIES_CACHED)
def _countries_of(place: str) -> frozenset[str]:
    return frozenset(country_gazetteer.classify(place))


def scoped_rows(table: Any, where: str, columns: list[str]) -> list[dict[str, Any]]:
    """The ``columns`` of the rows ``where`` selects, at most :data:`MAX_ROWS` of them: the one
    scan a company's locations and its levels (`level_counts`) each make."""
    return (
        table.search()
        .where(where, prefilter=True)
        .select(columns)
        .limit(MAX_ROWS)
        .to_list()
    )


#: What separates two places in one location string: "Berlin, CT; Westwood, MA".
_PLACES_SEPARATOR = re.compile(r"[;|]")


def _city(place: str, code: str) -> str:
    """The city ``place`` names first — before its first comma — when that names no country but
    ``code``: "Dublin" of "Dublin, Ireland" under IE. A first part naming another country
    ("London" of "London, Dublin" under IE) leaves the whole place, as written."""
    head = _PLACES_SEPARATOR.split(place, maxsplit=1)[0].split(",", 1)[0].strip()
    return head if head and _countries_of(head) <= {code} else place


def _places(ranked: list[tuple[str, int]]) -> list[dict[str, Any]]:
    return [
        {"location": place, "count": count}
        for place, count in ranked[:PLACES_PER_COUNTRY]
    ]


def _by_country(ranked: list[tuple[str, int]]) -> dict[str, Any]:
    """``ranked`` places rolled up by the countries they name: each country's jobs and its top
    cities, most jobs first, ties by code, a city spelled as its commonest place writes it; the
    jobs whose place names no country, with its top places; and the jobs at places past
    :data:`MAX_PLACES_READ`, whose country was not read."""
    jobs: Counter[str] = Counter()
    places: dict[str, Counter[str]] = {}
    spelled: dict[tuple[str, str], str] = {}
    no_country: Counter[str] = Counter()
    for place, count in ranked[:MAX_PLACES_READ]:
        codes = _countries_of(place)
        for code in codes:
            jobs[code] += count
            city = _city(place, code)
            written = spelled.setdefault((code, city.casefold()), city)
            places.setdefault(code, Counter())[written] += count
        if not codes:
            no_country[place] += count
    return {
        "countries": [
            {"code": code, "jobs": count, "places": _places(most_first(places[code]))}
            for code, count in most_first(jobs)
        ],
        "no_country": {
            "jobs": sum(no_country.values()),
            "places": _places(most_first(no_country)),
        },
        "places_unread": sum(count for _, count in ranked[MAX_PLACES_READ:]),
    }


def top(table: Any, where: str, limit: int) -> dict[str, Any]:
    """The ``limit`` locations the rows ``where`` selects carry most, most first, ties by name,
    and every location's country (:func:`_by_country`).

    ``jobs`` is how many rows were counted, ``unstated`` how many of them name no location, and
    ``distinct`` how many different locations they name."""
    rows = scoped_rows(table, where, ["location"])
    counted: Counter[str] = Counter()
    for row in rows:
        if place := " ".join(str(row.get("location") or "").split()):
            counted[place] += 1
    ranked = most_first(counted)
    return {
        "jobs": len(rows),
        "unstated": len(rows) - sum(counted.values()),
        "distinct": len(counted),
        "capped": len(rows) >= MAX_ROWS,
        "locations": [
            {"location": place, "count": count} for place, count in ranked[:limit]
        ],
        **_by_country(ranked),
    }
