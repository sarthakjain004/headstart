"""Spire2Grow (iExchange) career-site scraper — ``jobs.myntra.com``, ``jobs.tatacommunications.com``.

A Spire2Grow career site is a Flutter web app on the customer's own host that reads one public
API, ``io.spire2grow.com/ies/v1/p``. Everything below was measured 2026-09-30 against the live
API and written up in ``docs/spire2grow/2026-09-30_career-api-measurement.md``: every production
workspace discovery found (4, 307 postings), plus the vendor's largest UAT demo (1,547 rows) for
paging.

**A Board is its career-site host, not its workspace id.** The app resolves its host to a
workspace with ``/workspaceId?domain={host}`` and sends that id as a ``workspaceid`` header on
every other call. The host is the slug for three measured reasons: the public job link is
``https://{host}/jobs/{displayId}`` and no workspace endpoint names the host back, so a
workspace-keyed Board could not build its own links; the workspace id is case-sensitive
(``myntra-93as3`` answers 404 where ``MYNTRA-93as3`` answers the Board), which board keys, compared
case-folded, cannot hold; and an unknown workspace answers the listing with HTTP 200 and zero
rows, where an unknown host is a real 404 (``No Workspace Found for the domain name``). The domain
lookup is itself case-sensitive (``JOBS.MYNTRA.COM`` 404s), so the slug is the lower-cased host.

**Headers.** ``workspaceid`` is the only one the API needs: without it every call is a 401.
``workflowid`` (the issue's ``WFU_<ms>``) is ignored — fresh, stale, constant, garbage and absent
values all answered the same 200 on 7 of 7 tries — and ``language`` changed nothing (``fr`` read
the same 53 postings). The app's own ``language: en`` is sent anyway. The User-Agent does not
matter (``headstart/0.1``, none and ``python-requests`` all 200).

**The listing is one request, and it is the whole posting.** ``/requisition/_search`` pages from
1 (``page=0`` is a 401) and took ``size`` up to 2,000 unclamped on the 1,547-row UAT demo, so
``size=1000`` reads every production Board measured in one call. The envelope states ``total``,
which equalled the rows served on all four Boards. Paging past ``page * size > 10,000`` answers
401 (an Elasticsearch result window), so the walk stops there and says so. Small pages are **not**
stable: walking Tata Communications at ``size=50`` returned 211 rows with a duplicate and a miss
on 2 of 3 walks, so rows are de-duplicated by ``displayId`` and a shortfall against ``total`` is
reported. The per-posting ``/requisition/displayId/{id}`` detail returned exactly the listing
row's keys and description (1 of 1 compared), so there is no detail pass.

**The search endpoint is metered per client address, across every tenant.** ``_search`` answers
429 ``Too many requests`` with ``X-Rate-Limit-Retry-After-Seconds`` after about three calls in a
burst (8 of 14 got through at 10 s spacing), and a drained budget refused the other two tenants too (6 of 6); ``_count``,
``workspaceId`` and the detail are unmetered (600 ``_count`` calls at 167 req/s, zero 429s). So
every ``_search`` in the process goes through one :class:`Pacer`, and a 429 rests it for the
window the header states. One Board costs one ``_search``.

**Fields.** ``jobPosting.startDate`` is the date the page shows as "Posted" (epoch ms; unchanged
across two fetches 20 s apart on 53 of 53, and equal to ``createdOn`` on 303 of 307).
``jobPosting.endDate`` is never in the past on a listed posting (0 of 307) and is not mapped.
``requiredExperienceInMonths`` is stated on 307 of 307 as whole months (12 of 614 bounds are not a
multiple of 12), passed as "N-M months", which ``experience.from_field`` rounds outward to whole
years. ``jobType`` is the workplace: ONSITE, HYBRID, ``NA`` or absent; no REMOTE value was observed
and no location or title says remote (0 of 307). ``departmentName`` is stated on Myntra and Tata
(263 of 264) and absent on Spire. ``jobLocation`` is a list, joined "; " without repeats: every
posting measured names one place, 38 of 43 Spire rows listing that same place twice. No field
states a salary. ``aboutCompany`` is the employer's boilerplate and
``recruiter`` a named person's email; neither is read.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from headstart.jobs.job import Job, host_of, html_to_text, is_remote
from headstart.network import http
from headstart.scrapers.base import USER_AGENT, BaseScraper, gone_board_error
from headstart.scrapers.pacer import Pacer

_API = "https://io.spire2grow.com/ies/v1/p"
#: Rows asked per `_search` page. Honoured unclamped to 2,000 (module docstring); the largest
#: production Board measured is 211, so one page reads every known Board.
_SIZE = 1000
#: `page * size` beyond this answers 401: the index's result window (module docstring).
_WINDOW = 10_000
#: Spacing of `_search` starts across the process. At 10 s spacing 8 of 14 calls got through and
#: `X-Rate-Limit-Remaining` alternated between two counters, which reads as two servers allowing
#: about two calls a minute each. 31 s keeps even every call landing on one server under that.
#: One Board is one call, so the four known Boards take about two minutes.
_SPACING_S = 31.0
#: What a 429 without the header rests for: the longest `X-Rate-Limit-Retry-After-Seconds` seen.
_DEFAULT_REST_S = 60.0
_TRIES = 3
#: The fetch seam's own retry ladder, minus 429: its seconds of backoff cannot outlast the window.
_RETRY_ON = http.TRANSIENT - {429}

_PACER = Pacer(_SPACING_S)

#: `employmentType` -> label. The three values observed on 307 postings (304 / 2 / 1); an
#: unobserved one passes through as the API spells it.
_TYPE_LABELS: dict[str, str] = {
    "FULL_TIME": "Full-Time",
    "PART_TIME": "Part-Time",
    "APPRENTICESHIP": "Apprenticeship",
}


class _RateLimited(Exception):
    """Still refused after resting through the stated window `_TRIES - 1` times."""


def _location(row: dict) -> str | None:
    places: list[str] = []
    for place in row.get("jobLocation") or []:
        name = (place or {}).get("fqLocationName")
        if name and name not in places:
            places.append(name)
    return "; ".join(places) or None


def _remote(row: dict, location: str | None) -> bool | None:
    """The stated workplace, else the location guess. HYBRID is not remote (ashby's rule), and
    ``NA`` states nothing."""
    workplace = (row.get("jobType") or "").upper()
    if workplace == "REMOTE":  # not observed; the one value that could say remote
        return True
    if workplace == "ONSITE":
        return False
    if workplace == "HYBRID":
        return None
    return is_remote(location)


def _experience(row: dict) -> str | None:
    months = row.get("requiredExperienceInMonths") or {}
    lo, hi = months.get("from"), months.get("to")
    if lo is None:
        return None
    return f"{lo} months" if hi is None else f"{lo}-{hi} months"


def _posted_at(row: dict) -> str | None:
    ms = (row.get("jobPosting") or {}).get("startDate")
    if not isinstance(ms, int | float):
        return None
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


class Spire2GrowScraper(BaseScraper):
    ats = "spire2grow"
    # scraper: f"https://{host}/jobs/{displayId}". Rendered in Chrome 2026-09-30: the app reads
    # the posting through `/requisition/displayId/{id}` and shows it; the `r_…` internal id in
    # the same place shows "the job you're looking for doesn't seem to exist". The host serves
    # the app's shell with a 200 for any path, so only a render tells the two apart.
    # `displayId` is digits on Myntra and Tata and `S-048`-style on Spire.
    url_shape = r"https://[a-z0-9.-]+/jobs/[\w-]+"
    pacer = _PACER

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """The career-site host, lower-cased: the domain lookup is case-sensitive."""
        return (host_of(tenant) or host_of(url)).strip().lower()

    def url(self) -> str:
        """The Board's first request: its host resolved to a workspace."""
        return f"{_API}/workspaceId?{urlencode({'domain': self.slug})}"

    def job_url(self, display_id: str) -> str:
        return f"https://{self.slug}/jobs/{display_id}"

    def _workspace(self) -> str:
        response = self._fetch(
            "GET", self.url(), headers={"User-Agent": USER_AGENT}, timeout=30
        )
        if response.status_code == 404:
            raise gone_board_error(f"no Spire2Grow workspace for {self.slug}")
        response.raise_for_status()
        return response.text.strip()

    def _search(self, workspace: str, page: int) -> dict:
        query = urlencode(
            {
                "page": page,
                "size": _SIZE,
                "selectedSortOrder": "desc",
                "selectedSortField": "postedOn",
            }
        )
        url = f"{_API}/requisition/_search?{query}"
        headers = {
            "User-Agent": USER_AGENT,
            "workspaceid": workspace,
            "language": "en",
        }
        for i in range(_TRIES):
            self.pacer.wait()
            response = self._fetch(
                "GET", url, headers=headers, timeout=60, retry_on=_RETRY_ON
            )
            if response.status_code != 429:
                response.raise_for_status()
                return json.loads(response.text)
            rest = float(
                response.headers.get("X-Rate-Limit-Retry-After-Seconds")
                or _DEFAULT_REST_S
            )
            self._log.info(
                f"{self.board_key()}: 429 on _search — resting every Spire2Grow search "
                f"{rest:.0f}s (try {i + 1}/{_TRIES})"
            )
            self.pacer.rest(rest + 1)
        raise _RateLimited(url)

    def fetch_raw(self) -> Any:
        workspace = self._workspace()
        rows: dict[str, dict] = {}
        total = 0
        page = 1
        while True:
            data = self._search(workspace, page)
            if page == 1 and "entities" not in data:
                self.note_unreadable_board(
                    "an `entities` list", f"keys {sorted(data)[:5]}"
                )
            entities = data.get("entities") or []
            total = data.get("total") or total
            for row in entities:
                rows.setdefault(str(row.get("displayId")), row)
            if not entities or len(rows) >= total:
                break
            if (page + 1) * _SIZE > _WINDOW:
                self.mark_truncated(
                    f"reached the {_WINDOW:,}-row result window at {len(rows)} of {total} "
                    "postings — the rest unread"
                )
                break
            page += 1
        if total and len(rows) < total:
            self.mark_truncated_unless_negligible(
                len(rows),
                total,
                f"read {len(rows)} of {total} postings — the rest is unread, not absent",
            )
        return {"entities": list(rows.values()), "total": total}

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        listed = raw.get("entities") or []
        for row in listed:
            display_id = row.get("displayId")
            title = (row.get("jobTitle") or "").strip()
            if not display_id or not title:
                continue
            location = _location(row)
            kind = row.get("employmentType")
            jobs.append(
                Job(
                    id=self.job_id(str(display_id)),
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=_remote(row, location),
                    department=row.get("departmentName") or None,
                    url=self.job_url(str(display_id)),
                    posted_at=_posted_at(row),
                    scraped_at=scraped_at,
                    description=html_to_text(row.get("jobDescription")),
                    experience=_experience(row),
                    employment_type=_TYPE_LABELS.get(kind, kind) if kind else None,
                    salary=self._salary_field(row),
                )
            )
        self.note_unread_rows(
            len(listed) - len(jobs), len(listed), "had no id or title"
        )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        # No salary field on any of 307 postings across the three hiring workspaces (keys
        # searched for sal/ctc/pay/comp: none), so there is nothing to format.
        return None
