"""Which served Jobs are copies of one requisition: a search page lists **one posting on two
Boards** once (ADR-0274, widened by ADR-0323, ADR-0331 and ADR-0338, narrowed by ADR-0365), and a
requirements sample counts **one requisition** once, wherever it was posted (ADR-0332, kept by
ADR-0370). The second is the wider: every posting on two Boards is one requisition, and so is one
requisition posted per country.

**One posting on two Boards** (:func:`one_posting`) is the same title, brackets included, on
another Board of the same employer, such as a Radancy career front and the Workday Board behind
it. Two rows on one Board are two postings, and so are two titles that differ only in brackets:
Capital One's "Machine Learning Engineer 5" and "Machine Learning Engineer 5 (Senior Manager, IC)"
are two requisitions (the round-5 critique, ADR-0365). The employer is matched two ways:

- **Its name, or another spelling of it**: "EVERSOURCE" and "Eversource Energy" (the round-2
  critique, 2026-09-29). Two spellings are one company when they are the same words once legal
  forms and three generic words ("Group", "Technologies", "Energy") drop. The rows must also share
  the first place (the city a location string names first) and the countries, as the `country`
  filter's gazetteer reads the whole location. One first place is the same words in any order, or
  one's words a run of the other's ("Hyderabad" in "India - Hyderabad", ADR-0365) that no word
  beginning a place's name leads: "York" in "New York" is another place (ADR-0370).
- **A short and a long name** of it: "TSMC" on SuccessFactors and "TSMC - Taiwan Semiconductor
  Manufacturing Company Limited" on Avature (the round-3 critique), where one name's words begin
  the other's. A longer name is as often another company ("GE" and "GE HealthCare"), so this needs
  the same countries and the same stated annual pay range, currency included, on both rows
  (ADR-0338). The first place is not compared: "Vancouver, WA, US" and "USA-Washington" are one
  place written two ways.

Rows naming no company are never one posting: two unnamed Boards are not one company. A group of
postings holds at most one row of each Board, so a company's four same-titled requisitions in one
city, each on its Workday Board and its Radancy front, are four groups of two, not one of eight.

**One requisition** (:func:`one_requisition`) is also the same company, spelled alike, and the
same title with its bracketed parts dropped, placed anywhere and on any of its Boards: "Backend
Developer (Peru)" and "Backend Developer (Chile)" at "Anyone AI" (ADR-0274), or one Board's
"DevOps Engineer" in five countries. Rows naming no company are one requisition only on one Board.
A requirements sample counts what employers ask for, and one requisition's description posted
per country is one employer's text, however many rows carry it (ADR-0332).

On a search page, grouping only lists a copy under the row it repeats: every row keeps its number,
id and link, and paging is the Space's.
"""

from __future__ import annotations

import re
from collections.abc import Callable
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

#: Words that begin a place's name and make it another place: "New York" is not "York", "East
#: Naples" not "Naples", "North Amityville" not "Amityville" (ADR-0370).
_PLACE_NAME_LEADS = frozenset(
    {
        "new",
        "old",
        "east",
        "west",
        "north",
        "south",
        "upper",
        "lower",
        "great",
        "little",
        "port",
        "fort",
        "mount",
        "lake",
        "saint",
        "st",
        "san",
        "santa",
    }
)

_WORD = re.compile(r"[^\W_]+")
_BRACKETED = re.compile(r"\([^)]*\)|\[[^\]]*\]")
#: What separates two places in one location string: "Berlin, CT; Westwood, MA".
_PLACES_SEPARATOR = re.compile(r"[;|]")


def _title(title: Any) -> str:
    """A title case- and spacing-blind, brackets kept."""
    return " ".join(str(title or "").casefold().split())


def _title_stem(title: Any) -> str:
    """A title case-blind with its bracketed parts dropped: what one requisition's postings per
    country share."""
    return _title(_BRACKETED.sub(" ", str(title or "")))


def _name(row: dict[str, Any]) -> str:
    return str(row.get("company") or "").casefold().strip()


