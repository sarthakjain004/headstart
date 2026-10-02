"""gr8people public career sites, keyed by their lower-case career host.

Measured 2026-10-02: Teradata's native GraphQL listing returned 168 unique
postings (116 pass the tech gate), including descriptions and every location.
No token was needed on Teradata, Batesville, Ardene or E-TRADE. Both public
GraphQL searches include descriptions; follow the browser's google-job-discovery
flag and its kill switch. Randstad's native search includes two deleted records
(148 vs Google's 146); Carrier uses native search and its Google surface errors.
Both gr8people.com and workgr8.com are supported; no per-job detail is needed.

Use first=100: first=500/1000 silently clamps to 100 and falsely reports
hasNextPage=false on Teradata. Cursor pagination plus the stated total guards
against that shortfall. A repeated cursor/page is incomplete, never an empty
authoritative Board. The /jobs page must exist: Ardene's root, /jobs and job
page all 404 although its API still lists 22 postings.

The sitemap is not the listing: Teradata's had 159 ids against 168 public API
postings. ID-only links redirect to their canonical title slug (Teradata and
Batesville), or render directly (E-TRADE). Native workplaceType is authoritative:
REMOTE=True, ON_SITE=False, HYBRID=None. Salary is emitted only with a stated
period; ON_TARGET_EARNINGS does not state one. See docs/gr8people/.
"""

from __future__ import annotations

import json
import re
from typing import Any

from headstart.jobs import salary
from headstart.jobs.job import Job, host_of, html_to_text
from headstart.network import http
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    BoardUnreadable,
    gone_board_error,
)
from headstart.scrapers.job_posting_jsonld import job_location_text

SEARCH_QUERY = """query searchJobs($first: Int, $after: String) {
  searchJobs: searchJobPostings(first: $first, after: $after) {
    results {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        key title descriptionHTML structuredDataJSON workplaceType postedOn
        positionType { name }
        primaryPlace { name }
        places { nodes { name } }
        jobCategory { name }
        payRangeLow payRangeHigh payRangeType currencyCode
      }
    }
  }
}"""


def page_props(page: str) -> dict:
    """The public page's server-rendered settings, never an authenticated token."""
    match = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.DOTALL
    )
    return json.loads(match[1]).get("props", {}) if match else {}


def uses_google_search(page: str) -> bool:
    """Follow the browser's google-job-discovery flag and its kill switch."""
    flags = dict(page_props(page).get("initialFeatureFlags") or [])
    return bool(
        flags.get("google-job-discovery")
        and not flags.get("ops-kill-switch-google-cts")
    )


def search_body(
    after: str | None = None, first: int = 100, *, google: bool = False
) -> dict:
    """A public, unfiltered page; omit after entirely on the first request."""
    variables: dict[str, Any] = {"first": first}
    if after:
        variables["after"] = after
    return {
        "operationName": "searchJobs",
        "query": SEARCH_QUERY.replace("searchJobPostings", "searchGoogleJobDiscovery")
        if google
        else SEARCH_QUERY,
        "variables": variables,
    }


def search_results(body: dict) -> dict:
    """Read a valid listing; GraphQL errors (including HTTP 200 errors) are failures."""
    if body.get("errors") and not all(
        error.get("extensions", {}).get("code") == "NOT_FOUND"
        and (error.get("path") or [])[:3] == ["searchJobs", "results", "nodes"]
        for error in body["errors"]
    ):
        raise BoardUnreadable("gr8people search returned GraphQL errors")
    results = ((body.get("data") or {}).get("searchJobs") or {}).get("results")
    if (
        not isinstance(results, dict)
        or not isinstance(results.get("nodes"), list)
        or not isinstance(results.get("totalCount"), int)
    ):
        raise BoardUnreadable("gr8people search has no posting list/count")
    return results


