"""The company a job is shown under: its served name, or its Board's Company directory name when
the served one names nothing but the Board (ADR-0323, ADR-0331).

A Board that states no company name is served under its own key or host, or under nothing:
"aah.wd5.myworkdayjobs.com/external", "egud.fa.us2.oraclecloud.com",
"https://dasstateoh.taleo.net/careersection/oh_ext", or an empty string (491 Oracle rows): 730
of the 498,460 served rows, on 72 Boards, on 2026-09-29. The Company directory, which the Trends
picker and Hot read (ADR-0185), names many of those Boards from a curated alias or the Board's
other rows ("Kotak" for `oracle:hcbt.fa.em2.oraclecloud.com`), so such a row is shown under the
directory's name, marked as the directory's. A Board the directory was asked about and does not
name is shown as naming no company, never under its host.

A served name names no company when `company_name.names_no_company` reads it as the Board's key,
URL, host or path, lowercase: "Checkout.com" on `ashby:checkout.com` is a
company, "egud.fa.us2.oraclecloud.com" on its own pod is not. Looking the directory up is a
courtesy: a Board the Space could not be asked about keeps its served name, whatever it is.

A company named like an agency and on no curated list is shown tagged "operator unverified"
(`tagged`, ADR-0352), by the rule `hiring_now`'s flag reads (ADR-0335).
"""

from __future__ import annotations

from typing import Any

from headstart.boards import board_operator
from headstart.boards.board_identity import board_of
from headstart.boards.company_name import (
    FROM_DIRECTORY,
    names_no_company,
    with_directory_name,
)
from headstart.space_mcp import company_scope, scraped_text
from headstart.space_mcp.space_client import InvalidRequest, SpaceClient, SpaceError

#: The most Boards one answer looks up: `/companies/lookup`'s own bound.
_MAX_BOARDS_LOOKED_UP = 10


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
        if names_no_company(row.get("company"), board):
            unnamed[i] = board
    if not unnamed:
        return rows
    boards = list(dict.fromkeys(unnamed.values()))[:_MAX_BOARDS_LOOKED_UP]
    labels = _directory_labels(client, boards)
    out = list(rows)
    for i, board in unnamed.items():
        if board.casefold() in labels:
            out[i] = with_directory_name(rows[i], board, labels[board.casefold()])
    return out


def said(row: dict[str, Any], limit: int) -> str:
    """The company as an answer says it: quoted, as scraped text, marked when it is the
    directory's name for the Board, and plain words when there is none."""
    if not str(row.get("company") or "").strip():
        return "no company name"
    text = scraped_text.quoted(row["company"], limit)
    return f"{text} (directory name)" if row.get(FROM_DIRECTORY) else text


#: The tag a company earns when ADR-0335's rule flags it (ADR-0352).
UNVERIFIED = "operator unverified"

#: What the tag means, said once in an answer that carries it.
UNVERIFIED_NOTE = (
    'A company tagged "operator unverified" is named like a staffing firm or recruiter and '
    "HeadStart has not checked who posts for it ('employer' is only the default): read a "
    "posting (get_job) before calling it the employer."
)


def tagged(row: dict[str, Any], board: str, limit: int) -> str:
    """:func:`said`, tagged "operator unverified" when `board_operator.unverified` flags the
    company over ``board`` and its name (ADR-0335, ADR-0352): a tag, never a filter."""
    text = said(row, limit)
    if board_operator.unverified([board], str(row.get("company") or "")):
        return f"{text} ({UNVERIFIED})"
    return text
