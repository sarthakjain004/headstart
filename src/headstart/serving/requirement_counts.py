"""What a sample of served Jobs asks for: the counts behind an agent's requirements view
(``/requirements``, ADR-0324).

A career switcher asks "what does a data engineer need", and the honest answer is a count over
Jobs, not a paraphrase of one. :func:`summarize` takes the sampled Jobs (:meth:`JobSearch.
requirements` picks them) and first makes each requisition count once (ADR-0332): a Job whose
company names nothing but its Board is shown under the Company directory's name
(`company_name.with_directory_name`, ADR-0323's rule), and of the Jobs that copy one requisition
(`jobs.requisition_copies`, the rule a search page lists them by) only the first read is counted.
Given ``per_company``, at most that many of one company's are counted (ADR-0352). Then, per
counted Job:

- **skills**: the tech skills its description mentions (`tech_skills`), each as a share of the Jobs
  that carry a description, with how many distinct employers mention it. A skill named only in one
  employer's boilerplate reads as many Jobs and one employer, so the second figure is what tells
  demand from repetition;
- **experience**: its minimum years in bands, kept apart by source: stated by the Job (a field, or
  read from the description), estimated from the title's seniority, or not stated;
- **salary**: per currency, the quartiles of each stated range's midpoint, annual as the served
  columns hold it (ADR-0082);
- **remote**, **companies** (with the Board key of each one's first Job) and **countries** (every
  country its location names, ADR-0273's gazetteer);
- **categories**: the role family of each Job, when role assignments are given.

Nothing of a description is returned: only counts and the vocabulary's own skill names.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from headstart.boards.board_identity import board_of
from headstart.boards.company_name import FROM_DIRECTORY, with_directory_name
from headstart.jobs import requisition_copies, work_authorization
from headstart.search_filters import country_filter, country_gazetteer
from headstart.serving import per_company_cap
from headstart.serving.count_ranking import most_first
from headstart.serving.tech_skills import Vocabulary

#: The columns a sampled row needs, besides ``id``.
COLUMNS = (
    "title",
    "company",
    "location",
    "remote",
    "min_years",
    "experience_source",
    "min_salary_annual",
    "max_salary_annual",
    "salary_currency",
    "description",
)

#: How many of each list the answer carries.
SKILLS_SHOWN = 40
COMPANIES_SHOWN = 10
COUNTRIES_SHOWN = 10
CURRENCIES_SHOWN = 5

#: Minimum-years bands, the Trends level bands (critique round 2, P1-9): (label, low, high).
EXPERIENCE_BANDS = (("0-1", 0, 1), ("2-4", 2, 4), ("5-7", 5, 7), ("8+", 8, None))

#: `experience_source` values that mean the Job itself stated the years (ADR-0018): its ATS
#: field, or a number read from its description. "seniority" is an estimate from the title.
_STATED = frozenset({"field", "regex"})


def _band(years: int) -> str:
    for label, low, high in EXPERIENCE_BANDS:
        if years >= low and (high is None or years <= high):
            return label
    return EXPERIENCE_BANDS[0][0]


def _experience(jobs: list[Mapping[str, Any]]) -> dict[str, Any]:
    stated: Counter[str] = Counter()
    estimated = unstated = 0
    for job in jobs:
        years = job.get("min_years")
        if years is None:
            unstated += 1
        elif job.get("experience_source") in _STATED:
            stated[_band(int(years))] += 1
        else:
            estimated += 1
    return {
        "stated": {label: stated[label] for label, _, _ in EXPERIENCE_BANDS},
        "estimated_from_title": estimated,
        "not_stated": unstated,
    }


def _midpoint(job: Mapping[str, Any]) -> float | None:
    low, high = job.get("min_salary_annual"), job.get("max_salary_annual")
    if low is not None and high is not None:
        return (low + high) / 2
    return low if low is not None else high


def _salary(jobs: list[Mapping[str, Any]]) -> dict[str, Any]:
    by_currency: dict[str, list[float]] = {}
    for job in jobs:
        middle, currency = _midpoint(job), job.get("salary_currency")
        if middle is not None and currency:
            by_currency.setdefault(currency, []).append(float(middle))
    stating = Counter({currency: len(v) for currency, v in by_currency.items()})
    currencies = []
    for currency, count in most_first(stating)[:CURRENCIES_SHOWN]:
        values = by_currency[currency]
        if count >= 2:
            p25, median, p75 = statistics.quantiles(values, n=4, method="inclusive")
        else:
            p25 = median = p75 = values[0]
        currencies.append(
            {
                "currency": currency,
                "jobs": count,
                "p25": round(p25),
                "median": round(median),
                "p75": round(p75),
            }
        )
    return {"stating": stating.total(), "currencies": currencies}


def _companies(
    jobs: list[Mapping[str, Any]], per_company: int | None
) -> list[dict[str, Any]]:
    """The companies with the most of ``jobs``, each with how many were sampled (``jobs``) and,
    under ``per_company``, how many of those were counted (``counted``)."""
    counted: Counter[str] = Counter()
    first: dict[str, Mapping[str, Any]] = {}
    for job in jobs:
        key = per_company_cap.company(job)
        counted[key] += 1
        first.setdefault(key, job)
    return [
        {
            "company": first[key].get("company") or None,
            FROM_DIRECTORY: bool(first[key].get(FROM_DIRECTORY)),
            "board": first[key]["board"],
            "jobs": count,
            **({"counted": min(count, per_company)} if per_company else {}),
        }
        for key, count in most_first(counted)[:COMPANIES_SHOWN]
    ]


def _capped(
    jobs: list[Mapping[str, Any]], per_company: int | None
) -> list[Mapping[str, Any]]:
    """``jobs`` with the rows a search page's cap would hold past its first ``per_company`` of
    one company left out (`per_company_cap.spread`, ADR-0352): one company's boilerplate would
    otherwise set the skills and years of the whole sample."""
    if not per_company:
        return jobs
    spread = per_company_cap.spread([dict(job) for job in jobs], per_company)
    return [job for job in spread if not job.get(per_company_cap.PAST_COMPANY_CAP)]


def _countries(jobs: list[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    counted: Counter[str] = Counter()
    none = 0
    for job in jobs:
        codes = country_gazetteer.classify(job.get("location"))
        counted.update(codes)
        none += not codes
    return [
        {"code": code, "name": country_filter.name(code), "jobs": count}
        for code, count in most_first(counted)[:COUNTRIES_SHOWN]
    ], none


def _skills(
    jobs: list[Mapping[str, Any]], vocabulary: Vocabulary
) -> tuple[list[dict[str, Any]], int]:
    counted: Counter[str] = Counter()
    employers: dict[str, set[str]] = {}
    described = 0
    for job in jobs:
        text = job.get("description")
        if not text:
            continue
        described += 1
        for skill in vocabulary.mentioned(text, job.get("company")):
            counted[skill] += 1
            employers.setdefault(skill, set()).add(per_company_cap.company(job))
    return [
        {
            "skill": skill,
            "kind": vocabulary.kind_of(skill),
            "jobs": count,
            "employers": len(employers[skill]),
        }
        for skill, count in most_first(counted)[:SKILLS_SHOWN]
    ], described


def _on_its_board(
    job: Mapping[str, Any],
    board_and_name: Callable[[str], tuple[str, str | None]] | None,
) -> dict[str, Any]:
    """``job`` with its Board key, shown under the directory's name when its own company names
    nothing but that Board. Without a directory the Board is `board_of`'s guess."""
    job_id = str(job.get("id") or "")
    board, name = board_and_name(job_id) if board_and_name else (board_of(job_id), None)
    return {**with_directory_name(dict(job), board, name), "board": board}


