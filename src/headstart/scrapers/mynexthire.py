"""MyNextHire (Nexthire, by Smaclify Technologies) job-board scraper.

Measured 2026-09-30 (`docs/mynexthire/2026-09-30_reqlist-measurement.md`, ADR-0364).

A Board is a tenant label on the vendor's domain, `{slug}.mynexthire.com` (case-insensitive:
`SWIGGY` answered the same 420,476 bytes). `{slug}.careers.mynexthire.io` is a newer front for
the same tenant and is not a second Board (azentio, conseroglobal: 2 of 2 list on `.com`).
Reading a Board is one unauthenticated POST, the call the tenant's own careers page makes:

    POST /employer/careers/reqlist/get   {"source": "careers"}   -> {"reqDetailsBOList": [...]}

It returns every open requisition in one response: no pagination, no cap (aziro's 163, the
largest Board, came back whole, and each Board's careers page counts the same rows). The listing's
`jdDisplay` is the full description as plain text, so there is no detail pass: the per-posting
record the page opens (`{slug}.prod.us1.mynexthire.io/d17/careers/requisition/object`) carries
the same text as HTML, plus skills (swiggy and microlise, 2 of 2 matched word for word). The text
stays as served: `html_to_text` would eat a "<3" or "a > b" it holds as prose.

**Dead versus empty.** `*.mynexthire.com` is a wildcard DNS zone, so every label resolves. An
unknown label answers 417 "Invalid company short name", a lapsed customer 402 "… subscription
for client … has expired." — the two refusals `departed` reads, raised as gone. A Board with
nothing open answers 200 with `reqDetailsBOList: null` (meesho's page renders "Current Openings
[0]"), which is an empty Board, not an unreadable one.

**The company** is the `clientName` of the tenant's client record, the record the careers page
fetches first (`/employer/jobboard/details_by_shortname/get/{slug}/`, 35 of 35 live tenants
state one). One GET, only for a Board with postings.

**Fields.** No salary: `ctcBandLowEnd`/`ctcBandHighEnd` read 0.0 on 630 of 630 postings.
`careerStream` is the department (see `_department`). Every posting states `approvedOn`, the
approval timestamp; two reads seconds apart returned identical bytes, so it is not fabricated.
No field states remote work; `location` names it on 1 of 630.
"""

from __future__ import annotations

import base64
import json
import math
from datetime import datetime
from typing import Any

from headstart.jobs.job import Job, is_remote
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    BoardUnreadable,
    gone_board_error,
)

#: Every `employmentType` value seen in two reads of the live Boards (2026-09-30; 630 postings
#: over 30 Boards in the second), as a label the employment-type filter reads. Mapped rather than passed through because two raw values
#: misread: `third_party_consultant` flags part-time (the filter's substring "part"), and
#: `consultant` flags full-time. `onroll` is on the company's own payroll ("permanent/on-roll
#: position", pratham). `azentio_group` (one tenant's label, 12 postings) and `conversion` (2,
#: meaning stated nowhere) name no type, and neither does a value not yet seen.
_EMPLOYMENT_TYPES = {
    "full_time": "Full-time",
    "full-time": "Full-time",
    "onroll": "Full-time",
    "permanent": "Permanent",
    "contract": "Contract",
    "fixed-term-contract": "Contract",
    "consultant": "Contract",
    "third_party_consultant": "Contract",
    "intern": "Internship",
    "internship": "Internship",
}


#: The two refusals that mean the tenant has no Board: an unknown label answers 417 "Invalid
#: company short name" (3 of 3), a lapsed customer 402 "… subscription for client … has
#: expired." (2 of 2). The label space is a wildcard DNS zone, so these bodies are the only
#: dead signal there is. 417 alone is not one: a malformed body answers 417 "Invalid source".
_GONE_MARKERS = ("Invalid company short name", "subscription for client")


#: The listing request: the one field the endpoint requires. An empty body answers 417 "Source
#: is mandatory."; this one answered byte-for-byte what the careers page's own three-field body
#: does (swiggy, 420,476 bytes both).
LISTING_BODY = {"source": "careers"}


def departed(status: int, body: Any) -> str | None:
    """The refusal a listing response (its status and decoded JSON) carries when the tenant has
    no Board, or None — the dead rule the scraper and the liveness probe share."""
    if status < 400:
        return None
    message = body.get("errorMessage") if isinstance(body, dict) else None
    if isinstance(message, str) and any(m in message for m in _GONE_MARKERS):
        return message
    return None


def _experience(low: float | None, high: float | None) -> str | None:
    """`expMin`/`expMax` as "{min}-{max} years", widened to whole years. Both are stated on 630
    of 630 postings (0-0 on 34, the ones marked `fresher`); 3 are fractional ("6.5-7.5"), which
    `experience.from_field` reads as 6 with no ceiling, so the floor rounds down and the ceiling
    up."""
    if low is None or high is None:
        return None
    return f"{math.floor(low)}-{math.ceil(high)} years"


