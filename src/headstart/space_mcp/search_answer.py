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
from headstart.space_mcp import company_names, scraped_text
from headstart.space_mcp.space_client import SpaceClient, SpaceRoute

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

#: `/search` addresses at most `max_k * max_page` rows of one query (`JobSearch`, ADR-0074).
LAST_PAGE = 20
SORT_WINDOW = 2_000

#: A Blocking filter as `/facets` names it (a `SearchFilters` field) -> as this tool names it.
_ARGUMENT_OF_FILTER = {
    "etype": "employment_type",
    "india": "india_place",
    "posted_within": "posted_within_days",
    "seen_within": "first_seen_within_hours",
    "kw": "keyword",
}

#: Facet dimensions, in the order the answer lists them, as this tool names them.
_FACET_NAMES = {
    "remote": "remote",
    "etype": "employment_type",
    "max_years": "max_years",
    "has_salary": "has_salary",
    "posted_within": "posted_within_days",
    "seen_within": "first_seen_within_hours",
    "ats": "ats",
}
_FACET_OPTIONS_SHOWN = 12

#: A company or location past this is cut; a title keeps `scraped_text.FIELD_LIMIT`.
SHORT_FIELD = 60


def _params(
    arguments: dict[str, Any], scope: company_names.CompanyScope | None
) -> list[tuple[str, str]]:
    """The query string both routes are asked, in `JobSearch.parse_filters`' own names."""
    params: list[tuple[str, str]] = [("strict", "1")]
    if query := (arguments.get("query") or "").strip():
        params.append(("q", query))
    if scope is not None:
        params += scope.params()
    if category := arguments.get("category"):
        params.append(("family", category))
    # `parse_filters` compares both flags to the literal "true".
    for flag in ("remote", "has_salary"):
        if arguments.get(flag):
            params.append((flag, "true"))
    plain = {
        "max_years": "max_years",
        "employment_type": "etype",
        "india_place": "india",
        "location": "location",
        "salary_min": "salary_min",
        "salary_max": "salary_max",
        "salary_currency": "salary_currency",
        "posted_within_days": "posted_within",
        "first_seen_within_hours": "seen_within",
        "ats": "ats",
    }
    for argument, name in plain.items():
        if arguments.get(argument) is not None and arguments.get(argument) != "":
            params.append((name, str(arguments[argument])))
    if keyword := (arguments.get("keyword") or "").strip():
        params.append(("kw", keyword))
        params.append(("kw_in", arguments.get("keyword_in") or "title"))
    if sort := SORTS[arguments.get("sort") or "relevance"]:
        params.append(("sort", sort))
    params.append(("k", str(arguments.get("limit") or 10)))
    params.append(("page", str(arguments.get("page") or 1)))
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
    arguments: dict[str, Any], scope: company_names.CompanyScope | None
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
    sort = arguments.get("sort") or "relevance"
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
    facets: dict[str, Any], scope: company_names.CompanyScope | None, arguments
) -> str:
    blocking = facets.get("blocking")
    if blocking == "company" and scope is not None and scope.substring is not None:
        return (
            f"0 jobs: no company name contains {scraped_text.quoted(scope.substring)}. Try a "
            "shorter or different spelling, or a key from read_trends or hiring_now."
        )
    if blocking:
        name = _ARGUMENT_OF_FILTER.get(blocking, blocking)
        return f"0 jobs. The filter costing the most is `{name}`; try without it."
    scoped = scope is not None or arguments.get("category")
    return (
        "0 jobs, and no single filter is to blame: nothing matches even with every filter removed"
        + ("; the company or category scope is what leaves nothing." if scoped else ".")
    )


def _facet_lines(facets: dict[str, Any]) -> list[str]:
    lines = ["Counts if only that one filter changed:"]
    by_dimension = facets.get("facets") or {}
    for dimension, name in _FACET_NAMES.items():
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
        scope = company_names.for_search(
            client, company, needs_boards=bool(arguments.get("category"))
        )
    params = _params(arguments, scope)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows_asked = pool.submit(client.read, SpaceRoute.SEARCH, params)
        facets_asked = pool.submit(client.read, SpaceRoute.FACETS, params)
        rows, facets = rows_asked.result(), facets_asked.result()
    total = int(facets.get("total") or 0)
    k, page = int(arguments.get("limit") or 10), int(arguments.get("page") or 1)
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
                f"This is the last reachable page ({SORT_WINDOW:,} rows); narrow the filters to "
                "see others."
                if page >= LAST_PAGE
                else f"More: page={page + 1}."
            )
    if arguments.get("detail") == "full":
        lines += _facet_lines(facets)
    if tick := facets.get("newest_tick"):
        lines.append(f"Data as of the trends tick {tick}.")
    return "\n".join(lines)
