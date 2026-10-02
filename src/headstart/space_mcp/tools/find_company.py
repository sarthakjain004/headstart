"""One `find_company` answer: the Company directory entries a typed name may mean (ADR-0275).

It reads the Trends picker's own suggestions (`/companies/suggest`), or looks a Board key up
exactly (`/companies/lookup`), through `company_scope.find`, and takes none of them: every
candidate is listed with its key, its Boards, its tech openings and how it matched, so the agent
and the user choose. One employer can be several entries (one per ATS with no curated alias, or a
regional arm with a Tenant of its own), which is why the list is the answer, not a pick. The
picker offers one entry per name, the largest (`company_suggestions.suggest`), and so does this.
"""

from __future__ import annotations

from typing import Any

from headstart.mcp_protocol.messages import ToolFailure
from headstart.space_mcp import company_scope, noun_counts, scraped_text
from headstart.space_mcp.space_client import SpaceClient
from headstart.space_mcp.space_tool import SpaceTool

#: How each `/companies/suggest` match reads (`company_suggestions.MATCH_KINDS`, and alias).
MATCH_WORDS = {
    "exact": "exact name",
    "alias": "a known alias of this company",
    "prefix": "the name starts with it",
    "words": "every word starts a word of the name",
    "typo": "one typo from a word of the name, or from its start",
    "joined": "the name with spaces ignored",
}

#: The matches that name the company typed: its own name, or an alias of it.
_NAMED = ("exact", "alias")

#: The matches that are a guess, to be confirmed with the user rather than relied on.
_GUESSES = ("prefix", "words", "typo", "joined")

#: The most Board keys one candidate lists; a Hyatt holds 83.
BOARDS_SHOWN = 3

#: A company name past this is cut, as search cuts one.
COMPANY_FIELD = 60


def _candidate(number: int, company: company_scope.DirectoryCompany) -> str:
    matched = (
        "matched by its Board key"
        if company.match is None
        else MATCH_WORDS.get(company.match, company.match)
    )
    shown = company.board_keys[:BOARDS_SHOWN]
    more = len(company.board_keys) - len(shown)
    boards = ", ".join(shown) + (f", …{more} more" if more > 0 else "")
    count = len(company.board_keys)
    return (
        f"{number:>2}. {scraped_text.quoted(company.label, COMPANY_FIELD)} · key {company.key} · {matched} · "
        f"{company.tech_openings()} · {noun_counts.counted(count, 'Board')} "
        f"({boards}) on {', '.join(company.atses) or 'an ATS'}"
    )


def answer(client: SpaceClient, arguments: dict[str, Any]) -> str:
    name = (arguments.get("name") or "").strip()
    if not name:
        raise ToolFailure("find_company needs `name`: the company to look up.")
    found, read_as = company_scope.find(client, name, int(arguments["limit"]))
    # Beside the name itself, a typo is another company: "Adyen" offered Adventist Health.
    if any(company.match in _NAMED for company in found):
        found = [company for company in found if company.match != "typo"]
    lines = [f"{read_as}."] if read_as else []
    if not found:
        lines.append(
            f"The Company directory has no company matching {scraped_text.quoted(name)}. It "
            "holds the companies whose Boards HeadStart counts; search_jobs' `company` also "
            "matches any company name containing the text."
        )
        return "\n".join(lines)
    lines.append(
        f"{noun_counts.counted(len(found), 'directory company', 'directory companies')} for "
        f"{scraped_text.quoted(name)}, best match first:"
    )
    lines.append(scraped_text.SCRAPED_NOTE)
    lines += [_candidate(n, company) for n, company in enumerate(found, start=1)]
    if any(company.match in _GUESSES for company in found):
        lines.append(
            "A match that is not an exact name or an alias is a guess: confirm it with the user."
        )
    lines.append(
        "One employer can be several entries, one per ATS or regional arm. Of entries with "
        "the same name only the largest is listed, as on the site; a smaller one is reached "
        "by its Board key. Pass a key as company_profile's or search_jobs' `company`, or in "
        "read_trends' `companies`."
    )
    return "\n".join(lines)


TOOL = SpaceTool(
    name="find_company",
    title="Look a company up",
    description=(
        "Look a company up in HeadStart's Company directory by name — typos, prefixes and "
        "known aliases are matched — and list every candidate with its key, its Boards (the "
        "ATS job boards it is read from), its tech openings now and how it matched. Nothing "
        "is picked: one employer can be several entries (one per ATS, or a regional arm), and "
        "a typo or prefix match is a guess to confirm with the user. Of entries with the same "
        "name only the largest is listed, as in the site's picker. A key, such as "
        "'greenhouse:stripe', names one entry exactly: pass it as `company` to company_profile "
        "or search_jobs, or in read_trends' `companies`. A Board key given as `name` is looked "
        "up exactly."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "maxLength": 100,
                "description": "The company's name, or a Board key such as 'ashby:openai'.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 8,
            },
        },
        "additionalProperties": False,
    },
    when_to_use=(
        "Use find_company to look a company up by name (typos allowed) and get the key the "
        "other tools take."
    ),
    answer=answer,
    max_chars=14_000,
)
