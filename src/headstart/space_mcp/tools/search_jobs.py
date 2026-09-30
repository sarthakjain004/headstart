"""One `search_jobs` answer: the Space's `/search` page and `/facets` counts, read together.

The two routes are asked the same parameters at once, with ``strict=1``, so the rows and the total
describe one query and nothing the Space would drop is dropped silently. The answer says what was
searched, how the rows are ordered (a sort with a query orders only those of the 2,000 closest
matches that score at least the floor, `JobSearch.run`, ADR-0338), the rows themselves with every scraped field quoted, and — when nothing matches —
which filter is to blame, named as this tool names it. A concise answer asks `/facets` for the
total alone (``counts=total``, ADR-0274); only ``detail=full`` pays for every option's count, and
for where every matching job is (``places=1``, ADR-0355): the jobs in each country, as ``country``
would total them, with each country's top cities, over the whole match rather than a sample.

A row carries its posting's age, flagged past a year, and its employment type as scraped beside
the `employment_type` values it counts as, and its company as the Company directory names it when
the served name is only its Board's host (`shown_company`). Rows on one page that copy one
posting — per country, or on two Boards of its employer (`requisition_copies`) — are listed under
the first of them, with only what differs; every id and link stays. A relevance page lists at most
`per_company` jobs of one company before every other company's and says how many more each has,
and a company named like an agency and on no curated list is tagged "operator unverified"
(ADR-0352).
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from typing import Any

from headstart.boards.board_identity import board_of
from headstart.jobs import requisition_copies, work_authorization
from headstart.mcp_protocol.messages import ToolFailure
from headstart.search_filters import (
    employment_type_filter,
)
from headstart.serving import per_company_cap
from headstart.space_mcp import (
    company_scope,
    job_places,
    noun_counts,
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

#: Under this, a ranking's closest row matches nothing closely (ADR-0367). On 2026-09-30, the
#: closest row of 22 queries naming real tech roles, 5 of them in one country, scored 0.733 to
#: 0.871, and of 15 naming no tech role ("pastry chef", injection text) 0.611 to 0.793: 9 of those
#: 15 score under it, and none of the 22.
WEAK_MATCH = 0.72

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

#: How many countries a full answer lists where its matching jobs are (ADR-0355): enough that a
#: comparison of two large markets finds both; send `country` for any other one.
COUNTRIES_SHOWN = 15

#: `/facets`' value asking where every matching job is (`job_search.FACET_PLACES`, ADR-0355),
#: restated as `get_job` restates its bounds: `job_search` loads the index runtime, which the MCP
#: server's own install leaves out. A test holds them equal.
FACET_PLACES = "1"

#: Longer than any id: a clipped id could not be sent back as a key.
ID_FIELD = 300

#: An employment type as scraped is a word or two ("Intern - Temporary Employee" is 27).
TYPE_FIELD = 30

#: A posting older than this many days is flagged in its row: it may well have closed.
STALE_DAYS = 365

#: At or under this `max_years`, a senior-titled row is tagged (ADR-0359): the user is new, and
#: the job's served floor is the smallest its description states (ADR-0079), which may be a side
#: clause ("1+ years of Kubernetes") under a senior role's real requirement.
SENIOR_TAG_MAX_YEARS = 2
_SENIOR_TITLE = re.compile(
    r"(?i)\b(?:senior|sr\.?|staff|principal|lead|manager|director)\b"
)
_JUNIOR_TITLE = re.compile(r"(?i)\b(?:associate|junior|jr\.?)\b")
SENIOR_TITLE_TAG = (
    "senior title: its stated minimum may be a side clause, not the role's requirement; "
    "read get_job's line on the floors it states before calling it a fit"
)

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


#: A keyword about work authorisation is one the stance rules' own prefilter matches, and
#: what hand-read descriptions say of each kind (ADR-0333): a description matching one usually
#: says the opposite of what the user hopes. Relocation is its "reloca"; the rest is sponsorship.
_WORK_AUTHORIZATION_WORD = re.compile(work_authorization.PREFILTER)
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
    found = [m.group().lower() for m in _WORK_AUTHORIZATION_WORD.finditer(keyword)]
    if any(not word.startswith("reloca") for word in found):
        return _SPONSORSHIP_KEYWORD_NOTE
    return _RELOCATION_KEYWORD_NOTE if found else None


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
    if per_company := _per_company(arguments):
        params.append(("per_company", str(per_company)))
    params.append(("k", str(arguments["limit"])))
    params.append(("page", str(arguments["page"])))
    return params


def _per_company(arguments: dict[str, Any]) -> int | None:
    """The most jobs of one company a page lists before other companies' (ADR-0352), or None
    when it does not apply: only a relevance ranking has places to spread, and a `company`
    asks for that company's jobs."""
    ranked = (arguments.get("query") or "").strip() or (
        arguments.get("similar_to") or ""
    ).strip()
    per_company = arguments.get("per_company")
    if (
        not ranked
        or arguments["sort"] != "relevance"
        or (arguments.get("company") or "").strip()
        or not per_company
    ):
        return None
    return int(per_company)


