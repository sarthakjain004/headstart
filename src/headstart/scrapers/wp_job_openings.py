"""WP Job Openings (HireZoot): a WordPress site's REST listing, then each tech posting's page.

**A self-hosted ATS, not a Career front.** WP Job Openings is a WordPress plugin by AWSM
Innovations (wordpress.org slug ``wp-job-openings``, renamed HireZoot in its version 4). A company
installs it on its own WordPress site, posts openings as the post type ``awsm_job_openings``, and
takes applications through the plugin's own form, stored as the post type
``awsm_job_application`` — every one of the first 187 job pages sampled, on 28 sites, carried
that form. Storing applications is what makes it an **ATS** (CONTEXT.md) rather than a **Career
front**, which mirrors a Board on another ATS and hands its Apply button off. So the Board is the
WordPress site, keyed by its host (``wp_job_openings:finac.io``), as Zoho and iCIMS Boards are
(ADR-0266).

Everything below was measured live on 2026-09-28; the write-up, with every sample size, is
``docs/wp_job_openings/2026-09-28_rest-and-job-page-measurement.md``.

**The listing is WordPress's REST route for the post type** (:data:`_ROUTE`): the plugin
registers it with ``show_in_rest``, so every site that has not switched the REST API off answers
it, with the Board's own count in ``X-WP-Total`` (326 of 326 sites that listed). It lists only
published postings, which is what a job seeker can apply to. The two other listings lost on
exactly that and on coverage: the RSS feed lists the plugin's public ``expired`` status too (11
of the 75 ids on ``finac.io``'s feed, and all 4 on ``a5econsulting.com``'s, are closed postings
the route omits), and a sitemap exists on only 21 of the 32 sites first surveyed; where both
exist the sitemap lists exactly the route's links. Pages are walked by id (``orderby=id``) at
the route's 100-a-page ceiling (``per_page=101`` answers 400) until ``X-WP-TotalPages``; ordering
by id keeps a posting published mid-walk from shifting the pages under the walk. A site whose
security plugin refuses the route to anonymous callers (13 of 481 candidates) is not read.

**Every tech posting's page is read for the fields the REST row cannot carry.** The plugin's job
specs (category, type, location and any the site adds) are custom taxonomies registered without
``show_in_rest``, so the REST row names them only as ``class_list`` slugs
(``job-location-delhi-gurgaon`` for "Delhi - Gurgaon"), and only on WordPress 6.5 or later
(17,576 of 18,126 rows; 21 of 417 Hiring Boards carry none). The page carries the plugin's JSON-LD
``JobPosting`` (4,117 of 4,384 pages sampled on 417 Boards): the location names, and the employer
as ``hiringOrganization``, which names 371 of the 417 Boards. Its specifications block, which the
pages of 263 of the 417 show, names every spec the site defines, and is read on its own where a
page has no JSON-LD (216 of the 4,384 answered 200 without it). The REST row's own title, body
and date are kept: the JSON-LD ``description`` is the raw post source, block comments and all. A
posting whose page is not read ships with the slugs spelled as words.

**The tech gate is a measured approximation.** It reads the title and the ``job-category-*``
slug off the REST row, while ``parse`` prefers the category's own name off the page. On 4,384
sampled postings the two disagreed on 9 of the 857 tech ones (1.1%), each a category the slug
spells lossily ("Data&AI" as ``dataai``) or a row with no ``class_list`` (CONTEXT.md §Detail
pass).
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import unquote

from headstart.boards import company_name
from headstart.jobs.job import Job, host_of, html_to_text, is_remote
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    BoardUnreadable,
    DetailLost,
    DetailRequest,
)
from headstart.scrapers.job_posting_jsonld import (
    find_job_posting,
    has_unparseable_jsonld,
    hiring_organization,
)

#: At the measured knee: one site served 1.2 page GETs a second at one in flight, 3.1 at four and
#: 3.5 at eight, then slower at 16 (2.9) and 32 (2.5) while latency grew from 0.5 s to 11 s — an
#: uncached PHP page, CPU-bound on the site's own server. No refusal at any width, on two sites.
_DETAIL_WORKERS = 4
#: The route through WordPress's ``rest_route`` query, which every WordPress answers; the
#: ``/wp-json/`` path needs the site's pretty permalinks and its web server's rewrite, and 404'd
#: or redirected to a page on 19 of the 330 sites that listed postings either way.
_ROUTE = "?rest_route=/wp/v2/awsm_job_openings"
#: curl_cffi's Firefox TLS fingerprint rather than the session's Chrome one. Hostinger's CDN
#: (``server: hcdn``) refuses every Chrome and Safari fingerprint curl_cffi offers, whatever the
#: User-Agent, and serves Firefox's: 22 of 481 candidate sites answered 403 on every path under
#: Chrome and listed postings under Firefox. The opposite held on 2 sites, whose bot wall
#: challenges Firefox and not Chrome.
IMPERSONATE = "firefox"
#: The REST route's ceiling: ``per_page=101`` answers 400 ``rest_invalid_param``.
_PER_PAGE = 100
#: Only what `parse` reads; the full row carries theme and SEO plugin fields at ~3x the size.
_FIELDS = "id,link,title,content,date_gmt,class_list"
#: The plugin's default spec taxonomies; a site may add its own (``experience``, ``job-country``).
_CATEGORY, _TYPE, _LOCATION = "job-category", "job-type", "job-location"
_SPEC = re.compile(
    r'<div class="awsm-job-specification-item awsm-job-specification-([a-z0-9_-]+)">(.*?)</div>',
    re.DOTALL,
)
_SPEC_TERM = re.compile(
    r'<span class="awsm-job-specification-term">(.*?)</span>', re.DOTALL
)
_TAG = re.compile(r"<[^>]+>")
#: How much of a Board's pages must agree on one ``hiringOrganization`` before it names the Board.
#: The plugin writes one site-wide setting into every page, so any agreement short of all is a
#: setting changed mid-walk; iCIMS's 0.9 is kept for that case.
_AGREEMENT = 0.9


class WpJobOpeningsScraper(BaseScraper):
    """One WordPress site running WP Job Openings, keyed by its host (e.g. ``finac.io``)."""

    ats = "wp_job_openings"
    # The REST row's own `link`, the post's permalink under the site's own permalink settings:
    # `/jobs/{slug}/` by default, any base a site sets (`/career/`, `/current-openings/`), or
    # WordPress's plain `?awsm_job_openings={slug}` where pretty permalinks are off.
    url_shape = r"https?://[^/?#\s]+/\S*"
    has_detail_pass = True  # places and employer come off the job page (ADR-0050)
    detail_workers = _DETAIL_WORKERS
    detail_streams = _DETAIL_WORKERS

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """The site's host, whichever of the row's two columns carries it."""
        return (host_of(url) or tenant).strip().lower()

    def url(self) -> str:
        return f"https://{self.slug}/{_ROUTE}"

    def _page_url(self, page: int) -> str:
        return (
            f"{self.url()}&per_page={_PER_PAGE}&page={page}&orderby=id&order=asc"
            f"&_fields={_FIELDS}"
        )

    def _listing(self) -> list[dict[str, Any]]:
        """Every published posting the REST route lists.

        A 200 that is not a JSON list raises rather than reads as an empty Board: it is a page a
        maintenance, cache or security plugin put in the route's place, and an empty answer would
        evict every posting the Board still lists (ADR-0083 grace or not).
        """
        rows: dict[int, dict[str, Any]] = {}
        total, pages, page = 0, 1, 1
        while page <= pages:
            response = self._fetch(
                "GET",
                self._page_url(page),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=60,
                impersonate=IMPERSONATE,
            )
            response.raise_for_status()
            try:
                # From bytes, which `json` decodes past a UTF-8 byte-order mark: 2 of 427
                # Hiring Boards send one ahead of the list.
                batch = json.loads(response.content)
            except ValueError:
                batch = None
            if not isinstance(batch, list):
                raise BoardUnreadable(
                    f"the REST route answered {response.status_code} without a JSON list"
                )
            for row in batch:
                if isinstance(row, dict) and isinstance(row.get("id"), int):
                    rows.setdefault(row["id"], row)
            total = _int(response.headers.get("x-wp-total"), total)
            pages = _int(response.headers.get("x-wp-totalpages"), pages)
            page += 1
        if len(rows) < total:
            self.mark_truncated_unless_negligible(
                len(rows),
                total,
                f"the REST route listed {len(rows)} of the {total} postings it states",
            )
        return list(rows.values())

    def fetch_raw(self) -> Any:
        rows = self._listing()
        if not rows:
            return {"rows": [], "pages": {}}
        pages = self.run_detail_pass(
            rows,
            key_of=lambda row: str(row["id"]),
            what="job pages",
            title_of=_title,
            department_of=lambda row: _slug_terms(row, _CATEGORY),
        )
        self.adopt_company(
            company_name.agreed_name(
                (page.get("company") for page in pages.values()), _AGREEMENT
            )
        )
        return {"rows": rows, "pages": dict(pages)}

    def detail_request(self, row: dict[str, Any]) -> DetailRequest:
        link = row.get("link")
        if not isinstance(link, str) or not link.startswith(("https://", "http://")):
            raise DetailLost("no link on the REST row")
        return DetailRequest(
            link,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            options={"impersonate": IMPERSONATE},
        )

    def read_detail(self, row: dict[str, Any], response: Any) -> dict[str, Any]:
        fields = page_fields(response.text)
        if fields is None:
            if has_unparseable_jsonld(response.text):
                raise DetailLost("unparseable JSON-LD on a 200")
            raise DetailLost("no JobPosting JSON-LD or specifications block on a 200")
        return fields

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        pages = raw.get("pages") or {}
        jobs: list[Job] = []
        untitled = 0
        for row in raw.get("rows") or []:
            title = _title(row)
            if not title:
                untitled += 1
                continue
            page = pages.get(str(row["id"])) or {}
            specs = page.get("specs") or {}
            location = page.get("location") or _joined(
                specs.get(_LOCATION) or _slug_terms_list(row, _LOCATION)
            )
            jobs.append(
                Job(
                    id=self.job_id(str(row["id"])),
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=is_remote(location),
                    department=_joined(
                        specs.get(_CATEGORY) or _slug_terms_list(row, _CATEGORY)
                    ),
                    url=self.job_url(row.get("link") or ""),
                    posted_at=_utc(row.get("date_gmt")),
                    scraped_at=scraped_at,
                    description=html_to_text(_rendered(row.get("content"))),
                    employment_type=_joined(
                        specs.get(_TYPE) or _slug_terms_list(row, _TYPE)
                    ),
                )
            )
        self.note_unread_rows(untitled, len(raw.get("rows") or []), "had no title")
        return jobs

    def job_url(self, link: str) -> str:
        """The REST row's own ``link``: the permalink WordPress serves the posting at."""
        return link

    def _salary_field(self, raw: Any) -> str | None:
        """None: the plugin has no pay field, and its JSON-LD states no ``baseSalary`` (0 of 187
        pages). A site can define a spec of its own for it, and 20 of 417 Hiring Boards did,
        under a dozen names ("salary", "base-salary", "salarisrange", "rate-of-pay") and in free
        text ("Negotiable", "Thoả thuận", "400万円～500万円"); the description is read for pay
        downstream instead."""
        return None


