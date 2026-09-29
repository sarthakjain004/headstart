"""One `search_jobs` answer: the Space's `/search` page and `/facets` counts, read together.

The two routes are asked the same parameters at once, with ``strict=1``, so the rows and the total
describe one query and nothing the Space would drop is dropped silently. The answer says what was
searched, how the rows are ordered (a sort with a query orders only those of the 2,000 closest
matches that score at least the floor, `JobSearch.run`, ADR-0338), the rows themselves with every scraped field quoted, and — when nothing matches —
which filter is to blame, named as this tool names it. A concise answer asks `/facets` for the
total alone (``counts=total``, ADR-0274); only ``detail=full`` pays for every option's count.

A row carries its posting's age, flagged past a year, and its employment type as scraped beside
the `employment_type` values it counts as, and its company as the Company directory names it when
the served name is only its Board's host (`shown_company`). Rows on one page that copy one
posting — per country, or on two Boards of its employer (`requisition_copies`) — are listed under
the first of them, with only what differs; every id and link stays.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from typing import Any

from headstart.jobs import requisition_copies
from headstart.mcp_protocol.messages import ToolFailure
from headstart.search_filters import (
    employment_type_filter,
)
from headstart.space_mcp import (
    company_scope,
    role_families,
    scraped_text,
    search_arguments,
    shown_company,
)
from headstart.space_mcp.space_client import (
    CALL_DEADLINE_S,
    DeadlinePassed,
    SpaceClient,
    SpaceRoute,
)
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

#: `JobSearch` pages at most 20 deep, and a query's sort re-orders those of its `max_k * max_page`
#: = 2,000 nearest matches that score at least `job_search.SORT_FLOOR` (ADR-0074, ADR-0338,
#: `JobSearch.run`) — the Space's figures, restated for the wording.
LAST_PAGE = 20
SORT_WINDOW = 2_000
SORT_FLOOR = 0.67

_ARGUMENT_OF = {
    space: argument for argument, space in search_arguments.SPACE_NAME.items()
}

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

#: Longer than any id: a clipped id could not be sent back as a key.
ID_FIELD = 300

#: An employment type as scraped is a word or two ("Intern - Temporary Employee" is 27).
TYPE_FIELD = 30

#: A posting older than this many days is flagged in its row: it may well have closed.
STALE_DAYS = 365

#: Said in place of the client's deadline sentence when a description keyword ran past it. The
#: description read is the slow part, not the other filters, and the Space keeps what it read
#: once the read finishes (ADR-0320), so the same call soon after is quick.
_DESCRIPTION_PAST_DEADLINE = (
    f"HeadStart did not answer within this call's {CALL_DEADLINE_S:g} s, so it stopped "
    "waiting. Reading job descriptions for the keyword is the slow part. HeadStart finishes that "
    "read after this call ends and keeps its matches unless there are very many, so the same "
    "call in a minute or two is usually quick. Or look for the keyword in titles "
    "(keyword_in: title), or add a company."
)


#: A keyword about work authorisation, and what hand-read descriptions say of each kind
#: (ADR-0333): a description matching one usually says the opposite of what the user hopes.
_SPONSORSHIP_WORD = re.compile(
    r"(?i)sponsor|visa|clearance|citizen|authori[sz]ation|authori[sz]ed|immigra|h-?1-?b"
)
_RELOCATION_WORD = re.compile(r"(?i)relocat")
_SPONSORSHIP_KEYWORD_NOTE = (
    "A description matching this keyword often refuses sponsorship rather than offering it: "
    "of 100 hand-read descriptions mentioning sponsorship, 80 refused it and 11 offered it "
    "(2026-09-29). Send work_authorization offers_sponsorship instead, or read each job with "
    "get_job (its Mentions line) before saying it offers sponsorship."
)
_RELOCATION_KEYWORD_NOTE = (
    "A description matching this keyword does not always offer relocation: of 63 hand-read "
    "descriptions mentioning relocation, 38 offered it, 14 said none is offered and 11 said "
    "neither (2026-09-29). Send work_authorization offers_relocation instead, or read each "
    "job with get_job before saying it offers relocation."
)


def _keyword_note(arguments: dict[str, Any]) -> str | None:
    """The warning a keyword about visas or relocation earns: its matches often refuse it."""
    keyword = (arguments.get("keyword") or "").strip()
    if not keyword:
        return None
    if _SPONSORSHIP_WORD.search(keyword):
        return _SPONSORSHIP_KEYWORD_NOTE
    if _RELOCATION_WORD.search(keyword):
        return _RELOCATION_KEYWORD_NOTE
    return None


def _params(
    arguments: dict[str, Any], scope: company_scope.CompanyScope | None
) -> list[tuple[str, str]]:
    """The query string both routes are asked, in `JobSearch.parse_filters`' own names."""
    params: list[tuple[str, str]] = [("strict", "1")]
    if query := (arguments.get("query") or "").strip():
        params.append(("q", query))
    if similar_to := (arguments.get("similar_to") or "").strip():
        params.append(("like", similar_to))
    if scope is not None:
        params += scope.params()
    if category := arguments.get("category"):
        params.append(("family", category))
    params += search_arguments.filter_params(arguments)
    if keyword := (arguments.get("keyword") or "").strip():
        params.append((search_arguments.SPACE_NAME["keyword"], keyword))
        params.append(
            (
                search_arguments.SPACE_NAME["keyword_in"],
                arguments.get("keyword_in") or "title",
            )
        )
    if sort := SORTS[arguments["sort"]]:
        params.append(("sort", sort))
    params.append(("k", str(arguments["limit"])))
    params.append(("page", str(arguments["page"])))
    return params