class Gr8PeopleScraper(BaseScraper):
    ats = "gr8people"
    url_shape = r"https://[\w.-]+/jobs/\d+(?:/[^/?#]+)?"

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        return host_of(tenant if "." in tenant else url).lower()

    def url(self) -> str:
        return f"https://{self.slug}/graphql"

    def board_page(self) -> str:
        return f"https://{self.slug}/jobs"

    def job_url(self, native_id: str) -> str:
        return f"https://{self.slug}/jobs/{native_id}"

    def public_client(self) -> str | None:
        # The shared-client validator compares ids before burying an alias. Two
        # non-redirecting host pairs shared their (orgId, clientId) and identical
        # posting ids in the 55-Board census. An org alone is wrong: ActOne has
        # multiple independent public clients under b1c84d.
        try:
            response = self._fetch("GET", self.board_page())
            if response.status_code != 200:
                return None
            props = page_props(response.text)
            visit = props.get("pageProps", {}).get("visit") or props.get("visit") or {}
            if visit.get("orgId") and visit.get("clientId"):
                return f"{visit['orgId']}/{visit['clientId']}"
        except (ValueError, KeyError, http.RequestsError):
            return None
        return None

    def _salary_field(self, raw: dict) -> str | None:
        # ANNUAL on 8/100 sampled Teradata postings; OTE on 10 does not name a period.
        period = {"ANNUAL": "per-year", "HOURLY": "per-hour"}.get(
            raw.get("payRangeType")
        )
        low = raw.get("payRangeLow")
        if low is None or period is None or not raw.get("currencyCode"):
            return None
        return salary.to_field(
            low, raw.get("payRangeHigh"), raw["currencyCode"], period
        )

    def fetch_raw(self) -> list[dict]:
        page = self._fetch(
            "GET",
            self.board_page(),
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        )
        if page.status_code in (404, 410):
            raise gone_board_error(f"no public gr8people Board on {self.slug}")
        page.raise_for_status()
        if "assets.gr8people.com" not in page.text:
            raise BoardUnreadable("gr8people public Board page has no vendor assets")
        google = uses_google_search(page.text)
        after = None
        cursors: set[str] = set()
        rows: dict[str, dict] = {}
        expected = 0
        while True:
            try:
                response = self._fetch(
                    "POST",
                    self.url(),
                    json=search_body(after, google=google),
                    headers={"User-Agent": USER_AGENT},
                )
                response.raise_for_status()
                result = search_results(response.json())
            except (ValueError, http.RequestsError) as exc:
                if not rows:
                    raise
                self.mark_truncated(f"listing page failed: {type(exc).__name__}")
                break
            expected = max(expected, result["totalCount"])
            before = len(rows)
            for row in result["nodes"]:
                if re.fullmatch(r"\d+", str(row.get("key") or "")) and row.get("title"):
                    rows[str(row["key"])] = row
            info = result.get("pageInfo") or {}
            if info.get("hasNextPage") is False:
                break
            cursor = info.get("endCursor")
            if len(rows) == before or not cursor or cursor in cursors:
                self.mark_truncated("listing pagination did not advance")
                break
            cursors.add(cursor)
            after = cursor
        if len(rows) < expected:
            self.mark_truncated_unless_negligible(
                len(rows),
                expected,
                f"listing returned {len(rows)}/{expected} unique postings",
            )
        return list(rows.values())

    def parse(self, raw: list[dict], scraped_at: str) -> list[Job]:
        jobs = []
        seen: set[str] = set()
        for row in raw:
            native_id = str(row.get("key") or "")
            if (
                not re.fullmatch(r"\d+", native_id)
                or not row.get("title")
                or native_id in seen
            ):
                continue
            seen.add(native_id)
            places = [
                p["name"]
                for p in (row.get("places") or {}).get("nodes", [])
                if p.get("name")
            ]
            location = "; ".join(dict.fromkeys(places)) or (
                row.get("primaryPlace") or {}
            ).get("name")
            if not location:
                try:
                    location = job_location_text(
                        json.loads(row.get("structuredDataJSON") or "{}").get(
                            "jobLocation"
                        )
                    )
                except (ValueError, TypeError):
                    location = None
            jobs.append(
                Job(
                    id=self.job_id(native_id),
                    ats=self.ats,
                    company=self.company,
                    title=row["title"],
                    department=(row.get("jobCategory") or {}).get("name"),
                    location=location,
                    remote={"REMOTE": True, "ON_SITE": False}.get(
                        row.get("workplaceType")
                    ),
                    url=self.job_url(native_id),
                    posted_at=row.get("postedOn"),
                    scraped_at=scraped_at,
                    description=html_to_text(row.get("descriptionHTML")) or None,
                    employment_type=(row.get("positionType") or {}).get("name"),
                    salary=self._salary_field(row),
                )
            )
        return jobs
