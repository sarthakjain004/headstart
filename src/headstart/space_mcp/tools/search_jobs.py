"""One `search_jobs` answer: the Space's `/search` page and `/facets` counts, read together.

The two routes are asked the same parameters at once, with ``strict=1``, so the rows and the total
describe one query and nothing the Space would drop is dropped silently. The answer says what was
searched, how the rows are ordered (a sort with a query orders only the 2,000 closest matches,
`JobSearch.run`), the rows themselves with every scraped field quoted, and — when nothing matches —
which filter is to blame, named as this tool names it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from headstart.mcp_protocol.stdio import ToolFailure
from headstart.search_filters import (
    employment_type_filter,
    india_filter,
    india_gazetteer,
)
from headstart.space_mcp import company_scope, role_families, scraped_text
from headstart.space_mcp.space_client import SpaceClient, SpaceRoute
from headstart.space_mcp.space_tool import SpaceTool

#: `sort` as this tool spells it -> as `/search` does; relevance is the query's own order.
SORTS = {
    "relevance": None,
    "posted": "posted",
    "first_seen": "seen",
    "salary": "salary",
}

_SORT_WORDS = {
    "posted": "most recently posted first",
    "first_seen": "newest to HeadStart first",
    "salary": "highest salary first",
}

#: `JobSearch` pages at most 20 deep, and a query's sort re-orders its `max_k * max_page` = 2,000
#: nearest matches (ADR-0074, `JobSearch.run`) — the Space's figures, restated for the wording.
LAST_PAGE = 20
SORT_WINDOW = 2_000

#: Every filter argument as this tool names it -> as the Space does: the query-string name
#: `JobSearch.parse_filters` reads, which is also the `SearchFilters` field `/facets` names a
#: facet dimension or a Blocking filter by. One map, read both ways.
SPACE_NAME = {
    "remote": "remote",
    "has_salary": "has_salary",
    "max_years": "max_years",
    "employment_type": "etype",
    "india_place": "india",
    "location": "location",
    "company": "company",
    "salary_min": "salary_min",
    "salary_max": "salary_max",
    "salary_currency": "salary_currency",
    "posted_within_days": "posted_within",
    "first_seen_within_hours": "seen_within",
    "keyword": "kw",
    "keyword_in": "kw_in",
    "ats": "ats",
}
_ARGUMENT_OF = {space: argument for argument, space in SPACE_NAME.items()}

#: Sent as the literal "true" `parse_filters` compares against; the company and the keyword are
#: sent by their own rules below.
_FLAGS = ("remote", "has_salary")
_SENT_ELSEWHERE = (*_FLAGS, "company", "keyword", "keyword_in")

#: Facet dimensions in the order the answer lists them (the Space's own names).
_FACET_ORDER = (
    "remote",
    "etype",
    "max_years",
    "has_salary",
    "posted_within",
    "seen_within",
    "ats",
)
_FACET_OPTIONS_SHOWN = 12

#: A company or location past this is cut; a title keeps `scraped_text.FIELD_LIMIT`.
SHORT_FIELD = 60


#: Every place the Space's India filter names: the whole country, its region, its cities.
_INDIA_PLACES = [
    india_filter.WHOLE_COUNTRY,
    *india_gazetteer.REGIONS,
    *india_gazetteer.CITIES,
]


def _params(
    arguments: dict[str, Any], scope: company_scope.CompanyScope | None
) -> list[tuple[str, str]]:
    """The query string both routes are asked, in `JobSearch.parse_filters`' own names."""
    params: list[tuple[str, str]] = [("strict", "1")]
    if query := (arguments.get("query") or "").strip():
        params.append(("q", query))
    if scope is not None:
        params += scope.params()
    if category := arguments.get("category"):
        params.append(("family", category))
    for flag in _FLAGS:
        if arguments.get(flag):
            params.append((SPACE_NAME[flag], "true"))
    for argument, name in SPACE_NAME.items():
        value = arguments.get(argument)
        if argument not in _SENT_ELSEWHERE and value is not None and value != "":
            params.append((name, str(value)))
    if keyword := (arguments.get("keyword") or "").strip():
        params.append((SPACE_NAME["keyword"], keyword))
        params.append(
            (SPACE_NAME["keyword_in"], arguments.get("keyword_in") or "title")
        )
    if sort := SORTS[arguments["sort"]]:
        params.append(("sort", sort))
    params.append(("k", str(arguments["limit"])))
    params.append(("page", str(arguments["page"])))
    return params


