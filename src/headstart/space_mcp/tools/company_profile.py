"""One `company_profile` answer: one Company directory entry's hiring, or several rolled up into
one employer's (ADR-0338), read from their own routes.

Each company is resolved exactly as `read_trends` resolves one (`company_scope.for_trends`): a
Board key, or a name the directory holds exactly or by alias; anything looser is refused with the
suggestions. Several — every "Deloitte" entry, say — are read as one scope: their Boards together,
so each served job counts once, with each entry listed. Then, at once (ADR-0275):

- `/facets` with ``board=`` for each of its Boards — the scope `search_jobs` sends for a key — for
  how many of its served jobs are remote, of each employment type, stating a salary, and posted
  or new recently; a line whose every count is 0 is left out;
- `/trends?company=<key>` over the trailing :data:`TREND_DAYS` days, split by job category (the
  default split for one company), for its tech openings now, its category mix, and the postings
  opened and closed. Those lead: the change in openings also moves when HeadStart re-counts, so
  it follows them with its re-counted part named;
- `/companies/locations` over its Boards, for the countries its served jobs name, each with its
  top places (a place's first part, its spellings merged), by the `country` filter's own
  gazetteer (ADR-0323, ADR-0331);
- `/companies/levels` over its Boards, for how many of its served jobs are in each Trends level
  band, each counted once (ADR-0323) — not the Search rail's experience ceilings, where a job
  stating no experience counts at every one;
- for a name, `/companies/suggest` again, to name the other directory companies it may also mean
  — Deloitte is five, and the exact name is not the largest;
- `/facets` again with `search_jobs`' default `max_age_days`, for how many of its served jobs are
  over a year old, which `search_jobs` leaves out by default; when some are, the same two counts
  per listed job category, so a category's count here can be matched to `search_jobs`' (ADR-0338).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from headstart.mcp_protocol.messages import ToolFailure
from headstart.search_filters import country_filter
from headstart.space_mcp import company_scope, scraped_text, search_arguments
from headstart.space_mcp.space_client import SpaceClient, SpaceError, SpaceRoute
from headstart.space_mcp.space_tool import SpaceTool

#: The trailing window the trend is read over.
TREND_DAYS = 30

#: How many job categories, countries, other directory companies and Board keys the answer lists.
CATEGORIES_SHOWN = 12
COUNTRIES_SHOWN = 8
OTHERS_SHOWN = 3
BOARDS_SHOWN = 10

#: The most directory companies one profile rolls up, and the most Boards `/companies/locations`
#: and `/companies/levels` take in one request.
COMPANIES_ROLLED_UP = 10
BOARDS_READ = 200

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


def _counts_line(
    title: str, counts: int | dict[str, int], note: str = ""
) -> str | None:
    """One breakdown line — one count, or a count per label — or None when every count on it is
    0. A job that states no employment type counts as full-time (ADR-0341)."""
    if isinstance(counts, int):
        return f"  {title}: {counts:,}{note}" if counts else None
    if not any(counts.values()):
        return None
    said = " · ".join(f"{label} {count:,}" for label, count in counts.items())
    return f"  {title}: {said}{note}"


def _breakdown(facets: dict[str, Any], levels: dict[str, Any]) -> list[str]:
    total = int(facets.get("total") or 0)
    posted = _options(facets, "posted_within")
    seen = _options(facets, "seen_within")
    lines = [
        _counts_line("remote", _options(facets, "remote").get(True, 0)),
        _counts_line(
            "employment type",
            {str(value): count for value, count in _options(facets, "etype").items()},
            " (a job that states no type counts as full-time)",
        ),
        _counts_line(
            "level, each job once, in the Trends Level view's bands"
            + (" (the first rows only)" if levels.get("capped") else ""),
            {band["label"]: band["count"] for band in levels.get("bands") or []},
        ),
        _counts_line("salary stated", _options(facets, "has_salary").get(True, 0)),
        _counts_line(
            "posted by the employer in the last",
            {
                _POSTED.get(days, f"{days} days"): count
                for days, count in posted.items()
            },
        ),
        _counts_line(
            "new to HeadStart in the last",
            {words: seen[hours] for hours, words in _NEW.items() if hours in seen},
        ),
    ]
    return [
        (
            f"Of the {total:,} jobs search serves on its Boards (each count on its own, a line "
            "whose every count is 0 left out; search_jobs with the key and that filter lists "
            "them):"
        ),
        *(line for line in lines if line),
    ]


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


def _shown_categories(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The job categories the answer lists, largest first: a hidden family's line comes last as
    "Other" (ADR-0306), so the mix still adds up."""
    reading = payload.get("reading") or {}
    unlisted = set(payload.get("unlisted_series") or ())
    categories = sorted(
        (line for line in reading.get("lines") or [] if line["move"]["latest"] > 0),
        key=lambda line: (line["name"] in unlisted, -line["move"]["latest"]),
    )
    return categories


