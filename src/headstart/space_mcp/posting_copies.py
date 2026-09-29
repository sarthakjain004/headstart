"""Which rows of one search page are copies of one posting, so the page lists each once
(ADR-0274, widened by ADR-0323).

Two kinds of copy reach a page:

- **One posting per country** on one Board: the same company and title, brackets aside —
  "Backend Developer (Peru)", "Backend Developer (Chile)" at "Anyone AI" — placed apart.
- **One posting on two Boards** of its employer, such as a Radancy career front and the Workday
  Board behind it: the same title and place under two spellings of the company, "EVERSOURCE" and
  "Eversource Energy" (the round-2 critique, 2026-09-29). Two spellings are one company when, their
  legal suffixes dropped, one's words begin the other's; that is loose, so it also needs the
  same title stem and the same first place, the city a location string names first.

Grouping only lists a copy under the row it repeats: every row keeps its number, id and link, and
paging is the Space's.
"""

from __future__ import annotations

import re
from typing import Any

#: Words a company's name can carry or drop between two of its Boards.
_LEGAL_WORDS = frozenset(
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
        "plc",
        "gmbh",
        "ag",
        "sa",
        "bv",
        "nv",
        "pvt",
        "lp",
        "llp",
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
    return tuple(word for word in words if word not in _LEGAL_WORDS)


def _one_company(a: Any, b: Any) -> bool:
    """Two spellings of one company: one's words, legal suffixes dropped, begin the other's."""
    short, long = sorted((_company_words(a), _company_words(b)), key=len)
    return bool(short) and long[: len(short)] == short


def _first_place(location: Any) -> tuple[str, ...]:
    """The city a location names first — the words before its first comma — case-blind."""
    first = _PLACES_SEPARATOR.split(str(location or ""), maxsplit=1)[0]
    return tuple(_WORD.findall(first.split(",", 1)[0].casefold()))


def _copies(head: dict[str, Any], row: dict[str, Any]) -> bool:
    stem = title_stem(head.get("title"))
    if not stem or stem != title_stem(row.get("title")):
        return False
    one, other = head.get("company"), row.get("company")
    if not one or not other:
        # Two Boards that name no company are not thereby one company.
        return False
    if str(one).casefold().strip() == str(other).casefold().strip():
        return True
    place = _first_place(head.get("location"))
    return (
        bool(place)
        and place == _first_place(row.get("location"))
        and _one_company(one, other)
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
