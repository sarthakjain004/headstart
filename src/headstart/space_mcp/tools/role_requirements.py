"""One `role_requirements` answer: what a sample of a role's or a category's postings ask for,
read from the Space's `/requirements` (ADR-0324).

A career switcher's question, "what does a data engineer typically need", answered as counts over
postings rather than by reading five of them: the tech skills their descriptions mention, the
minimum years they state, their salaries, how many are remote, and where and at whom they are.
The Space picks the sample (the postings closest to `query`, or a category's newest) and counts
it; this module only sends the arguments and says what was counted, over how many, of how many.
Descriptions are scraped text, so the answer carries none of it: counts, the vocabulary's own
skill names, and the quoted company names search already shows.
"""

from __future__ import annotations

from typing import Any

from headstart.mcp_protocol.messages import ToolFailure
from headstart.search_filters import country_filter, india_filter, india_gazetteer
from headstart.space_mcp import company_scope, role_families, scraped_text
from headstart.space_mcp.space_client import SpaceClient, SpaceRoute
from headstart.space_mcp.space_tool import SpaceTool

#: How many postings the answer counts over: the Space's default sample (ADR-0324).
SAMPLE_SIZE = 300

#: A company name past this is cut, as search cuts one.
SHORT_FIELD = 60

#: Every filter argument as this tool names it -> as `/requirements` (and `/search`) name it.
_SPACE_NAME = {
    "country": "country",
    "india_place": "india",
    "location": "location",
    "max_years": "max_years",
}

_INDIA_PLACES = [
    india_filter.WHOLE_COUNTRY,
    *india_gazetteer.REGIONS,
    *india_gazetteer.CITIES,
]


def _params(
    arguments: dict[str, Any], scope: company_scope.CompanyScope | None
) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = [("strict", "1"), ("n", str(SAMPLE_SIZE))]
    if query := (arguments.get("query") or "").strip():
        params.append(("q", query))
    if category := arguments.get("category"):
        params.append(("family", category))
    if scope is not None:
        params += scope.params()
    if arguments.get("remote"):
        params.append(("remote", "true"))
    for argument, name in _SPACE_NAME.items():
        value = arguments.get(argument)
        if value is not None and value != "":
            params.append((name, str(value)))
    return params


def _category(name: str | None) -> str:
    named = role_families.label(name) if name else None
    return f"{named} ({name})" if named else str(name)


def _subject(arguments: dict[str, Any]) -> str:
    query = (arguments.get("query") or "").strip()
    category = arguments.get("category")
    if query and category:
        return (
            f"postings in {_category(category)} closest to {scraped_text.quoted(query)}"
        )
    if query:
        return f"postings closest to {scraped_text.quoted(query)}"
    return f"the newest postings in {_category(category)}"


def _lead(arguments: dict[str, Any], counted: dict[str, Any]) -> list[str]:
    sampled, matching = counted["sampled"], counted["matching"]
    query = (arguments.get("query") or "").strip()
    category = arguments.get("category")
    admitted = (
        f"{matching:,} {'in the category ' if category else ''}that the filters admit"
    )
    lines = [
        (
            f"What {_subject(arguments)} ask for: counted over {sampled:,} postings, "
            f"of {admitted}."
        )
    ]
    if query and not category:
        lines.append(
            "The query ranks postings but does not narrow them, so the total is every posting "
            "the filters admit; the sample is the ones most like the query."
        )
    if query and category and sampled < min(SAMPLE_SIZE, matching):
        lines.append(
            f"Only {sampled:,} of the category's postings are among the 2,000 closest to the "
            "query, so the sample is smaller than asked; a broader query, or the category "
            "alone, reaches more."
        )
    if counted.get("closest_score") is not None:
        lines.append(
            f"Similarity to the query runs from {counted['closest_score']:.2f} (the closest) "
            f"to {counted['farthest_score']:.2f} (the farthest counted)."
        )
    return lines


