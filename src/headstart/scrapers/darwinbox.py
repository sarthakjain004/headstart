"""Darwinbox careers scraper ({tenant}.darwinbox.{in,com} candidate career site).

Darwinbox is an Angular SPA whose public board loads from one unauthenticated JSON
endpoint (the documented Bulk-Candidates API needs Basic Auth + an API key, but the
careers SPA itself does not):

  POST /ms/candidateapi/job/alljobs?companyId=main
       body {"companyId":"main","page":N,"sort_option":"new","limit":100}
       -> {"status":"success","data":[ ...jobs... ]}

Two wrinkles drive the shape of this scraper:
  * Cloudflare TLS-fingerprints the edge, intermittently 403-ing a plain urllib client,
    so this is the one scraper that fetches via curl_cffi (impersonate="chrome"). The 403
    is transient, hence it's in the retry set alongside 429/5xx.
  * The server caps each page at 100 regardless of `limit`, so we page until a short batch.
    The data-center TLD varies (~77% .in, ~23% .com); we resolve it on the first page.

Only tenants with recruitment enabled return jobs; HR-only tenants (e.g. games24x7,
recruitment_enabled:false) return an empty list.
"""

from __future__ import annotations

from typing import Any

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import BaseScraper

_PAGE_SIZE = 100  # server caps each page at 100 regardless of the requested limit
_TLDS = ("in", "com")
_UA = "headstart/0.1 (job-board reader)"


class DarwinboxScraper(BaseScraper):
    ats = "darwinbox"

    def url(self) -> str:
        host = getattr(self, "_host", None) or f"https://{self.slug}.darwinbox.in"
        return f"{host}/ms/candidate/careers"

    def _alljobs(self, host: str, page: int) -> list[dict]:
        """POST one page of the board (retry — incl. the Cloudflare 403 blip — lives in fetch)."""
        api = f"{host}/ms/candidateapi/job/alljobs?companyId=main"
        body = {"companyId": "main", "page": page, "sort_option": "new", "limit": _PAGE_SIZE}
        response = http.fetch("POST", api, json=body, timeout=30,
                              headers={"User-Agent": _UA, "Accept": "application/json"})
        response.raise_for_status()
        return response.json().get("data") or []

    def fetch_raw(self) -> Any:
        # data-center TLD varies per tenant; resolve it on the first page, then paginate.
        last_error: Exception | None = None
        host = batch = None
        for tld in _TLDS:
            candidate = f"https://{self.slug}.darwinbox.{tld}"
            try:
                batch = self._alljobs(candidate, 1)
                host = candidate
                break
            except Exception as exc:  # noqa: BLE001 - wrong-TLD host: try the other one
                last_error = exc
        if host is None:
            raise last_error
        self._host = host
        jobs = list(batch)
        page = 1
        while len(batch) == _PAGE_SIZE and page < 99:
            page += 1
            batch = self._alljobs(host, page)
            jobs.extend(batch)
        return jobs

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        host = getattr(self, "_host", None) or f"https://{self.slug}.darwinbox.in"
        jobs: list[Job] = []
        for j in raw:
            # `locations` is the board's display string, but it collapses to a generic
            # "Multiple Locations" when a job spans cities. In that case recover the real
            # city list from tool_tip_locations ("{office}, {city}, {state}, {country}").
            tips = j.get("tool_tip_locations") or []
            if len(tips) > 1:
                cities = []
                for tip in tips:
                    parts = [p.strip() for p in tip.split(",")]
                    city = parts[1] if len(parts) > 1 else parts[0]
                    if city and city not in cities:
                        cities.append(city)
                location = ", ".join(cities) or None
            else:
                location = j.get("locations") or None
            salary = (j.get("salary_range") or "").strip() or None
            if salary and j.get("salary_timeframe"):
                salary = f"{salary} ({j['salary_timeframe']})"
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{j['id']}",
                    ats=self.ats,
                    company=self.company,
                    title=(j.get("title") or j.get("designation_name") or "").strip(),
                    location=location,
                    remote=bool(j.get("is_remote")) or is_remote(location),
                    department=j.get("department_name"),
                    url=f"{host}/ms/candidate/careers/jobs/{j['id']}",
                    posted_at=j.get("posted_on"),
                    scraped_at=scraped_at,
                    description=html_to_text(j.get("jd")),
                    experience=j.get("experience"),
                    employment_type=j.get("emp_type_name"),
                    salary=salary,
                )
            )
        return jobs
