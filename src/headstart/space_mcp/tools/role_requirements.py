"""One `role_requirements` answer: what a sample of a role's or a category's postings ask for,
read from the Space's `/requirements` (ADR-0324).

A career switcher's question, "what does a data engineer typically need", answered as counts over
postings rather than by reading five of them: the tech skills their descriptions mention, the
minimum years they state, their salaries, how many are remote, and where and at whom they are.
The Space picks the sample (the postings closest to `query`, or a category's newest), counts each
requisition once however many Boards or countries copy it, and names a Board that names no company
by the Company directory's name (ADR-0332). This module only sends the arguments, which are the
Search filters `search_jobs` takes, read by the same rules (`search_arguments`), `max_age_days`'s
default among them (ADR-0338), and says what was counted, over how many, of how many.
Descriptions are scraped text, so the answer carries none of it: counts, the vocabulary's own
skill names, and the quoted company names search already shows. Unless a company is named, at most
:data:`PER_COMPANY` of one company's postings are counted, and a company named like an agency and
on no curated list is tagged "operator unverified" (ADR-0352).
"""

from __future__ import annotations

from typing import Any

from headstart.mcp_protocol.messages import ToolFailure
from headstart.space_mcp import (
    company_scope,
    role_families,
    scraped_text,
    search_arguments,
    shown_company,
)
from headstart.space_mcp.space_client import SpaceClient, SpaceRoute
from headstart.space_mcp.space_tool import SpaceTool

#: A company name past this is cut, as search cuts one.
SHORT_FIELD = 60

#: The rows the Space reads for a sample (`JobSearch.REQUIREMENTS_SAMPLE`), restated for the
#: description, which is written before any answer; an answer states its own.
SAMPLE_SIZE = 300

#: The most postings of one company a sample counts (ADR-0352): across 11 live samples a cap of
#: 8 left out a median 1.4% of the sample, binding only on the companies far above the rest
#: (DigitalXNode 15, STAFIDE 21); 5 reshaped every sample's ordinary head.
PER_COMPANY = 8

#: The Search filters this tool takes, each as `search_jobs` takes it; `max_age_days` too, so a
#: sample leaves out what search leaves out by default (ADR-0338).
_FILTERS = (
    "company",
    "remote",
    "country",
    "india_place",
    "location",
    "max_years",
    "max_age_days",
    "include_non_tech",
)


def _params(
    arguments: dict[str, Any], scope: company_scope.CompanyScope | None
) -> list[tuple[str, str]]:
    """The query string: the role and the category by this route's names, then the company
    scope and the other filters as `search_jobs` sends them."""
    params: list[tuple[str, str]] = [("strict", "1")]
    if query := (arguments.get("query") or "").strip():
        params.append(("q", query))
    if category := arguments.get("category"):
        params.append(("family", category))
    if scope is not None:
        params += scope.params()
    else:
        # A named company asks for its own postings; any other sample counts at most
        # PER_COMPANY of one company's (ADR-0352).
        params.append(("per_company", str(PER_COMPANY)))
    return params + search_arguments.filter_params(arguments)


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
    distinct, read, matching = counted["distinct"], counted["read"], counted["matching"]
    query = (arguments.get("query") or "").strip()
    category = arguments.get("category")
    admitted = (
        f"{matching:,} postings {'in the category ' if category else ''}that the filters "
        "admit, copies included"
    )
    over = counted.get("over_company_cap") or 0
    copies = read - distinct - over
    lines = [
        (
            f"What {_subject(arguments)} ask for: counted over {distinct:,} distinct postings, "
            f"of {admitted}"
            + (
                f" ({read:,} postings read; {copies:,} copies of one counted once)."
                if copies
                else "."
            )
        )
    ]
    if over:
        lines.append(
            f"At most {counted['per_company']} postings of one company are counted, so one "
            f"company's wording cannot speak for the role: {over:,} more were left out."
        )
    if query and not category:
        lines.append(
            "The query ranks postings but does not narrow them, so the total is every posting "
            "the filters admit; the sample is the ones most like the query."
        )
    window = counted["category_window"]
    if window and read < min(counted["sample_size"], matching):
        lines.append(
            f"Only {read:,} of the category's postings, copies included, are among the "
            f"{window:,} closest to the query, so the sample is smaller than it could be; a "
            "broader query, or the category alone, reaches more."
        )
    if counted.get("closest_score") is not None:
        lines.append(
            f"Similarity to the query runs from {counted['closest_score']:.2f} (the closest) "
            f"to {counted['farthest_score']:.2f} (the farthest counted)."
        )
    return lines


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
            f"{skill['skill']} {_share(skill['jobs'], described)} "
            f"({skill['employers']:,} employer{'' if skill['employers'] == 1 else 's'})"
        )
    lines += [
        f"  {kinds.get(kind, kind)}: {' · '.join(named)}"
        for kind, named in grouped.items()
    ]
    return lines


