"""One `company_profile` answer: one Company directory entry's hiring, read from its own routes.

The company is resolved exactly as `read_trends` resolves one (`company_scope.for_trends`): a
Board key, or a name the directory holds exactly or by alias; anything looser is refused with the
suggestions. Then, at once (ADR-0275):

- `/facets` with ``board=`` for each of its Boards — the scope `search_jobs` sends for a key — for
  how many of its served jobs are remote, of each employment type, open at each experience
  ceiling, stating a salary, and posted or new recently;
- `/trends?company=<key>` over the trailing :data:`TREND_DAYS` days, split by job category (the
  default split for one company), for its tech openings now, its category mix, and the postings
  opened and closed. Those lead: the change in openings also moves when HeadStart re-counts, so
  it follows them with its re-counted part named;
- `/companies/locations` over its Boards, for the places its served jobs name most;
- for a name, `/companies/suggest` again, to name the other directory companies it may also mean
  — Deloitte is five, and the exact name is not the largest.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from headstart.mcp_protocol.messages import ToolFailure
from headstart.space_mcp import company_scope, scraped_text
from headstart.space_mcp.space_client import SpaceClient, SpaceRoute
from headstart.space_mcp.space_tool import SpaceTool

#: The trailing window the trend is read over.
TREND_DAYS = 30

#: How many job categories, locations, other directory companies and Board keys the answer lists.
CATEGORIES_SHOWN = 12
LOCATIONS_SHOWN = 10
OTHERS_SHOWN = 3
BOARDS_SHOWN = 10

#: A location or company name past this is cut, as search cuts one.
SHORT_FIELD = 60

#: The facet options the breakdown reads, by the Space's dimension name and option value, in the
#: words the answer uses; the site's labels ("Entry level") are not this tool's arguments.
_POSTED = {1: "24 hours", 7: "7 days", 30: "30 days", 90: "90 days"}
_NEW = {24: "24 hours", 168: "7 days"}


def _now() -> datetime:
    """Now, in UTC: where the trend window is counted back from. Its own function so a test can
    pin it to its fixture's ticks."""
    return datetime.now(UTC)


def _options(facets: dict[str, Any], dimension: str) -> dict[Any, int]:
    """One facet dimension's counts by option value, its "Any" row left out."""
    return {
        option.get("value"): int(option.get("count") or 0)
        for option in (facets.get("facets") or {}).get(dimension) or []
        if option.get("value") is not None
    }


def _breakdown(facets: dict[str, Any]) -> list[str]:
    total = int(facets.get("total") or 0)
    lines = [
        (
            f"Of the {total:,} jobs search serves on its Boards (each count on its own; "
            "search_jobs with the key and that filter lists them):"
        )
    ]
    if remote := _options(facets, "remote"):
        lines.append(f"  remote: {remote.get(True, 0):,}")
    if types := _options(facets, "etype"):
        lines.append(
            "  employment type: "
            + " · ".join(f"{value} {count:,}" for value, count in types.items())
            + " (a job whose Board states no type counts in none)"
        )
    if ceilings := _options(facets, "max_years"):
        lines.append(
            "  open to someone with at most: "
            + " · ".join(
                f"{value} years {count:,}" for value, count in ceilings.items()
            )
            + " (a job stating no experience counts at every level)"
        )
    if salary := _options(facets, "has_salary"):
        lines.append(f"  salary stated: {salary.get(True, 0):,}")
    if posted := _options(facets, "posted_within"):
        lines.append(
            "  posted by the employer in the last: "
            + " · ".join(
                f"{_POSTED.get(value, f'{value} days')} {count:,}"
                for value, count in posted.items()
            )
        )
    if seen := _options(facets, "seen_within"):
        lines.append(
            "  new to HeadStart in the last: "
            + " · ".join(
                f"{words} {seen[hours]:,}"
                for hours, words in _NEW.items()
                if hours in seen
            )
        )
    return lines


def _turnover(move: dict[str, Any], since: str | None, window_from: str) -> str:
    turnover = move.get("turnover")
    if not turnover:
        return "postings opened and closed were not counted in this window"
    closed = turnover.get("closed")
    said = f"{turnover['opened']:,} postings opened and " + (
        f"{closed:,} closed" if closed is not None else "closures not counted"
    )
    if since and since > window_from:
        said += f" (counted since {since[:10]})"
    return said


def _trend(payload: dict[str, Any], key: str) -> list[str]:
    reading = payload.get("reading")
    if reading is None:
        why = payload.get("reading_error") or "no reason given"
        return [f"No trend: the Space could not read this company's counts ({why})."]
    window = reading.get("window")
    total = reading.get("total")
    if not window or not total:
        return [f"No trend counts for this company in the last {TREND_DAYS} days."]
    move = total["move"]
    change = move["latest"] - move["start"]
    recounted = move.get("not_hiring_total") or 0
    non_tech = reading.get("non_tech_jobs")
    lines = [
        f"Tech openings now: {reading.get('openings', move['latest']):,}, as Trends counts them"
        + (
            f"; search also serves {non_tech:,} jobs on its Boards that the tech filter sets "
            "aside."
            if non_tech
            else "."
        ),
        f"Recent hiring, {window['from'][:10]} → {window['to'][:10]}: "
        + _turnover(move, payload.get("turnover_since"), window["from"])
        + f". Tech openings counted {move['start']:,} → {move['latest']:,} ({change:+,})"
        + (
            f", {recounted:+,} of it re-counting by HeadStart, not hiring"
            if recounted
            else ""
        )
        + f"; read_trends with companies [{key}] breaks the change down.",
    ]
    # A hidden family's line comes last as "Other" (ADR-0306), so the mix still adds up.
    unlisted = set(payload.get("unlisted_series") or ())
    categories = sorted(
        (line for line in reading.get("lines") or [] if line["move"]["latest"] > 0),
        key=lambda line: (line["name"] in unlisted, -line["move"]["latest"]),
    )
    if categories:
        shown = categories[:CATEGORIES_SHOWN]
        more = len(categories) - len(shown)
        lines.append(
            "Job categories now, largest first: "
            + " · ".join(_category(line) for line in shown)
            + (f" · …{more} more" if more > 0 else "")
            + "."
        )
    return lines