def _refuse_by_policy(arguments: dict[str, Any]) -> None:
    """What the schema cannot say: combinations the Space would misread."""
    if (arguments.get("query") or "").strip() and (
        arguments.get("similar_to") or ""
    ).strip():
        raise ToolFailure(
            "similar_to ranks by one job and query by a description of the role; send one."
        )
    bounded = (
        arguments.get("salary_min") is not None
        or arguments.get("salary_max") is not None
    )
    low, high = arguments.get("salary_min"), arguments.get("salary_max")
    if low is not None and high is not None and low > high:
        raise ToolFailure(
            f"salary_min {low:,} is above salary_max {high:,}, so no range could be read as "
            "both; send the lower figure as salary_min."
        )
    floor, ceiling = (
        arguments.get("required_years_at_least"),
        arguments.get("max_years"),
    )
    if floor is not None and ceiling is not None and floor > ceiling:
        raise ToolFailure(
            f"required_years_at_least {floor} is above max_years {ceiling}: no job asks for "
            "at least one and at most the other. max_years is the user's own experience; "
            "required_years_at_least a floor on what the job asks."
        )
    if bounded and not arguments.get("salary_currency"):
        raise ToolFailure(
            "salary_min and salary_max need salary_currency: an unqualified bound is read as "
            "USD, so 30 lakh would become $3,000,000. For 30 lakh send salary_min 3000000 with "
            "salary_currency INR."
        )
    if arguments.get("keyword_in") and not (arguments.get("keyword") or "").strip():
        raise ToolFailure("keyword_in only scopes a keyword; send keyword too.")


def _money(row: dict[str, Any]) -> str | None:
    low, high = row.get("min_salary_annual"), row.get("max_salary_annual")
    # A figure with no currency is said so, not left bare: "85,000–155,000" read as dollars.
    currency = row.get("salary_currency") or "currency not stated:"
    if low is not None or high is not None:
        if low is not None and high is not None and high != low:
            return f"{currency} {low:,.0f}–{high:,.0f} a year"
        if low is not None:
            return f"{currency} {low:,.0f} a year"
        return f"{currency} up to {high:,.0f} a year"
    if row.get("salary"):
        return f"salary {scraped_text.quoted(row['salary'], 60)}"
    return None


def _today() -> date:
    """Today in UTC, what a posting's age is counted to; its own function so a test can pin it."""
    return datetime.now(UTC).date()


