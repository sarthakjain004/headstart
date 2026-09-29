"""The ``country`` Search filter: one ISO 3166-1 alpha-2 code, matched on ``location`` (ADR-0273).

The places live in :mod:`headstart.search_filters.country_gazetteer`; this module is the filter's
face: the codes it accepts, the labels the page and the MCP tool show, and the clause
:func:`headstart.search_filters.compiler.build_filter` compiles. India is the India filter's
whole-country rule (:func:`headstart.search_filters.india_filter.clause`), materialized column
and all, so ``country=IN`` and ``india=india`` always return the same rows.
"""

from __future__ import annotations

from headstart.search_filters import country_gazetteer, india_filter

INDIA = "IN"

#: Every code the filter accepts, by served Jobs (measured 2026-09-29): India second, after the
#: United States, as the served table counts them.
CODES: tuple[str, ...] = (
    "US",
    INDIA,
    *(code for code in country_gazetteer.COUNTRIES if code != "US"),
)

_NAMES = {code: c.name for code, c in country_gazetteer.COUNTRIES.items()} | {
    INDIA: "India"
}


def name(code: str) -> str:
    """The country's English name, for a label or an answer line."""
    return _NAMES[code]


def options() -> list[tuple[str, str]]:
    """(code, name) pairs in :data:`CODES` order: the page's dropdown and the MCP enum."""
    return [(code, _NAMES[code]) for code in CODES]


#: What a caller may write for a country besides its code or its English name (ADR-0322): the
#: abbreviations people use that are not the ISO code ("UK" is GB's, "UAE" AE's), and the names
#: :func:`name` spells otherwise. Keys are :func:`_spelling` forms.
_ALIASES = {
    "uk": "GB",
    "greatbritain": "GB",
    "britain": "GB",
    "usa": "US",
    "unitedstatesofamerica": "US",
    "uae": "AE",
    "emirates": "AE",
    "ksa": "SA",
    "korea": "KR",
    "republicofkorea": "KR",
    "czechrepublic": "CZ",
    "turkey": "TR",
    "holland": "NL",
    "thenetherlands": "NL",
}


def _spelling(text: str) -> str:
    """``text`` case-folded with everything but letters dropped: "U.S.A." and "usa" are one."""
    return "".join(char for char in text.casefold() if char.isalpha())


_BY_SPELLING = (
    {_spelling(code): code for code in CODES}
    | {_spelling(country): code for code, country in _NAMES.items()}
    | _ALIASES
)


def code_for(asked: str) -> str | None:
    """The code ``asked`` means: a code in any case ("gb"), an English name ("Germany"), or a
    common abbreviation ("UK", "USA", "UAE"); None for anything else."""
    return _BY_SPELLING.get(_spelling(asked))


def clause(code: str, materialized: bool) -> str | None:
    """The where-clause for one code, or None for a code the filter does not know.

    ``materialized`` is :func:`headstart.search_filters.india_filter.has_column` of the open
    table, which only India's rule reads.
    """
    if code == INDIA:
        return india_filter.clause(india_filter.WHOLE_COUNTRY, materialized)
    return country_gazetteer.where(code)
