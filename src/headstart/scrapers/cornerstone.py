"""Cornerstone OnDemand (csod) career-site scraper (``{corp}.csod.com/ux/ats/careersite/...``).

Adapted from kalil0321/ats-scrapers' ``cornerstone.py`` (MIT) — same attribution convention as
``phenom.py`` and ``bamboohr.py``. Every choice below was measured on 2026-09-23 against the live
hosts (404 seed tenants walked in full, 24,800 listing rows, 924 job ads, 3,904 rate-ramp
requests); upstream got four of them wrong. The write-up is
``docs/cornerstone/2026-09-23_careersite-api-measurement.md``; the decisions are ADR-0183.

**The Board is the tenant; the slug is its ``{corp}.csod.com`` label** (equal to the career-site
URL's ``?c=`` on 564 of 564 seed URLs; case-insensitive, so lowercased). A tenant runs several
career sites — up to 114 ids, 53 of them active — and the site id is a *filter*: a wrong one
answers 200 with ``totalCount: 0``, the oracle ``siteNumber`` trap again. Upstream's default of
site 1 is inactive on many tenants, and the seed list's own site URLs reached only 31,545 of the
42,534 postings those tenants publish. A requisition is often posted to several sites (12,798
postings on 119 of 368 hiring tenants), so per-site Boards would serve 15,549 rows twice with
nothing to catch it. This scraper walks every site and unions by ``requisitionId``.

**The walk.** ``GET /services/x/career-site/v1/careersites/{id}`` answers 200 ``{"active": …}``
for a site and 404 past the last. Ids run ``1..N`` on 396 of 398 tenants; the other two have one
id that answers 500 with live sites after it, so only a 404 ends the walk. Inactive sites held no
posting on any tenant and are not searched; everything else is (a 500 site searched to 0).

**The token.** The career-site page embeds ``csod.context={…}``: an anonymous JWT and the API
pod (``endpoints.cloud`` — ``us``/``uk``/``eu-fra``/``eu-cdg``/``au``/…; upstream's default
``na.api.csod.com`` does not resolve). The token is per corp and bound to its pod; it lives 21
minutes to 24 hours by tenant, and an expired one answers 401 with an empty body — so every
request refreshes it once on a 401 and retries. On US-pod tenants the tenant-host endpoints also
need ``ASP.NET_SessionId``, whose value is the JWT's ``aud`` (14 of 14 US-pod tenants 401 without
it, 40 of 40 tenants 200 with it). A page for a site id that does not exist redirects to
``/ui/error``; ``myhr-ece`` has no site 1, so ids 1-3 are tried before a Board is called
siteless — the answer an LMS-only corp gives for every id (``csod.com`` hosts both products).
On 6 tenants (metso, transgourmet, …) the pod answers the search with 404
``ResourceNotFound`` while the career-site page and its sites are live; the page itself renders
"Current Openings" with nothing listed, so that site is read as empty, not as a failure.
The page's cookies are dropped from the pooled session as soon as it is read: with them in the
jar, the explicit session header 401s on US-pod tenants (6 of 6 trials; jar alone 12 of 12 and
header alone 12 of 12 answer 200), and re-reading the page redirects to ``/ui/error``.

**The listing** is ``POST {pod}rec-job-search/external/jobs``, filtered by ``careerSitePageId``
(``careerSiteId`` is ignored; both are sent, as the page does). ``pageSize`` clamps silently at
1,000 and ``totalCount`` matched the rows served on the five largest sites (to 3,372), so the walk
ends on a short page or at the total. ``cultureName`` changes the date format, not the rows, so it
is always ``en-US`` and ``postingEffectiveDate`` reads as M/D/YYYY.

**The description is the job ad**, ``Services/API/ATS/CareerSite/{site}/JobRequisitions/{id}``
(p50 8.2 KB): the listing's ``externalDescription`` is one field of it, HTML-stripped with every
``&`` deleted, and the ad was more than 20% longer on 750 of 924 postings (median 3.24x). The ad
is keyed by a site the posting is on, so each posting keeps the lowest site it was listed on,
which its URL names too. An empty ad (21 of 923) falls back to the listing text; tenant
placeholders — ``<<INTERNAL JOB DESCRIPTION>>``, ``>``, "see JD", every text under 30 word
characters once ``<<…>>`` tokens go (2,406 of 24,800 listing texts) — are no description. The ad
answers ``application/json`` with no charset; its bytes are parsed as JSON, i.e. UTF-8.

**The tech gate is exact** (ADR-0166): no surface states a department, and ``parse`` reads the
listing's ``displayJobTitle``, which the ad's ``title`` equalled on 58 of 58. ADR-0048's skip of an
already-described Job is taken too — the ad supplies nothing but the description. A gated,
skipped or failed ad leaves the Job without a description, never without the Job.

Not on any surface, so never set: department, salary (75 of 924 ads end in a templated "Monthly
Salary 25,000.00 - 28,000.00" prose line, 45 of them "0.00 - 0.00" — the description extractor's
business), employment type, experience. ``remote`` is the location text's: tenants write it into
the city ("Deutscher Standort/Remote"). No page names the employer at Board level (no ``<title>``
on 30 of 30 tenants; ``csod.context`` and ``careersites/{id}`` carry no name), so the company is
the slug. No rate limit was found — up to 196 req/s on one tenant and 172 across 60, zero
non-200s — and every host is User-Agent-agnostic.
"""

