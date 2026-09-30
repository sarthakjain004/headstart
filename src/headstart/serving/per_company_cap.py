"""At most ``per_company`` rows of one company before every other company's closer matches, on
a relevance-ranked search (``/search?per_company=``, ADR-0352).

A ranked page is the query's closest matches in order, and one employer with many near-identical
postings fills it: Reflection held 9 of 10 rows of a London staff-platform search, Capital One 15
of 20 of a new-grad one (round-4 critique, P1-5). :func:`spread` re-orders the ranked window so
each company's first ``per_company`` rows keep their places and its others follow every
company's kept rows, still in similarity order. Nothing is dropped, so the total and the rows a
page can reach are the search's own, and the order is one fixed list over the window, so every
page cuts the same list.

A company is its name case-folded, or its Board when it names none (:func:`company`, which a
requirements sample's employer counts use too, `requirement_counts`). Every row counts toward its
company's ``per_company``, since every row fills a place on the page (ADR-0365). A row that is a
kept row's posting on another of its company's Boards (`requisition_copies.joins`) is kept with it
even past the cap, as the page lists it under that row: a company takes a new posting only while
it holds fewer than ``per_company`` rows, and a posting it took is never split from its copies.

Each kept row of a company with held rows carries ``more_from_company``, how many; each held row
carries ``past_company_cap``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from headstart.boards.board_identity import board_of
from headstart.jobs import requisition_copies

#: Marks a kept row of a company whose other matches follow later: how many.
MORE_FROM_COMPANY = "more_from_company"
#: Marks a row moved after the kept rows.
PAST_COMPANY_CAP = "past_company_cap"


def company(row: Mapping[str, Any]) -> str:
    """Who a row is at: its company case-folded, or its Board when it names none, so rows naming
    no company are not all one employer. The Board is the row's ``board`` when it carries one (a
    requirements sample's, the Company directory's), else `board_of` its id. The one employer key
    of a per-company cap, a requirements sample's employer counts and a search page's held line."""
    name = " ".join(str(row.get("company") or "").split()).casefold()
    return (
        name or str(row.get("board") or board_of(str(row.get("id") or ""))).casefold()
    )


def spread(rows: list[dict[str, Any]], per_company: int) -> list[dict[str, Any]]:
    """``rows``, ranked, with each company's rows past its first ``per_company`` moved after
    every kept row, marked (the module docstring)."""
    postings: dict[str, list[list[dict[str, Any]]]] = {}
    kept: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    for row in rows:
        mine = postings.setdefault(company(row), [])
        posting = next((p for p in mine if requisition_copies.joins(p, row)), None)
        if posting is not None:
            posting.append(row)
            kept.append(row)
        elif sum(map(len, mine)) < per_company:
            mine.append([row])
            kept.append(row)
        else:
            held.append(row)
    more = Counter(company(row) for row in held)
    for row in kept:
        if n := more.get(company(row)):
            row[MORE_FROM_COMPANY] = n
    for row in held:
        row[PAST_COMPANY_CAP] = True
    return kept + held
