"""The Search filters as the Space MCP server's tools take them: each filter argument's schema,
the name the Space reads it by, the query string it becomes, and how an answer says it.
`search_jobs` takes every one and `role_requirements` a subset (ADR-0332), `max_age_days` among
them (ADR-0338), so a filter reads the same way in both. So does the note a query gets when it
holds what only a filter narrows by (ADR-0338).
"""

from __future__ import annotations

import re
from typing import Any

from headstart.boards.board_operator import OPERATORS
from headstart.jobs import work_authorization
from headstart.mcp_protocol.messages import ToolFailure
from headstart.search_filters import (
    country_filter,
    country_gazetteer,
    india_filter,
    india_gazetteer,
)
from headstart.space_mcp import company_scope, role_families, scraped_text
from headstart.trends.hot_ranking import HIDDEN_BY_DEFAULT

#: Every filter argument as the tools name it -> as the Space does: the query-string name
#: `JobSearch.parse_filters` reads, which is also the `SearchFilters` field `/facets` names a
#: facet dimension or a Blocking filter by. One map, read both ways.
SPACE_NAME = {
    "remote": "remote",
    "has_salary": "has_salary",
    "max_years": "max_years",
    "employment_type": "etype",
    "india_place": "india",
    "country": "country",
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
    "max_age_days": "max_age_days",
    "required_years_at_least": "required_years_at_least",
    "exclude_company": "exclude_company",
    "work_authorization": "work_authorization",
    "include_non_tech": "include_non_tech",
}
#: Sent as the literal "true" `parse_filters` compares against; the company and the keyword are
#: sent by their own rules below, and `max_age_days` 0 (any age) as nothing.
FLAGS = ("remote", "has_salary", "include_non_tech")
_SENT_ELSEWHERE = (*FLAGS, "company", "keyword", "keyword_in")

#: `max_age_days` when the caller sends none (ADR-0322): a relevance search led with Jobs posted
#: in 2022 (round-2 critique P1-6). 0 is any age, and is not sent.
DEFAULT_MAX_AGE_DAYS = 365

#: The Operators a search keeps when the caller names none: those the Hiring now tab shows
#: unless asked (ADR-0238), so a search and the tab leave out the same staffing firms and job
#: boards.
DEFAULT_OPERATORS = [op for op in OPERATORS if op not in HIDDEN_BY_DEFAULT]

#: Each Operator as a sentence names the companies it labels.
_OPERATOR_WORDS = {
    "employer": "employers",
    "services": "IT services firms",
    "staffing": "staffing firms",
    "aggregator": "job boards",
}

#: Every place the Space's India filter names: the whole country, its region, its cities.
INDIA_PLACES = [
    india_filter.WHOLE_COUNTRY,
    *india_gazetteer.REGIONS,
    *india_gazetteer.CITIES,
]


#: The schema of each filter more than one tool takes, as `tools/list` serves it.
PROPERTIES: dict[str, dict[str, Any]] = {
    "company": {
        "type": "string",
        "maxLength": 100,
        "description": (
            "A company name, matched as the site's company box matches (any company name "
            "containing the text), or a Board key from an earlier answer such as "
            "'lever:razorpay'. In search_jobs beside `category` it needs a directory "
            "company: a key such as 'greenhouse:stripe', or an exact name."
        ),
    },
    "remote": {"type": "boolean", "description": "Remote jobs only."},
    "max_years": {
        "type": "integer",
        "minimum": 0,
        "maximum": 30,
        "description": (
            "The user's own years of experience ('3+ years' is 3): keeps "
            "jobs asking for at most this many, and jobs that state no experience "
            "(their rows say 'experience not stated')."
        ),
    },
    "country": {
        "type": "string",
        "enum": list(country_filter.CODES),
        "description": (
            "ISO 3166-1 alpha-2 code (US, GB, DE, IN); a country's name or 'UK', "
            "'USA', 'UAE' is read as its code. Matches every way a job's "
            "location names the country: its name, states, cities and codes. IN is "
            "india_place 'india'."
        ),
    },
    "india_place": {
        "type": "string",
        "enum": INDIA_PLACES,
        "description": "An Indian city or region, or 'india' for anywhere in India.",
    },
    "location": {
        "type": "string",
        "maxLength": 60,
        "description": (
            "Text the job's location contains, any country. Accents and a city's other "
            "spellings read alike: Zurich finds Zürich, Bangalore finds Bengaluru."
        ),
    },
    "max_age_days": {
        "type": "integer",
        "minimum": 0,
        "maximum": 3650,
        "default": DEFAULT_MAX_AGE_DAYS,
        "description": (
            "Leaves out postings older than this many days: the posted date, else "
            "the day HeadStart first saw the job. 365 unless sent; 0 for any age."
        ),
    },
    "include_non_tech": {
        "type": "boolean",
        "default": False,
        "description": (
            "Also list the jobs HeadStart's classifier is confident are not tech (a store "
            "cashier, a plant's process engineer), which are left out unless this is true, "
            "as the site leaves them out unless its 'Include non-tech roles' switch is on. "
            "The answer says how many were left out. Send it for a role the user really "
            "wants that is not software or tech."
        ),
    },
    "operators": {
        "type": "array",
        "items": {"type": "string", "enum": list(OPERATORS)},
        "maxItems": len(OPERATORS),
        "default": DEFAULT_OPERATORS,
        "description": (
            "Who may post the jobs, as hiring_now labels companies: employer, services (an "
            "IT services firm), staffing (an agency posting clients' contracts) or aggregator "
            "(a job board re-posting others' jobs). Staffing and aggregator are left out "
            "unless named, as on the site's Hiring now tab, or a `company` is named; the "
            "answer says how many jobs that left out. Labels come from a curated list: an "
            "unlisted company counts as an employer."
        ),
    },
    "work_authorization": {
        "type": "string",
        "enum": list(work_authorization.STANCES),
        "description": (
            "Use this, not `keyword`, for visa sponsorship or relocation: a description "
            "that mentions sponsorship usually refuses it. offers_sponsorship: the "
            "description offers or may offer visa sponsorship and nothing in it refuses "
            "it; refuses_sponsorship: it refuses sponsorship ('now or in the future', "
            "'Visa Sponsorship: No') or requires citizenship; offers_relocation: it offers "
            "relocation help. Text-derived, not a field the employer set: HeadStart's rules "
            "(headstart.jobs.work_authorization) read each sponsorship, citizenship and "
            "relocation sentence with its negation. On 660 hand-read descriptions "
            "(ADR-0333) about 1 in 35 jobs it names is wrong: precision 0.97 for "
            "offers_sponsorship and refuses_sponsorship, 0.99 for offers_relocation; it "
            "finds about 96% of each. A posting without a description never matches."
        ),
    },
}

