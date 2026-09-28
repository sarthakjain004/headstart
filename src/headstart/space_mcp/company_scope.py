"""What a typed company means to each tool — the site's two company controls, kept apart.

The site has two, with different meanings, and this server keeps both rather than inventing a
third:

- **The Search company box** is a case-folded substring of each job's served company name
  (`search_filters.compiler`). "Nvidia" finds the Eightfold "NVIDIA Corporation" and the Workday
  "Nvidia"; "Citi" also finds "Citizens". A plain name in `search_jobs` is sent as exactly that.
- **The Trends picker** offers one Company directory entry per name — the one with the most
  openings (`trends.company_suggestions.suggest`). A name in `read_trends` takes the same one,
  and only when the Space says the suggestion matches it exactly or by alias; anything looser is
  answered with the suggestions, never guessed.

A **key** — any Board key, such as ``lever:razorpay`` or the ``ats:slug`` start of a result id — is
looked up exactly (`/companies/lookup`), and stands for every Board of its company, as the
browser's Trends and Hot hand-offs do. A colon alone does not make a key: 15 of the directory's
38,673 names carry one ("dmg::media", "Ed:Za", measured 2026-09-28). So a value shaped like a key
that the directory does not hold is read as a name after all, and the answer says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from headstart.mcp_protocol.stdio import ToolFailure
from headstart.space_mcp import scraped_text
from headstart.space_mcp.space_client import InvalidRequest, SpaceClient, SpaceRoute

#: The suggestion kinds a typed name may be taken as: its own name, or a name it is an alias of
#: ("aws" → Amazon). A prefix, a word match or a typo is a guess, and is offered back instead.
_ACCEPTED_MATCHES = ("exact", "alias")


@dataclass(frozen=True)
class DirectoryCompany:
    """One Company directory entry as the Space serves it."""

    key: str
    label: str
    board_keys: tuple[str, ...]
    openings: int

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> DirectoryCompany:
        return cls(
            key=item["key"],
            label=item.get("label") or item.get("name") or item["key"],
            board_keys=tuple(item.get("board_keys") or (item["key"],)),
            openings=int(item.get("openings") or 0),
        )

    def described(self) -> str:
        boards = len(self.board_keys)
        return (
            f"{scraped_text.quoted(self.label)} ({self.key}, {boards} Board"
            f"{'' if boards == 1 else 's'}, {self.openings:,} tech openings)"
        )


@dataclass(frozen=True)
class CompanyScope:
    """What `search_jobs` sends for a company: a company-box substring, or a directory company's
    every Board. Exactly one is set."""

    substring: str | None = None
    company: DirectoryCompany | None = None
    #: How the value was read, for the answer's first line, when that is not what it looks like.
    read_as: str | None = None

    def params(self) -> list[tuple[str, str]]:
        if self.company is not None:
            return [("board", board) for board in self.company.board_keys]
        return [("company", self.substring or "")]


#: A Board key's shape: a lower-case ATS name, a colon, then a slug with no spaces.
_KEY_SHAPE = re.compile(r"[a-z][a-z0-9_]*:\S+")


def _looks_like_key(value: str) -> bool:
    """Shaped like a Board key (``ats:slug``). Only the lookup can say it is one."""
    return bool(_KEY_SHAPE.fullmatch(value))


#: Said when a value shaped like a key turned out not to be one and was read as a name.
def _not_a_key(value: str) -> str:
    return (
        f"{scraped_text.quoted(value)} is not a Board key the Company directory holds, so it "
        "was read as a company name"
    )


def lookup(client: SpaceClient, keys: list[str]) -> list[DirectoryCompany]:
    """The directory companies ``keys`` belong to, in order — any Board key, case-blind. An
    unknown key is the Space's own refusal, which names it."""
    answer = client.read(
        SpaceRoute.COMPANIES_LOOKUP, [("board", key.strip()) for key in keys]
    )
    return [DirectoryCompany.from_item(item) for item in answer.get("companies", [])]


def _suggestion_list(items: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{scraped_text.quoted(item.get('label') or item.get('name'))} — key {item['key']}, "
        f"{', '.join(item.get('atses') or [])}, {item.get('openings', 0):,} openings, "
        f"{item.get('match', '?')} match"
        for item in items
    )


def _by_name(client: SpaceClient, value: str, note: str = "") -> DirectoryCompany:
    """The directory company named ``value``, exactly or by alias, as the Trends picker offers it;
    a :class:`ToolFailure` with the suggestions otherwise. ``note`` leads any refusal."""
    answer = client.read(SpaceRoute.COMPANIES_SUGGEST, [("q", value), ("limit", "8")])
    items = answer.get("companies") or []
    if items and items[0].get("match") in _ACCEPTED_MATCHES:
        return DirectoryCompany.from_item(items[0])
    if not items:
        raise ToolFailure(
            f"{note}The Company directory has no company matching "
            f"{scraped_text.quoted(value)}. Trends reads directory companies only; "
            "search_jobs' `company` also matches any company name containing the text."
        )
    raise ToolFailure(
        f"{note}No directory company is named exactly {scraped_text.quoted(value)}. Pass one "
        f"of these keys instead: {_suggestion_list(items)}."
    )


def for_trends(client: SpaceClient, value: str) -> DirectoryCompany:
    """The directory company a typed name or key means, the way the site's Trends picker reads
    it; a :class:`ToolFailure` with the suggestions when a name is not exact."""
    value = value.strip()
    if _looks_like_key(value):
        try:
            return lookup(client, [value])[0]
        except InvalidRequest:
            return _by_name(client, value, _not_a_key(value) + ". ")
    return _by_name(client, value)


def for_search(client: SpaceClient, value: str, *, needs_boards: bool) -> CompanyScope:
    """What `search_jobs` sends for ``value``: a key's Boards; with ``needs_boards`` (a
    `category` hand-off, which names Boards) a name read as the Trends picker reads it; else the
    company box's substring, exactly as the site's Search sends it."""
    value = value.strip()
    read_as = None
    if _looks_like_key(value):
        try:
            return CompanyScope(company=lookup(client, [value])[0])
        except InvalidRequest:
            read_as = _not_a_key(value)
    if needs_boards:
        return CompanyScope(
            company=_by_name(client, value, f"{read_as}. " if read_as else ""),
            read_as=(
                "category needs a directory company, so this name was read as the directory's "
                "largest company of that name (as the site's Trends picker reads it), not as "
                "the company box's substring"
            ),
        )
    return CompanyScope(substring=value, read_as=read_as)