def _trend(payload: dict[str, Any], keys: str, old: dict[str, int]) -> list[str]:
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
        + f"; read_trends with companies [{keys}] breaks the change down.",
    ]
    if categories := _shown_categories(payload):
        shown = categories[:CATEGORIES_SHOWN]
        more = len(categories) - len(shown)
        lines.append(
            "Job categories now, largest first: "
            + " · ".join(_category(line, old) for line in shown)
            + (f" · …{more} more" if more > 0 else "")
            + "."
        )
    return lines


def _category(line: dict[str, Any], old: dict[str, int]) -> str:
    move = line["move"]
    said = f"{line.get('label')} {move['latest']:,}"
    turnover = move.get("turnover") or {}
    opened, closed = turnover.get("opened") or 0, turnover.get("closed") or 0
    notes = [f"{opened:,} opened, {closed:,} closed"] if opened or closed else []
    if old.get(line["name"]):
        notes.append(f"{old[line['name']]:,} over a year old")
    return said + (f" ({'; '.join(notes)})" if notes else "")


def _places(places: list[dict[str, Any]]) -> str:
    return " · ".join(
        f"{scraped_text.quoted(place['location'], SHORT_FIELD)} {place['count']:,}"
        for place in places
    )


def _country(code: str) -> str:
    return country_filter.name(code) if code in country_filter.CODES else code


def _locations(answer: dict[str, Any]) -> str:
    jobs = int(answer.get("jobs") or 0)
    countries = answer.get("countries") or []
    no_country = answer.get("no_country") or {}
    if not countries and not no_country.get("jobs"):
        return f"Locations: none of its {jobs:,} served jobs names one."
    said = f"Where its {jobs:,} served jobs are"
    if countries:
        shown = countries[:COUNTRIES_SHOWN]
        more = len(countries) - len(shown)
        said += (
            ", by country as search_jobs' `country` reads each place (a job naming two "
            "countries counts in both), with its top places, a first place's spellings merged"
            + (" (the first rows only)" if answer.get("capped") else "")
            + ": "
            + " · ".join(
                f"{_country(c['code'])} {c['jobs']:,} ({_places(c['places'])})"
                for c in shown
            )
            + (f" · …{more} more countries" if more > 0 else "")
            + "."
        )
    else:
        said += ": no place names a country the `country` filter reads."
    if no_country.get("jobs"):
        said += (
            f" No country is read from the places of {no_country['jobs']:,} "
            f"({_places(no_country.get('places') or [])})."
        )
    if unstated := int(answer.get("unstated") or 0):
        said += f" {unstated:,} name no place."
    if unread := int(answer.get("places_unread") or 0):
        said += f" {unread:,} are at places too rare to be read."
    return said