def _scope_line(
    arguments: dict[str, Any], scope: company_scope.CompanyScope | None
) -> str | None:
    said = []
    if scope is not None:
        said.append(
            f"company {scope.company.described()}"
            if scope.company is not None
            else f"company name contains {scraped_text.quoted(scope.substring)}"
        )
        if scope.read_as:
            said.append(scope.read_as)
    if arguments.get("remote"):
        said.append("remote only")
    for argument in ("country", "india_place"):
        if arguments.get(argument):
            said.append(f"{argument} {arguments[argument]}")
    if arguments.get("location"):
        said.append(f"location contains {scraped_text.quoted(arguments['location'])}")
    if arguments.get("max_years") is not None:
        said.append(
            f"open to someone with at most {arguments['max_years']} years, jobs that state "
            "no experience included"
        )
    return ("Filters: " + " · ".join(said) + ".") if said else None


def _share(count: int, whole: int) -> str:
    return f"{round(100 * count / whole)}%" if whole else "0%"


def _skill_lines(counted: dict[str, Any]) -> list[str]:
    described, skills = counted["described"], counted["skills"]
    if not described:
        return [
            "None of the sampled postings carries a description, so no skills were counted."
        ]
    lines = [
        (
            f"Skills their descriptions mention most, as a share of the {described:,} "
            "sampled postings with a description, and how many employers mention each:"
        )
    ]
    kinds = counted.get("kinds") or {}
    grouped: dict[str, list[str]] = {}
    for skill in skills:
        grouped.setdefault(skill["kind"], []).append(
            f"{skill['skill']} {_share(skill['postings'], described)} "
            f"({skill['employers']:,} employers)"
        )
    lines += [
        f"  {kinds.get(kind, kind)}: {' · '.join(named)}"
        for kind, named in grouped.items()
    ]
    return lines


def _experience_line(counted: dict[str, Any]) -> str:
    experience = counted["experience"]
    bands = " · ".join(
        f"{band.replace('-', '–')}: {count:,}"
        for band, count in experience["stated"].items()
    )
    return (
        f"Minimum years the posting states: {bands}; estimated from the title's seniority: "
        f"{experience['estimated_from_title']:,}; not stated: {experience['not_stated']:,} "
        f"(of {counted['sampled']:,})."
    )


def _salary_line(counted: dict[str, Any]) -> str:
    salary = counted["salary"]
    if not salary["stating"]:
        return f"Salary: none of the {counted['sampled']:,} states one."
    currencies = " · ".join(
        f"{c['currency']}, {c['postings']:,} posting{'' if c['postings'] == 1 else 's'}: "
        f"{c['p25']:,} / {c['median']:,} / {c['p75']:,}"
        for c in salary["currencies"]
    )
    return (
        f"Salary, stated by {salary['stating']:,} of {counted['sampled']:,} (a year; the "
        f"middle of each stated range; 25th percentile / median / 75th, per currency): "
        f"{currencies}."
    )


def _company_line(counted: dict[str, Any]) -> str:
    named = " · ".join(
        f"{scraped_text.quoted(c['company'], SHORT_FIELD)} "
        f"(key {scraped_text.quoted(c['board'], 300)}) {c['postings']:,}"
        for c in counted["companies"]
    )
    return f"Companies with the most sampled postings: {named or 'none named'}."


def _country_line(counted: dict[str, Any]) -> str:
    named = " · ".join(
        f"{c['name']} ({c['code']}) {c['postings']:,}" for c in counted["countries"]
    )
    return (
        f"Countries their locations name (one naming several counts in each): "
        f"{named or 'none'}; no known country: {counted['no_country']:,}."
    )


def _category_line(counted: dict[str, Any]) -> str | None:
    """The sample's categories, each one this server offers named; a hidden one (ADR-0306) is
    counted with the postings in no tech category, as the site's Other row counts it."""
    offered = role_families.names()
    categories = [
        c
        for c in counted.get("categories") or []
        if offered is None or c["family"] in offered
    ]
    if not categories:
        return None
    placed = sum(c["postings"] for c in categories)
    named = " · ".join(
        f"{_category(c['family'])} {c['postings']:,}" for c in categories
    )
    return (
        f"Job categories of the sampled postings: {named}; other or no tech category: "
        f"{counted['sampled'] - placed:,}."
    )


def _no_postings(
    client: SpaceClient,
    counted: dict[str, Any],
    scope: company_scope.CompanyScope | None,
) -> str:
    if scope is not None and scope.substring is not None and not counted["matching"]:
        return (
            f"No postings to count: no company name contains "
            f"{scraped_text.quoted(scope.substring)}. "
            + company_scope.alternatives(client, scope.substring)
        )
    return "No postings to count: widen the filters or the category."


