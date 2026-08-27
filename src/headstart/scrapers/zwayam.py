"""Zwayam job-board scraper (Info Edge / Naukri Talent Cloud).

Zwayam serves every tenant's Board from one shared API host, ``public.zwayam.com``, and selects
the Board by the **career-site hostname** — so this scraper's slug is a host
(``careers.persistent.com``, ``impetus.openings.co``), the way zoho's and eightfold's are, not a
bare tenant label. Discovery of those hosts is a separate problem with its own writeup:
``docs/discovery/zwayam-tenant-discovery.md``.

Four things about the protocol are not what the earlier capture in
``experiment/ats-provider-expansion/artifacts/research_zwayam.md`` recorded, each measured against
the live endpoint on 2026-08-27 rather than carried over:

* **``companyId`` is ignored by the search.** That capture describes a two-call flow — POST the
  config endpoint for a numeric company id, base64 it, then search. Measured on 4 Boards, passing
  ``base64("1")`` returns the correct per-Board count, and passing *another tenant's* real id
  alongside Persistent's ``domain`` returns Persistent's own count. **``domain`` alone is the
  key** (see the Origin bullet), so the listing walk makes one request per page rather than two
  per Board. The field can even be omitted entirely (measured 2026-08-27, 2/2 normal pages back)
  — an earlier claim here that omitting it 400s was wrong; what actually errors is an *empty or
  non-base64 value*, which answers 200 with body ``code: 500``. It is kept, with a fixed valid
  value, to mirror the real client. The one place the *real* numeric id is required is the
  ``jobs-service`` detail endpoint (the detail-pass section below), which is why the config call
  still exists in this file — demoted from step 1 of every scrape to a helper the detail pass
  invokes only when it has something to fetch.
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
* **Liveness is in the body, never the status — and so are server errors.** A hostname that is
  not a registered Board answers ``HTTP 200`` with ``"code": 200, "data": null``. A *failing*
  request also answers ``HTTP 200`` with ``data: null`` — but with ``"code": 500`` (measured
  2026-08-27 by sending malformed input). The two must not read the same: treating a transient
  ``code: 500`` as "Board has nothing" marks every posting Unconfirmed, and a second one evicts
  them all (ADR-0083) — so :meth:`ZwayamScraper._page` raises on a non-200 body code and only a
  body code of 200 with ``data: null`` means a dead Board.

**Page size is fixed at 10 and cannot be raised.** ``paginationEndNo``, ``pageSize``, ``size``,
``noOfRecords``, ``recordsPerPage``, ``limit`` and ``count`` were each probed against a 723-job
Board and every one returned the same 10 rows. So a Board costs ``ceil(jobs / 10)`` requests, and
the largest known Board (``career.axismaxlife.com``, 7,638 postings) costs ~764. That is inherent
to the endpoint, not a tuning choice.

**A selective detail pass, for the rows the listing leaves textless.** The listing row carries
the full description for most rows — but not all: 2,162 of 16,427 rows walked across 19 Boards
(13%) have *nothing* in any of the four description fields, while the per-job detail endpoint
(``jobs-service/v1/jobs/careersite``, JSON POST of ``jobUrl`` + the *real numeric* ``companyId``)
holds their complete postings (a 6,033-char JD was measured behind a listing row with none). So
the detail POST is made **only for rows whose listing text is empty** — a Board with full listings
costs zero detail requests, and the ADR-0050 store's skip-list keeps a fetched description from
ever being fetched twice. The detail also serves *longer* text than the listing on some rows that
do carry a medium description (one Board measured 632 chars listed vs 6,572 in detail); fetching
detail for every row would recover those but triples the request count, and is deliberately not
done — the trigger is "no text at all", not "short text".

**Three frontend generations, three job-link shapes.** The API is one host, but the careers sites
in front of it are not one SPA — classified live across all 224 hiring Boards (2026-08-27):

* **Angular** (104 Boards, 17,152 postings; nearly every custom domain): serves a ``<base href>``,
  routes ``{base}jobview/{jobUrl}`` — no hyphen, read out of the app's own click handler
  (``o.substring(0,o.indexOf("jobslist/"))+"jobview/"+t``). Any path answers 200 (client routing).
* **Next.js** (102 Boards, 2,570 postings; all on ``openings.co``): no ``<base>``, ``/_next/``
  asset paths, and its build manifest routes ``/job-view/[slug]`` — **hyphenated**, rooted at
  ``/``. The wrong spelling is a hard 404 here (verified 10/10 Boards), not a client-routed 200.
* **Old shell** (2 Boards, 1,714 postings — Adani and Menate): an Angular 1.8 page whose route
  table (``js/app/app.js``) contains ``/job-view/:jobUrl`` with html5Mode off and the default
  hash prefix, so the user-facing link is ``/#!/job-view/{jobUrl}``. Both plain paths 404.

:meth:`ZwayamScraper._link_base` reads one homepage GET per Board to pick the shape, so a run
costs ``ceil(jobs / 10) + 1`` requests per Board plus the selective details. When that GET fails
the shape falls back on the measured hostname prior: ``openings.co`` Boards are Next 102:12, every
custom domain measured is Angular 92:0.

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
from urllib.parse import quote

from headstart import http, log
from headstart.models import Job, host_of, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper

_log = log.get(__name__)

_API = "https://public.zwayam.com/jobs/search"
#: Resolves a Board host to its tenant record; the detail endpoint needs the numeric ``id`` from
#: here (base64 or a wrong id both fail it), which is this call's only remaining job.
_CONFIG_API = "https://public.zwayam.com/data-service/v2/public-configurations"
#: Per-job detail (JSON POST, unlike the multipart search): the only source of text for the 13%
#: of rows whose listing carries no description at all (module docstring).
_DETAIL_API = "https://public.zwayam.com/jobs-service/v1/jobs/careersite"
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
#: Delhi/Himachal.
#:
#: Non-INR currencies do exist but are always *stated*, so this default never overrides one: QAR on
#: ``kpmgcareersqatar.com`` (which carries no amounts at all) and EUR on one
#: ``careers.torryharris.com`` row. An earlier version of this comment called QAR the only one —
#: it was not, and the survey behind it was too small to say so. What the wider look does support
#: is the narrower claim that matters here: no Board was found posting a *bare* non-rupee figure.
#: A tenant that starts doing so breaks the assumption, and nothing here would detect it.
#:
#: (The EUR row reads ``2500000-3500000 EUR`` — plainly rupees mislabelled by the tenant. A stated
#: currency still wins, so the row is left wrong rather than second-guessed here.)
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


def search_request(host: str, start: int = 0) -> tuple[str, dict[str, str], bytes]:
    """``(url, headers, body)`` for one page of ``host``'s Board — the whole request, in one place.

    Public because ``check_liveness``'s ``p_zwayam`` asks the same question the scrape does, and a
    probe that asks it *differently* classifies Boards the scrape then handles differently. It
    previously imported only the body helpers and re-declared the headers, which is exactly how the
    two drift: its copy already sent a different ``User-Agent``, the one header measured to decide
    whether this endpoint answers at all.
    """
    return (
        _API,
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": f"multipart/form-data; boundary={_BOUNDARY}",
            # Ignored by the server (module docstring); sent to mirror the real client.
            "Origin": f"https://{host}",
            "Referer": f"https://{host}/",
        },
        _multipart(
            {
                "filterCri": _filter_at(start),
                "domain": host,
                "companyId": _IGNORED_COMPANY_ID,
            }
        ),
    )


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
    fact about the form rather than about the job. A zero *max* under a real min is the same
    unfilled half, not a ceiling: those rows read "Above 3.5 years" in the tenant's own phrasing
    (59 of 60 lo>hi pairs across 16,427 walked rows, 2026-08-27), and rendering them "3.5-0
    years" ships an inverted range where "3.5+ years" is what is meant — and what
    ``experience.extract`` reads as an open floor.
    """
    lo, hi = source.get("minYearOfExperience"), source.get("maxYearOfExperience")
    if isinstance(lo, int | float) and isinstance(hi, int | float) and (lo or hi):
        if hi <= 0 < lo:
            return f"{lo:g}+ years"
        return f"{lo:g}-{hi:g} years"
    return (source.get("experienceUIField") or "").strip() or None


