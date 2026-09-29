"""Which served rows are copies of one posting, so a search page lists each once (ADR-0274,
widened by ADR-0323 and ADR-0331) and a requirements sample counts each once (ADR-0324).

Two kinds of copy reach a page:

- **One posting per country**: the same company and title, brackets aside — "Backend Developer
  (Peru)", "Backend Developer (Chile)" at "Anyone AI" — placed apart. Rows naming no company are
  copies only on one Board: two unnamed Boards are not one company.
- **One posting on two Boards** of its employer, such as a Radancy career front and the Workday
  Board behind it, under two spellings of the company: "EVERSOURCE" and "Eversource Energy" (the
  round-2 critique, 2026-09-29). Two spellings are one company when they are the same words once
  legal forms and three generic words ("Group", "Technologies", "Energy") drop. That is looser
  than one spelling, so it also needs the same title stem, the same first place (the city a
  location string names first) and the same countries, as the `country` filter's gazetteer reads
  the whole location.

On a search page, grouping only lists a copy under the row it repeats: every row keeps its number,
id and link, and paging is the Space's.
"""

from __future__ import annotations

import re
from typing import Any

from headstart.boards.board_identity import board_of
from headstart.search_filters import country_gazetteer

#: Words that tell no two companies apart: legal forms, and the three generic words measured
#: dropping between two Boards of one employer ("Rakuten Group", "L3Harris Technologies",
#: "Eversource Energy"). Each generic word also joins a different company to its namesake
#: ("Siemens Energy" to "Siemens"), which the same title, city and countries must then all
#: match too (ADR-0331).
_GENERIC_WORDS = frozenset(
    {
        "the",
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
        "co",
        "corp",
        "corporation",
        "company",
        "companies",
        "plc",
        "gmbh",
        "ag",
        "sa",
        "bv",
        "nv",
        "pvt",
        "lp",
        "llp",
        "group",
        "technologies",
        "energy",
    }
)

_WORD = re.compile(r"[^\W_]+")
_BRACKETED = re.compile(r"\([^)]*\)|\[[^\]]*\]")
#: What separates two places in one location string: "Berlin, CT; Westwood, MA".
_PLACES_SEPARATOR = re.compile(r"[;|]")


def title_stem(title: Any) -> str:
    """A title case-blind with its bracketed parts dropped: what per-country copies share."""
    return " ".join(_BRACKETED.sub(" ", str(title or "")).lower().split())


def _company_words(company: Any) -> tuple[str, ...]:
    words = _WORD.findall(str(company or "").casefold())
    return tuple(word for word in words if word not in _GENERIC_WORDS)


def _first_place(location: Any) -> tuple[str, ...]:
    """The city a location names first — the words before its first comma — case-blind."""
    first = _PLACES_SEPARATOR.split(str(location or ""), maxsplit=1)[0]
    return tuple(_WORD.findall(first.split(",", 1)[0].casefold()))


def _one_place(one: Any, other: Any) -> bool:
    """The same first city, in the same countries."""
    place = _first_place(one)
    return (
        bool(place)
        and place == _first_place(other)
        and country_gazetteer.classify(str(one or ""))
        == country_gazetteer.classify(str(other or ""))
    )


def _copies(head: dict[str, Any], row: dict[str, Any]) -> bool:
    stem = title_stem(head.get("title"))
    if not stem or stem != title_stem(row.get("title")):
        return False
    one = str(head.get("company") or "").casefold().strip()
    other = str(row.get("company") or "").casefold().strip()
    if not one or not other:
        return not one and not other and _same_board(head, row)
    if one == other:
        return True
    words = _company_words(one)
    return (
        bool(words)
        and words == _company_words(other)
        and _one_place(head.get("location"), row.get("location"))
    )


def _same_board(head: dict[str, Any], row: dict[str, Any]) -> bool:
    return (
        board_of(str(head.get("id") or "")).casefold()
        == board_of(str(row.get("id") or "")).casefold()
    )


def groups(rows: list[dict[str, Any]]) -> list[list[int]]:
    """``rows``' indexes grouped as copies, each group led by its first row, in page order of
    those first rows. A row joins the first earlier group whose first row it copies."""
    found: list[list[int]] = []
    for i, row in enumerate(rows):
        for group in found:
            if _copies(rows[group[0]], row):
                group.append(i)
                break
        else:
            found.append([i])
    return found