def _refuse_by_policy(arguments: dict[str, Any]) -> None:
    """What the schema cannot say: combinations the Space would misread."""
    if (arguments.get("query") or "").strip() and (
        arguments.get("similar_to") or ""
    ).strip():
        raise ToolFailure(
            "similar_to ranks by one job and query by a description of the role; send one."
        )
    search_arguments.refuse_unreadable_salary(arguments)
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
        return f" ({noun_counts.counted(days, 'day')} ago)"
    return f" ({days / 365.25:.1f} years ago: over a year old)"


def _employment_type(raw: Any, title: Any) -> str | None:
    """The type as the employer wrote it, beside the `employment_type` values the filter counts
    it as (`employment_type_filter.flags`, which the index writes): each marked where the title,
    not the type, gave it, and full-time marked where it is only the default for a type no rule
    reads (ADR-0341). None for a row that states no type and whose title gives none."""
    rules = employment_type_filter.RULES
    text = str(raw or "")
    read = [value for value, rule in rules.items() if rule.matches(text)]
    from_title = [
        f"{value}, from the title"
        for value, rule in rules.items()
        if value not in read and rule.matches(text, str(title or ""))
    ]
    if not raw:
        return f"type not stated ({'; '.join(from_title)})" if from_title else None
    said = read + from_title or ["full-time, by default"]
    return f"type {scraped_text.quoted(raw, TYPE_FIELD)} ({'; '.join(said)})"


def _senior_for_new(row: dict[str, Any], max_years: int | None) -> bool:
    """Whether a row is tagged for a user of at most `SENIOR_TAG_MAX_YEARS` years: its title
    reads Senior, Staff, Principal, Lead, Manager or Director, and not Associate or Junior. A
    disclosure only: the row is still listed (ADR-0079, ADR-0359)."""
    title = str(row.get("title") or "")
    return (
        max_years is not None
        and max_years <= SENIOR_TAG_MAX_YEARS
        and bool(_SENIOR_TITLE.search(title))
        and not _JUNIOR_TITLE.search(title)
    )


def _facts(row: dict[str, Any], today: date, max_years: int | None) -> list[str]:
    """Everything a row says after its title and company."""
    experience_filtered = max_years is not None
    facts = [scraped_text.quoted(row.get("location"), scraped_text.SHORT_FIELD)]
    if row.get("remote"):
        facts.append("remote")
    if kind := _employment_type(row.get("employment_type"), row.get("title")):
        facts.append(kind)
    if row.get("min_years") is not None:
        facts.append(f"{row['min_years']}+ yrs")
    elif experience_filtered:
        facts.append("experience not stated")
    if _senior_for_new(row, max_years):
        facts.append(SENIOR_TITLE_TAG)
    if money := _money(row):
        facts.append(money)
    posted, seen = row.get("posted_at"), row.get("first_seen")
    if posted:
        facts.append(f"posted {str(posted)[:10]}{_age(str(posted), today)}")
    if seen:
        age = "" if posted else _age(str(seen), today)
        facts.append(f"first seen {str(seen)[:10]}{age}")
    if sponsorship := _sponsorship(row):
        facts.append(sponsorship)
    if row.get(per_company_cap.PAST_COMPANY_CAP):
        facts.append("past per_company: its company's closer jobs are listed earlier")
    return facts