def _work_authorization_line(counted: dict[str, Any]) -> str | None:
    """How many sampled descriptions offer or refuse visa sponsorship and offer relocation, as
    HeadStart's rules read them (ADR-0333)."""
    held, described = counted.get("work_authorization"), counted["described"]
    if not held or not described:
        return None
    counted = ", ".join(
        f"{words} in {held[stance]:,} ({_share(held[stance], described)})"
        for stance, words in search_arguments.STANCE_WORDS.items()
        if stance in held
    )
    return (
        f"Of the {described:,} with a description, read by HeadStart's rules (not a field, "
        f"and they can err), the description {counted}."
    )


def _experience_line(counted: dict[str, Any]) -> str:
    experience = counted["experience"]
    bands = " · ".join(
        f"{band.replace('-', '–')}: {count:,}"
        for band, count in experience["stated"].items()
    )
    return (
        f"Minimum years the posting states: {bands}; estimated from the title's seniority: "
        f"{experience['estimated_from_title']:,}; not stated: {experience['not_stated']:,} "
        f"(of {counted['distinct']:,})."
    )


def _salary_line(counted: dict[str, Any]) -> str:
    salary = counted["salary"]
    if not salary["stating"]:
        return f"Salary: none of the {counted['distinct']:,} states one."
    currencies = " · ".join(
        f"{c['currency']}, {c['jobs']:,} posting{'' if c['jobs'] == 1 else 's'}: "
        f"{c['p25']:,} / {c['median']:,} / {c['p75']:,}"
        for c in salary["currencies"]
    )
    return (
        f"Salary, stated by {salary['stating']:,} of {counted['distinct']:,} (a year; the "
        f"middle of each stated range; 25th percentile / median / 75th, per currency): "
        f"{currencies}."
    )


def _company_count(company: dict[str, Any]) -> str:
    """How many of a company's postings the sample held, and counted when the cap cut it."""
    jobs, kept = company["jobs"], company.get("counted", company["jobs"])
    return f"{jobs:,}" if kept == jobs else f"{jobs:,} sampled, {kept:,} counted"


def _company_line(counted: dict[str, Any]) -> list[str]:
    named = " · ".join(
        shown_company.tagged(c, c["board"], SHORT_FIELD)
        + f" (key {scraped_text.quoted(c['board'], 300)}) {_company_count(c)}"
        for c in counted["companies"]
    )
    lines = [f"Companies with the most sampled postings: {named or 'none named'}."]
    if shown_company.UNVERIFIED in named:
        lines.append(shown_company.UNVERIFIED_NOTE)
    return lines


def _country_line(counted: dict[str, Any]) -> str:
    named = " · ".join(
        f"{c['name']} ({c['code']}) {c['jobs']:,}" for c in counted["countries"]
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
    placed = sum(c["jobs"] for c in categories)
    named = " · ".join(f"{_category(c['family'])} {c['jobs']:,}" for c in categories)
    return (
        f"Job categories of the sampled postings: {named}; other or no tech category: "
        f"{counted['distinct'] - placed:,}."
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
    if (
        any(arguments.get(name) not in (None, "", False) for name in _FILTERS)
        or search_arguments.operators_kept(arguments)
        or counted.get("non_tech_left_out")
    ):
        # The category is the lead's own subject, so the scope line leaves it out.
        filters = {k: v for k, v in arguments.items() if k != "category"}
        lines.append(
            search_arguments.scope_line(
                filters,
                scope,
                counted.get("operators_left_out"),
                counted.get("non_tech_left_out"),
            )
        )
    if note := search_arguments.query_constraints_note(arguments.get("query") or ""):
        lines.append(note)
    if not counted["distinct"]:
        lines.append(_no_postings(client, counted, scope))
    else:
        lines.append(scraped_text.SCRAPED_NOTE)
        if not arguments.get("category") and (category := _category_line(counted)):
            lines.append(category)
        lines += _skill_lines(counted)
        lines.append(_experience_line(counted))
        lines.append(_salary_line(counted))
        lines.append(
            f"Remote: {counted['remote']:,} of {counted['distinct']:,} "
            f"({_share(counted['remote'], counted['distinct'])})."
        )
        if stances := _work_authorization_line(counted):
            lines.append(stances)
        lines += _company_line(counted)
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
        f"the {SAMPLE_SIZE} postings closest to it among those the filters admit, each "
        "requisition counted once however many Boards or countries copy it, and at most "
        f"{PER_COMPANY} of one company's unless `company` is named. `category` alone samples the "
        "category's newest postings across the whole index; with `query`, the closest within "
        "it. Say what was counted: how many postings, of how many, and how they were picked, "
        "as the answer states it. Skills come from a fixed list of tech skills; a skill few "
        "employers mention may be one employer's self-description. For a career switcher, "
        "read the skills with the stated years. The filters mean what they mean in "
        "search_jobs, `include_non_tech` too: postings HeadStart's classifier is confident "
        "are not tech are left out of the sample unless it is true, and the answer says how "
        "many. No description text is returned."
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
            **{name: search_arguments.PROPERTIES[name] for name in _FILTERS},
            "operators": search_arguments.PROPERTIES["operators"],
        },
        "additionalProperties": False,
    },
    when_to_use=(
        "Use role_requirements for what postings for a role or category ask for: skills, "
        "years, salary, remote share, companies and countries."
    ),
    answer=answer,
    max_chars=12_000,
    argument_readers={
        "category": role_families.resolve,
        "country": search_arguments.read_country,
    },
)
