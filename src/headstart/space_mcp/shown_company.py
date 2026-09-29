"""The company a job is shown under: its served name, or its Board's Company directory name when
the served one names nothing but the Board (ADR-0323).

A Board that states no company name is served under its own key or host, or under nothing:
"aah.wd5.myworkdayjobs.com/external", "egud.fa.us2.oraclecloud.com",
"https://dasstateoh.taleo.net/careersection/oh_ext", or an empty string (491 Oracle rows): 730
of the 498,460 served rows, on 72 Boards, on 2026-09-29. The Company directory, which the Trends
picker and Hot read (ADR-0185), names many of those Boards from a curated alias or the Board's
other rows ("Kotak" for `oracle:hcbt.fa.em2.oraclecloud.com`), so such a row is shown under the
directory's name, marked as the directory's. A Board the directory was asked about and does not
name is shown as naming no company, never under its host.

A served name only counts as naming the Board when it is lowercase and `company_name.echoes_board`
reads it as the Board's key, URL, host or path: "Checkout.com" on `ashby:checkout.com` is a
company, "egud.fa.us2.oraclecloud.com" on its own pod is not. Looking the directory up is a
courtesy: a Board the Space could not be asked about keeps its served name, whatever it is.
"""

from __future__ import annotations

import re
from typing import Any

from headstart.boards.board_identity import board_of
from headstart.boards.company_name import echoes_board
from headstart.space_mcp import company_scope, scraped_text
from headstart.space_mcp.space_client import InvalidRequest, SpaceClient, SpaceError

#: The row key saying its ``company`` is the directory's name for its Board, not a served one.
_FROM_DIRECTORY = "company_from_directory"

#: The most Boards one answer looks up: `/companies/lookup`'s own bound.
_MAX_BOARDS_LOOKED_UP = 10

#: What a served name must hold to be read as a host, URL or path rather than a word.
_HOST_OR_PATH = re.compile(r"[./:@]")


def _names_no_company(name: Any, board: str) -> bool:
    """Whether a served company name is empty or only the Board's own key, host or path."""
    text = str(name or "").strip()
    return not text or (
        text == text.lower()
        and bool(_HOST_OR_PATH.search(text))
        and echoes_board(text, board)
    )


def _directory_labels(client: SpaceClient, boards: list[str]) -> dict[str, str | None]:
    """The directory label of each Board the Space answered for, by its key case-folded: None
    for one the directory does not hold. A Board it could not be asked about is absent. One
    lookup names up to ten; a Board the directory lacks refuses the whole lookup, so then each
    is asked alone."""
    try:
        found = company_scope.lookup(client, boards)
        asked = boards
    except InvalidRequest:
        found, asked = [], []
        for board in boards:
            try:
                found += company_scope.lookup(client, [board])
            except InvalidRequest:
                pass
            except SpaceError:
                break
            asked.append(board)
    except SpaceError:
        return {}
    labels: dict[str, str | None] = {board.casefold(): None for board in asked}
    labels.update({key.casefold(): c.label for c in found for key in c.board_keys})
    return labels


def named(client: SpaceClient, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``rows`` with each company that names no company replaced by its Board's directory name
    (marked as the directory's), or by None where the directory holds the Board and names none.
    A row whose company is a name, or whose Board was not looked up, comes back unchanged; no
    lookup is made when every row names its company."""
    unnamed: dict[int, str] = {}
    for i, row in enumerate(rows):
        board = board_of(str(row.get("id") or ""))
        if _names_no_company(row.get("company"), board):
            unnamed[i] = board
    if not unnamed:
        return rows
    boards = list(dict.fromkeys(unnamed.values()))[:_MAX_BOARDS_LOOKED_UP]
    labels = _directory_labels(client, boards)
    out = list(rows)
    for i, board in unnamed.items():
        if board.casefold() in labels:
            label = labels[board.casefold()]
            out[i] = {**rows[i], "company": label, _FROM_DIRECTORY: label is not None}
    return out


def said(row: dict[str, Any], limit: int) -> str:
    """The company as an answer says it: quoted, as scraped text, marked when it is the
    directory's name for the Board, and plain words when there is none."""
    if not str(row.get("company") or "").strip():
        return "no company name"
    text = scraped_text.quoted(row["company"], limit)
    return f"{text} (directory name)" if row.get(_FROM_DIRECTORY) else text