def page_fields(page: str) -> dict[str, Any] | None:
    """What one job page adds to its REST row, or None when it carries neither a ``JobPosting``
    nor a specifications block.

    ``location`` is the JSON-LD's places ("; "-joined: 470 of 3,063 located postings name
    several), ``company`` its ``hiringOrganization``, and ``specs`` every spec the page's
    specifications block shows, by taxonomy. Either half can be missing: a site can hide the
    block, and some pages serve it with no JSON-LD (on ``abatec.co.uk``, 27 of 30 sampled).
    """
    specs = {
        taxonomy: terms
        for taxonomy, body in _SPEC.findall(page)
        if (terms := [_text(term) for term in _SPEC_TERM.findall(body) if _text(term)])
    }
    node = find_job_posting(page)
    if node is None and not specs:
        return None
    node = node or {}
    places = node.get("jobLocation")
    return {
        "location": _joined(
            place.get("address")
            for place in (places if isinstance(places, list) else [places])
            if isinstance(place, dict) and isinstance(place.get("address"), str)
        ),
        "company": hiring_organization(node.get("hiringOrganization")),
        "specs": specs,
    }


def _title(row: dict[str, Any]) -> str:
    """The REST row's title, entities decoded and the trailing no-break space some carry cut."""
    return html.unescape(_rendered(row.get("title")) or "").strip()


