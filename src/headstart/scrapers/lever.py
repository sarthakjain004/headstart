"""Lever job-board scraper (api.lever.co, with EU-instance fallback).

Lever runs a global instance (api.lever.co) and a separate EU instance (api.eu.lever.co,
behind jobs.eu.lever.co). The company slug alone doesn't say which, so we try global first
and fall back to EU when the slug isn't found there.

The company name is the public board's ``<title>``, on the instance that answered: an EU Board's
page lives on ``jobs.eu.lever.co``, and asking ``jobs.lever.co`` for it 404s — which left every EU
Board on its slug (57 Boards, 841 rows among the affected ones, 2026-09-24). Where the board page
itself is disabled but a posting page still answers, that page's JSON-LD ``hiringOrganization``
names the company instead (`veeva`, 157 rows). 79 global Boards had both disabled: their API
lists postings whose hosted links 404 (368 rows).
"""

from __future__ import annotations

import re
from typing import Any

from headstart import company_name, http, salary
from headstart.models import (
    Job,
    epoch_ms_to_iso,
    html_to_text,
    is_remote,
    requisition_of,
)
from headstart.scrapers.base import BaseScraper, classify_exception
from headstart.scrapers.job_posting_jsonld import find_job_posting, hiring_organization

#: Lever's two instances, global first — the order a scrape asks them in. Public: the liveness
#: probe asks the same two, starting from whichever the row's URL hints at (ADR-0203).
GLOBAL_API_HOST = "api.lever.co"
EU_API_HOST = "api.eu.lever.co"
API_HOSTS = (GLOBAL_API_HOST, EU_API_HOST)

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

    Known residual gap, found review round 2, not fixed: several ISO alpha-2 codes double as
    US state postal abbreviations this whole-word check can't distinguish from (CA/California,
    CO/Colorado, DE/Delaware, GA/Georgia, IN/Indiana, LA/Louisiana, MA/Massachusetts,
    MD/Maryland, PA/Pennsylvania, SC/South Carolina, SD/South Dakota, VA/Virginia, among
    others) — a posting whose ``country`` is e.g. "CO" (Colombia) with an unrelated
    "Denver, CO" entry elsewhere in ``allLocations`` would read the state tag as the country
    already being named and skip the append. Live-probed 2026-08-26 across 143 boards / 2,535
    postings for exactly this shape (country code present only inside a longer non-standalone
    entry): 0 hits. Left undocumented-but-live rather than restructured, given zero confirmed
    occurrences — a per-entry-exact-match rewrite would close it but is more invasive than this
    round's evidence justifies.
    """
    boundary = r"(?<![a-z]){}(?![a-z])"
    if re.search(boundary.format(re.escape(code.lower())), composed_lower):
        return True
    if name and re.search(boundary.format(re.escape(name.lower())), composed_lower):
        return True
    # "USA" is a common colloquial short form Lever locations use in place of the full
    # "United States" name; without this, e.g. "Select USA Remote Locations" (real freedompay
    # shape, live 2026-08-26) reads as not-yet-naming the US and gets a redundant ", US"
    # appended, defeating the no-duplicate-append purpose this function exists for.
    return code.upper() == "US" and bool(
        re.search(boundary.format("usa"), composed_lower)
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
    url_shape = r"https://jobs(\.eu)?\.lever\.co/[^/]+/[0-9a-f-]{36}"
    #: The API host that answered `fetch_raw`, which says which instance the Board lives on.
    _api_host = GLOBAL_API_HOST
    #: One posting's hosted page, for a Board whose board page is disabled (module docstring).
    _first_posting: str | None = None

    def url(self) -> str:
        return self.listing_url_on(GLOBAL_API_HOST)

    def listing_url_on(self, api_host: str) -> str:
        """This Board's postings on one of :data:`API_HOSTS`; the slug does not say which."""
        return f"https://{api_host}/v0/postings/{self.slug}?mode=json"

    def job_url(self, url: str) -> str:
        """Lever's postings API states the job's own link directly (``hostedUrl``); nothing to
        build, so this simply names that as the declared source (ADR-0153)."""
        return url

    def board_page(self) -> str:
        """The public board, whose ``<title>`` is the company name with no wrapper at all.

        The postings API carries no company name — its keys are the posting's own fields and
        nothing else — so this is the only place Lever states it (`headstart.company_name`).
        On the instance the listing answered from: an EU Board's page is on ``jobs.eu.lever.co``."""
        host = "jobs.eu.lever.co" if self._api_host == EU_API_HOST else "jobs.lever.co"
        return f"https://{host}/{self.slug}"

    def company_from_page(self, page: str | None) -> str | None:
        """The board title; else, when the board page did not answer at all, the
        ``hiringOrganization`` of one posting page (module docstring). A board page that answered
        with a title the guards refuse is not second-guessed from a posting."""
        if page is not None or not self._first_posting:
            return super().company_from_page(page)
        try:
            response = self._fetch_once("GET", self._first_posting)
        except http.RequestsError as exc:
            # Said here: base's own line after this names only the board page's answer.
            self._log.info(
                f"{self.board_key()}: no company name — posting fallback raised "
                f"{classify_exception(exc)}"
            )
            return None
        posting = (
            find_job_posting(response.text) if response.status_code == 200 else None
        )
        return company_name.from_field(
            self.ats, hiring_organization((posting or {}).get("hiringOrganization"))
        )

    def fetch_raw(self) -> Any:
        # try the global instance, then EU; a 404 on both means the company isn't on Lever —
        # which must RAISE, not read as an empty board: swallowing it left dead boards
        # "alive with zero jobs" forever, invisible to the ADR-0058 quarantine.
        for api_host in API_HOSTS:
            response = self._fetch("GET", self.listing_url_on(api_host))
            if response.status_code == 404:
                continue
            response.raise_for_status()
            self._api_host = api_host
            postings = response.json()
            self._first_posting = next(
                (p.get("hostedUrl") for p in postings if p.get("hostedUrl")), None
            )
            return postings
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
                    id=self.job_id(j["id"]),
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("text") or "").strip(),
                    location=location,
                    remote=remote,
                    department=categories.get("department") or categories.get("team"),
                    url=self.job_url(j.get("hostedUrl", "")),
                    posted_at=epoch_ms_to_iso(j.get("createdAt")),
                    scraped_at=scraped_at,
                    description=_description(j),
                    employment_type=categories.get("commitment"),
                    salary=self._salary_field(j.get("salaryRange")),
                    # What an Eightfold site in front of this Board states as `atsJobId`
                    # (ADR-0210).
                    requisition=requisition_of(j["id"]),
                )
            )
        return jobs

    def _salary_field(self, raw: dict | None) -> str | None:
        """Format Lever's structured salaryRange, e.g. '50000-70000 USD per-year-salary'."""
        raw = raw or {}
        lo, hi = raw.get("min"), raw.get("max")
        if not lo and not hi:
            return None
        return salary.to_field(
            lo or hi,
            hi if lo and hi else None,
            raw.get("currency"),
            raw.get("interval"),
        )