def _work_authorization(jobs: list[Mapping[str, Any]]) -> dict[str, int]:
    """How many of ``jobs`` hold each work-authorisation stance its description states. As the
    filter does, ``may_offer_sponsorship`` counts the Jobs that offer it too (ADR-0353)."""
    held = Counter(
        stance
        for job in jobs
        for stance in work_authorization.filtered_stances(
            work_authorization.stances(
                job.get("description"),
                title=job.get("title"),
                location=job.get("location"),
            )
        )
    )
    return {stance: held[stance] for stance in work_authorization.STANCES}


def summarize(
    jobs: Iterable[Mapping[str, Any]],
    vocabulary: Vocabulary,
    family_of: Callable[[str], str | None] | None = None,
    board_and_name: Callable[[str], tuple[str, str | None]] | None = None,
    per_company: int | None = None,
) -> dict[str, Any]:
    """The counts over ``jobs``, the sampled Jobs with ``id`` and :data:`COLUMNS`, in the order
    sampled. ``board_and_name`` gives a Job id's Board and its Company directory name
    (`TrendHistory.board_and_name_of_job`). ``read`` is how many Jobs were read; ``distinct``
    how many were counted, one per requisition and at most ``per_company`` of one company's
    (ADR-0352), which every other count is over; ``over_company_cap`` how many requisitions that
    cap left out. ``companies`` still says how many of each company's were sampled."""
    read = [_on_its_board(job, board_and_name) for job in jobs]
    sampled = [read[group[0]] for group in requisition_copies.groups(read)]
    jobs = _capped(sampled, per_company)
    skills, described = _skills(jobs, vocabulary)
    countries, no_country = _countries(jobs)
    counted: dict[str, Any] = {
        "read": len(read),
        "distinct": len(jobs),
        "per_company": per_company,
        "over_company_cap": len(sampled) - len(jobs),
        "described": described,
        "skills": skills,
        "kinds": vocabulary.kinds,
        "vocabulary_size": len(vocabulary.skills),
        "experience": _experience(jobs),
        "salary": _salary(jobs),
        "remote": sum(bool(job.get("remote")) for job in jobs),
        # Each text-derived stance (ADR-0333), over the Jobs with a description.
        "work_authorization": _work_authorization(jobs),
        "companies": _companies(sampled, per_company),
        "countries": countries,
        "no_country": no_country,
    }
    if family_of is not None:
        placed = Counter(
            family
            for job in jobs
            if (family := family_of(str(job.get("id") or ""))) is not None
        )
        counted["categories"] = [
            {"family": family, "jobs": count} for family, count in most_first(placed)
        ]
    return counted
