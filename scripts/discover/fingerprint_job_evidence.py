"""Reconcile harvested apply URLs against bounded, provider-native listing responses.

A missing match is inconclusive: the harvest can contain expired jobs and probes read at most
three pages. A match compares exact provider job identifiers in the same tenant namespace.
"""

from __future__ import annotations

import json
import re
from urllib.parse import unquote, urlsplit


def match_listing(ats: str, tenant: str, payload: dict, urls: set[str]) -> list[str]:
    """Pure schema/identifier reconciliation, with exact segments (never substring IDs)."""
    matched = []
    if ats == "zwayam":
        rows = (payload.get("data") or {}).get("data", [])
        slugs = {
            str(r.get("_source", {}).get("jobUrl"))
            for r in rows
            if r.get("_source", {}).get("jobUrl")
        }
        for url in urls:
            parsed = urlsplit(url)
            if parsed.hostname != tenant:
                continue
            path = unquote(parsed.path).rstrip("/")
            if any(
                path.endswith(
                    ("/jobview/" + unquote(slug), "/job-view/" + unquote(slug))
                )
                for slug in slugs
            ):
                matched.append(url)
    elif ats == "phenom":
        rows = ((payload.get("refineSearch") or {}).get("data") or {}).get("jobs", [])
        ids = {str(r["jobId"]) for r in rows if r.get("jobId")}
        for url in urls:
            parsed = urlsplit(url)
            job = re.search(r"/job/([^/]+)(?:/|$)", unquote(parsed.path))
            if parsed.hostname == tenant and job and job.group(1) in ids:
                matched.append(url)
    elif ats == "workday":
        expected_host = urlsplit(tenant).hostname
        expected_site = unquote(urlsplit(tenant).path).strip("/").split("/")[-1]
        paths = {
            unquote(r["externalPath"]).rstrip("/")
            for r in payload.get("jobPostings", [])
            if r.get("externalPath")
        }
        for url in urls:
            parsed = urlsplit(url)
            source_path = unquote(parsed.path).rstrip("/")
            if parsed.hostname == expected_host and any(
                source_path.endswith(path)
                and source_path[: -len(path)].rstrip("/").split("/")[-1]
                == expected_site
                for path in paths
                if path.startswith("/job/")
            ):
                matched.append(url)
    elif ats == "greenhouse":
        ids = {str(r["id"]) for r in payload.get("jobs", []) if r.get("id")}
        for url in urls:
            parsed = urlsplit(url)
            job = re.fullmatch(r"/([^/]+)/(?:jobs/)?(\d+)/?", parsed.path)
            if (
                parsed.hostname
                in {
                    "boards.greenhouse.io",
                    "job-boards.greenhouse.io",
                    "boards.eu.greenhouse.io",
                    "job-boards.eu.greenhouse.io",
                }
                and job
                and job.group(1) == tenant
                and job.group(2) in ids
            ):
                matched.append(url)
    return sorted(matched)


def check_jobs(
    ats: str, tenant: str, urls: set[str], get, post, max_pages: int = 3
) -> tuple[str, list[str]]:
    """Use scraper-owned request shapes, never scrape details or entire paginated boards."""
    from headstart.scrapers.phenom import PhenomScraper
    from headstart.scrapers.workday import WorkdayScraper
    from headstart.scrapers.zwayam import body_error_code, search_request

    if ats not in {"zwayam", "phenom", "workday", "greenhouse"}:
        return "not-implemented-for-provider", []
    if not urls:
        return "no-source-job-urls", []
    for page in range(min(max(max_pages, 1), 3)):
        if ats == "zwayam":
            endpoint, headers, body = search_request(tenant, page * 10)
            data, error = post(endpoint, headers, body)
            if data and body_error_code(data) is not None:
                return "api-error", []
        elif ats == "phenom":
            data, error = post(
                f"https://{tenant}/widgets",
                {"Content-Type": "application/json"},
                PhenomScraper(tenant)._search_payload(page * 20, 20),
            )
        elif ats == "workday":
            data, error = post(
                WorkdayScraper(tenant).url(),
                {"Content-Type": "application/json"},
                {
                    "appliedFacets": {},
                    "limit": 20,
                    "offset": page * 20,
                    "searchText": "",
                },
            )
        else:
            raw, _final, error = get(
                f"https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs"
            )
            try:
                data = json.loads(raw)
            except ValueError:
                return "api-error", []
        if error or not isinstance(data, dict):
            return "api-error", []
        try:
            matches = match_listing(ats, tenant, data, urls)
        except (TypeError, AttributeError, KeyError):
            return "api-schema-error", []
        if matches:
            return "matched-job", matches[:3]
        if ats == "greenhouse":
            break
    return "no-match-in-bounded-sample", []