def _department(row: dict[str, Any]) -> str | None:
    """`careerStream`, unless the tenant left it "NA", where `buName` states the department.

    `careerStream` over `buName` because it is the recall-safe one for the tech gate: over 345
    postings on 12 Boards the gate kept 202 with it and 164 with `buName`, and every posting
    `buName` kept, it kept too. It is often a tenant-wide default (aziro files 159 of 163 under
    "Engineering", its sales and marketing roles too), and the gate's creep is the price.
    `buName` names a client, not a department, on aziro ("Rubrik- US"). Five tenants state "NA"
    on every posting (bindz 36, licious 32, amagi 15, azentio 12, daloopa 8 of 630)."""
    stream = row.get("careerStream")
    return row.get("buName") if stream in (None, "", "NA") else stream


def _location(row: dict[str, Any]) -> str | None:
    """Every `locationList` office, "; "-joined, else the flat `location`. One posting of 630
    names two offices (Mumbai and Pune) while its `location` says only "Mumbai"; on every other
    `location` equals the one office."""
    offices = [
        (place.get("office") or place.get("address") or "").strip()
        for place in row.get("locationList") or []
    ]
    joined = "; ".join(dict.fromkeys(o for o in offices if o))
    return joined or (row.get("location") or "").strip() or None


def _posted_at(value: str | None) -> str | None:
    """`approvedOn` ("2026-09-07T05:46:36.697+0000") as ISO-8601."""
    return (
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z").isoformat()
        if value
        else None
    )


class MyNextHireScraper(BaseScraper):
    ats = "mynexthire"
    url_shape = (
        r"https://[a-z0-9-]+\.mynexthire\.com/employer/jobs/careers"
        r"\?src=careers&p=[A-Za-z0-9+/]+={0,2}"
    )

    def url(self) -> str:
        return f"https://{self.slug}.mynexthire.com/employer/careers/reqlist/get"

    def job_url(self, native_id: str) -> str:
        context = {
            "pageType": "jd",
            "cvSource": "careers",
            "reqId": int(native_id),
            "requester": {"id": "", "code": "", "name": ""},
            "page": "careers",
            "bufilter": -1,
        }
        p = base64.b64encode(json.dumps(context, separators=(",", ":")).encode())
        return (
            f"https://{self.slug}.mynexthire.com/employer/jobs/careers"
            f"?src=careers&p={p.decode()}"
        )

    def fetch_raw(self) -> Any:
        response = self._fetch(
            "POST",
            self.url(),
            json=LISTING_BODY,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=45,
        )
        try:
            raw = response.json()
        except ValueError:
            raw = None
        message = departed(response.status_code, raw)
        if message:
            raise gone_board_error(f"{response.status_code} {message}")
        response.raise_for_status()
        if not isinstance(raw, dict) or not isinstance(
            raw.get("reqDetailsBOList", False), list | None
        ):
            self.note_unreadable_board("a reqDetailsBOList", f"{str(raw)[:120]!r}")
            raise BoardUnreadable(f"{self.board_key()}: no reqDetailsBOList")
        # A Board with nothing open states the list as null, not [] (meesho, prodindefault:
        # 2 of 2; meesho's careers page renders "Current Openings [0]" off it).
        raw["reqDetailsBOList"] = raw["reqDetailsBOList"] or []
        if raw["reqDetailsBOList"] and self.wants_company_name():
            self.adopt_company(self._client_name())
        return raw

    def _client_name(self) -> str | None:
        """`clientName` of the tenant's client record — the record the careers page loads first
        to learn how to render itself. One more GET per Board with postings, never retried and
        never worth the Board."""
        try:
            response = self._fetch_once(
                "GET",
                f"https://{self.slug}.mynexthire.com/employer/jobboard/"
                f"details_by_shortname/get/{self.slug}/",
                accept="application/json",
            )
            name = (
                response.json().get("clientName")
                if response.status_code == 200
                else None
            )
        except Exception as exc:  # noqa: BLE001 - a display name is never worth the Board
            self._log.info(
                f"{self.board_key()}: no company name — {type(exc).__name__}"
            )
            return None
        return name if isinstance(name, str) else None

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for row in raw["reqDetailsBOList"]:
            native_id = str(row["reqId"])
            location = _location(row)
            jobs.append(
                Job(
                    id=self.job_id(native_id),
                    ats=self.ats,
                    company=self.company,
                    title=row["reqTitle"].strip(),
                    location=location,
                    remote=is_remote(location),
                    department=_department(row),
                    url=self.job_url(native_id),
                    posted_at=_posted_at(row.get("approvedOn")),
                    scraped_at=scraped_at,
                    # Blank on 5 of 630 (all test postings: "Test", "test mars2").
                    description=(row.get("jdDisplay") or "").strip() or None,
                    experience=_experience(row.get("expMin"), row.get("expMax")),
                    employment_type=_EMPLOYMENT_TYPES.get(row.get("employmentType")),
                    salary=self._salary_field(row),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        """None: the only pay fields, `ctcBandLowEnd`/`ctcBandHighEnd`, read 0.0 on 630 of 630
        postings over 30 Boards (2026-09-30), whatever `reqCurrency` says."""
        return None
