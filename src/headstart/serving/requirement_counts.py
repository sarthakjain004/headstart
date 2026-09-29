"""What a sample of served Jobs asks for: the counts behind an agent's requirements view
(``/requirements``, ADR-0324).

A career switcher asks "what does a data engineer need", and the honest answer is a count over
postings, not a paraphrase of one. :func:`summarize` takes the sampled rows
(:meth:`JobSearch.requirements` picks them) and counts, per row:

- **skills**: the tech skills its description mentions (`tech_skills`), each as a share of the
  rows that carry a description, with how many distinct employers mention it. A skill named only
  in one employer's boilerplate reads as many postings and one employer, so the second figure is
  what tells demand from repetition;
- **experience**: its minimum years in bands, kept apart by source: stated by the posting (a
  field, or read from the description), estimated from the title's seniority, or not stated;
- **salary**: per currency, the quartiles of each stated range's midpoint, annual as the served
  columns hold it (ADR-0082);
- **remote**, **companies** (by served company name, with the Board key of its first row) and
  **countries** (every country its location names, ADR-0273's gazetteer);
- **categories**: the role family of each row, when a lookup is given.

Nothing of a description is returned: only counts and the vocabulary's own skill names.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from headstart.search_filters import country_filter, country_gazetteer
from headstart.serving.tech_skills import Vocabulary

#: The columns a sampled row needs, besides ``id``.
COLUMNS = (
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

#: `experience_source` values that mean the posting itself stated the years (ADR-0018): its
#: ATS field, or a number read from its description. "seniority" is an estimate from the title.
_STATED = frozenset({"field", "regex"})


def _band(years: int) -> str:
    for label, low, high in EXPERIENCE_BANDS:
        if years >= low and (high is None or years <= high):
            return label
    return EXPERIENCE_BANDS[0][0]


def _experience(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    stated: Counter[str] = Counter()
    estimated = unstated = 0
    for row in rows:
        years = row.get("min_years")
        if years is None:
            unstated += 1
        elif row.get("experience_source") in _STATED:
            stated[_band(int(years))] += 1
        else:
            estimated += 1
    return {
        "stated": {label: stated[label] for label, _, _ in EXPERIENCE_BANDS},
        "estimated_from_title": estimated,
        "not_stated": unstated,
    }


def _midpoint(row: Mapping[str, Any]) -> float | None:
    low, high = row.get("min_salary_annual"), row.get("max_salary_annual")
    if low is not None and high is not None:
        return (low + high) / 2
    return low if low is not None else high


def _salary(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    by_currency: dict[str, list[float]] = {}
    for row in rows:
        middle, currency = _midpoint(row), row.get("salary_currency")
        if middle is not None and currency:
            by_currency.setdefault(currency, []).append(float(middle))
    ranked = sorted(by_currency.items(), key=lambda item: (-len(item[1]), item[0]))
    currencies = []
    for currency, values in ranked[:CURRENCIES_SHOWN]:
        if len(values) >= 2:
            p25, median, p75 = statistics.quantiles(values, n=4, method="inclusive")
        else:
            p25 = median = p75 = values[0]
        currencies.append(
            {
                "currency": currency,
                "postings": len(values),
                "p25": round(p25),
                "median": round(median),
                "p75": round(p75),
            }
        )
    return {
        "stating": sum(len(values) for values in by_currency.values()),
        "currencies": currencies,
    }


def _board(job_id: str) -> str:
    """The Board key of a Job id: its ``ats:slug`` start."""
    return ":".join(job_id.split(":")[:2])


def _companies(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    counted: Counter[str] = Counter()
    named: dict[str, tuple[str, str]] = {}
    for row in rows:
        name = " ".join(str(row.get("company") or "").split())
        if not name:
            continue
        key = name.casefold()
        counted[key] += 1
        named.setdefault(key, (name, _board(str(row.get("id") or ""))))
    ranked = sorted(counted.items(), key=lambda item: (-item[1], item[0]))
    return [
        {"company": named[key][0], "board": named[key][1], "postings": count}
        for key, count in ranked[:COMPANIES_SHOWN]
    ]


def _countries(rows: list[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    counted: Counter[str] = Counter()
    none = 0
    for row in rows:
        codes = country_gazetteer.classify(row.get("location"))
        counted.update(codes)
        none += not codes
    ranked = sorted(counted.items(), key=lambda item: (-item[1], item[0]))
    return [
        {"code": code, "name": country_filter.name(code), "postings": count}
        for code, count in ranked[:COUNTRIES_SHOWN]
    ], none


def _skills(
    rows: list[Mapping[str, Any]], vocabulary: Vocabulary
) -> tuple[list[dict[str, Any]], int]:
    postings: Counter[str] = Counter()
    employers: dict[str, set[str]] = {}
    described = 0
    for row in rows:
        text = row.get("description")
        if not text:
            continue
        described += 1
        company = " ".join(str(row.get("company") or "").split()).casefold()
        for skill in vocabulary.mentioned(text, row.get("company")):
            postings[skill] += 1
            employers.setdefault(skill, set()).add(company)
    ranked = sorted(postings.items(), key=lambda item: (-item[1], item[0]))
    return [
        {
            "skill": skill,
            "kind": vocabulary.kind_of(skill),
            "postings": count,
            "employers": len(employers[skill]),
        }
        for skill, count in ranked[:SKILLS_SHOWN]
    ], described


def summarize(
    rows: Iterable[Mapping[str, Any]],
    vocabulary: Vocabulary,
    family_of: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """The counts over ``rows``, each a sampled Job with ``id`` and :data:`COLUMNS`."""
    rows = list(rows)
    skills, described = _skills(rows, vocabulary)
    countries, no_country = _countries(rows)
    counted: dict[str, Any] = {
        "sampled": len(rows),
        "described": described,
        "skills": skills,
        "kinds": vocabulary.kinds,
        "vocabulary_size": len(vocabulary.skills),
        "experience": _experience(rows),
        "salary": _salary(rows),
        "remote": sum(bool(row.get("remote")) for row in rows),
        "companies": _companies(rows),
        "countries": countries,
        "no_country": no_country,
    }
    if family_of is not None:
        families = Counter(family_of(str(row.get("id") or "")) for row in rows)
        counted["categories"] = [
            {"family": family, "postings": count}
            for family, count in sorted(
                families.items(), key=lambda item: (-item[1], str(item[0]))
            )
            if family is not None
        ]
    return counted