def _sponsorship(row: dict[str, Any]) -> str | None:
    """Which kind of sponsorship a `may_offer_sponsorship` row's description states (ADR-0367)."""
    read = row.get("sponsorship")
    if not isinstance(read, dict):
        return None
    if read.get("stance") == work_authorization.OFFERS_SPONSORSHIP:
        return "sponsorship: offers"
    because = search_arguments.may_offer_said(read.get("because") or [])
    return (
        f"sponsorship: may offer ({because})" if because else "sponsorship: may offer"
    )


def _weak_match_line(
    arguments: dict[str, Any], rows: list[dict[str, Any]]
) -> str | None:
    """A first page whose closest row scores under :data:`WEAK_MATCH`: nothing is close."""
    scores = [row["score"] for row in rows if row.get("score") is not None]
    if int(arguments["page"]) != 1 or not scores or max(scores) >= WEAK_MATCH:
        return None
    return (
        f"Nothing matches closely: the closest row scores {max(scores):.2f}, under "
        f"{WEAK_MATCH:.2f}, where rows naming the role asked for usually score. These rows "
        "are loose matches, most likely other roles; say so rather than presenting them as "
        "matches."
    )


def _score(row: dict[str, Any]) -> str:
    return f"{row['score']:.2f} " if row.get("score") is not None else ""


def _where(row: dict[str, Any]) -> str:
    return (
        f"id {scraped_text.quoted(row.get('id'), ID_FIELD)} · "
        f"{scraped_text.link(row.get('url'))}"
    )


def _company(row: dict[str, Any]) -> str:
    """The row's company as shown, tagged when its operator is unverified (ADR-0352)."""
    return shown_company.tagged(
        row, board_of(str(row.get("id") or "")), scraped_text.SHORT_FIELD
    )


