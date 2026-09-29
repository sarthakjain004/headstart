"""The company a job is shown under: its served name, or its Board's Company directory name when
the served one names nothing but the Board (ADR-0323).

A Board that states no company name is served under its own key or host, or under nothing:
"aah.wd5.myworkdayjobs.com/external", "egud.fa.us2.oraclecloud.com",
"https://dasstateoh.taleo.net/careersection/oh_ext", or an empty string (491 Oracle rows): 730
of the 498,460 served rows, on 72 Boards, on 2026-09-29. The Company directory, which the Trends
picker and Hot read (ADR-0185), names many of those Boards from a curated alias or the Board's
other rows ("Kotak" for `oracle:hcbt.fa.em2.oraclecloud.com`), so such a row is shown under the
directory's name, marked as the directory's. A Board the directory does not name either is shown
as naming no company, never under its host.

A served name only counts as naming the Board when it is lowercase and `company_name.echoes_board`
reads it as the Board's key, URL, host or path: "Checkout.com" on `ashby:checkout.com` is a
company, "egud.fa.us2.oraclecloud.com" on its own pod is not. Looking the directory up is a
courtesy, so a Space that cannot answer it leaves the served names as they are.
"""

from __future__ import annotations

import re
from typing import Any

from headstart.boards.company_name import echoes_board
from headstart.space_mcp import company_scope, scraped_text
from headstart.space_mcp.space_client import InvalidRequest, SpaceClient, SpaceError

#: The row key saying its ``company`` is the directory's name for its Board, not a served one.
FROM_DIRECTORY = "company_from_directory"

#: The most Boards one answer looks up: `/companies/lookup`'s own bound.
MAX_BOARDS_LOOKED_UP = 10

#: What a served name must hold to be read as a host, URL or path rather than a word.
_HOST_OR_PATH = re.compile(r"[./:@]")


def board_of(job_id: Any) -> str:
    """The ``ats:slug`` Board key of a job id: all but its last colon-separated part."""
    return str(job_id or "").rsplit(":", 1)[0]


def names_no_company(name: Any, board: str) -> bool:
    """Whether a served company name is empty or only the Board's own key, host or path."""
    text = str(name or "").strip()
    return not text or (
        text == text.lower()
        and bool(_HOST_OR_PATH.search(text))
        and echoes_board(text, board)
    )


def _directory_labels(client: SpaceClient, boards: list[str]) -> dict[str, str]:
    """Each Board's directory label, by its key case-folded, for the Boards the directory
    holds. One lookup names up to ten; a Board the directory lacks refuses the whole lookup, so
    then each is asked alone."""
    try:
        try:
            found = company_scope.lookup(client, boards)
        except InvalidRequest:
            found = []
            for board in boards:
                try:
                    found += company_scope.lookup(client, [board])
                except InvalidRequest:
                    continue
    except SpaceError:
        return {}
    return {key.casefold(): c.label for c in found for key in c.board_keys}


def named(client: SpaceClient, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``rows`` with each company that names no company replaced by its Board's directory name
    (marked :data:`FROM_DIRECTORY`), or by None where the directory names none either. Rows
    whose company is a name come back unchanged, and no lookup is made when every row has one."""
    unnamed = [
        board_of(row.get("id"))
        for row in rows
        if names_no_company(row.get("company"), board_of(row.get("id")))
    ]
    if not unnamed:
        return rows
    boards = list(dict.fromkeys(unnamed))[:MAX_BOARDS_LOOKED_UP]
    labels = _directory_labels(client, boards)
    out = []
    for row in rows:
        board = board_of(row.get("id"))
        if not names_no_company(row.get("company"), board):
            out.append(row)
            continue
        label = labels.get(board.casefold())
        out.append({**row, "company": label, FROM_DIRECTORY: label is not None})
    return out


def said(row: dict[str, Any], limit: int) -> str:
    """The company as an answer says it: quoted, as scraped text, marked when it is the
    directory's name for the Board, and plain words when there is none."""
    if not row.get("company"):
        return "no company name"
    text = scraped_text.quoted(row["company"], limit)
    return f"{text} (directory name)" if row.get(FROM_DIRECTORY) else text