def _amount(value: object) -> str:
    """One salary bound, or "" when the tenant left it blank. Zero counts as blank."""
    text = str(value or "").strip()
    try:
        return "" if float(text) == 0 else text
    except ValueError:
        return text


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
    # A zero bound is an unfilled half of the form, not a stated floor or ceiling — the same
    # reading `_experience` gives an all-zero pair. Kept as a *pair* check rather than dropped
    # blindly: emitting "1000000-0" makes `salary.extract` reject the whole row, losing a real
    # 1,000,000 floor that parses fine on its own (9 of 234 live amount rows, 2026-08-27).
    lo = _amount(source.get("minJobSalary"))
    hi = _amount(source.get("maxJobSalary"))
    if not lo and not hi:
        return None
    currency = (source.get("currencyType") or "").strip() or _DEFAULT_CURRENCY
    span = f"{lo}-{hi}" if lo and hi else (lo or hi)
    return f"{span} {currency}".strip()


def _description(source: dict) -> str | None:
    """Longest available body. ``medium*`` is the full posting; ``short*`` is a truncated teaser
    on some tenants and the whole posting on others, so it is the fallback rather than the
    preference. The ``*WithoutHtml`` variants are pre-stripped by the vendor; the others are HTML
    (and never longer than their pre-stripped sibling — 0 of 16,427 walked rows, so the order
    between the pairs costs nothing). Last comes the text the detail pass injected, which by the
    ``need`` selection in :meth:`ZwayamScraper.fetch_raw` only exists when every listing field
    above was empty.
    """
    for key in ("mediumDescriptionWithoutHtml", "shortDescriptionWithoutHtml"):
        value = (source.get(key) or "").strip()
        if value:
            return value
    for key in ("mediumDescription", "shortDescription"):
        value = html_to_text(source.get(key))
        if value:
            return value
    return source.get("_detail_description") or None


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
    #: A *selective* pass: the detail POST is made only for the ~13% of rows whose listing
    #: carries no description in any field (module docstring). True so the embed planner knows a
    #: zwayam vector can have been built before its text arrived (ADR-0050).
    has_detail_pass = True

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """Host only. The API keys on the hostname, so a ledger row carrying a full URL or a
        deep link has to normalise to the same string the API expects — the same reason zoho and
        personio override this."""
        return host_of(url) or tenant.strip().lower()

    def url(self) -> str:
        """The Board's human careers page. This is also what :meth:`_link_base` reads to tell
        the frontend generations apart; the JSON lives at the shared :data:`_API` instead."""
        return f"https://{self.slug}/"

    def _page(self, start: int) -> dict[str, Any]:
        url, headers, body = search_request(self.slug, start)
        response = http.fetch(
            "POST",
            url,
            data=body,
            headers=headers,
            timeout=45,
            **self._egress(),
        )
        response.raise_for_status()
        payload = response.json() or {}
        code = payload.get("code")
        if code is not None and code != 200:
            # HTTP 200 with a non-200 body code is how this endpoint reports its own failures
            # (module docstring). Without this raise it reads exactly like a dead Board's
            # `data: null` and silently empties a live one — the ADR-0083 mass-eviction setup.
            raise RuntimeError(
                f"zwayam body code {code}: {payload.get('message') or 'no message'}"
            )
        return payload

    def _fallback_link_base(self) -> str:
        """The link shape to assume when the homepage cannot be read or matches no marker.

        Chosen from the measured hostname prior (module docstring): ``openings.co`` hosts are the
        Next.js generation 102:12, every classified custom domain is Angular 92:0. A wrong guess
        here costs a dead job link, not a lost Job.
        """
        if self.slug.endswith("openings.co"):
            return f"https://{self.slug}/job-view/"
        return f"https://{self.slug}/jobview/"

    def _link_base(self) -> str:
        """Everything of the job deep link before the encoded ``jobUrl`` — read from one homepage
        GET, because the three frontend generations route the job view three different ways
        (module docstring) and nothing the API returns tells them apart (the config endpoint's
        ``folder`` for coforge is ``coforgetech`` while its Angular base path is ``/coforge/``).

        The markers, checked in this order: a ``<base href>`` is the Angular generation (its base
        path plus ``jobview/`` — measured across 10 Boards: 8 declare ``/{slug}/``, one ``/``);
        ``/_next/`` asset paths are the Next.js generation (``/job-view/`` at the root — the wrong
        spelling hard-404s there, 10/10 Boards); an ``ng-view`` mount is the old Angular 1 shell
        (hash-routed ``/#!/job-view/``, from its own ``app.js`` route table and the 1.6+ default
        hash prefix, both Boards). A failed GET degrades to the hostname prior rather than
        sinking the Board.
        """
        try:
            response = http.fetch(
                "GET",
                self.url(),
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                timeout=30,
                **self._egress(),
            )
            html = response.text or ""
        except Exception as exc:  # noqa: BLE001 - a link prefix must not fail the Board
            fallback = self._fallback_link_base()
            _log.info(
                f"{self.board_key()}: homepage unread ({type(exc).__name__}), "
                f"assuming {fallback}"
            )
            return fallback
        match = _BASE_HREF.search(html)
        if match:
            prefix = match.group(1).strip() or "/"
            if "://" in prefix or prefix.startswith("//"):
                # An absolute <base href> is legal HTML. Concatenating it onto the Board host
                # would build `https://host/https://cdn.../jobview/…`, so fall back rather than
                # emit a link that cannot resolve.
                _log.info(f"{self.board_key()}: absolute base href {prefix!r}, using /")
                prefix = "/"
            elif not prefix.startswith("/"):
                prefix = "/" + prefix
            if not prefix.endswith("/"):
                prefix += "/"
            return f"https://{self.slug}{prefix}jobview/"
        if "/_next/" in html:
            return f"https://{self.slug}/job-view/"
        if "ng-view" in html:
            return f"https://{self.slug}/#!/job-view/"
        return self._fallback_link_base()

    def _company_id(self) -> int | None:
        """The tenant's numeric id, from the config endpoint — the detail POST rejects anything
        else (base64 400s, a wrong id is another tenant). ``None`` on any failure: a Board whose
        config call breaks loses this run's detail fetches, never its Jobs."""
        try:
            response = http.fetch(
                "POST",
                _CONFIG_API,
                data=_multipart({"companyUrl": self.slug}),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": f"multipart/form-data; boundary={_BOUNDARY}",
                },
                timeout=30,
                **self._egress(),
            )
            response.raise_for_status()
            payload = response.json() or {}
            company = (payload.get("responseObject") or {}).get("company") or {}
            native = company.get("id")
            return native if isinstance(native, int) else None
        except Exception as exc:  # noqa: BLE001 - a lost detail pass must not fail the Board
            _log.info(f"{self.board_key()}: config call failed ({type(exc).__name__})")
            return None

    def _job_detail(self, company_id: int, job_url: str) -> str | None:
        """One Job's full posting text — the detail JSON's ``longDescription``, stripped.

        A JSON POST, unlike the multipart search; ``fan_out`` turns any raising call into
        ``None``, which :meth:`report_detail_gaps` then counts.
        """
        response = http.fetch(
            "POST",
            _DETAIL_API,
            json={"jobUrl": job_url, "companyId": company_id},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
            },
            timeout=30,
            **self._egress(),
        )
        response.raise_for_status()
        detail = response.json() or {}
        return html_to_text(detail.get("longDescription")) or None

    def fetch_raw(self) -> Any:
        """Walk the Board's pages, fetch detail text for the rows the listing left empty, then
        read the homepage once for the Board's link shape.

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
                # A hostname that is not a registered Board answers 200 with data: null (and a
                # body code of 200 — `_page` raised otherwise). Nothing to scrape, and not an
                # error — the ledger simply holds a host that no longer is.
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
                known = f" of {total}" if total is not None else ""
                self.mark_truncated(
                    f"stopped at the {_MAX_PAGES}-page cap with {len(rows)}{known} postings"
                )
        if total is not None and rows and len(rows) < total:
            # `mark_truncated` keeps the FIRST reason, so the page cap above still wins where it
            # fired — this is the shortfall that reaches `harvest` when it did not.
            self.mark_truncated(f"read {len(rows)} of {total} postings")
        if self.truncated:
            _log.warning(f"{self.board_key()}: {self.truncated}")
        # Selective detail pass: only rows with no listing text at all, minus the ids whose text
        # the ADR-0050 store already holds. A Board with full listings makes zero detail calls.
        need = [
            row
            for row in rows
            if _description(row) is None
            and (row.get("jobUrl") or "").strip()
            and self.needs_detail(str(row.get("id")))
        ]
        if need:
            company_id = self._company_id()
            if company_id is not None:
                details = self.fan_out(
                    need,
                    lambda row: self._job_detail(company_id, row["jobUrl"].strip()),
                )
                self.report_detail_gaps(details, "descriptions")
                for row, text in zip(need, details):
                    row["_detail_description"] = text
        return {"rows": rows, "link_base": self._link_base() if rows else ""}

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        rows = (raw or {}).get("rows") or []
        link_base = (raw or {}).get("link_base") or self._fallback_link_base()
        jobs: list[Job] = []
        for source in rows:
            native_id = source.get("id")
            title = (source.get("jobTitle") or "").strip()
            if native_id is None or not title:
                continue
            location = _location(source)
            job_url = (source.get("jobUrl") or "").strip()
            if not job_url:
                # Unobserved: 0 of 16,427 rows across 19 Boards. The alternative — falling back
                # to the Board root — would emit a link that no per-Job URL shape can match, so
                # the row is dropped and logged instead of shipping an unverifiable link.
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
                    # Near-no native remote flag: `workMode` is null on 16,422 of 16,427 rows
                    # walked, and the 5 set say "In Office/On-site" — nothing to read a remote
                    # signal from, so this is the post-hoc location heuristic alone.
                    remote=is_remote(location),
                    # The lowercase key is null while capitalised `DepartmentName` carries the
                    # real value on 1,399 of 16,427 walked rows (8.5%) — the two are the same
                    # value everywhere both are set, so the fallback only ever recovers.
                    department=(
                        source.get("departmentName")
                        or source.get("DepartmentName")
                        or ""
                    ).strip()
                    or None,
                    # Percent-encoded with `safe=""`: real `jobUrl` values carry spaces, commas
                    # and even slashes (11 of 16,427 rows), and the Next.js generation routes
                    # %2F within the one [slug] segment but hard-404s a raw slash (verified
                    # live). `link_base` already ends at the generation's own job route — see
                    # `_link_base` for how the three frontend shapes are told apart.
                    url=f"{link_base}{quote(job_url, safe='')}",
                    posted_at=_posted_at(source),
                    scraped_at=scraped_at,
                    description=_description(source),
                    experience=_experience(source),
                    # `jobType` is "J" on all 16,427 rows walked and `employeeType`/
                    # `jobTypeFieldDisplayName` are null, so the listing states no employment
                    # type. Left None rather than mapped from a constant that means nothing.
                    employment_type=None,
                    salary=_salary(source),
                )
            )
        return jobs