def _refuse_by_policy(arguments: dict[str, Any]) -> None:
    """What the schema cannot say: combinations the Space would misread."""
    bounded = (
        arguments.get("salary_min") is not None
        or arguments.get("salary_max") is not None
    )
    if bounded and not arguments.get("salary_currency"):
        raise ToolFailure(
            "salary_min and salary_max need salary_currency: an unqualified bound is read as "
            "USD, so 30 lakh would become $3,000,000. For 30 lakh send salary_min 3000000 with "
            "salary_currency INR."
        )
    if arguments.get("keyword_in") and not (arguments.get("keyword") or "").strip():
        raise ToolFailure("keyword_in only scopes a keyword; send keyword too.")
    if arguments.get("category") and not (arguments.get("company") or "").strip():
        raise ToolFailure(
            "category narrows one company's jobs to a job category, so it needs company. For a "
            "category across the whole index, use read_trends, or describe the role in query."
        )


def _money(row: dict[str, Any]) -> str | None:
    low, high = row.get("min_salary_annual"), row.get("max_salary_annual")
    currency = row.get("salary_currency") or ""
    if low is not None or high is not None:
        if low is not None and high is not None and high != low:
            return f"{currency} {low:,.0f}–{high:,.0f} a year".strip()
        if low is not None:
            return f"{currency} {low:,.0f} a year".strip()
        return f"{currency} up to {high:,.0f} a year".strip()
    if row.get("salary"):
        return f"salary {scraped_text.quoted(row['salary'], 60)}"
    return None


def _row(number: int, row: dict[str, Any]) -> str:
    facts = [
        scraped_text.quoted(row.get("title")),
        scraped_text.quoted(row.get("company"), SHORT_FIELD),
        scraped_text.quoted(row.get("location"), SHORT_FIELD),
    ]
    if row.get("remote"):
        facts.append("remote")
    if row.get("employment_type"):
        facts.append(str(row["employment_type"]))
    if row.get("min_years") is not None:
        facts.append(f"{row['min_years']}+ yrs")
    if money := _money(row):
        facts.append(money)
    dates = []
    if row.get("posted_at"):
        dates.append(f"posted {str(row['posted_at'])[:10]}")
    if row.get("first_seen"):
        dates.append(f"first seen {str(row['first_seen'])[:10]}")
    score = f"{row['score']:.2f} " if row.get("score") is not None else ""
    return (
        f"{number:>2}. {score}{' · '.join(facts)}"
        + (f" · {' · '.join(dates)}" if dates else "")
        + f"\n    id {row.get('id')} · {scraped_text.link(row.get('url'))}"
    )


def _scope_line(
    arguments: dict[str, Any], scope: company_scope.CompanyScope | None
) -> str:
    said = []
    if scope is not None:
        if scope.company is not None:
            said.append(f"company {scope.company.described()}")
        else:
            said.append(
                f"company name contains {scraped_text.quoted(scope.substring)} "
                "(the site's company box)"
            )
        if scope.read_as:
            said.append(scope.read_as)
    if arguments.get("category"):
        said.append(f"category {arguments['category']}")
    if arguments.get("remote"):
        said.append("remote only")
    if arguments.get("max_years") is not None:
        said.append(f"open to someone with at most {arguments['max_years']} years")
    for argument in ("employment_type", "india_place", "ats"):
        if arguments.get(argument):
            said.append(f"{argument} {arguments[argument]}")
    if arguments.get("location"):
        said.append(f"location contains {scraped_text.quoted(arguments['location'])}")
    currency = arguments.get("salary_currency")
    if arguments.get("salary_min") is not None:
        said.append(f"salary at least {arguments['salary_min']:,} {currency} a year")
    if arguments.get("salary_max") is not None:
        said.append(f"salary at most {arguments['salary_max']:,} {currency} a year")
    if (
        arguments.get("salary_min") is not None
        or arguments.get("salary_max") is not None
        or arguments.get("has_salary")
    ):
        said.append("only jobs that state a salary can match")
    if arguments.get("posted_within_days"):
        said.append(f"posted in the last {arguments['posted_within_days']} days")
    if arguments.get("first_seen_within_hours"):
        said.append(
            f"new to HeadStart in the last {arguments['first_seen_within_hours']} hours"
        )
    if keyword := (arguments.get("keyword") or "").strip():
        said.append(
            f"keyword {scraped_text.quoted(keyword)} in {arguments.get('keyword_in') or 'title'}"
        )
    return "Scope: " + (" · ".join(said) if said else "the whole index") + "."


