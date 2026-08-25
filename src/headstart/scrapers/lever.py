"""Lever job-board scraper (api.lever.co, with EU-instance fallback).

Lever runs a global instance (api.lever.co) and a separate EU instance (api.eu.lever.co,
behind jobs.eu.lever.co). The company slug alone doesn't say which, so we try global first
and fall back to EU when the slug isn't found there.
"""

from __future__ import annotations

import re
from typing import Any

from headstart import http
from headstart.models import Job, epoch_ms_to_iso, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

# ISO 3166-1 alpha-2 -> common English short name, used only to recognize when the
# top-level `country` is already spelled out in the composed location string (so it isn't
# appended a second time). Measured 2026-08-25 over 286 live Boards / 5,796 postings: 75
# distinct codes in use, spanning far enough across the standard that a curated subset would
# risk missing one — so this is the complete alpha-2 list rather than a curated subset.
_COUNTRY_NAMES: dict[str, str] = {
    "AD": "Andorra",
    "AE": "United Arab Emirates",
    "AF": "Afghanistan",
    "AG": "Antigua and Barbuda",
    "AI": "Anguilla",
    "AL": "Albania",
    "AM": "Armenia",
    "AO": "Angola",
    "AQ": "Antarctica",
    "AR": "Argentina",
    "AS": "American Samoa",
    "AT": "Austria",
    "AU": "Australia",
    "AW": "Aruba",
    "AX": "Åland Islands",
    "AZ": "Azerbaijan",
    "BA": "Bosnia and Herzegovina",
    "BB": "Barbados",
    "BD": "Bangladesh",
    "BE": "Belgium",
    "BF": "Burkina Faso",
    "BG": "Bulgaria",
    "BH": "Bahrain",
    "BI": "Burundi",
    "BJ": "Benin",
    "BL": "Saint Barthélemy",
    "BM": "Bermuda",
    "BN": "Brunei",
    "BO": "Bolivia",
    "BQ": "Bonaire, Sint Eustatius and Saba",
    "BR": "Brazil",
    "BS": "Bahamas",
    "BT": "Bhutan",
    "BV": "Bouvet Island",
    "BW": "Botswana",
    "BY": "Belarus",
    "BZ": "Belize",
    "CA": "Canada",
    "CC": "Cocos Islands",
    "CD": "Democratic Republic of the Congo",
    "CF": "Central African Republic",
    "CG": "Republic of the Congo",
    "CH": "Switzerland",
    "CI": "Ivory Coast",
    "CK": "Cook Islands",
    "CL": "Chile",
    "CM": "Cameroon",
    "CN": "China",
    "CO": "Colombia",
    "CR": "Costa Rica",
    "CU": "Cuba",
    "CV": "Cape Verde",
    "CW": "Curaçao",
    "CX": "Christmas Island",
    "CY": "Cyprus",
    "CZ": "Czech Republic",
    "DE": "Germany",
    "DJ": "Djibouti",
    "DK": "Denmark",
    "DM": "Dominica",
    "DO": "Dominican Republic",
    "DZ": "Algeria",
    "EC": "Ecuador",
    "EE": "Estonia",
    "EG": "Egypt",
    "EH": "Western Sahara",
    "ER": "Eritrea",
    "ES": "Spain",
    "ET": "Ethiopia",
    "FI": "Finland",
    "FJ": "Fiji",
    "FK": "Falkland Islands",
    "FM": "Micronesia",
    "FO": "Faroe Islands",
    "FR": "France",
    "GA": "Gabon",
    "GB": "United Kingdom",
    "GD": "Grenada",
    "GE": "Georgia",
    "GF": "French Guiana",
    "GG": "Guernsey",
    "GH": "Ghana",
    "GI": "Gibraltar",
    "GL": "Greenland",
    "GM": "Gambia",
    "GN": "Guinea",
    "GP": "Guadeloupe",
    "GQ": "Equatorial Guinea",
    "GR": "Greece",
    "GS": "South Georgia",
    "GT": "Guatemala",
    "GU": "Guam",
    "GW": "Guinea-Bissau",
    "GY": "Guyana",
    "HK": "Hong Kong",
    "HM": "Heard Island",
    "HN": "Honduras",
    "HR": "Croatia",
    "HT": "Haiti",
    "HU": "Hungary",
    "ID": "Indonesia",
    "IE": "Ireland",
    "IL": "Israel",
    "IM": "Isle of Man",
    "IN": "India",
    "IO": "British Indian Ocean Territory",
    "IQ": "Iraq",
    "IR": "Iran",
    "IS": "Iceland",
    "IT": "Italy",
    "JE": "Jersey",
    "JM": "Jamaica",
    "JO": "Jordan",
    "JP": "Japan",
    "KE": "Kenya",
    "KG": "Kyrgyzstan",
    "KH": "Cambodia",
    "KI": "Kiribati",
    "KM": "Comoros",
    "KN": "Saint Kitts and Nevis",
    "KP": "North Korea",
    "KR": "South Korea",
    "KW": "Kuwait",
    "KY": "Cayman Islands",
    "KZ": "Kazakhstan",
    "LA": "Laos",
    "LB": "Lebanon",
    "LC": "Saint Lucia",
    "LI": "Liechtenstein",
    "LK": "Sri Lanka",
    "LR": "Liberia",
    "LS": "Lesotho",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "LY": "Libya",
    "MA": "Morocco",
    "MC": "Monaco",
    "MD": "Moldova",
    "ME": "Montenegro",
    "MF": "Saint Martin",
    "MG": "Madagascar",
    "MH": "Marshall Islands",
    "MK": "North Macedonia",
    "ML": "Mali",
    "MM": "Myanmar",
    "MN": "Mongolia",
    "MO": "Macau",
    "MP": "Northern Mariana Islands",
    "MQ": "Martinique",
    "MR": "Mauritania",
    "MS": "Montserrat",
    "MT": "Malta",
    "MU": "Mauritius",
    "MV": "Maldives",
    "MW": "Malawi",
    "MX": "Mexico",
    "MY": "Malaysia",
    "MZ": "Mozambique",
    "NA": "Namibia",
    "NC": "New Caledonia",
    "NE": "Niger",
    "NF": "Norfolk Island",
    "NG": "Nigeria",
    "NI": "Nicaragua",
    "NL": "Netherlands",
    "NO": "Norway",
    "NP": "Nepal",
    "NR": "Nauru",
    "NU": "Niue",
    "NZ": "New Zealand",
    "OM": "Oman",
    "PA": "Panama",
    "PE": "Peru",
    "PF": "French Polynesia",
    "PG": "Papua New Guinea",
    "PH": "Philippines",
    "PK": "Pakistan",
    "PL": "Poland",
    "PM": "Saint Pierre and Miquelon",
    "PN": "Pitcairn",
    "PR": "Puerto Rico",
    "PS": "Palestine",
    "PT": "Portugal",
    "PW": "Palau",
    "PY": "Paraguay",
    "QA": "Qatar",
    "RE": "Réunion",
    "RO": "Romania",
    "RS": "Serbia",
    "RU": "Russia",
    "RW": "Rwanda",
    "SA": "Saudi Arabia",
    "SB": "Solomon Islands",
    "SC": "Seychelles",
    "SD": "Sudan",
    "SE": "Sweden",
    "SG": "Singapore",
    "SH": "Saint Helena",
    "SI": "Slovenia",
    "SJ": "Svalbard and Jan Mayen",
    "SK": "Slovakia",
    "SL": "Sierra Leone",
    "SM": "San Marino",
    "SN": "Senegal",
    "SO": "Somalia",
    "SR": "Suriname",
    "SS": "South Sudan",
    "ST": "Sao Tome and Principe",
    "SV": "El Salvador",
    "SX": "Sint Maarten",
    "SY": "Syria",
    "SZ": "Eswatini",
    "TC": "Turks and Caicos Islands",
    "TD": "Chad",
    "TF": "French Southern Territories",
    "TG": "Togo",
    "TH": "Thailand",
    "TJ": "Tajikistan",
    "TK": "Tokelau",
    "TL": "Timor-Leste",
    "TM": "Turkmenistan",
    "TN": "Tunisia",
    "TO": "Tonga",
    "TR": "Turkey",
    "TT": "Trinidad and Tobago",
    "TV": "Tuvalu",
    "TW": "Taiwan",
    "TZ": "Tanzania",
    "UA": "Ukraine",
    "UG": "Uganda",
    "UM": "United States Minor Outlying Islands",
    "US": "United States",
    "UY": "Uruguay",
    "UZ": "Uzbekistan",
    "VA": "Vatican City",
    "VC": "Saint Vincent and the Grenadines",
    "VE": "Venezuela",
    "VG": "British Virgin Islands",
    "VI": "United States Virgin Islands",
    "VN": "Vietnam",
    "VU": "Vanuatu",
    "WF": "Wallis and Futuna",
    "WS": "Samoa",
    "YE": "Yemen",
    "YT": "Mayotte",
    "ZA": "South Africa",
    "ZM": "Zambia",
    "ZW": "Zimbabwe",
}