def _refuse_by_policy(arguments: dict[str, Any]) -> None:
    if not (arguments.get("query") or "").strip() and not arguments.get("category"):
        raise ToolFailure(
            "Name a role in `query` ('data engineer'), a job `category`, or both: the answer "
            "counts what those postings ask for."
        )


def answer(client: SpaceClient, arguments: dict[str, Any]) -> str:
    _refuse_by_policy(arguments)
    scope = None
    if company := (arguments.get("company") or "").strip():
        scope = company_scope.for_search(client, company, needs_boards=False)
    counted = client.read(SpaceRoute.REQUIREMENTS, _params(arguments, scope))
    lines = _lead(arguments, counted)
    if scope_line := _scope_line(arguments, scope):
        lines.append(scope_line)
    if not counted["sampled"]:
        lines.append(_no_postings(client, counted, scope))
    else:
        lines.append(scraped_text.SCRAPED_NOTE)
        if not arguments.get("category") and (category := _category_line(counted)):
            lines.append(category)
        lines += _skill_lines(counted)
        lines.append(_experience_line(counted))
        lines.append(_salary_line(counted))
        lines.append(
            f"Remote: {counted['remote']:,} of {counted['sampled']:,} "
            f"({_share(counted['remote'], counted['sampled'])})."
        )
        lines.append(_company_line(counted))
        lines.append(_country_line(counted))
        lines.append(
            f"Skills are matched against HeadStart's list of {counted['vocabulary_size']:,} "
            "tech skills, once per posting. A mention can be an employer describing itself, "
            "so a skill few employers mention is weaker evidence than its share. To read the "
            "postings, use search_jobs with the same query and filters."
        )
    if tick := counted.get("newest_tick"):
        lines.append(f"Data as of the trends tick {tick}.")
    return "\n".join(lines)


TOOL = SpaceTool(
    name="role_requirements",
    title="What a role's postings ask for",
    description=(
        "What postings for a role or a job category ask for, counted over a sample of them: "
        "the tech skills their descriptions mention (each as a share of the sampled postings, "
        "with how many employers mention it), the minimum years they state, salary quartiles "
        "per currency, the remote share, and the companies and countries with the most of "
        "them. `query` is the role only, as in search_jobs ('data engineer'); the sample is "
        f"the {SAMPLE_SIZE} postings closest to it among those the filters admit. `category` "
        "alone samples the category's newest postings across the whole index; with `query`, "
        "the closest within it. Say what was counted: how many postings, of how many, and how "
        "they were picked, as the answer states it. Skills come from a fixed list of tech "
        "skills; a skill few employers mention may be one employer's self-description. For "
        "a career switcher, read the skills with the stated years. `company` matches as "
        "search_jobs' does. No description text is returned."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "maxLength": 200,
                "description": "The role only, such as 'data engineer'.",
            },
            "category": role_families.schema(
                "A job category: its postings across the whole index, or with `query` the "
                "closest within it."
            ),
            "company": {
                "type": "string",
                "maxLength": 100,
                "description": (
                    "A company name (matched as a substring), or a Board key such as "
                    "'lever:razorpay'."
                ),
            },
            "remote": {"type": "boolean", "description": "Remote postings only."},
            "country": {
                "type": "string",
                "enum": list(country_filter.CODES),
                "description": "ISO 3166-1 alpha-2 code (US, GB, DE, IN).",
            },
            "india_place": {
                "type": "string",
                "enum": _INDIA_PLACES,
                "description": "An Indian city or region, or 'india' for anywhere in India.",
            },
            "location": {
                "type": "string",
                "maxLength": 60,
                "description": "Text the posting's location contains, any country.",
            },
            "max_years": {
                "type": "integer",
                "minimum": 0,
                "maximum": 30,
                "description": (
                    "The user's own years of experience: keeps postings asking for at most "
                    "this many, and those that state none."
                ),
            },
        },
        "additionalProperties": False,
    },
    when_to_use=(
        "Use role_requirements for what postings for a role or category ask for: skills, "
        "years, salary, remote share, companies and countries."
    ),
    answer=answer,
    max_chars=12_000,
    argument_readers={"category": role_families.resolve},
)