from __future__ import annotations

import base64
import html
import json
import re
import threading
from datetime import datetime
from typing import Any

from headstart import http
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_CONTEXT = re.compile(r"csod\.context=(\{.*?\});", re.DOTALL)
#: The search's silent ceiling: 5,000 asked, 1,000 served.
_PAGE_SIZE = 1000
#: Our ceilings on the walks, far past anything measured (largest site 3,372 postings; most
#: site ids 114). Reaching either means we stopped reading, not that the Board ended.
_MAX_PAGES = 50
_MAX_SITES = 500
#: Career-site ids tried for the token before a Board is called siteless (`myhr-ece` starts at 2).
_PAGE_ATTEMPTS = 3
_TEMPLATE_TOKEN = re.compile(r"<<[^<>]*>>")
#: Under this many word characters, a description is a tenant placeholder (module docstring).
_MIN_WORD_CHARS = 30


def _title(row: dict) -> str:
    """`displayJobTitle`, unescaped: titles holding `<` arrive HTML-escaped (835 rows)."""
    return html.unescape(row.get("displayJobTitle") or "").strip()


def _location(row: dict) -> str | None:
    """Every place the posting names, each "city, state, country", "; "-joined in order."""
    places: list[str] = []
    for place in row.get("locations") or []:
        parts = (place.get("city"), place.get("state"), place.get("country"))
        text = ", ".join(p for p in parts if p)
        if text and text not in places:
            places.append(text)
    return "; ".join(places) or None


def _posted_at(value: str | None) -> str | None:
    """`postingEffectiveDate` as an ISO date — M/D/YYYY because the search is asked in en-US."""
    try:
        return datetime.strptime(value or "", "%m/%d/%Y").date().isoformat()  # noqa: DTZ007
    except ValueError:
        return None


def _real_text(value: str | None) -> str | None:
    """The text of `value`, or None when it is a tenant placeholder rather than a description."""
    text = html_to_text(value)
    if not text:
        return None
    if len(re.findall(r"\w", _TEMPLATE_TOKEN.sub("", text))) < _MIN_WORD_CHARS:
        return None
    return text


def _unindexed(response: Any) -> bool:
    """Whether a search answer is the measured 404 `{"error": {"code": "ResourceNotFound"}}`."""
    if response.status_code != 404:
        return False
    try:
        return response.json()["error"]["code"] == "ResourceNotFound"
    except (ValueError, KeyError, TypeError):
        return False