def _already_names_country(composed_lower: str, code: str, name: str | None) -> bool:
    """Whether ``composed_lower`` already spells out this country — as a whole word, not a
    substring landing inside an unrelated one.

    A bare-substring check on a 2-letter code is unsound: ``"in"`` occurs inside ``"Beijing"``,
    so a raw ``code.lower() in composed_lower`` reads a Chennai/Beijing posting as already
    naming India and skips the append — the exact case this function exists to append *for*.
    Found live, review round 1: ``_location({"allLocations": ["Chennai", "Beijing"]}, "IN")``
    returned ``"Chennai, Beijing"`` with India never named. A full country name is safer as a
    substring (multi-word names rarely land inside another word by accident) but is checked the
    same way here for one rule rather than two.
    """
    boundary = r"(?<![a-z]){}(?![a-z])"
    if re.search(boundary.format(re.escape(code.lower())), composed_lower):
        return True
    return bool(name) and bool(
        re.search(boundary.format(re.escape(name.lower())), composed_lower)
    )


def _location(categories: dict, country: str | None) -> str | None:
    """Join every ``allLocations`` entry, then append the unread top-level ``country``.

    location-audit-2026-08-25/lever.md: ``categories.location`` is only ``allLocations[0]``
    (0 mismatches confirmed across 36,565 live records) — reading it alone silently drops
    every other entry, 7.96% of postings across 213 Boards, including 34 that hide an India
    location behind an unrelated kept location. ``country`` (ISO-2, 88.60% populated) is read
    nowhere despite being orthogonal to the string: 71.8% of the time its code doesn't appear
    in the composed location at all. Appended only when neither the code nor its full name is
    already present as a whole word, so it can't duplicate what's already spelled out.
    """
    parts = [p for p in (categories.get("allLocations") or []) if p]
    if not parts:
        loc = categories.get("location")
        parts = [loc] if loc else []
    composed = ", ".join(parts)
    if country:
        name = _COUNTRY_NAMES.get(country.upper())
        if not _already_names_country(composed.lower(), country, name):
            composed = f"{composed}, {country}" if composed else country
    return composed or None