def _order_line(arguments: dict[str, Any]) -> str:
    query = (arguments.get("query") or "").strip()
    sort = arguments["sort"]
    currency = arguments.get("salary_currency")
    words = _SORT_WORDS.get(sort, "")
    if sort == "salary":
        words += f", in {currency}" if currency else ", compared in USD"
    if query and sort != "relevance":
        return (
            f"Ordered {words} among the {SORT_WINDOW:,} closest matches to the query, not "
            "across the whole index; omit query for a global order."
        )
    if query:
        return "Ordered by similarity to the query, which orders the matches but does not narrow them."
    return f"Ordered {words or 'newest to HeadStart first'} across every match."


def _nothing_matched(
    facets: dict[str, Any], scope: company_scope.CompanyScope | None, arguments
) -> str:
    blocking = facets.get("blocking")
    if blocking == "company" and scope is not None and scope.substring is not None:
        return (
            f"0 jobs: no company name contains {scraped_text.quoted(scope.substring)}. Try a "
            "shorter or different spelling, or a key from read_trends or hiring_now."
        )
    if blocking:
        name = _ARGUMENT_OF.get(blocking, blocking)
        return f"0 jobs. The filter costing the most is `{name}`; try without it."
    scoped = scope is not None or arguments.get("category")
    return (
        "0 jobs, and no single filter is to blame: nothing matches even with every filter removed"
        + ("; the company or category scope is what leaves nothing." if scoped else ".")
    )


def _facet_lines(facets: dict[str, Any]) -> list[str]:
    lines = ["Counts if only that one filter changed:"]
    by_dimension = facets.get("facets") or {}
    for dimension in _FACET_ORDER:
        name = _ARGUMENT_OF.get(dimension, dimension)
        options = by_dimension.get(dimension)
        if not options:
            continue
        # The Space's own order, unless there are too many to list: then the largest.
        if len(options) > _FACET_OPTIONS_SHOWN:
            options = sorted(options, key=lambda o: -(o.get("count") or 0))
        shown = options[:_FACET_OPTIONS_SHOWN]
        text = " · ".join(f"{o.get('label')} {o.get('count', 0):,}" for o in shown)
        more = len(options) - len(shown)
        lines.append(f"  {name}: {text}" + (f" · …{more} more" if more > 0 else ""))
    return lines


def answer(client: SpaceClient, arguments: dict[str, Any]) -> str:
    _refuse_by_policy(arguments)
    scope = None
    if company := (arguments.get("company") or "").strip():
        scope = company_scope.for_search(
            client, company, needs_boards=bool(arguments.get("category"))
        )
    params = _params(arguments, scope)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows_asked = pool.submit(client.read, SpaceRoute.SEARCH, params)
        facets_asked = pool.submit(client.read, SpaceRoute.FACETS, params)
        rows, facets = rows_asked.result(), facets_asked.result()
    total = int(facets.get("total") or 0)
    k, page = int(arguments["limit"]), int(arguments["page"])
    lines = [_scope_line(arguments, scope)]
    if not rows:
        lines.insert(
            0,
            _nothing_matched(facets, scope, arguments)
            if total == 0
            else f"{total:,} jobs match these filters, but page {page} is past them.",
        )
    else:
        first = (page - 1) * k + 1
        lines.insert(
            0,
            f"{total:,} jobs match these filters. Showing {first:,}–{first + len(rows) - 1:,}.",
        )
        lines.append(_order_line(arguments))
        lines.append(scraped_text.SCRAPED_NOTE)
        lines += [_row(first + i, row) for i, row in enumerate(rows)]
        shown_to = first + len(rows) - 1
        if shown_to < total:
            lines.append(
                f"This is the last reachable page: the Space pages {LAST_PAGE} deep, "
                f"{LAST_PAGE * k:,} rows at limit {k}; narrow the filters to see others."
                if page >= LAST_PAGE
                else f"More: page={page + 1}."
            )
    if arguments.get("detail") == "full":
        lines += _facet_lines(facets)
    if tick := facets.get("newest_tick"):
        lines.append(f"Data as of the trends tick {tick}.")
    return "\n".join(lines)