def _category(line: dict[str, Any]) -> str:
    move = line["move"]
    said = f"{line.get('label')} {move['latest']:,}"
    turnover = move.get("turnover") or {}
    opened, closed = turnover.get("opened") or 0, turnover.get("closed") or 0
    if opened or closed:
        said += f" ({opened:,} opened, {closed:,} closed)"
    return said


def _locations(answer: dict[str, Any]) -> str:
    places = answer.get("locations") or []
    jobs = int(answer.get("jobs") or 0)
    if not places:
        return f"Locations: none of its {jobs:,} served jobs names one."
    return (
        f"Top locations of its {jobs:,} served jobs, as the employer wrote them "
        f"({answer.get('distinct', 0):,} distinct, {answer.get('unstated', 0):,} naming none"
        + ("; the first rows only" if answer.get("capped") else "")
        + "): "
        + " · ".join(
            f"{scraped_text.quoted(place['location'], SHORT_FIELD)} {place['count']:,}"
            for place in places
        )
        + "."
    )


def _company_lines(
    pick: company_scope.DirectoryCompany,
    by_name: bool,
    others: list[company_scope.DirectoryCompany],
) -> list[str]:
    lines = [f"Company: {pick.described()}."]
    if by_name:
        lines[0] += (
            " A name is read as the directory's largest company of that name, as the site's "
            "Trends picker reads it."
        )
    if others:
        lines.append(
            "Other directory companies the name may mean (find_company lists them all): "
            + "; ".join(other.offered() for other in others)
            + "."
        )
    shown = pick.board_keys[:BOARDS_SHOWN]
    more = len(pick.board_keys) - len(shown)
    lines.append(
        "Its Boards: "
        + ", ".join(shown)
        + (f", …{more} more" if more > 0 else "")
        + "."
    )
    return lines


def answer(client: SpaceClient, arguments: dict[str, Any]) -> str:
    value = (arguments.get("company") or "").strip()
    if not value:
        raise ToolFailure(
            "company_profile needs `company`: a key from find_company, or an exact name."
        )
    pick = company_scope.for_trends(client, value)
    by_name = value.lower() not in {board.lower() for board in pick.board_keys}
    boards = [("board", board) for board in pick.board_keys]
    since = (_now() - timedelta(days=TREND_DAYS)).isoformat(timespec="seconds")
    with ThreadPoolExecutor(max_workers=4) as pool:
        facets = pool.submit(client.read, SpaceRoute.FACETS, [("strict", "1"), *boards])
        trends = pool.submit(
            client.read,
            SpaceRoute.TRENDS,
            [("since", since), ("company", pick.key)],
        )
        places = pool.submit(
            client.read,
            SpaceRoute.COMPANIES_LOCATIONS,
            [*boards, ("limit", str(LOCATIONS_SHOWN))],
        )
        similar = (
            pool.submit(company_scope.suggest, client, value, OTHERS_SHOWN + 1)
            if by_name
            else None
        )
        facets, trends, places = facets.result(), trends.result(), places.result()
        others = [c for c in (similar.result() if similar else []) if c.key != pick.key]
    lines = _company_lines(pick, by_name, others[:OTHERS_SHOWN])
    lines.append(scraped_text.SCRAPED_NOTE)
    lines += _trend(trends, pick.key)
    lines.append(_locations(places))
    lines += _breakdown(facets)
    lines.append(
        f"To list its jobs: search_jobs with company {pick.key} (add category for one job "
        "category)."
    )
    if tick := facets.get("newest_tick"):
        lines.append(f"Data as of the trends tick {tick}.")
    return "\n".join(lines)


TOOL = SpaceTool(
    name="company_profile",
    title="One company's hiring profile",
    description=(
        "One company's hiring profile: its tech openings now; postings opened and closed "
        "recently; its job-category mix; the places its jobs name most; and how many of its "
        "jobs are remote, of each employment type, open at each experience level, show a "
        "salary, and were posted recently. `company` is a directory company: a key from "
        "find_company (such as 'greenhouse:stripe') or its exact name, read as the site's "
        "Trends picker reads it. Tell the user which company and Boards it was read as, and "
        "name the other companies the answer says the name may mean. Locations are as each "
        "employer wrote them, not merged, and a job stating no experience counts at every "
        "level."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "maxLength": 100,
                "description": (
                    "A key from find_company, such as 'greenhouse:stripe', or the company's "
                    "exact name."
                ),
            },
        },
        "additionalProperties": False,
    },
    when_to_use=(
        "Use company_profile for one company's hiring: openings, categories, locations, "
        "levels and remote share."
    ),
    answer=answer,
    max_chars=8_000,
)
