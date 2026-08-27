"""Zwayam job-board scraper (Info Edge / Naukri Talent Cloud).

Zwayam serves every tenant's Board from one shared API host, ``public.zwayam.com``, and selects
the Board by the **career-site hostname** — so this scraper's slug is a host
(``careers.persistent.com``, ``impetus.openings.co``), the way zoho's and eightfold's are, not a
bare tenant label. Discovery of those hosts is a separate problem with its own writeup:
``docs/discovery/zwayam-tenant-discovery.md``.

Four things about the protocol are not what the earlier capture in
``experiment/ats-provider-expansion/artifacts/research_zwayam.md`` recorded, each measured against
the live endpoint on 2026-08-27 rather than carried over:

* **``companyId`` is ignored by the server.** That capture describes a two-call flow — POST the
  config endpoint for a numeric company id, base64 it, then search. Measured on 4 Boards, passing
  ``base64("1")`` returns the correct per-Board count, and passing *another tenant's* real id
  alongside Persistent's ``domain`` returns Persistent's own count. **``domain`` alone is the
  key** (see the Origin bullet), so the config call is dead weight and this scraper makes one
  request per page rather than two per Board. The field is still sent because omitting it 400s.
* **A non-default ``User-Agent`` is required, and a missing one HANGS.** Measured 2026-08-27:
  ``curl/8.7.1`` and ``python-requests``'s own default both **time out** rather than answering, so
  a caller that treats a timeout as a transient fault will retry forever. It is not a *browser*
  check — this repo's own ``headstart/0.1 (job-board reader)`` returns 200 like Chrome does — it
  is the stock tool agents that get blackholed.
* **``Origin``/``Referer`` are ignored.** The capture calls them part of what selects the Board and
  says a mismatch 403s. Measured on the same Board: omitting them entirely returns the right Board,
  and sending *another tenant's* Origin still returns the one named in ``domain``. ``domain`` alone
  is the key. They are still sent below because mirroring the real client is cheap insurance
  against the server starting to check, but **no logic may depend on them**.
* **Liveness is in the body, never the status.** A hostname that is not a registered Board answers
  ``HTTP 200`` with ``"data": null``.

**Page size is fixed at 10 and cannot be raised.** ``paginationEndNo``, ``pageSize``, ``size``,
``noOfRecords``, ``recordsPerPage``, ``limit`` and ``count`` were each probed against a 723-job
Board and every one returned the same 10 rows. So a Board costs ``ceil(jobs / 10)`` requests, and
the largest known Board (``career.axismaxlife.com``, 7,638 postings) costs ~764. That is inherent
to the endpoint, not a tuning choice.

**No detail pass.** The listing row already carries the description, so ``description`` can never
go missing the way ADR-0050 describes for the ATSes that fetch it separately.

**No reproducible rate limit.** A 2026-08-27 load test could not make the endpoint refuse:
~2,160 requests across 150 sequential, 32-wide concurrency (~94 req/s), 60 distinct ``domain``
values, and 1,500 sustained at 34 req/s — zero non-200s. One Akamai 403 was seen during 2026-08
discovery and is real, but it is rare and transient rather than a threshold to pace against. The
binding cost is **bytes, not requests**: a 10-row page is 70-200 KB, so a full 22,456-posting
scrape moves roughly 340 MB.
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
#: Currency to assume when ``currencyType`` is absent, which is most rows that carry amounts.
#:
#: Without it those figures reach ``salary.extract`` bare, and its plausibility guard falls back to
#: **USD** bounds for an unknown currency — so a real ₹17,00,000-20,00,000 reads as $1.7M,
#: implausible, and is dropped. The effect is perverse: small placeholder ranges survive while the
#: genuinely large rupee salaries are exactly the ones lost.
#:
#: Safe here because it only applies where the tenant stated nothing, and every such row measured
#: is an Indian job (2026-08-27, rows carrying amounts across 12 Boards): ``trask.openings.co``
#: reads Czech but its salaried postings are Bengaluru/Ahmedabad; ``careers.eaplworld.com`` is all
#: Delhi/Himachal. The one non-INR currency seen anywhere, QAR on ``kpmgcareersqatar.com``, is
#: always *stated* — that Board carries no amounts at all — so this default never overrides it.
#: A tenant that starts posting bare non-rupee figures would break the assumption; the guard is
#: that stated currencies always win.
_DEFAULT_CURRENCY = "INR"
#: The careers SPA declares its own path prefix here; the job deep link has to carry it.
_BASE_HREF = re.compile(r"<base\s+href=\"([^\"]*)\"", re.IGNORECASE)


def _filter_at(start: int) -> str:
    """The ``filterCri`` field for the page beginning at ``start``.

    Built fresh each call rather than string-substituted into a rendered template: the earlier
    form did ``json.dumps(...).replace('"paginationStartNo": 0', ...)``, which silently stops
    matching if the separator spacing ever changes and would then request page 0 forever.
    """
    return json.dumps(
        {
            "paginationStartNo": start,
            "selectedCall": "sort",
            "sortCriteria": {"name": "modifiedDate", "isAscending": False},
            "anyOfTheseWords": "",
        }
    )


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
    """The structured numeric pair where it says something, else the tenant's free text.

    The pair is preferred over ``experienceUIField`` because it is the one
    ``experience.extract`` can always read. Measured 2026-08-27: ``extract("Upto 4 years")``
    returns **None**, while the same job's ``minYearOfExperience``/``maxYearOfExperience`` of
    (0, 4) render as "0-4 years" and parse to 0-4. Preferring the prose there silently loses a
    stated range, so the free text is the fallback rather than the preference — it still carries
    the Boards that filled in the phrasing and left the numbers blank.

    "Both zero" is not a range: it is what an untouched form submits, so "0-0 years" would be a
    fact about the form rather than about the job.
    """
    lo, hi = source.get("minYearOfExperience"), source.get("maxYearOfExperience")
    if isinstance(lo, int | float) and isinstance(hi, int | float) and (lo or hi):
        return f"{lo:g}-{hi:g} years"
    return (source.get("experienceUIField") or "").strip() or None


def _salary(source: dict) -> str | None:
    """Every posted range, whether or not the tenant flipped its display toggle.

    ``showSal`` is the tenant's own careers-page toggle, and it is off on most rows that
    nonetheless carry amounts (19 of 23 across four Boards). An earlier version honoured it, on
    the reasoning that publishing a withheld figure asserts something the employer chose not to.
    **That was overruled deliberately: a figure beats an empty column here.**

    What makes the trade different on this ATS than it looks: salary has no second path. The
    Tier-2 description mine that supplies most of the index's parsed salaries — 84% of rows with
    a figure have no raw field string — recovers **0 of 52** on Zwayam, because Indian postings
    do not state compensation in prose. So this field is the only source there will ever be, and
    the toggle was not one filter among several but the whole gate.

    The cost is accepted knowingly: some tenants leave a form default in place (one Board posts an
    identical ``100000-200000`` with no currency across ten unrelated roles), so a minority of
    published figures are placeholders rather than offers.
    """
    lo = (str(source.get("minJobSalary") or "")).strip()
    hi = (str(source.get("maxJobSalary") or "")).strip()
    if not lo and not hi:
        return None
    currency = (source.get("currencyType") or "").strip() or _DEFAULT_CURRENCY
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
        # `domain` alone selects the Board — Origin/Referer are measurably ignored (module
        # docstring). Sent anyway to mirror the real client, but nothing here depends on them.
        # The User-Agent is NOT decorative: without a non-default one the endpoint hangs.
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
        if "://" in prefix or prefix.startswith("//"):
            # An absolute <base href> is legal HTML. Concatenating it onto the Board host would
            # build `https://host/https://cdn.../jobview/…`, so fall back rather than emit a link
            # that cannot resolve.
            _log.info(f"{self.board_key()}: absolute base href {prefix!r}, using /")
            return "/"
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
                self.mark_truncated(
                    f"stopped at the {_MAX_PAGES}-page cap with {len(rows)} of {total}"
                )
        if total is not None and rows and len(rows) < total:
            # `mark_truncated` keeps the FIRST reason, so the page cap above still wins where it
            # fired — this is the shortfall that reaches `harvest` when it did not.
            self.mark_truncated(f"read {len(rows)} of {total} postings")
        if self.truncated:
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
            if not job_url:
                # Unobserved: 0 of 182 rows across four Boards. The alternative — falling back to
                # the Board root — would emit a link that no per-Job URL shape can match, so the
                # row is dropped and logged instead of shipping an unverifiable link.
                _log.warning(
                    f"{self.board_key()}: job {native_id} has no jobUrl, skipped"
                )
                continue
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
                    url=f"https://{self.slug}{prefix}jobview/{job_url}",
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
