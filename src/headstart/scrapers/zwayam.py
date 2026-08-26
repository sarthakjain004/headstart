"""Zwayam job-board scraper (Info Edge / Naukri Talent Cloud).

Zwayam serves every tenant's Board from one shared API host, ``public.zwayam.com``, and selects
the Board by the **career-site hostname** — so this scraper's slug is a host
(``careers.persistent.com``, ``impetus.openings.co``), the way zoho's and eightfold's are, not a
bare tenant label. Discovery of those hosts is a separate problem with its own writeup:
``docs/discovery/zwayam-tenant-discovery.md``.

Three things about the protocol are not what the earlier capture in
``experiment/ats-provider-expansion/artifacts/research_zwayam.md`` recorded, each re-verified
2026-08-27 before this scraper was written:

* **``companyId`` is ignored by the server.** That capture describes a two-call flow — POST the
  config endpoint for a numeric company id, base64 it, then search. Measured on 4 Boards, passing
  ``base64("1")`` returns the correct per-Board count, and passing *another tenant's* real id
  alongside Persistent's ``domain`` returns Persistent's own count. ``domain`` plus a matching
  ``Origin``/``Referer`` is the entire key, so the config call is dead weight and this scraper
  makes one request per page rather than two per Board. The field is still sent because omitting
  it 400s.
* **A browser ``User-Agent`` is required.** Without one both endpoints return an empty body — not
  the 403 the capture describes, so a caller that trusts the status sees "success, no jobs".
* **Liveness is in the body, never the status.** A hostname that is not a registered Board answers
  ``HTTP 200`` with ``"data": null``.

**Page size is fixed at 10 and cannot be raised.** ``paginationEndNo``, ``pageSize``, ``size``,
``noOfRecords``, ``recordsPerPage``, ``limit`` and ``count`` were each probed against a 723-job
Board and every one returned the same 10 rows. So a Board costs ``ceil(jobs / 10)`` requests, and
the largest known Board (``career.axismaxlife.com``, 7,638 postings) costs ~764. That is inherent
to the endpoint, not a tuning choice.

**No detail pass.** The listing row already carries the description, so ``description`` can never
go missing the way ADR-0050 describes for the ATSes that fetch it separately.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from headstart import http, log
from headstart.models import Job, host_of, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_log = log.get(__name__)

_API = "https://public.zwayam.com/jobs/search"
#: Sent because omitting it 400s, and ignored by the server, so its value is arbitrary. Kept as a
#: constant rather than threaded from a config call that would buy nothing.
_IGNORED_COMPANY_ID = "MQ=="  # base64("1")
#: Guards a runaway Board. At the server's fixed 10 rows a page (module docstring), 1,200 pages is
#: 12,000 postings — well above the largest Board seen (7,638) and far below anything that could
#: pin a shard.
_MAX_PAGES = 1_200
#: The careers SPA declares its own path prefix here; the job deep link has to carry it.
_BASE_HREF = re.compile(r"<base\s+href=\"([^\"]*)\"", re.IGNORECASE)

_FILTER = json.dumps(
    {
        "paginationStartNo": 0,
        "selectedCall": "sort",
        "sortCriteria": {"name": "modifiedDate", "isAscending": False},
        "anyOfTheseWords": "",
    }
)


def _filter_at(start: int) -> str:
    return _FILTER.replace('"paginationStartNo": 0', f'"paginationStartNo": {start}')


#: Fixed boundary. The body is three short constant-shaped text fields with no user content that
#: could contain it, so there is nothing for a random boundary to protect against.
_BOUNDARY = "----headstartZwayamBoundary"


def _multipart(fields: dict[str, str]) -> bytes:
    """Encode ``fields`` as ``multipart/form-data``.

    The endpoint accepts *only* multipart — a JSON or urlencoded body 400s — and the repo's HTTP
    seam is ``curl_cffi``, which does not implement requests' ``files=``. Encoding it here keeps
    the scraper on the shared transport (retries, pooling, spare-egress routing) rather than
    reaching for a second HTTP client to get one content type.
    """
    out: list[str] = []
    for name, value in fields.items():
        out.append(
            f'--{_BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        )
    out.append(f"--{_BOUNDARY}--\r\n")
    return "".join(out).encode()


def _location(source: dict) -> str | None:
    """Prefer the structured location record over the flat ``location`` string.

    ``location`` is the tenant's own free text and arrives shouted ("HYDERABAD"); the record
    carries a cased ``formattedLocation`` plus city/state/country, which is what the India
    gazetteer (ADR-0024) and the remote heuristic both read better.
    """
    records = source.get("jobLocationRecord") or []
    formatted = [
        (r.get("formattedLocation") or "").strip()
        for r in records
        if isinstance(r, dict)
    ]
    joined = ", ".join(dict.fromkeys(x for x in formatted if x))
    return joined or (source.get("location") or "").strip() or None


def _experience(source: dict) -> str | None:
    """The tenant's own phrasing where it exists, else the numeric pair.

    ``experienceUIField`` is a human string the tenant typed ("11.5-13.5 years", "Upto 4 years")
    and is the better input to ``experience.extract``'s Tier-1 field parse. It is often null, in
    which case the numeric pair stands in — but only when it says something: ``minYearOfExperience``
    and ``maxYearOfExperience`` are both 0 on a Board that never filled them in, and "0-0 years"
    is a fact about the form, not the job.
    """
    stated = (source.get("experienceUIField") or "").strip()
    if stated:
        return stated
    lo, hi = source.get("minYearOfExperience"), source.get("maxYearOfExperience")
    if not isinstance(lo, int | float) or not isinstance(hi, int | float):
        return None
    if not lo and not hi:
        return None
    return f"{lo:g}-{hi:g} years"


def _salary(source: dict) -> str | None:
    """The posted range, but only where the tenant chose to publish it.

    ``showSal`` is the tenant's own display toggle and is false on the large majority of rows
    (37 of 40 across four Boards). The amounts are still present in the payload when it is off,
    and publishing those would put a figure on the job that the employer deliberately withheld —
    so the toggle is honoured rather than the amounts being read whenever they parse.
    """
    if not source.get("showSal"):
        return None
    lo = (str(source.get("minJobSalary") or "")).strip()
    hi = (str(source.get("maxJobSalary") or "")).strip()
    if not lo and not hi:
        return None
    currency = (source.get("currencyType") or "").strip()
    span = f"{lo}-{hi}" if lo and hi else (lo or hi)
    return f"{span} {currency}".strip()


def _description(source: dict) -> str | None:
    """Longest available body. ``medium*`` is the full posting; ``short*`` is a truncated teaser
    on some tenants and the whole posting on others, so it is the fallback rather than the
    preference. The ``*WithoutHtml`` variants are pre-stripped by the vendor; the others are HTML.
    """
    for key in ("mediumDescriptionWithoutHtml", "shortDescriptionWithoutHtml"):
        value = (source.get(key) or "").strip()
        if value:
            return value
    for key in ("mediumDescription", "shortDescription"):
        value = html_to_text(source.get(key))
        if value:
            return value
    return None


def _posted_at(source: dict) -> str | None:
    """``createdDate`` is epoch milliseconds; the schema wants ISO-8601."""
    raw = source.get("createdDate")
    if not isinstance(raw, int | float) or raw <= 0:
        return None
    try:
        return datetime.fromtimestamp(raw / 1000, UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


class ZwayamScraper(BaseScraper):
    ats = "zwayam"
    #: Every field comes off the listing row — see the module docstring.
    has_detail_pass = False

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """Host only. The API keys on the hostname, so a ledger row carrying a full URL or a
        deep link has to normalise to the same string the API expects — the same reason zoho and
        personio override this."""
        return host_of(url) or tenant.strip().lower()

    def url(self) -> str:
        """The Board's human careers page. This is also what :meth:`_path_prefix` reads for the
        SPA's declared base path; the JSON lives at the shared :data:`_API` instead."""
        return f"https://{self.slug}/"

    def _headers(self) -> dict[str, str]:
        # Origin/Referer must match the Board being asked for: they are part of what selects it,
        # and a mismatch 403s. The browser UA is required — see the module docstring.
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": f"multipart/form-data; boundary={_BOUNDARY}",
            "Origin": f"https://{self.slug}",
            "Referer": f"https://{self.slug}/",
        }

    def _page(self, start: int) -> dict[str, Any]:
        response = http.fetch(
            "POST",
            _API,
            data=_multipart(
                {
                    "filterCri": _filter_at(start),
                    "domain": self.slug,
                    "companyId": _IGNORED_COMPANY_ID,
                }
            ),
            headers=self._headers(),
            timeout=45,
            **self._egress(),
        )
        response.raise_for_status()
        return response.json() or {}

    def _path_prefix(self) -> str:
        """The SPA's own base path, e.g. ``/coforge/``, used to build job deep links.

        Measured across 10 Boards: 8 declare ``/{slug}/``, one declares ``/`` and one serves no
        ``<base>`` at all. The prefix is **not derivable** from anything the API returns — the
        config endpoint's ``folder`` for coforge is ``coforgetech`` while its base path is
        ``/coforge/`` — so it is read from the served HTML, once per Board, and defaults to ``/``.

        A wrong prefix costs a dead job link rather than a lost Job, so a failed fetch degrades to
        the default instead of sinking the Board.
        """
        try:
            response = http.fetch(
                "GET",
                self.url(),
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                timeout=30,
                **self._egress(),
            )
            match = _BASE_HREF.search(response.text or "")
        except Exception as exc:  # noqa: BLE001 - a link prefix must not fail the Board
            _log.info(
                f"{self.board_key()}: base path unread ({type(exc).__name__}), using /"
            )
            return "/"
        if not match:
            return "/"
        prefix = match.group(1).strip() or "/"
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        return prefix if prefix.endswith("/") else prefix + "/"

    def fetch_raw(self) -> Any:
        """Walk the Board's pages, then read the SPA base path once for the deep links.

        Stops on the server's own ``hasMoreData``, on a short/empty page, or at
        :data:`_MAX_PAGES`. A stop at the cap is recorded in :attr:`truncated` so the merge stage
        knows this Board's list is not authoritative and holds its evictions (ADR-0053) — a
        scraper that quietly returns a partial list is what makes live postings look delisted.
        """
        rows: list[dict] = []
        total: int | None = None
        for page in range(_MAX_PAGES):
            payload = self._page(len(rows))
            data = payload.get("data")
            if not data:
                # A hostname that is not a registered Board answers 200 with data: null. Nothing
                # to scrape, and not an error — the ledger simply holds a host that no longer is.
                break
            total = data.get("totalCount", total)
            batch = [
                hit.get("_source") or {}
                for hit in (data.get("data") or [])
                if isinstance(hit, dict)
            ]
            if not batch:
                break
            rows.extend(batch)
            if not data.get("hasMoreData") or (
                total is not None and len(rows) >= total
            ):
                break
            if page == _MAX_PAGES - 1:
                self.truncated = (
                    f"stopped at the {_MAX_PAGES}-page cap with {len(rows)} of {total}"
                )
                _log.warning(f"{self.board_key()}: {self.truncated}")
        if total is not None and rows and len(rows) < total and not self.truncated:
            self.truncated = f"read {len(rows)} of {total} postings"
            _log.warning(f"{self.board_key()}: {self.truncated}")
        return {"rows": rows, "prefix": self._path_prefix() if rows else "/"}

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        rows = (raw or {}).get("rows") or []
        prefix = (raw or {}).get("prefix") or "/"
        jobs: list[Job] = []
        for source in rows:
            native_id = source.get("id")
            title = (source.get("jobTitle") or "").strip()
            if native_id is None or not title:
                continue
            location = _location(source)
            job_url = (source.get("jobUrl") or "").strip()
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{native_id}",
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    # No native remote flag: `workMode` is null on every row surveyed (40/40
                    # across four Boards), so this is the post-hoc location heuristic alone.
                    remote=is_remote(location),
                    department=(source.get("departmentName") or "").strip() or None,
                    url=(
                        f"https://{self.slug}{prefix}jobview/{job_url}"
                        if job_url
                        else f"https://{self.slug}{prefix}"
                    ),
                    posted_at=_posted_at(source),
                    scraped_at=scraped_at,
                    description=_description(source),
                    experience=_experience(source),
                    # `jobType` is "J" on every row surveyed and `employeeType`/
                    # `jobTypeFieldDisplayName` are null, so the listing states no employment
                    # type. Left None rather than mapped from a constant that means nothing.
                    employment_type=None,
                    salary=_salary(source),
                )
            )
        return jobs
