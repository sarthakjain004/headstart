"""The India Search filter (``india``) and its materialized ``country`` column (ADR-0138, ADR-0193).

The filter takes a canonical place from the India gazetteer (:mod:`headstart.geo`): "india"
itself, a region, or a city. Only the whole-country case is materialized — ``country = 'IN'``
replaces the ~3 KB ``regexp_like`` alternation :func:`headstart.geo.where` builds — and a city or
region keeps the gazetteer clause, whatever the table carries. The column, the value the index
writes (:func:`country`, filled through ``ingest.derived_meta``, since the embedding store's meta
carries it), and the clause :func:`headstart.search_filter_compiler.build_filter` compiles live here. The matching
itself stays in :mod:`headstart.geo`, where ``where`` and ``classify`` read the same constants.
"""

from __future__ import annotations

from collections.abc import Collection

from headstart import geo

COLUMN = "country"
#: The sentinel :func:`headstart.geo.where` uses for the whole country, as opposed to a
#: region or city key.
WHOLE_COUNTRY = "india"


def country(location: str | None) -> str | None:
    """The served ``country`` value for one row's raw location: ``"IN"`` or None."""
    return geo.classify(location)


def has_column(schema_names: Collection[str]) -> bool:
    return COLUMN in schema_names


def clause(place: str, materialized: bool) -> str | None:
    """The where-clause for one canonical place, or None for a place the gazetteer does not know.

    ``materialized`` is :func:`has_column` of the open table, carried by ``IndexCapabilities``.
    """
    if place == WHOLE_COUNTRY and materialized:
        return f"{COLUMN} = 'IN'"
    return geo.where(place)  # canonical-place lookup — unknown values are ignored