def _row(number: int, row: dict[str, Any], facts: list[str]) -> str:
    said = [
        scraped_text.quoted(row.get("title")),
        _company(row),
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
        said.append(_company(row))
    said += [fact for fact in facts if fact not in head_facts]
    return (
        f"    also #{number}: {_score(row)}{' · '.join(said) or 'as above'}\n"
        f"      {_where(row)}"
    )


def _page_lines(
    first: int, rows: list[dict[str, Any]], max_years: int | None
) -> tuple[list[str], bool]:
    """One page's rows numbered from ``first``, and whether any went under another: a row copying
    an earlier row's posting (`requisition_copies`) is listed under it as "also #N". Only within the
    page, so paging and the header's row numbers are the Space's."""
    today = _today()
    facts = [_facts(row, today, max_years) for row in rows]
    groups = requisition_copies.groups(rows)
    lines = []
    for head, *others in groups:
        lines.append(_row(first + head, rows[head], facts[head]))
        lines += [
            _also(first + i, rows[i], facts[i], rows[head], facts[head]) for i in others
        ]
    return lines, len(groups) < len(rows)


def _held_line(rows: list[dict[str, Any]]) -> str | None:
    """How many more jobs each company on the page has after every other company's
    (`more_from_company`, ADR-0352), and how to list them."""
    held: dict[str, tuple[int, str]] = {}
    for row in rows:
        if more := row.get(per_company_cap.MORE_FROM_COMPANY):
            name = str(row.get("company") or "").strip()
            said = name or board_of(str(row.get("id") or ""))
            held.setdefault(per_company_cap.company(row), (int(more), said))
    if not held:
        return None
    said = "; ".join(
        f"{more:,} more from {scraped_text.quoted(name, scraped_text.SHORT_FIELD)}: send company "
        f"{scraped_text.quoted(name, scraped_text.SHORT_FIELD)}"
        for more, name in held.values()
    )
    return (
        f"Listed after every other company's jobs, past per_company: {said}. Say so rather "
        "than calling this page all there is from them."
    )


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
    if (per_company := _per_company(arguments)) is not None:
        return (
            f"Ordered by similarity to {ranked_by}, which orders the matches but does not "
            f"narrow them, with at most {per_company} jobs of one company before every other "
            "company's (per_company; 0 lists the ranking as it is)."
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
    if left_out := facets.get("non_tech_left_out"):
        return (
            f"0 jobs: the {left_out:,} that match are roles HeadStart's classifier is confident "
            "are not tech, which are left out; send include_non_tech true to see them."
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
    matched = f"{total:,} " + (
        "job matches these filters" if total == 1 else "jobs match these filters"
    )
    if (arguments.get("similar_to") or "").strip():
        return f"{matched}; similar_to only ranks them and does not narrow this count."
    if (arguments.get("query") or "").strip():
        return f"{matched}; the query only ranks them and does not narrow this count."
    return f"{matched}."


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
    counted = (
        [*params, ("places", FACET_PLACES)] if full else [*params, ("counts", "total")]
    )
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
        search_arguments.scope_line(
            arguments,
            scope,
            facets.get("operators_left_out"),
            facets.get("non_tech_left_out"),
        )
    ]
    if note := search_arguments.query_constraints_note(arguments.get("query") or ""):
        lines.append(note)
    if coverage := _coverage_line(arguments, facets):
        lines.append(coverage)
    if note := _keyword_note(arguments):
        lines.append(note)
    if full and total and (places := facets.get("places")):
        # Above the rows, so the size guard, which cuts from the end, never cuts it.
        lines.append(
            job_places.said(places, f"the {total:,} matching jobs", COUNTRIES_SHOWN)
            + " A country's count is the total search_jobs gives with that `country`."
        )
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
        if weak := _weak_match_line(arguments, rows):
            lines.insert(1, weak)
        lines.append(_order_line(arguments, rows))
        lines.append(scraped_text.SCRAPED_NOTE)
        page_lines, grouped = _page_lines(first, rows, arguments.get("max_years"))
        if grouped:
            lines.append(
                "A row repeating one above it is listed under it as 'also #N', with only what "
                "differs: the same company and title (brackets aside); the same title, first "
                "city and countries under another spelling of the company; or the same title, "
                "countries and stated pay under a shorter or longer name of it (ADR-0338), as "
                "one posting on two of its Boards is."
            )
        lines += page_lines
        if held := _held_line(rows):
            lines.append(held)
        if any(shown_company.UNVERIFIED in line for line in page_lines):
            lines.append(shown_company.UNVERIFIED_NOTE)
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
        f"closest matches (similarity {SORT_FLOOR:.2f}+) — for a global order (the highest salary or "
        "newest anywhere) omit `query` and narrow with `keyword` and the filters. "
        "`company` matches as the site's company box does (any company name containing "
        "the text); beside `category` it needs a directory company: a key such as "
        "'greenhouse:stripe', or an exact name. When `company` matched as "
        "text, tell the user so, since it also takes in any other employer whose "
        "name contains that text. `sort` salary orders by "
        "the low end of each stated range; without a currency it is ordered in USD. "
        "Jobs HeadStart's classifier is confident are not tech (a cashier, a process engineer) "
        "are left out unless `include_non_tech` is true; the answer says how many. "
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
            "employment_type": search_arguments.PROPERTIES["employment_type"],
            "country": search_arguments.PROPERTIES["country"],
            "india_place": search_arguments.PROPERTIES["india_place"],
            "location": search_arguments.PROPERTIES["location"],
            "salary_min": search_arguments.PROPERTIES["salary_min"],
            "salary_max": search_arguments.PROPERTIES["salary_max"],
            "salary_currency": search_arguments.PROPERTIES["salary_currency"],
            "has_salary": search_arguments.PROPERTIES["has_salary"],
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
            "include_non_tech": search_arguments.PROPERTIES["include_non_tech"],
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
                    "or else USD. With `query`, any sort orders only the matches scoring "
                    f"at least {SORT_FLOOR:.2f} among its {SORT_WINDOW:,} closest."
                ),
            },
            "per_company": {
                "type": "integer",
                "minimum": 0,
                "maximum": 40,
                "default": 3,
                "description": (
                    "With `query` or `similar_to` and sort relevance: at most this many jobs "
                    "of one company before every other company's; its others follow them, "
                    "and the answer says how many. 0 lists the ranking as it is. Not applied "
                    "with `company`."
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
                "description": (
                    "full adds the count behind each filter's options, and where every "
                    "matching job is: the jobs in each country (what `country` would total, "
                    "a job naming two in both) with its top cities, over the whole match."
                ),
            },
        },
        "additionalProperties": False,
    },
    when_to_use=(
        "Use search_jobs to find openings: put the role in `query`, and years, pay, place, company and dates in their own fields — never in `query`. "
        "`detail` full counts matches per country."
    ),
    answer=answer,
    max_chars=30_000,
    argument_readers={
        "category": role_families.resolve,
        "country": search_arguments.read_country,
    },
)