def _rendered(field: Any) -> str | None:
    value = field.get("rendered") if isinstance(field, dict) else None
    return value if isinstance(value, str) else None


def _slug_terms_list(row: dict[str, Any], taxonomy: str) -> list[str]:
    """``taxonomy``'s terms off the REST row's ``class_list``, each slug spelled as words.

    WordPress writes a post's terms as ``{taxonomy}-{term slug}`` classes, so a slug is read back
    by its taxonomy's prefix; ``job-location-new-delhi`` reads "New Delhi". A slug of non-Latin
    text is percent-encoded, and is decoded first.
    """
    prefix = f"{taxonomy}-"
    classes = row.get("class_list")
    if not isinstance(classes, list):
        return []
    return [
        unquote(cls[len(prefix) :]).replace("-", " ").strip().title()
        for cls in classes
        if isinstance(cls, str) and cls.startswith(prefix) and len(cls) > len(prefix)
    ]


def _slug_terms(row: dict[str, Any], taxonomy: str) -> str | None:
    return _joined(_slug_terms_list(row, taxonomy))


def _joined(values: Any) -> str | None:
    """Distinct non-empty values, in order, "; "-joined; None when there are none."""
    return (
        "; ".join(dict.fromkeys(v.strip() for v in values if v and v.strip())) or None
    )


def _text(fragment: str) -> str:
    return html.unescape(_TAG.sub("", fragment)).strip()


def _utc(date_gmt: Any) -> str | None:
    """The REST row's ``date_gmt`` ("2026-07-30T17:25:40") as ISO-8601 UTC."""
    if not isinstance(date_gmt, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", date_gmt
    ):
        return None
    return f"{date_gmt}+00:00"


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