TOOL = SpaceTool(
    name="search_jobs",
    title="Find open tech jobs",
    description=(
        "Search HeadStart's tech job index: `query` describes only the role ('backend "
        "engineer at a climate startup'); years, pay, place, company, employment type "
        "and dates go in their own fields, never in `query`. Omit `query` to list the "
        "newest jobs that match the filters. With a `query`, `sort` orders only the "
        "2,000 closest matches — for a global order (the highest salary anywhere, the "
        "newest anywhere) omit `query` and narrow with `keyword` and the filters. "
        "`company` matches as the site's company box does (any company name containing "
        "the text) unless `category` is set, which needs a directory company: a key "
        "such as 'greenhouse:stripe', or an exact name. A salary sort without a "
        "currency is ordered in USD. No account applies, so a user's hidden companies "
        "are not removed. Returns the total, one page of jobs with their links, and — "
        "when nothing matches — the filter costing the most."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "maxLength": 200,
                "description": "The role only. Omit to list the newest jobs.",
            },
            "company": {
                "type": "string",
                "maxLength": 100,
                "description": (
                    "A company name (matched as a substring), or a Board key from "
                    "an earlier answer such as 'lever:razorpay'."
                ),
            },
            "category": role_families.schema(
                "A job category within `company`'s jobs; needs `company`."
            ),
            "remote": {"type": "boolean", "description": "Remote jobs only."},
            "max_years": {
                "type": "integer",
                "minimum": 0,
                "maximum": 30,
                "description": "Open to someone with at most this many years.",
            },
            "employment_type": {
                "type": "string",
                "enum": list(employment_type_filter.RULES),
            },
            "india_place": {
                "type": "string",
                "enum": _INDIA_PLACES,
                "description": "An Indian city or region, or 'india' for anywhere in India.",
            },
            "location": {
                "type": "string",
                "maxLength": 60,
                "description": "Text the job's location contains, any country.",
            },
            "salary_min": {
                "type": "integer",
                "minimum": 0,
                "description": "Annual; needs salary_currency (30 lakh = 3000000 INR).",
            },
            "salary_max": {
                "type": "integer",
                "minimum": 0,
                "description": "Annual; needs salary_currency.",
            },
            "salary_currency": {
                "type": "string",
                "maxLength": 3,
                "description": "ISO 4217 code, such as USD, INR, EUR, GBP.",
            },
            "has_salary": {
                "type": "boolean",
                "description": "Only jobs that state a salary.",
            },
            "posted_within_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 365,
                "description": "Posted by the employer within this many days.",
            },
            "first_seen_within_hours": {
                "type": "integer",
                "minimum": 1,
                "maximum": 720,
                "description": "New to HeadStart within this many hours.",
            },
            "keyword": {
                "type": "string",
                "maxLength": 60,
                "description": "An exact word or phrase the job must contain.",
            },
            "keyword_in": {
                "type": "string",
                "enum": ["title", "description", "both"],
                "description": "Where keyword must appear; title by default.",
            },
            "ats": {
                "type": "string",
                "maxLength": 40,
                "description": "One ATS, such as greenhouse or workday.",
            },
            "sort": {
                "type": "string",
                "enum": list(SORTS),
                "default": "relevance",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 10,
            },
            "page": {
                "type": "integer",
                "minimum": 1,
                "maximum": LAST_PAGE,
                "default": 1,
            },
            "detail": {
                "type": "string",
                "enum": ["concise", "full"],
                "default": "concise",
                "description": "full adds the count behind each filter's options.",
            },
        },
        "additionalProperties": False,
    },
    when_to_use=(
        "Use search_jobs to find openings: put the role in `query`, and years, pay, place, company and dates in their own fields — never in `query`."
    ),
    answer=answer,
    max_chars=36_000,
)
