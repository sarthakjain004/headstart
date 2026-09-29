"""The Search filters as the Space MCP server's tools take them: each filter argument's schema,
the name the Space reads it by, the query string it becomes, and how an answer says it.
`search_jobs` takes every one and `role_requirements` a subset (ADR-0332), so a filter reads the
same way in both.
"""

from __future__ import annotations

from typing import Any

from headstart.search_filters import country_filter, india_filter, india_gazetteer
from headstart.space_mcp import company_scope, role_families, scraped_text

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
}
#: Sent as the literal "true" `parse_filters` compares against; the company, the keyword and
#: `max_age_days` (0 is sent as nothing) are sent by their own rules below.
FLAGS = ("remote", "has_salary")
_SENT_ELSEWHERE = (*FLAGS, "company", "keyword", "keyword_in", "max_age_days")

#: `max_age_days` when the caller sends none (ADR-0322): a relevance search led with Jobs posted
#: in 2022 (round-2 critique P1-6). 0 is any age, and is not sent.
DEFAULT_MAX_AGE_DAYS = 365

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
            "A company name (matched as a substring), or a Board key from "
            "an earlier answer such as 'lever:razorpay'."
        ),
    },
    "remote": {"type": "boolean", "description": "Remote jobs only."},
    "max_years": {
        "type": "integer",
        "minimum": 0,
        "maximum": 30,
        "description": (
            "The user's own years of experience ('3+ years' is 3): keeps "
            "jobs asking for at most this many."
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
        "description": "Text the job's location contains, any country.",
    },
}


def read_country(asked: Any) -> Any:
    """The code a caller's country means — "UK", "USA", "Germany" (ADR-0322) — or ``asked``
    unchanged, for the schema's enum to refuse with the codes it knows."""
    code = country_filter.code_for(asked) if isinstance(asked, str) else None
    return code or asked


def filter_params(arguments: dict[str, Any]) -> list[tuple[str, str]]:
    """The flags and valued filters in ``arguments``, in `JobSearch.parse_filters`' own names;
    those in ``_SENT_ELSEWHERE`` are sent by each tool's own rules."""
    params: list[tuple[str, str]] = []
    for flag in FLAGS:
        if arguments.get(flag):
            params.append((SPACE_NAME[flag], "true"))
    for argument, name in SPACE_NAME.items():
        value = arguments.get(argument)
        if argument not in _SENT_ELSEWHERE and value is not None and value != "":
            params.append((name, str(value)))
    return params


def scope_line(
    arguments: dict[str, Any], scope: company_scope.CompanyScope | None
) -> str:
    """What the filters in ``arguments`` scoped the answer to, as the tools name them."""
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
    if keyword := (arguments.get("keyword") or "").strip():
        said.append(
            f"keyword {scraped_text.quoted(keyword)} in {arguments.get('keyword_in') or 'title'}"
        )
    return "Scope: " + (" · ".join(said) if said else "the whole index") + "."