def _jwt_claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class CornerstoneScraper(BaseScraper):
    ats = "cornerstone"
    # scraper: the app's own `postingUrl`/`companyApplyUrl` shape, on a site the posting is on.
    url_shape = (
        r"https://[a-z0-9-]+\.csod\.com/ux/ats/careersite/\d+/home/requisition/\d+"
        r"\?c=[a-z0-9-]+"
    )
    # Measured clean to 128 concurrent (module docstring); 16 is what icims, oracle and pyjamahr
    # run, since `harvest` scrapes Boards concurrently and peak in-flight is the product.
    detail_workers = 16
    has_detail_pass = True  # the job ad fills `description` (ADR-0050)

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """The `{corp}.csod.com` label, lowercased, whether the row carries it bare or as a URL."""
        value = tenant if tenant.strip() else url
        host = value.split("://", 1)[-1].split("/", 1)[0]
        return host.split(".", 1)[0].strip().lower()

    def _host(self) -> str:
        return f"https://{self.slug}.csod.com"

    def url(self) -> str:
        return self._page_url(1)

    def _page_url(self, site: int) -> str:
        return f"{self._host()}/ux/ats/careersite/{site}/home?c={self.slug}"

    def job_url(self, site: int, requisition_id: str) -> str:
        return (
            f"{self._host()}/ux/ats/careersite/{site}/home/requisition/{requisition_id}"
            f"?c={self.slug}"
        )

    def _ad_url(self, row: dict) -> str:
        return (
            f"{self._host()}/Services/API/ATS/CareerSite/{row['_site']}/JobRequisitions/"
            f"{row['requisitionId']}?useMobileAd=false&cultureId=1"
        )

    # ---------------------------------------------------------------- the token

    def _read_context(self) -> bool:
        """Read the token and API pod off the first career-site page that serves them. False
        when every id tried redirects to `/ui/error`: the tenant has no career site."""
        for site in range(1, _PAGE_ATTEMPTS + 1):
            response = self._fetch(
                "GET",
                self._page_url(site),
                headers={"User-Agent": USER_AGENT},
                timeout=30,
                allow_redirects=False,
            )
            location = response.headers.get("location") or ""
            if 300 <= response.status_code < 400 and location.endswith("/ui/error"):
                continue
            response.raise_for_status()
            match = _CONTEXT.search(response.text)
            if not match:
                raise ValueError(
                    f"{self.board_key()}: no csod.context on site {site}'s page"
                )
            context = json.loads(match.group(1))
            # Drop the page's cookies from the pooled session at once: with them in the jar,
            # US-pod tenant hosts 401 the explicit session header (6 of 6 trials) and a later
            # re-read of this page redirects to `/ui/error` (module docstring).
            self.board_fetcher.clear_cookies(domain=f"{self.slug}.csod.com")
            self._token = context["token"]
            self._pod = context["endpoints"]["cloud"]
            return True
        return False

    def _headers(self, *, tenant_host: bool) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}", "User-Agent": USER_AGENT}
        if tenant_host:
            headers["Accept"] = "application/json"
            headers["Cookie"] = f"ASP.NET_SessionId={_jwt_claims(self._token)['aud']}"
        return headers

    def _refresh(self, stale: str) -> None:
        """Read a new token, unless a concurrent request already replaced `stale`."""
        with self._token_lock:
            if self._token == stale:
                self._read_context()

    def _call(self, method: str, url: str, *, tenant_host: bool, **kwargs: Any) -> Any:
        """One request with the token; on a 401, refresh it once and retry."""
        token = self._token
        response = self._fetch(
            method,
            url,
            headers=self._headers(tenant_host=tenant_host),
            timeout=30,
            **kwargs,
        )
        if response.status_code == 401:
            self._refresh(token)
            response = self._fetch(
                method,
                url,
                headers=self._headers(tenant_host=tenant_host),
                timeout=30,
                **kwargs,
            )
        return response

    # ---------------------------------------------------------------- the listing

    def _site_rows(self, site: int) -> list[dict]:
        """Every posting one career site lists, page by page until its `totalCount`."""
        rows: list[dict] = []
        total = 0
        for page in range(1, _MAX_PAGES + 1):
            response = self._call(
                "POST",
                f"{self._pod}rec-job-search/external/jobs",
                tenant_host=False,
                json={
                    "careerSiteId": site,
                    "careerSitePageId": site,
                    "pageNumber": page,
                    "pageSize": _PAGE_SIZE,
                    "cultureId": 1,
                    "cultureName": "en-US",
                },
            )
            if page == 1 and _unindexed(response):
                # The corp has no search index behind its career site, and the page itself
                # lists no openings (module docstring): an empty site. Any other 404 — and this
                # one past page 1, where rows were already read — raises.
                return rows
            response.raise_for_status()
            data = response.json()["data"]
            total = data.get("totalCount") or 0
            batch = data.get("requisitions") or []
            rows.extend(batch)
            if len(batch) < _PAGE_SIZE or len(rows) >= total:
                break
        else:
            # A hard cap: the rest is unreachable, not noise, so no tolerance applies (ADR-0121).
            self.mark_truncated(
                f"site {site} hit the {_MAX_PAGES}-page cap at {len(rows)} of {total} postings"
            )
            return rows
        if len(rows) < total:
            self.mark_truncated_unless_negligible(
                len(rows), total, f"site {site}: read {len(rows)} of {total} postings"
            )
        return rows

    def listing(self) -> list[dict] | None:
        """Every posting the Board lists, once each, tagged with the lowest site it is on — or
        None when the tenant has no career site. The liveness probe counts the same list."""
        self._token_lock = threading.Lock()
        if not self._read_context():
            return None
        postings: dict[str, dict] = {}
        for site in range(1, _MAX_SITES + 1):
            answer = self._call(
                "GET",
                f"{self._host()}/services/x/career-site/v1/careersites/{site}",
                tenant_host=True,
            )
            if answer.status_code == 404:
                break
            if answer.status_code < 500:
                answer.raise_for_status()  # a refusal is not "no such site"
                if answer.json()["data"].get("active") is False:
                    continue
            for row in self._site_rows(site):
                # First sighting wins: sites are walked in order, so a posting keeps the lowest
                # site it is on — the site its ad is read from and its URL names.
                postings.setdefault(str(row["requisitionId"]), {**row, "_site": site})
        else:
            self.mark_truncated(
                f"walked {_MAX_SITES} career sites without reaching the last"
            )
        return list(postings.values())

    def fetch_raw(self) -> Any:
        rows = self.listing()
        if rows is None:
            self.note_unreadable_board(
                "a career-site page on site ids 1-3", "a redirect to /ui/error on each"
            )
            return {"postings": [], "ads": {}}
        # Ads only for what the tech filter will keep (exact here) and the description store
        # does not already hold (module docstring).
        wanted = [
            r
            for r in self.tech_detail_wanted(rows, _title)
            if self.needs_detail(str(r["requisitionId"]))
        ]
        # Composed from the primitives, not `run_detail_pass` (ADR-0201): every ad request rides
        # a tenant token that a 401 refreshes mid-pass for the items still in flight.
        ads = self.fan_out_async(wanted, self._ad_async) if wanted else []
        # Reported, not marked truncated: a missing ad costs a description, not a posting.
        self.report_detail_gaps(ads, "job ads")
        return {
            "postings": rows,
            "ads": {
                str(r["requisitionId"]): a for r, a in zip(wanted, ads) if a is not None
            },
        }

    async def _ad_async(self, session: Any, row: dict) -> str | None:
        """The job ad's HTML ("" when the tenant left it empty), or None when it failed."""
        try:
            token = self._token
            response = await self._fetch_async(
                session,
                "GET",
                self._ad_url(row),
                headers=self._headers(tenant_host=True),
                timeout=30,
            )
            if response.status_code == 401:
                self._refresh(token)
                response = await self._fetch_async(
                    session,
                    "GET",
                    self._ad_url(row),
                    headers=self._headers(tenant_host=True),
                    timeout=30,
                )
            response.raise_for_status()
        except http.RequestsError as exc:
            self.note_detail_exception(exc)
            return None
        fields = json.loads(response.content)["data"][0]["items"][0]["fields"]
        return fields.get("ad") or ""

    # ---------------------------------------------------------------- parsing

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        ads = raw.get("ads") or {}
        jobs: list[Job] = []
        for row in raw.get("postings") or []:
            rid = str(row["requisitionId"])
            location = _location(row)
            jobs.append(
                Job(
                    id=self.job_id(rid),
                    ats=self.ats,
                    company=self.company,
                    title=_title(row),
                    location=location,
                    remote=is_remote(location),
                    department=None,  # on no surface (module docstring)
                    url=self.job_url(row["_site"], rid),
                    posted_at=_posted_at(row.get("postingEffectiveDate")),
                    scraped_at=scraped_at,
                    description=self._description(row, ads.get(rid)),
                    salary=self._salary_field(row),
                )
            )
        return jobs

    @staticmethod
    def _description(row: dict, ad: str | None) -> str | None:
        if (
            ad is None
        ):  # gated, already stored, or failed: the store or the next run fills it
            return None
        return _real_text(ad) or _real_text(row.get("externalDescription"))

    def _salary_field(self, raw: Any) -> str | None:
        """None: no surface states a salary field — the listing row has exactly six keys on
        24,800 of 24,800 rows, and the job ad and `jobDetails` none either (module docstring)."""
        return None
