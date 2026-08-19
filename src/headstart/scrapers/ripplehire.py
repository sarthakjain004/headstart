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

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_TOKEN = re.compile(r"token=([A-Za-z0-9_-]+)")
_PAGE_SIZE = 100
# The width this detail pass has always run at — `fan_out`'s default, stated here so the async
# path resolves to it rather than to the 100-stream default (base.fan_out_async, ADR-0047).
_DETAIL_WORKERS = 8
_MAX_PAGES = 200  # our own ceiling — the natural exit is the tenant's own job count
_UA = "headstart/0.1 (job-board reader)"


class RippleHireScraper(BaseScraper):
    ats = "ripplehire"
    detail_workers = _DETAIL_WORKERS  # also the async stream width (base.fan_out_async)
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    def url(self) -> str:
        return f"https://{self.slug}.ripplehire.com/candidate/careers"

    def fetch_raw(self) -> Any:
        # step 1: the careers URL redirects to /candidate/?token=… — grab the token (the pooled
        # session follows the redirect and keeps the session cookie for the search call)
        response = http.fetch(
            "GET", self.url(), headers={"User-Agent": _UA}, timeout=30
        )
        # An HTTP error here must raise, not read as an empty board (ADR-0058 needs the 404).
        # A 200 that redirects somewhere without a token still returns [] — that is the
        # portal's shape for "no public board", not a fetch failure.
        response.raise_for_status()
        m = _TOKEN.search(response.url)
        if not m:
            return []
        token = m.group(1)
        api = f"https://{self.slug}.ripplehire.com/candidate/candidatejobsearch"
        headers = {
            "User-Agent": _UA,
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
            data = http.fetch(
                "POST", api, data=body, headers=headers, timeout=30
            ).json()
            batch = data.get("jobVoList") or []
            jobs.extend(batch)
            page += 1
            if len(batch) < _PAGE_SIZE or len(jobs) >= data.get("totalJobCount", 0):
                break
        else:
            # Reached only by exhausting the cap — every natural end breaks above. Whatever
            # sits past it is unread, not absent (ADR-0053).
            self.mark_truncated(
                f"hit the {_MAX_PAGES}-page cap at {len(jobs)} jobs — the rest unread"
            )
        # detail pass: the list never carries jobDesc — fill it from the per-job detail JSON
        need = [j for j in jobs if j.get("jobSeq") and not j.get("jobDesc")]
        # Multiplexed by default (ADR-0016); HEADSTART_ASYNC_FANOUT=0 falls back to threads.
        if self.async_fanout_enabled():
            descriptions = self.fan_out_async(
                need,
                lambda session, j: self._job_description_async(
                    session, token, j["jobSeq"]
                ),
            )
        else:
            descriptions = self.fan_out(
                need, lambda j: self._job_description(token, j["jobSeq"])
            )
        self.report_detail_gaps(descriptions, "descriptions")
        for j, desc in zip(need, descriptions):
            j["jobDesc"] = desc
        return jobs

    def _detail_url(self, token: str, job_seq: Any) -> str:
        return (
            f"https://{self.slug}.ripplehire.com/candidate/candidatejobdetail"
            f"?token={token}&jobSeq={job_seq}&source=CAREERSITE&lang=en"
        )

    def _job_description(self, token: str, job_seq: Any) -> str | None:
        """GET one job's detail JSON and return jobVO.jobDesc (None on failure). Sync path."""
        try:
            data = http.fetch(
                "GET",
                self._detail_url(token, job_seq),
                headers={"User-Agent": _UA, "Accept": "application/json"},
                timeout=30,
            ).json()
        except (http.RequestsError, json.JSONDecodeError):
            return None  # a missing description must not drop the job
        return (data.get("jobVO") or {}).get("jobDesc") or None

    async def _job_description_async(
        self, session: Any, token: str, job_seq: Any
    ) -> str | None:
        """Same as :meth:`_job_description` over the shared multiplexed ``AsyncSession``."""
        try:
            response = await http.fetch_async(
                session,
                "GET",
                self._detail_url(token, job_seq),
                headers={"User-Agent": _UA, "Accept": "application/json"},
                timeout=30,
            )
            data = response.json()
        except (http.RequestsError, json.JSONDecodeError):
            return None  # a missing description must not drop the job
        return (data.get("jobVO") or {}).get("jobDesc") or None

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for j in raw:
            # tenants populate either jobLocation or locations (a comma-joined string)
            location = j.get("jobLocation") or j.get("locations") or None
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{j['jobSeq']}",
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("jobTitle") or "").strip(),
                    location=location,
                    remote=is_remote(location),
                    department=j.get("bussinessUnit"),
                    url=f"https://{self.slug}.ripplehire.com/candidate/careers",
                    posted_at=j.get("jobPostingDate") or j.get("careerSiteDate"),
                    scraped_at=scraped_at,
                    description=html_to_text(j.get("jobDesc")),
                    experience=j.get("jobReqExp"),
                    employment_type=j.get("jobType"),
                    salary=j.get("compensationRange") or j.get("compensationInfo"),
                )
            )
        return jobs