#: How an answer names each work-authorisation stance a description holds, in
#: `work_authorization.STANCES`' order: the search scope line and role_requirements' counts.
STANCE_WORDS = {
    work_authorization.OFFERS_SPONSORSHIP: "offers visa sponsorship",
    work_authorization.REFUSES_SPONSORSHIP: "refuses visa sponsorship or requires citizenship",
    work_authorization.OFFERS_RELOCATION: "offers relocation help",
}


def read_country(asked: Any) -> Any:
    """The code a caller's country means — "UK", "USA", "Germany" (ADR-0322) — or ``asked``
    unchanged, for the schema's enum to refuse with the codes it knows."""
    code = country_filter.code_for(asked) if isinstance(asked, str) else None
    return code or asked


#: What a query holds that only a filter narrows by (ADR-0338), each with the filter to use:
#: years of experience, and pay (a currency, a "k" or lakh figure, or a bare number of 4+ digits
#: that is not a year such as 2027).
_YEARS = re.compile(
    r"(?:\d+\s*\+?\s*(?:-\s*\d+\s*)?)?\b(?:years?|yrs?)\b", re.IGNORECASE
)
_PAY = re.compile(
    r"[$€£₹¥]\s*\d[\d,.]*\s*k?\b|\b\d[\d,.]*\s*(?:k|lpa|lakhs?|crores?)\b"
    r"|\b(?:usd|eur|gbp|inr|cad|aud|chf|sgd)\b|\b(?!(?:19|20)\d\d\b)\d{4,}\b",
    re.IGNORECASE,
)
_REMOTE = re.compile(r"\bremote\b", re.IGNORECASE)


def query_constraints_note(query: str) -> str | None:
    """A line for a query holding years, pay, a known place or "remote": the query only ranks,
    and each of those narrows only as its filter (ADR-0338). None for a query holding none."""
    found = []
    if years := _YEARS.search(query):
        found.append(
            f"{scraped_text.quoted(years.group().strip())} (years: send max_years for the "
            "user's own, or required_years_at_least)"
        )
    if pay := _PAY.search(query):
        found.append(
            f"{scraped_text.quoted(pay.group().strip())} (pay: send salary_min with "
            "salary_currency)"
        )
    if places := country_gazetteer.classify(query):
        named = ", ".join(sorted(country_filter.name(code) for code in places))
        found.append(f"a place read as {named} (send country or location)")
    if _REMOTE.search(query):
        found.append('"remote" (send remote true)')
    if not found:
        return None
    return (
        "The query only ranks jobs and narrows nothing, yet it holds what only a filter "
        "narrows by: " + "; ".join(found) + "."
    )


def filter_params(arguments: dict[str, Any]) -> list[tuple[str, str]]:
    """The flags and valued filters in ``arguments``, in `JobSearch.parse_filters`' own names;
    those in ``_SENT_ELSEWHERE`` are sent by each tool's own rules."""
    params: list[tuple[str, str]] = []
    for flag in FLAGS:
        if arguments.get(flag):
            params.append((SPACE_NAME[flag], "true"))
    for argument, name in SPACE_NAME.items():
        value = arguments.get(argument)
        # `max_age_days` 0 is any age, which is sent as nothing.
        sent = value not in (None, "") and (argument != "max_age_days" or value)
        if argument not in _SENT_ELSEWHERE and sent:
            params.append((name, str(value)))
    if (kept := operators_kept(arguments)) is not None:
        params.append(("operators", ",".join(kept)))
    return params


