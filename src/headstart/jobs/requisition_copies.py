"""Which served Jobs are one posting on two Boards of its employer, so a search page lists it once
(ADR-0274, widened by ADR-0323, ADR-0331 and ADR-0338, narrowed by ADR-0365) and a requirements
sample counts it once (ADR-0332).

A copy is the same title, brackets included, on another Board of the same employer, such as a
Radancy career front and the Workday Board behind it. Two rows on one Board are two postings, and
so are two titles that differ only in brackets: Capital One's "Machine Learning Engineer 5" and
"Machine Learning Engineer 5 (Senior Manager, IC)" are two requisitions (the round-5 critique,
ADR-0365). The employer is matched two ways:

- **Its name, or another spelling of it**: "EVERSOURCE" and "Eversource Energy" (the round-2
  critique, 2026-09-29). Two spellings are one company when they are the same words once legal
  forms and three generic words ("Group", "Technologies", "Energy") drop. The rows must also share
  the first place (the city a location string names first, one spelling's words all among the
  other's: "Hyderabad" and "India - Hyderabad") and the countries, as the `country` filter's
  gazetteer reads the whole location.
- **A short and a long name** of it: "TSMC" on SuccessFactors and "TSMC - Taiwan Semiconductor
  Manufacturing Company Limited" on Avature (the round-3 critique), where one name's words begin
  the other's. A longer name is as often another company ("GE" and "GE HealthCare"), so this needs
  the same countries and the same stated annual pay range, currency included, on both rows
  (ADR-0338). The first place is not compared: "Vancouver, WA, US" and "USA-Washington" are one
  place written two ways.

Rows naming no company are never copies: two unnamed Boards are not one company. A group holds at
most one row of each Board, so a company's four same-titled requisitions in one city, each on its
Workday Board and its Radancy front, are four groups of two, not one of eight.

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
#: What separates two places in one location string: "Berlin, CT; Westwood, MA".
_PLACES_SEPARATOR = re.compile(r"[;|]")


def _title(title: Any) -> str:
    """A title case- and spacing-blind, brackets kept."""
    return " ".join(str(title or "").casefold().split())


def _company_words(company: Any) -> tuple[str, ...]:
    words = _WORD.findall(str(company or "").casefold())
    return tuple(word for word in words if word not in _GENERIC_WORDS)


def _first_place(location: Any) -> tuple[str, ...]:
    """The city a location names first — the words before its first comma — case-blind."""
    first = _PLACES_SEPARATOR.split(str(location or ""), maxsplit=1)[0]
    return tuple(_WORD.findall(first.split(",", 1)[0].casefold()))


def _one_place(one: Any, other: Any) -> bool:
    """One first place, in the same countries: the same words, or one's words all among the
    other's, as a Workday Board and its Radancy front write "India - Hyderabad" and "Hyderabad,
    India" (ADR-0365)."""
    place, other_place = set(_first_place(one)), set(_first_place(other))
    return (
        bool(place)
        and bool(other_place)
        and (place <= other_place or other_place <= place)
        and country_gazetteer.classify(str(one or ""))
        == country_gazetteer.classify(str(other or ""))
    )


def copies(head: dict[str, Any], row: dict[str, Any]) -> bool:
    """Whether ``row`` is ``head``'s posting on another Board of its employer (module docstring)."""
    title = _title(head.get("title"))
    if not title or title != _title(row.get("title")) or _same_board(head, row):
        return False
    one = str(head.get("company") or "").casefold().strip()
    other = str(row.get("company") or "").casefold().strip()
    if not one or not other:
        return False
    words, other_words = _company_words(one), _company_words(other)
    if one == other or (words and words == other_words):
        return _one_place(head.get("location"), row.get("location"))
    if not words or not other_words:
        return False
    shorter, longer = sorted((words, other_words), key=len)
    return (
        longer[: len(shorter)] == shorter
        and _one_pay(head, row)
        and _same_countries(head.get("location"), row.get("location"))
    )


def joins(group: list[dict[str, Any]], row: dict[str, Any]) -> bool:
    """Whether ``row`` copies ``group``'s first row on a Board none of ``group`` is on."""
    return copies(group[0], row) and not any(_same_board(one, row) for one in group)


def _one_pay(head: dict[str, Any], row: dict[str, Any]) -> bool:
    """The same stated annual pay range, in the same currency, on both rows."""
    pay = [
        (job.get("min_salary_annual"), job.get("max_salary_annual"))
        for job in (head, row)
    ]
    return (
        pay[0] != (None, None)
        and pay[0] == pay[1]
        and head.get("salary_currency") == row.get("salary_currency")
    )


def _same_countries(one: Any, other: Any) -> bool:
    countries = country_gazetteer.classify(str(one or ""))
    return bool(countries) and countries == country_gazetteer.classify(str(other or ""))


def _same_board(head: dict[str, Any], row: dict[str, Any]) -> bool:
    return (
        board_of(str(head.get("id") or "")).casefold()
        == board_of(str(row.get("id") or "")).casefold()
    )


def groups(rows: list[dict[str, Any]]) -> list[list[int]]:
    """``rows``' indexes grouped as copies, each group led by its first row, in page order of
    those first rows. A row joins the first earlier group it `joins`."""
    found: list[list[int]] = []
    for i, row in enumerate(rows):
        for group in found:
            if joins([rows[j] for j in group], row):
                group.append(i)
                break
        else:
            found.append([i])
    return found