def _company_lines(
    picks: list[company_scope.DirectoryCompany],
    boards: list[str],
    by_name: bool,
    others: list[company_scope.DirectoryCompany],
) -> list[str]:
    if len(picks) == 1:
        lines = [f"Company: {picks[0].described()}."]
    else:
        lines = [
            (
                f"Companies rolled up as one employer: {len(picks)} directory companies, "
                f"{len(boards):,} Boards, {sum(p.openings for p in picks):,} tech openings "
                "in all. Every count below is over their Boards together, so each served "
                "job counts once; a posting listed on two of their Boards counts on each:"
            ),
            *(f"  {pick.described()}" for pick in picks),
        ]
    if by_name:
        lines[0] += (
            " A name is read as the directory's largest company of that name, as the site's "
            "Trends picker reads it."
        )
    if others:
        lines.append(
            "Other directory companies the name may mean (find_company lists them all; send "
            "several keys as `companies` for one employer's total): "
            + "; ".join(other.offered() for other in others)
            + "."
        )
    shown = boards[:BOARDS_SHOWN]
    more = len(boards) - len(shown)
    lines.append(
        f"{'Its' if len(picks) == 1 else 'Their'} Boards: "
        + ", ".join(shown)
        + (f", …{more} more" if more > 0 else "")
        + "."
    )
    return lines


def _picks(
    client: SpaceClient, arguments: dict[str, Any]
) -> tuple[list[str], list[company_scope.DirectoryCompany]]:
    """What was asked for, and the directory companies it means, each read as read_trends reads
    one, in order, once."""
    company = (arguments.get("company") or "").strip()
    several = [value.strip() for value in arguments.get("companies") or []]
    if company and several:
        raise ToolFailure(
            "Send `company` for one company or `companies` for several, not both."
        )
    values = [company] if company else [value for value in several if value]
    if not values:
        raise ToolFailure(
            "company_profile needs `company`: a key from find_company, or an exact name; or "
            "`companies`, several of them to read as one employer."
        )
    picks = {}
    for value in values:
        pick = company_scope.for_trends(client, value)
        picks.setdefault(pick.key, pick)
    return values, list(picks.values())


def _total(client: SpaceClient, params: list[tuple[str, str]]) -> int | None:
    """One `/facets` total, or None when the Space refuses the scope."""
    try:
        return int(client.read(SpaceRoute.FACETS, params).get("total") or 0)
    except SpaceError:
        return None


def _old_by_category(
    client: SpaceClient,
    boards: list[tuple[str, str]],
    trends: dict[str, Any],
    pool: ThreadPoolExecutor,
) -> dict[str, int]:
    """How many of each listed category's served jobs are over a year old, as `search_jobs`
    counts a category within a company: every age less the default window."""
    counted = [*boards, ("strict", "1"), ("counts", "total")]
    within = (
        search_arguments.SPACE_NAME["max_age_days"],
        str(search_arguments.DEFAULT_MAX_AGE_DAYS),
    )
    asked = {
        line["name"]: (
            pool.submit(_total, client, [*counted, ("family", line["name"])]),
            pool.submit(_total, client, [*counted, ("family", line["name"]), within]),
        )
        for line in _shown_categories(trends)[:CATEGORIES_SHOWN]
    }
    old = {}
    for name, (every, recent) in asked.items():
        every, recent = every.result(), recent.result()
        if every is not None and recent is not None and every > recent:
            old[name] = every - recent
    return old


def _age_line(total: int, old: int) -> str:
    return (
        f"{old:,} of its {total:,} served jobs were posted over a year ago (the posted date, "
        "else the day HeadStart first saw the job). search_jobs leaves those out unless "
        "max_age_days is 0, so its counts run lower than these, for each category by the "
        "count marked 'over a year old'."
    )