def operators_kept(arguments: dict[str, Any]) -> list[str] | None:
    """The Operators ``arguments`` keep, in :data:`OPERATORS`' order, or None for all of them,
    which leaves nothing out and so is not sent. The Space's default is every row, so the tool's
    default is sent like any other list (ADR-0335)."""
    asked = arguments.get("operators")
    if asked is None:
        return None
    if (arguments.get("company") or "").strip() and list(asked) == DEFAULT_OPERATORS:
        # A company named is the caller's own choice of who posts: Jobgether's jobs, asked for
        # by name, are not left out as a job board's.
        return None
    if not asked:
        raise ToolFailure(
            "operators names who may post the jobs; send at least one of: "
            + ", ".join(OPERATORS)
            + "."
        )
    kept = [op for op in OPERATORS if op in asked]
    return None if len(kept) == len(OPERATORS) else kept


def _operators_said(arguments: dict[str, Any], left_out: int | None) -> str | None:
    """What ``operators`` left out, in words, with how many jobs where the Space counted them."""
    kept = operators_kept(arguments)
    if kept is None:
        return None
    dropped = " and ".join(_OPERATOR_WORDS[op] for op in OPERATORS if op not in kept)
    counted = "" if left_out is None else f": {left_out:,} jobs"
    if kept == DEFAULT_OPERATORS:
        return (
            f"{dropped} left out, as the site's Hiring now tab hides them{counted} (name "
            "them in operators to include them)"
        )
    return f"{dropped} left out{counted}"


def non_tech_said(left_out: int) -> str:
    """How many jobs the Space left out as not tech, in words: a search's scope line (ADR-0349)."""
    return (
        f"{left_out:,} jobs HeadStart's classifier is confident are not tech (a cashier, a "
        "process engineer) left out, as the site leaves them out (send include_non_tech true "
        "to include them)"
    )


def scope_line(
    arguments: dict[str, Any],
    scope: company_scope.CompanyScope | None,
    operators_left_out: int | None = None,
    non_tech_left_out: int | None = None,
) -> str:
    """What the filters in ``arguments`` scoped the answer to, as the tools name them, with how
    many jobs ``operators`` left out where the Space counted them (``operators_left_out``) and how
    many the classifier's non-tech call did (``non_tech_left_out``, ADR-0349)."""
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
    if exclude := (arguments.get("exclude_company") or "").strip():
        said.append(f"no company name containing {scraped_text.quoted(exclude)}")
    if category := arguments.get("category"):
        named = role_families.label(category)
        said.append(f"category {category}" + (f" ({named})" if named else ""))
    if arguments.get("remote"):
        said.append("remote only")
    if arguments.get("max_years") is not None:
        said.append(
            f"open to someone with at most {arguments['max_years']} years, jobs that state "
            "no experience included"
        )
    if arguments.get("required_years_at_least") is not None:
        said.append(
            f"jobs asking for at least {arguments['required_years_at_least']} years (as "
            "stated, else estimated from the title's seniority), jobs whose experience is "
            "unknown left out"
        )
    for argument in ("employment_type", "country", "india_place", "ats"):
        if arguments.get(argument):
            said.append(f"{argument} {arguments[argument]}")
    if arguments.get("location"):
        said.append(f"location contains {scraped_text.quoted(arguments['location'])}")
    currency = arguments.get("salary_currency")
    if arguments.get("salary_min") is not None:
        said.append(
            f"salary range reaching {arguments['salary_min']:,} {currency} a year or more"
        )
    if arguments.get("salary_max") is not None:
        said.append(
            f"salary range starting at {arguments['salary_max']:,} {currency} a year or less"
        )
    if (
        arguments.get("salary_min") is not None
        or arguments.get("salary_max") is not None
    ):
        said.append(
            "a range overlapping the bounds counts; other currencies are converted at "
            "HeadStart's fixed rates, and one with no rate is left out"
        )
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
    if max_age := arguments.get("max_age_days"):
        # 365 is the default whether or not the caller sent it, so the sentence holds either way.
        said.append(
            f"posted in the last {max_age:,} days"
            + (
                ", the default; send max_age_days 0 for any age"
                if max_age == DEFAULT_MAX_AGE_DAYS
                else ""
            )
            + " (a job with no readable posted date counts from its first-seen day)"
        )
    elif max_age == 0:
        said.append("any age (max_age_days 0)")
    if stance := arguments.get("work_authorization"):
        said.append(
            f"description {STANCE_WORDS.get(stance, stance)} (work_authorization "
            f"{stance}: read from the text by HeadStart's rules, not a field; they can err)"
        )
    if keyword := (arguments.get("keyword") or "").strip():
        said.append(
            f"keyword {scraped_text.quoted(keyword)} in {arguments.get('keyword_in') or 'title'}"
        )
    if operators := _operators_said(arguments, operators_left_out):
        said.append(operators)
    if arguments.get("include_non_tech"):
        said.append("non-tech roles included (include_non_tech)")
    elif non_tech_left_out:
        said.append(non_tech_said(non_tech_left_out))
    return "Scope: " + (" · ".join(said) if said else "the whole index") + "."
