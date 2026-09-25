"""RippleHire job-board scraper ({slug}.ripplehire.com candidate career site).

Reverse-engineered by rendering the public /candidate/ portal (no login) and capturing its
job-search XHR. Three steps, no auth beyond a per-site token that the careers URL hands out:
  1. GET /candidate/careers            -> redirects to /candidate/?token={token}
  2. POST /candidate/candidatejobsearch  (form-encoded `careerSiteUrlParams` JSON, paginated)
     with `Accept: application/json` (the endpoint returns XML otherwise).
  3. GET /candidate/candidatejobdetail?token=…&jobSeq=…&source=CAREERSITE&lang=en per job —
     the search list always carries `jobDesc: null`; only this detail JSON (~5KB,
     `jobVO.jobDesc`) has the description. Param shape traced through the portal's
     RequireJS modules (entities/job.js `getJobEntityById`).

RippleHire is enterprise/IT-heavy (LTIMindtree ~937 jobs, Mphasis, UST, Tata Steel), which is
why this one was kept while the other India-tier login-walled ATSes were dropped.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    DetailLost,
    DetailRequest,
    DetailWithoutDescription,
)

#: The session token the careers page redirects onto. Public: the liveness probe reads the
#: same token before it asks for a count (ADR-0203).
CAREERS_TOKEN = re.compile(r"token=([A-Za-z0-9_-]+)")
_PAGE_SIZE = 100
# The width this detail pass has always run at — `fan_out`'s default, stated here so the async
# path resolves to it rather than to the 100-stream default (base.fan_out_async, ADR-0047).
_DETAIL_WORKERS = 8
_MAX_PAGES = 200  # our own ceiling — the natural exit is the tenant's own job count


def _location(j: dict) -> str | None:
    """Compose the city (``locations``) with the country (``jobLocation``), deduped.

    ``locations`` is the city field — 2,613 distinct values fleet-wide (Mumbai, Bangalore,
    Chennai). ``jobLocation`` is a coarse country picker — 34 distinct values (India, IND, USA).
    The old code read ``jobLocation or locations``, so on the 34.76% of jobs carrying both, the
    country won and the city — the only thing a ``geo.where(city)`` filter can match — was
    silently dropped (experiment/location-audit-2026-08-25/ripplehire.md, live-verified
    2026-08-25 across all 55 boards / 18,659 jobs: 33.21% served the wrong grain, 24.99% matched
    no city filter at all despite naming a real gazetteer city).

    Joining recovers both: split ``locations`` on its commas (itself sometimes multi-city, e.g.
    "Mumbai, Chennai, Bengaluru"), strip each part (150 jobs carry an untrimmed value, e.g.
    "Kolkata "), then append ``jobLocation`` only when it is not already a substring of the
    composed string — most India-tagged jobs already carry "India" somewhere in ``locations``
    or as ``jobLocation`` itself, and appending it unconditionally would duplicate it onto the
    end (measured: 300 jobs, 1.61%, need this de-dupe).
    """
    parts = [p.strip() for p in (j.get("locations") or "").split(",") if p.strip()]
    country = (j.get("jobLocation") or "").strip()
    if country and country.lower() not in ", ".join(parts).lower():
        parts.append(country)
    return ", ".join(parts) or None


class RippleHireScraper(BaseScraper):
    ats = "ripplehire"
    url_shape = (
        r"https://[^.]+\.ripplehire\.com/candidate/careers/?$"  # board-level: known gap
    )
    detail_workers = _DETAIL_WORKERS  # also the async stream width (base.fan_out_async)
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)
    #: The per-site token the careers URL redirects with; `fetch_raw` reads it before the Detail
    #: pass, and every detail URL carries it.
    _board_token: str | None = None

    def url(self) -> str:
        return f"https://{self.slug}.ripplehire.com/candidate/careers"

    def search_url(self) -> str:
        """The job-search endpoint the careers page's token unlocks."""
        return f"https://{self.slug}.ripplehire.com/candidate/candidatejobsearch"

    def job_url(self) -> str:
        """No per-job route exists on this ATS's public site (known gap, tracked in
        ``url_shape``'s own comment) — every Job serves the board-level careers page."""
        return self.url()

    def board_page(self) -> str:
        """The careers URL again — its ``<title>`` opens with ``"{Name} Careers |"``.

        The same page `url` names, fetched a second time rather than threaded through the token
        dance in `fetch_raw`; one request per Board, and it keeps the two concerns apart. Asked
        only when no detail record named the company first (:meth:`fetch_raw`)."""
        return self.url()

    def fetch_raw(self) -> Any:
        # step 1: the careers URL redirects to /candidate/?token=… — grab the token (the pooled
        # session follows the redirect and keeps the session cookie for the search call)
        response = self._fetch(
            "GET",
            self.url(),
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        # An HTTP error here must raise, not read as an empty board (ADR-0058 needs the 404).
        # A 200 that redirects somewhere without a token still returns [] — that is the
        # portal's shape for "no public board", not a fetch failure.
        response.raise_for_status()
        m = CAREERS_TOKEN.search(response.url)
        if not m:
            self.note_unreadable_board(
                "a redirect to /candidate/?token=", f"landed on {response.url[:60]}"
            )
            return []
        token = m.group(1)
        api = self.search_url()
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        jobs: list[dict] = []
        page = 0
        while page < _MAX_PAGES:  # the real exit is the count-based break below
            params = json.dumps(
                {
                    "page": page,
                    "search": "*:*",
                    "token": token,
                    "source": "CAREERSITE",
                    "pagesize": _PAGE_SIZE,
                }
            )
            body = urllib.parse.urlencode({"careerSiteUrlParams": params, "lang": "en"})
            response = self._fetch(
                "POST",
                api,
                data=body,
                headers=headers,
                timeout=30,
            )
            try:
                data = response.json()
            except ValueError:
                # A non-JSON body (a 5xx/403 HTML page) already fails the Board; raise it as
                # the HTTP error it is so the log names the status, not a JSONDecodeError. A
                # JSON error body still reaches the `jobVoList` lines below, as before.
                response.raise_for_status()
                raise
            if page == 0:
                # Kept from the first page: a page that ends the walk may be an error body with
                # no `totalJobCount`, and testing the shortfall against it reads `< 0`.
                total = data.get("totalJobCount", 0)
                if "jobVoList" not in data:
                    self.note_unreadable_board(
                        "a `jobVoList`",
                        f"HTTP {response.status_code}, keys {sorted(data)[:5]}",
                    )
            elif "jobVoList" not in data:
                self._log.info(
                    f"{self.board_key()}: page {page} answered {response.status_code} "
                    f"with no jobVoList — read {len(jobs)} of {total}"
                )
            batch = data.get("jobVoList") or []
            jobs.extend(batch)
            page += 1
            # Same shape as sensehq's guard, same review question: does a missing
            # `totalJobCount` make this stop after one page? The repo's own liveness ledger
            # answers it — `axisbank` reports 7,716 jobs, over 77 pages at this page size, which
            # this exact guard collected. If `totalJobCount` were commonly absent that board
            # would show ~100, not 7,716. Left unguarded for the same reason as sensehq: no live
            # evidence of the failure mode to fix against.
            if len(batch) < _PAGE_SIZE or len(jobs) >= data.get("totalJobCount", 0):
                if len(jobs) < total and "jobVoList" in data:
                    # A short page ended the walk below the stated total. A line, not
                    # `mark_truncated`: whether that shortfall costs eviction scope is not
                    # a logging decision.
                    self._log.info(
                        f"{self.board_key()}: read {len(jobs)} of "
                        f"{total} listed — a short page ended the walk"
                    )
                break
        else:
            # Reached only by exhausting the cap — every natural end breaks above. Whatever
            # sits past it is unread, not absent (ADR-0053).
            self.mark_truncated(
                f"hit the {_MAX_PAGES}-page cap at {len(jobs)} jobs — the rest unread"
            )
        # detail pass: the list never carries jobDesc — fill it from the per-job detail JSON,
        # which also carries department/posted_at/employment_type/salary that `parse` needs
        # (see `read_detail`). No tech gate: it is deferred for this ATS (ADR-0166).
        self._board_token = token
        need = [j for j in jobs if j.get("jobSeq") and not j.get("jobDesc")]
        records = self.run_detail_pass(
            need,
            key_of=lambda listing_row: str(listing_row["jobSeq"]),
            what="descriptions",
        )
        # Every detail record names the employer (`companyVO.companyName`, one value per Board),
        # so a Board with any detail read needs no title GET for its name.
        self.adopt_company(
            next((r["_company"] for r in records.values() if r.get("_company")), None)
        )
        for listing_row in need:
            # A missing detail record must not drop the Job: it ships on its listing fields.
            record = records.get(str(listing_row["jobSeq"])) or {}
            listing_row["jobDesc"] = record.get("jobDesc") or None
            listing_row["_detail"] = record
        return jobs

    def detail_request(self, listing_row: dict) -> DetailRequest:
        return DetailRequest(
            f"https://{self.slug}.ripplehire.com/candidate/candidatejobdetail"
            f"?token={self._board_token}&jobSeq={listing_row['jobSeq']}&source=CAREERSITE&lang=en",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    def read_detail(
        self, listing_row: dict, response: Any
    ) -> dict | DetailWithoutDescription:
        """The whole ``jobVO`` record, not only its ``jobDesc``.

        The search list always carries ``jobDesc: null``, so this was fetched for the
        description alone and everything else in ``jobVO`` was discarded — but that same record
        also carries ``bussinessUnit``/``jobPostingDate``/``jobTypeCustom3``/``compensationRange``,
        which are always empty on the list (experiment/location-audit-2026-08-25/ripplehire.md,
        live-verified across all 18,659 jobs on all 55 boards). Returning the full dict lets
        ``parse`` read those too, at zero extra requests.

        A response with no ``jobVO`` is a 200 this parser did not recognise, not a request that
        failed. A ``jobVO`` with no ``jobDesc`` is still kept — its other fields are real — and
        counted as the gap it is.
        """
        payload = response.json()
        record = payload.get("jobVO") or None
        if record is None:
            raise DetailLost("no jobVO on a 200")
        # The response's sibling `companyVO` names the employer; kept beside the record for
        # `fetch_raw`, which reads it once per Board.
        record["_company"] = (payload.get("companyVO") or {}).get("companyName")
        if not record.get("jobDesc"):
            return DetailWithoutDescription(record, "no jobDesc on the record")
        return record

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw:
            location = _location(j)
            # `department`/`posted_at`/`employment_type`/`salary` are always empty on the search
            # list (live-verified 2026-08-25) — read them from the `jobVO` detail record
            # `fetch_raw` already attaches as `_detail`, falling back to the list keys as a safety
            # net if that per-job fetch failed. `jobType` ("R"/"H") is a requisition-type code,
            # not an employment type, so — unlike the other three — it is deliberately NOT used
            # as a fallback for `employment_type`; `jobTypeCustom3` ("Full time", "FTE") is.
            detail = j.get("_detail") or {}
            jobs.append(
                Job(
                    id=self.job_id(j["jobSeq"]),
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("jobTitle") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=detail.get("bussinessUnit")
                    or j.get("bussinessUnit")
                    or None,
                    url=self.job_url(),
                    # `publishDetails.CAREER_SITE` is a real ISO-8601 timestamp for the same
                    # posting `jobPostingDate` gives non-ISO ("23-Jun-2020") — prefer it per
                    # Job.posted_at's own contract ("ISO-8601 if the source provides it").
                    posted_at=(
                        (detail.get("publishDetails") or {}).get("CAREER_SITE")
                        or detail.get("jobPostingDate")
                        or detail.get("careerSiteDate")
                        or j.get("jobPostingDate")
                        or j.get("careerSiteDate")
                    ),
                    scraped_at=scraped_at,
                    description=html_to_text(j.get("jobDesc")),
                    # `jobReqExp` reads identically on both surfaces ("5 - 8 Years") — unlike
                    # `jobMinExp`/`jobMaxExp`, which are YEARS on the list and MONTHS on `jobVO`
                    # for the same job (confirmed live 2026-08-25, x12 on all 160 paired records
                    # sampled). Neither of those is read here or anywhere else in this module, so
                    # that unit mismatch can't leak into `experience`.
                    experience=j.get("jobReqExp"),
                    employment_type=detail.get("jobTypeCustom3") or None,
                    salary=self._salary_field(j),
                )
            )
        return jobs

    def _salary_field(self, raw: dict) -> str | None:
        """`compensationRange`/`compensationInfo`, from the `jobVO` detail record
        (`fetch_raw`'s attached `_detail`) when it landed, else the list's own copy of the same
        two keys — the same detail-falls-back-to-list treatment `parse()` gives
        `department`/`posted_at`/`employment_type`."""
        detail = raw.get("_detail") or {}
        return (
            detail.get("compensationRange")
            or detail.get("compensationInfo")
            or raw.get("compensationRange")
            or raw.get("compensationInfo")
        )