def answer(client: SpaceClient, arguments: dict[str, Any]) -> str:
    values, picks = _picks(client, arguments)
    board_keys = list(
        {board.lower(): board for pick in picks for board in pick.board_keys}.values()
    )
    if len(board_keys) > BOARDS_READ:
        raise ToolFailure(
            f"These companies hold {len(board_keys):,} Boards, more than the {BOARDS_READ} one "
            "profile reads; send fewer companies."
        )
    by_name = any(
        value.lower() not in {b.lower() for b in board_keys} for value in values
    )
    # Only one company's name is offered its namesakes: a roll-up already names its parts.
    value = values[0] if by_name and len(values) == 1 else None
    boards = [("board", board) for board in board_keys]
    keys = [("company", pick.key) for pick in picks]
    since = (_now() - timedelta(days=TREND_DAYS)).isoformat(timespec="seconds")
    within = (
        search_arguments.SPACE_NAME["max_age_days"],
        str(search_arguments.DEFAULT_MAX_AGE_DAYS),
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        facets = pool.submit(client.read, SpaceRoute.FACETS, [("strict", "1"), *boards])
        recent = pool.submit(
            _total, client, [("strict", "1"), *boards, within, ("counts", "total")]
        )
        trends = pool.submit(client.read, SpaceRoute.TRENDS, [("since", since), *keys])
        places = pool.submit(client.read, SpaceRoute.COMPANIES_LOCATIONS, boards)
        levels = pool.submit(client.read, SpaceRoute.COMPANIES_LEVELS, boards)
        similar = (
            pool.submit(company_scope.suggest, client, value, OTHERS_SHOWN + 1)
            if value
            else None
        )
        facets, trends, places = facets.result(), trends.result(), places.result()
        levels, recent = levels.result(), recent.result()
        others = [
            c for c in (similar.result() if similar else []) if c.key != picks[0].key
        ]
        total = int(facets.get("total") or 0)
        old_total = total - recent if recent is not None else 0
        old = _old_by_category(client, boards, trends, pool) if old_total > 0 else {}
    lines = _company_lines(picks, board_keys, by_name, others[:OTHERS_SHOWN])
    lines.append(scraped_text.SCRAPED_NOTE)
    lines += _trend(trends, ", ".join(pick.key for pick in picks), old)
    if old_total > 0:
        lines.append(_age_line(total, old_total))
    lines.append(_locations(places))
    lines += _breakdown(facets, levels)
    lines.append(
        f"To list its jobs: search_jobs with company {picks[0].key} (add category for one job "
        "category)."
        if len(picks) == 1
        else "To list their jobs: search_jobs with each key as company (add category for one "
        "job category)."
    )
    if tick := facets.get("newest_tick"):
        lines.append(f"Data as of the trends tick {tick}.")
    return "\n".join(lines)


TOOL = SpaceTool(
    name="company_profile",
    title="One company's hiring profile",
    description=(
        "One company's hiring profile: its tech openings now; postings opened and closed "
        "recently; its job-category mix; the countries its jobs are in, with their top "
        "places; and how many of its jobs are remote, of each employment type, at each "
        "level, show a salary, and were posted recently. `company` is a directory company: a "
        "key from find_company (such as 'greenhouse:stripe') or its exact name, read as the "
        "site's Trends picker reads it. One employer can be several directory companies "
        "(Deloitte is five): send their keys as `companies` for one profile over them all, "
        "each job counted once and each company listed. Tell the user which companies and "
        "Boards it was read as, and name the other companies the answer says the name may "
        "mean. Counts here include postings over a year old, which search_jobs leaves out by "
        "default; the answer says how many. Countries are read as search_jobs' `country` "
        "reads a place; levels are the Trends bands, each job counted once, and a job "
        "stating no experience in its own band."
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
            "companies": {
                "type": "array",
                "items": {"type": "string", "maxLength": 100},
                "maxItems": COMPANIES_ROLLED_UP,
                "description": (
                    "In place of `company`: several keys or exact names, read as one "
                    "employer, such as every Deloitte entry find_company lists."
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
    max_chars=10_000,
)