def _company_words(company: Any) -> tuple[str, ...]:
    words = _WORD.findall(str(company or "").casefold())
    return tuple(word for word in words if word not in _GENERIC_WORDS)


def _first_place(location: Any) -> tuple[str, ...]:
    """The city a location names first — the words before its first comma — case-blind."""
    first = _PLACES_SEPARATOR.split(str(location or ""), maxsplit=1)[0]
    return tuple(_WORD.findall(first.split(",", 1)[0].casefold()))


def _named_within(shorter: tuple[str, ...], longer: tuple[str, ...]) -> bool:
    """Whether ``shorter``'s words run within ``longer``'s, not led by a word that begins a place's
    name: "Hyderabad" in "India - Hyderabad", never "York" in "New York"."""
    n = len(shorter)
    return any(
        longer[i : i + n] == shorter
        and (i == 0 or longer[i - 1] not in _PLACE_NAME_LEADS)
        for i in range(len(longer) - n + 1)
    )


def _one_place(one: Any, other: Any) -> bool:
    """One first place, in the same countries (module docstring)."""
    place, other_place = _first_place(one), _first_place(other)
    shorter, longer = sorted((place, other_place), key=lambda words: len(set(words)))
    return (
        bool(shorter)
        and (
            set(place) == set(other_place)
            # Each word once: "Tokyo - Tokyo" runs within "Ariake - Tokyo".
            or _named_within(tuple(dict.fromkeys(shorter)), longer)
        )
        and country_gazetteer.classify(str(one or ""))
        == country_gazetteer.classify(str(other or ""))
    )


def _one_employer_here(head: dict[str, Any], row: dict[str, Any]) -> bool:
    """Whether two rows are one employer's in one place: its name or another spelling in one
    first place, or a short and a long name with the same countries and stated pay."""
    one, other = _name(head), _name(row)
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


def one_posting(head: dict[str, Any], row: dict[str, Any]) -> bool:
    """Whether ``row`` is ``head``'s posting on another Board of its employer (module docstring)."""
    title = _title(head.get("title"))
    if not title or title != _title(row.get("title")) or _same_board(head, row):
        return False
    return _one_employer_here(head, row)


def one_requisition(head: dict[str, Any], row: dict[str, Any]) -> bool:
    """Whether ``row`` is a posting of ``head``'s requisition: one posting on two Boards, or the
    same company spelled alike and the same title, brackets aside, placed anywhere (module
    docstring)."""
    stem = _title_stem(head.get("title"))
    if not stem or stem != _title_stem(row.get("title")):
        return False
    one, other = _name(head), _name(row)
    if not one or not other:
        return not one and not other and _same_board(head, row)
    return one == other or _one_employer_here(head, row)


def joins(group: list[dict[str, Any]], row: dict[str, Any]) -> bool:
    """Whether ``row`` is ``group``'s first row's posting on a Board none of ``group`` is on."""
    return one_posting(group[0], row) and not any(
        _same_board(one, row) for one in group
    )


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


def _groups(
    rows: list[dict[str, Any]],
    joined: Callable[[list[dict[str, Any]], dict[str, Any]], bool],
) -> list[list[int]]:
    found: list[list[int]] = []
    for i, row in enumerate(rows):
        for group in found:
            if joined([rows[j] for j in group], row):
                group.append(i)
                break
        else:
            found.append([i])
    return found


def posting_groups(rows: list[dict[str, Any]]) -> list[list[int]]:
    """``rows``' indexes grouped as one posting on several Boards, each group led by its first
    row, in page order of those first rows. A row joins the first earlier group it `joins`."""
    return _groups(rows, joins)


def requisition_groups(rows: list[dict[str, Any]]) -> list[list[int]]:
    """``rows``' indexes grouped as one requisition, each group led by its first row, in page
    order of those first rows. A row joins the first earlier group whose first row's requisition
    it is a posting of (:func:`one_requisition`)."""
    return _groups(rows, lambda group, row: one_requisition(group[0], row))
