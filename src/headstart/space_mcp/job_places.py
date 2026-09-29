"""How an answer says where a set of jobs are: by country as the `country` filter reads each
place, each country with its top places, and the jobs whose places name no country.

`company_profile` says it of one company's served jobs, from `/companies/locations` (ADR-0323);
`search_jobs` at ``detail`` full says it of every job its filters match, from `/facets` with
``places=1`` (ADR-0355). The Space rolls both up alike (`serving.location_counts`), so one
sentence reads both.
"""

from __future__ import annotations

from typing import Any

from headstart.search_filters import country_filter
from headstart.space_mcp import scraped_text

#: A place past this is cut, as search cuts a location.
SHORT_FIELD = 60


def _places(places: list[dict[str, Any]]) -> str:
    return " · ".join(
        f"{scraped_text.quoted(place['location'], SHORT_FIELD)} {place['count']:,}"
        for place in places
    )


def _country(code: str) -> str:
    return country_filter.name(code) if code in country_filter.CODES else code


def said(answer: dict[str, Any], whose: str, shown: int) -> str:
    """Where ``answer``'s jobs are, as one line: its ``countries`` (the first ``shown``) with
    their places, its ``no_country`` and ``unstated`` jobs; ``whose`` names the jobs ("its 224
    served jobs")."""
    countries = answer.get("countries") or []
    no_country = answer.get("no_country") or {}
    if not countries and not no_country.get("jobs"):
        return f"Locations: none of {whose} names one."
    line = f"Where {whose} are"
    if countries:
        listed = countries[:shown]
        more = len(countries) - len(listed)
        line += (
            ", by country as search_jobs' `country` reads each place (a job naming two "
            "countries counts in both), with its top places, a first place's spellings merged"
            + (" (the first rows only)" if answer.get("capped") else "")
            + ": "
            + " · ".join(
                f"{_country(c['code'])} {c['jobs']:,} ({_places(c['places'])})"
                for c in listed
            )
            + (f" · …{more} more countries" if more > 0 else "")
            + "."
        )
    else:
        line += ": no place names a country the `country` filter reads."
    if no_country.get("jobs"):
        line += (
            f" No country is read from the places of {no_country['jobs']:,} "
            f"({_places(no_country.get('places') or [])})."
        )
    if unstated := int(answer.get("unstated") or 0):
        line += f" {unstated:,} name no place."
    return line