def _age(day: str, today: date) -> str:
    try:
        days = (today - date.fromisoformat(day[:10])).days
    except ValueError:
        return ""
    if days < 1:
        return " (today)"
    if days <= STALE_DAYS:
        return f" ({days} day{'' if days == 1 else 's'} ago)"
    return f" ({days / 365.25:.1f} years ago: over a year old)"


def _employment_type(raw: Any) -> str:
    """The type as the employer wrote it, beside the `employment_type` values it counts as."""
    kinds = [
        value
        for value, rule in employment_type_filter.RULES.items()
        if rule.matches(str(raw))
    ]
    return (
        f"type {scraped_text.quoted(raw, TYPE_FIELD)} "
        f"({', '.join(kinds) or 'no employment_type value'})"
    )


def _facts(row: dict[str, Any], today: date, experience_filtered: bool) -> list[str]:
    """Everything a row says after its title and company."""
    facts = [scraped_text.quoted(row.get("location"), SHORT_FIELD)]
    if row.get("remote"):
        facts.append("remote")
    if row.get("employment_type"):
        facts.append(_employment_type(row["employment_type"]))
    if row.get("min_years") is not None:
        facts.append(f"{row['min_years']}+ yrs")
    elif experience_filtered:
        facts.append("experience not stated")
    if money := _money(row):
        facts.append(money)
    posted, seen = row.get("posted_at"), row.get("first_seen")
    if posted:
        facts.append(f"posted {str(posted)[:10]}{_age(str(posted), today)}")
    if seen:
        age = "" if posted else _age(str(seen), today)
        facts.append(f"first seen {str(seen)[:10]}{age}")
    return facts


def _score(row: dict[str, Any]) -> str:
    return f"{row['score']:.2f} " if row.get("score") is not None else ""


def _where(row: dict[str, Any]) -> str:
    return (
        f"id {scraped_text.quoted(row.get('id'), ID_FIELD)} · "
        f"{scraped_text.link(row.get('url'))}"
    )


def _row(number: int, row: dict[str, Any], facts: list[str]) -> str:
    said = [
        scraped_text.quoted(row.get("title")),
        shown_company.said(row, SHORT_FIELD),
        *facts,
    ]
    return f"{number:>2}. {_score(row)}{' · '.join(said)}\n    {_where(row)}"


def _also(
    number: int,
    row: dict[str, Any],
    facts: list[str],
    head: dict[str, Any],
    head_facts: list[str],
) -> str:
    """A row listed under an earlier one on its page: only what differs from that one."""
    said = []
    if row.get("title") != head.get("title"):
        said.append(scraped_text.quoted(row.get("title")))
    if row.get("company") != head.get("company"):
        said.append(shown_company.said(row, SHORT_FIELD))
    said += [fact for fact in facts if fact not in head_facts]
    return (
        f"    also #{number}: {_score(row)}{' · '.join(said) or 'as above'}\n"
        f"      {_where(row)}"
    )


def _page_lines(
    first: int, rows: list[dict[str, Any]], experience_filtered: bool
) -> tuple[list[str], bool]:
    """One page's rows numbered from ``first``, and whether any went under another: a row copying
    an earlier row's posting (`requisition_copies`) is listed under it as "also #N". Only within the
    page, so paging and the header's row numbers are the Space's."""
    today = _today()
    facts = [_facts(row, today, experience_filtered) for row in rows]
    groups = requisition_copies.groups(rows)
    lines = []
    for head, *others in groups:
        lines.append(_row(first + head, rows[head], facts[head]))
        lines += [
            _also(first + i, rows[i], facts[i], rows[head], facts[head]) for i in others
        ]
    return lines, len(groups) < len(rows)


def _sorted_by_similarity(arguments: dict[str, Any]) -> bool:
    """Whether the Space re-orders only a query's or a job's closest matches above the floor."""
    ranked = (arguments.get("query") or "").strip() or (
        arguments.get("similar_to") or ""
    ).strip()
    return bool(ranked) and arguments["sort"] != "relevance"