def _salary(rng: dict | None) -> str | None:
    """Format Lever's structured salaryRange, e.g. '50000-70000 USD per-year-salary'."""
    rng = rng or {}
    lo, hi = rng.get("min"), rng.get("max")
    if not lo and not hi:
        return None
    span = f"{lo}-{hi}" if lo and hi else str(lo or hi)
    return " ".join(
        str(x) for x in (span, rng.get("currency"), rng.get("interval")) if x
    )


def _description(j: dict) -> str | None:
    """The full posting text: intro + the lists sections (Requirements etc.) + closing.

    ``descriptionPlain`` alone is just the intro — the years-of-experience requirements
    almost always live in ``lists``, so dropping them starves experience extraction and
    the embedding.
    """
    parts = [j.get("descriptionPlain") or j.get("description")]
    for lst in j.get("lists") or []:
        section = "\n".join(s for s in (lst.get("text"), lst.get("content")) if s)
        if section:
            parts.append(section)
    parts.append(j.get("additionalPlain") or j.get("additional"))
    return html_to_text("\n".join(p for p in parts if p))


class LeverScraper(BaseScraper):
    ats = "lever"

    def url(self) -> str:
        return f"https://api.lever.co/v0/postings/{self.slug}?mode=json"

    def fetch_raw(self) -> Any:
        # try the global instance, then EU; a 404 on both means the company isn't on Lever —
        # which must RAISE, not read as an empty board: swallowing it left dead boards
        # "alive with zero jobs" forever, invisible to the ADR-0058 quarantine.
        for host in ("api.lever.co", "api.eu.lever.co"):
            response = http.fetch(
                "GET", f"https://{host}/v0/postings/{self.slug}?mode=json"
            )
            if response.status_code == 404:
                continue
            response.raise_for_status()
            return response.json()
        # Both instances 404: the company is not on Lever. Raised in the shape
        # `board_failures.is_gone` matches, rather than left to curl_cffi's message wording.
        raise http.RequestsError(f"HTTP Error 404: no Lever board for {self.slug}")

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw:
            categories = j.get("categories") or {}
            location = _location(categories, j.get("country"))
            workplace = (j.get("workplaceType") or "").lower()
            remote = workplace == "remote" or bool(is_remote(location))
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{j['id']}",
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("text") or "").strip(),
                    location=location,
                    remote=remote,
                    department=categories.get("department") or categories.get("team"),
                    url=j.get("hostedUrl", ""),
                    posted_at=epoch_ms_to_iso(j.get("createdAt")),
                    scraped_at=scraped_at,
                    description=_description(j),
                    employment_type=categories.get("commitment"),
                    salary=_salary(j.get("salaryRange")),
                )
            )
        return jobs
