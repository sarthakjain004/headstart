"""RippleHire job-board scraper ({slug}.ripplehire.com candidate career site).

Reverse-engineered by rendering the public /candidate/ portal (no login) and capturing its
job-search XHR. Two steps, no auth beyond a per-site token that the careers URL hands out:
  1. GET /candidate/careers            -> redirects to /candidate/?token={token}
  2. POST /candidate/candidatejobsearch  (form-encoded `careerSiteUrlParams` JSON, paginated)
     with `Accept: application/json` (the endpoint returns XML otherwise).

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
_UA = "headstart/0.1 (job-board reader)"


class RippleHireScraper(BaseScraper):
    ats = "ripplehire"

    def url(self) -> str:
        return f"https://{self.slug}.ripplehire.com/candidate/careers"

    def fetch_raw(self) -> Any:
        # step 1: the careers URL redirects to /candidate/?token=… — grab the token (the pooled
        # session follows the redirect and keeps the session cookie for the search call)
        response = http.fetch(
            "GET", self.url(), headers={"User-Agent": _UA}, timeout=30
        )
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
        while page < 200:  # hard cap; loops break on the count below
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
        return jobs

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