def _order_line(arguments: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    query = (arguments.get("query") or "").strip()
    sort = arguments["sort"]
    currency = arguments.get("salary_currency")
    words = _SORT_WORDS.get(sort, "")
    if sort == "salary":
        words += f", in {currency}" if currency else ", compared in USD"
    similar_to = (arguments.get("similar_to") or "").strip()
    # That job's own vector ranks as a query's does (ADR-0277), so it is worded as one.
    ranked_by = (
        f"job {scraped_text.quoted(similar_to)} (itself left out)"
        if similar_to
        else "the query"
    )
    ranking = "similar_to" if similar_to else "query"
    if _sorted_by_similarity(arguments):
        scores = [row["score"] for row in rows if row.get("score") is not None]
        lowest = f"; the lowest shown scores {min(scores):.2f}" if scores else ""
        return (
            f"Ordered {words} among the closest matches to {ranked_by} that score at least "
            f"{SORT_FLOOR:.2f} (of its {SORT_WINDOW:,} closest), not across the whole index"
            f"{lowest}. Less similar rows are left out of a sorted answer, since they are "
            f"mostly other roles; omit {ranking} for a global order, or sort by relevance for "
            "every match."
        )
    if query or similar_to:
        return f"Ordered by similarity to {ranked_by}, which orders the matches but does not narrow them."
    return f"Ordered {words or 'newest to HeadStart first'} across every match."


def _nothing_matched(
    client: SpaceClient,
    facets: dict[str, Any],
    scope: company_scope.CompanyScope | None,
    arguments,
) -> str:
    if left_out := facets.get("operators_left_out"):
        return (
            f"0 jobs: the {left_out:,} that match are all posted by companies `operators` "
            "leaves out; name them in operators to see them."
        )
    blocking = facets.get("blocking")
    if blocking == "company" and scope is not None and scope.substring is not None:
        return (
            f"0 jobs: no company name contains {scraped_text.quoted(scope.substring)}. "
            + company_scope.alternatives(client, scope.substring)
        )
    if blocking:
        name = _ARGUMENT_OF.get(blocking, blocking)
        undo = "send max_age_days 0" if name == "max_age_days" else "try without it"
        return f"0 jobs. The filter costing the most is `{name}`; {undo}."
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
        text = " · ".join(_facet_option(name, o) for o in shown)
        more = len(options) - len(shown)
        lines.append(f"  {text}" + (f" · …{more} more" if more > 0 else ""))
    return lines


def _facet_option(name: str, option: dict[str, Any]) -> str:
    """One option as the argument that selects it (``max_years=0: 2,334``), not the site's label."""
    value = option.get("value")
    count = f"{option.get('count', 0):,}"
    if value is None:
        return f"{name} any: {count}"
    return f"{name}={str(value).lower() if isinstance(value, bool) else value}: {count}"


def scans_descriptions(arguments: dict[str, Any]) -> bool:
    """Whether a call with these arguments matches its keyword against descriptions: the slowest
    search there is, measured 16–18 s alone on the hosted Space and 29–36 s beside another, so
    the hosted route gives it a place of its own (ADR-0325)."""
    return bool(str(arguments.get("keyword") or "").strip()) and arguments.get(
        "keyword_in"
    ) in ("description", "both")


def _coverage_line(arguments: dict[str, Any], facets: dict[str, Any]) -> str | None:
    """How many jobs a description keyword could match at all: the page's own warning."""
    coverage = facets.get("description_coverage")
    if not scans_descriptions(arguments) or not coverage:
        return None
    return (
        f"Descriptions are stored for {coverage['covered']:,} of the {coverage['total']:,} "
        "jobs the other filters match; the keyword can match a description only in those."
    )


def _matched(total: int, arguments: dict[str, Any]) -> str:
    """The headline's count. A ranking is named in it, since the total was once read as the
    number of jobs like the query when it counted every job the filters allow."""
    if (arguments.get("similar_to") or "").strip():
        return f"{total:,} jobs match these filters; similar_to only ranks them and does not narrow this count."
    if (arguments.get("query") or "").strip():
        return f"{total:,} jobs match these filters; the query only ranks them and does not narrow this count."
    return f"{total:,} jobs match these filters."


def answer(client: SpaceClient, arguments: dict[str, Any]) -> str:
    _refuse_by_policy(arguments)
    scope = None
    if company := (arguments.get("company") or "").strip():
        scope = company_scope.for_search(
            client, company, needs_boards=bool(arguments.get("category"))
        )
    params = _params(arguments, scope)
    full = arguments.get("detail") == "full"
    # Concise prints only the total, so it asks for nothing else (ADR-0274): under a description
    # keyword every option's count re-scans the matches, 98.7 s against 10.6 s for the page.
    counted = params if full else [*params, ("counts", "total")]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows_asked = pool.submit(client.read, SpaceRoute.SEARCH, params)
            facets_asked = pool.submit(client.read, SpaceRoute.FACETS, counted)
            rows, facets = rows_asked.result(), facets_asked.result()
    except DeadlinePassed as exc:
        if scans_descriptions(arguments):
            raise ToolFailure(_DESCRIPTION_PAST_DEADLINE) from exc
        raise
    rows = shown_company.named(client, rows)
    total = int(facets.get("total") or 0)
    k, page = int(arguments["limit"]), int(arguments["page"])
    lines = [
        search_arguments.scope_line(arguments, scope, facets.get("operators_left_out"))
    ]
    if note := search_arguments.query_constraints_note(arguments.get("query") or ""):
        lines.append(note)
    if coverage := _coverage_line(arguments, facets):
        lines.append(coverage)
    if note := _keyword_note(arguments):
        lines.append(note)
    if not rows:
        lines.insert(
            0,
            _nothing_matched(client, facets, scope, arguments)
            if total == 0
            else f"{_matched(total, arguments)} Page {page} is past them."
            + (
                f" A sorted answer orders only the matches scoring at least {SORT_FLOOR:.2f} "
                "against the ranking, and fewer rows than this page starts at do; sort by "
                "relevance for every match."
                if _sorted_by_similarity(arguments)
                else ""
            ),
        )
    else:
        first = (page - 1) * k + 1
        lines.insert(
            0,
            f"{_matched(total, arguments)} Showing {first:,}–{first + len(rows) - 1:,}.",
        )
        lines.append(_order_line(arguments, rows))
        lines.append(scraped_text.SCRAPED_NOTE)
        page_lines, grouped = _page_lines(
            first, rows, experience_filtered=arguments.get("max_years") is not None
        )
        if grouped:
            lines.append(
                "A row repeating one above it is listed under it as 'also #N', with only what "
                "differs: the same company and title (brackets aside); the same title, first "
                "city and countries under another spelling of the company; or the same title, "
                "countries and stated pay under a shorter or longer name of it (ADR-0338), as "
                "one posting on two of its Boards is."
            )
        lines += page_lines
        shown_to = first + len(rows) - 1
        if shown_to < total:
            lines.append(
                f"This is the last reachable page: the Space pages {LAST_PAGE} deep, "
                f"{LAST_PAGE * k:,} rows at limit {k}; narrow the filters to see others."
                if page >= LAST_PAGE
                else f"More: page={page + 1}."
            )
    if full:
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
        "and dates go in their own fields, never in `query`. `query` ranks jobs by "
        "similarity but never narrows them: the total counts every job the filters "
        "allow, and less similar rows follow the close ones. To require a word (a "
        "language, 'ML', a title word), use `keyword`: each word must start a word, and a "
        "quoted phrase keeps its words together. In descriptions it can match only "
        "jobs with a stored description, and the answer says how many have one. "
        "For visa sponsorship or relocation use `work_authorization`, never `keyword`: a "
        "description that mentions sponsorship usually refuses it. "
        "Omit `query` to list the "
        "newest jobs that match the filters; `similar_to` a job id ranks by that "
        "job instead. With a `query`, `sort` orders only its "
        "closest matches (similarity 0.67+) — for a global order (the highest salary or "
        "newest anywhere) omit `query` and narrow with `keyword` and the filters. "
        "`company` matches as the site's company box does (any company name containing "
        "the text); beside `category` it needs a directory company: a key such as "
        "'greenhouse:stripe', or an exact name. When `company` matched as "
        "text, tell the user so, since it also takes in any other employer whose "
        "name contains that text. `sort` salary orders by "
        "the low end of each stated range; without a currency it is ordered in USD. "
        "Postings over `max_age_days` old (365 unless sent) are left out; totals count "
        "every other job the index serves, so they run higher than read_trends', "
        "which counts only jobs its classifier places in a tech category. "
        "No account applies, so a user's hidden companies "
        "are not removed. Returns the total, one page of jobs with their ids, links and "
        "ages, and — when nothing matches — the filter costing the most."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "maxLength": 200,
                "description": "The role only. Omit to list the newest jobs.",
            },
            "similar_to": {
                "type": "string",
                "maxLength": 300,
                "description": (
                    "A job id from an earlier answer: rank by that job instead of "
                    "`query`, leaving it out. Not with `query`."
                ),
            },
            "company": search_arguments.PROPERTIES["company"],
            "category": role_families.schema(
                "A job category, across the whole index or, with `company`, within that "
                "company's jobs."
            ),
            "remote": search_arguments.PROPERTIES["remote"],
            "max_years": search_arguments.PROPERTIES["max_years"],
            "required_years_at_least": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "description": (
                    "A floor on the job's required experience, not the user's: keeps jobs "
                    "asking for at least this many years ('roles needing 8+ years' is 8), "
                    "as stated or else estimated from the title's seniority. Jobs whose "
                    "experience is unknown are left out."
                ),
            },
            "employment_type": {
                "type": "string",
                "enum": list(employment_type_filter.RULES),
            },
            "country": search_arguments.PROPERTIES["country"],
            "india_place": search_arguments.PROPERTIES["india_place"],
            "location": search_arguments.PROPERTIES["location"],
            "salary_min": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Annual; needs salary_currency (30 lakh = 3000000 INR). Keeps a job whose "
                    "stated range reaches it; other currencies are converted at fixed rates."
                ),
            },
            "salary_max": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Annual; needs salary_currency. Keeps a job whose stated range starts at "
                    "or below it."
                ),
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
            "max_age_days": search_arguments.PROPERTIES["max_age_days"],
            "operators": search_arguments.PROPERTIES["operators"],
            "exclude_company": {
                "type": "string",
                "maxLength": 100,
                "description": (
                    "Leaves out every job whose company name contains this text, such as "
                    "the employer of a `similar_to` job."
                ),
            },
            "keyword": {
                "type": "string",
                "maxLength": 60,
                "description": (
                    "Words the job must contain, each at the start of a word: 'ai' finds "
                    "AI and AIOps but not Retail, 'java' also finds JavaScript. Put a phrase "
                    "in double quotes to keep its words together, in order: '\"ai engineer\"'. "
                    "In descriptions it can match only jobs with a stored description, and "
                    "the answer says how many have one. Not for visas or relocation: see "
                    "`work_authorization`."
                ),
            },
            "keyword_in": {
                "type": "string",
                "enum": ["title", "description", "both"],
                "description": "Where keyword must appear; title by default.",
            },
            "work_authorization": search_arguments.PROPERTIES["work_authorization"],
            "ats": {
                "type": "string",
                "maxLength": 40,
                "description": "One ATS, such as greenhouse or workday.",
            },
            "sort": {
                "type": "string",
                "enum": list(SORTS),
                "default": "relevance",
                "description": (
                    "salary orders by the low end of each stated range, in salary_currency "
                    "or else USD. With `query`, any sort orders only the 2,000 closest "
                    "matches."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 40,
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
    max_chars=30_000,
    argument_readers={
        "category": role_families.resolve,
        "country": search_arguments.read_country,
    },
)
