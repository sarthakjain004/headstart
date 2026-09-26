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
  invokes only when it has something to fetch, or the Board needs its company name (below).
* **A non-default ``User-Agent`` is required, and a missing one HANGS.** Measured 2026-08-27:
  ``curl/8.7.1`` and ``python-requests``'s own default both **time out** rather than answering, so
  a caller that treats a timeout as a transient fault will retry forever. It is not a *browser*
  check — this repo's own ``headstart/0.1`` returns 200 like Chrome does — it is the stock tool
  agents that get blackholed. Measured again 2026-09-07 while moving that shared string off a
  SuccessFactors denylist (``base.USER_AGENT``): this host additionally rejects any User-Agent
  carrying a **domain or an email** with ``curl (92) HTTP/2 stream error`` — 2 of 2 attempts on
  each of four candidates — which is half of why the shared value is bare rather than carrying a
  contact URL.
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

**A detail pass for every new Job — the listing's text cannot be trusted complete.** The listing
carries description fields, but what they hold ranges from the full posting to nothing at all:
2,162 of 16,427 rows walked across 19 Boards (13%) have *nothing* in any of the four fields, and
rows that do carry text can be silently truncated (one Board measured 632 chars listed against
909 of stripped detail text — 6,572 raw) with **no client-side way to tell a short posting from a
cut one**.
The per-job detail endpoint (``jobs-service/v1/jobs/careersite``, JSON POST of ``jobUrl`` + the
*real numeric* ``companyId``) holds the complete posting in ``longDescription`` (a 6,033-char JD
was measured behind a listing row with none), so it is fetched for **every row not on the
ADR-0050 skip-list** and wins over the listing text. The listing fields stand only when the
detail answers with no text; a failed detail call — or a failed config call, which fails every
detail on the Board — ships no description, so the next run retries it. The store bounds the cost: each Job's detail is fetched once in its lifetime (~15 KB a
response, so the first pass over the 22,456-posting corpus moves ~340 MB; steady state is new
postings only). What the detail holds is the tenant's own paste, junk included — one measured
posting carries an AI-chat UI's class markup verbatim, and ``html_to_text``'s
unescape-before-strip order (a deliberate Darwinbox accommodation, per its docstring) lets an
escaped ``&gt;`` inside such an attribute leak fragments of it into the text. Tenant data
quality, logged here so the next reader doesn't chase it as a scraper bug.

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
costs ``ceil(jobs / 10) + 1`` requests per Board plus the details. When that GET fails
the shape falls back on the measured hostname prior: ``openings.co`` Boards are Next 102:12, every
custom domain measured is Angular 92:0.

**No reproducible rate limit — on either endpoint.** A 2026-08-27 load test could not make the
search refuse: ~2,160 requests across 150 sequential, 32-wide concurrency (~94 req/s), 60
distinct ``domain`` values, and 1,500 sustained at 34 req/s — zero non-200s. The **detail**
endpoint was probed separately the same day (1,360 requests: 300 sequential, 600 at 32-wide, 400
cross-tenant at 16-wide, 60 config calls — ``experiment/zwayam-rate-limit/``): zero refusals
there too, but it is **slow, not limited** — ~1.4 s a response alone and ~3.4 s under 32-wide
load, so throughput lands at ~8-9 responses/s per IP at both widths probed (16-wide 7.8/s,
32-wide 9.3/s — different Boards, so they bound the ceiling rather than rank the widths; see
:attr:`ZwayamScraper.detail_workers` for why the narrower one is used regardless). One Akamai
403 was seen during 2026-08 discovery
and is real. It was read as rare and transient; **that reading is falsified** (measured
2026-09-17, `experiment/zwayam-403-wall/LOG.md`): it is a cumulative **per-IP request quota**, and
the 2026-08 probes simply stayed under it. From one IP, 100/200/300/400 cumulative requests all
answered 200, 23 of 100 were refused at 500 and 100 of 100 at 600 — volume, not width, which is
why a 32-wide burst never reproduced it. So request counts *do* bind, per IP and across every
tenant at once, since all three API calls share one origin; :attr:`egress_fallback_on` answers it
by rotating rather than by pacing. The other binding
costs are **bytes and detail latency**: a 10-row page is 70-200 KB and a
detail ~15 KB, so the first full pass moves ~680 MB and its 22,456 details take ~45 minutes of
aggregate wall-clock at the ceiling — once, since the ADR-0050 store prunes every later run to
new postings.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from headstart import log
from headstart.jobs import salary
from headstart.jobs.job import Job, host_of, html_to_text, is_remote
from headstart.network.fetcher import Fetcher
from headstart.scrapers.base import (
    USER_AGENT,
    BaseScraper,
    DetailLost,
    DetailRequest,
    classify_exception,
)
from headstart.scrapers.pacer import Pacer

_log = log.get(__name__)

_API = "https://public.zwayam.com/jobs/search"
#: Resolves a Board host to its tenant record; the detail endpoint needs the numeric ``id`` from
#: here (measured: base64 400s, another tenant's or a nonexistent id 404s), which is this call's
#: only remaining job.
_CONFIG_API = "https://public.zwayam.com/data-service/v2/public-configurations"
#: Per-job detail (JSON POST, unlike the multipart search): the source of every Job's
#: description, since the listing's own text can be silently truncated (module docstring).
_DETAIL_API = "https://public.zwayam.com/jobs-service/v1/jobs/careersite"
#: **This path, and only this path, requires a browser User-Agent.**
#:
#: ``jobs-service`` sits behind an Akamai rule that ``data-service`` and the search host do not:
#: :data:`~headstart.scrapers.base.USER_AGENT` (``headstart/0.1``) is answered with a 403
#: ``Access Denied`` HTML page, so *every* description fetch failed — 56,771 of 56,771 detail-Jobs
#: across the five runs of 2026-09-16, on every Board, with ``learned 0`` descriptions each run.
#: It read as ``HTTPError`` rather than ``HTTP 403`` because the call discarded the status.
#:
#: Measured live 2026-09-16, 10 real jobs across 5 Boards (talentsst1, careers.newtonschool.co,
#: careerscc.vit.ac.in, verticalraisersindiapvtltd, naukrift): the repo UA scored **0/10** and a
#: Chrome UA **10/10**, returning real ``longDescription`` bodies. Same client, same IP, same
#: minute, and ``Origin``/``Referer`` changed nothing either way — so this is the UA alone, not a
#: CORS or IP rule. Controls on the same host in the same second: ``data-service`` answered a real
#: application 400 under the repo UA, so the edge refusal is path-scoped, not host-wide.
#:
#: Kept separate from ``base.USER_AGENT`` deliberately: that value is bare *because* this same host
#: rejects any agent carrying a domain or an email (module docstring), and a SuccessFactors
#: denylist wants it bare too. Widening the shared constant to a browser string to satisfy one
#: path would re-open both.
_DETAIL_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
#: Ignored by the search — even omissible (module docstring) — so its value is arbitrary; sent to
#: mirror the real client, and kept valid base64 because an *empty or malformed* value body-500s.
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
#: The config call names the tenant as well as numbering it — ``responseObject.company.
#: companyName``, on 164 of 165 affected Boards (2026-09-24) — so it is made for a Board that
#: needs its name even when no detail is wanted. That is one more metered request per Board
#: against the per-IP quota (`egress_fallback_on`), so config calls are spaced process-wide. The
#: 2026-09-24 census asked all 165 Boards from one IP one at a time, ~1.5-2 s apart: 164 answered
#: and one was refused 403; a 70-Board retry pass at 2 s answered every one.
_CONFIG_SPACING_S = 2.0
_CONFIG_PACER = Pacer(_CONFIG_SPACING_S)
#: The careers SPA declares its own path prefix here; the job deep link has to carry it.
_BASE_HREF = re.compile(r"<base\s+href=\"([^\"]*)\"", re.IGNORECASE)
#: Where :meth:`ZwayamScraper.fetch_raw` records the text a Job should ship with. Absent means
#: **no description this run** — either the detail failed (retry next run) or the ADR-0050 store
#: already holds this Job's text and will supply it, which is why the listing's own fields are
#: never read at parse time.
_TEXT = "_resolved_description"


def _native_id(row: dict) -> str | None:
    return None if row.get("id") is None else str(row["id"])


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


def body_error_code(payload: dict) -> object | None:
    """The body ``code`` when the response reports its own failure, else None.

    The endpoint's failures arrive as HTTP 200 with ``code: 500`` and the same ``data: null`` a
    dead Board answers (module docstring). Public for the same reason as :func:`search_request`:
    ``check_liveness``'s ``p_zwayam`` must draw the dead-vs-failed line exactly where the scrape
    does, or the two classify the same Board differently. A body without a ``code`` passes — the
    field has been present on every response measured, and if the vendor drops it the null data
    should keep meaning what it always has.
    """
    code = payload.get("code")
    return code if code is not None and code != 200 else None


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


def _listing_description(source: dict) -> str | None:
    """The best text the *listing row itself* carries, or None.

    Never the whole answer — the listing can be silently truncated, which is why there is a
    detail pass (module docstring) — so :meth:`ZwayamScraper.fetch_raw` decides when this is
    allowed to stand. The fields are tried in a fixed order rather than compared by length:
    ``medium*`` over the sometimes-teaser ``short*``, and the vendor-pre-stripped
    ``*WithoutHtml`` variants over their HTML siblings, which is measurement-backed rather than
    arbitrary (a ``*WithoutHtml`` value was never the shorter of its pair — 0 of 16,427 walked
    rows — so length-comparing them would pick the same field at more cost).
    """
    for key in ("mediumDescriptionWithoutHtml", "shortDescriptionWithoutHtml"):
        value = (source.get(key) or "").strip()
        if value:
            return value
    for key in ("mediumDescription", "shortDescription"):
        value = html_to_text(source.get(key))
        if value:
            return value
    return None


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
    #: zwayam meters **cumulative requests per IP** over a window and refuses with a bare 403 — no
    #: `Retry-After`, no `cf-mitigated`, no interstitial body. Every tenant is probed through the
    #: one shared API (`public.zwayam.com`), so one origin carries the whole ATS and the quota is
    #: spent within a single run: 47-71% of attempted Boards failed in each of the five runs
    #: 35175065218-35188643520, 55-73% of every run's board errors.
    #:
    #: Opted in on the **mechanism**, which is the bar this attribute's own docstring sets after
    #: freshteam (#311) and personio (#312/#313) were opted in on an aggregate count and reverted.
    #: Measured 2026-09-17 from one IP (`experiment/zwayam-403-wall/LOG.md`):
    #:   - 100/200/300/400 cumulative requests -> 200 on every one; 23 of 100 refused at 500;
    #:     100 of 100 at 600. It meters volume, not width — 60 requests at 16-wide all answered.
    #:   - Against 25 slugs that had *just* been refused, interleaved: **WARP cleared 25/25, the
    #:     direct route 12/25.** A different address is the only thing that clears it.
    #:   - End to end, in `check_liveness`'s equivalent rung: a 3,239-Board sweep went from 2,816
    #:     UNKNOWN to **3**, and 756 of 757 ledger-live Boards re-confirmed live. The Boards were
    #:     never gone — the quota was spent.
    #:
    #: Carried by `base._fetch`, which all four request sites here go through, so the opt-in is
    #: not inert (the caution above about direct `http.fetch` calls does not apply). Three of the
    #: four hit the metered API — `_page` (`_API`), `_config` (`_CONFIG_API`) and
    #: `detail_request` (`_DETAIL_API`, sent by `run_detail_pass` through `_fetch`), all on
    #: `public.zwayam.com`. The fourth, `_link_base`, GETs the Board's own
    #: customer domain and passes `marks_wall=False` for that reason; see the note there.
    #:
    #: `_DETAIL_API`'s documented UA-rule 403 (above) is a malformed-request 403, not a quota one.
    #: It is answered by sending the right User-Agent, and a rotation cannot fix it — but it is
    #: constant-cost and self-inflicted rather than traffic-dependent, so it cannot spend the
    #: rotation budget the way a per-tenant WAF could.
    egress_fallback_on = frozenset({403})
    # scraper: f"{link_base}{quote(jobUrl)}" where the SLUG IS THE BOARD HOST — the API keys on
    # the hostname, and Boards sit on customer domains (careers.persistent.com) as well as the
    # vendor namespace ({slug}.openings.co), so there is no host to anchor on. `link_base` is one
    # of the three frontend generations' own job routes (module docstring, classified live
    # across all 224 hiring Boards 2026-08-27): Angular's `{base href}jobview/` (the optional
    # path segments), Next.js's root `/job-view/`, or the old Angular 1 shell's hash route
    # `/#!/job-view/`. The trailing `jobUrl` is the vendor's own slug, percent-encoded. NOTE the
    # Angular generation answers 200 for ANY path and the hash route never reaches the server, so
    # `status_ok` is not evidence of a good link for this ATS — only the shape is (Next.js is the
    # one generation where a bad route would actually 404).
    url_shape = (
        r"https://[^/]+(?:(?:/[\w.-]+)*/jobview/|/job-view/|/#!/job-view/)[\w.%~-]+$"
    )
    #: The detail POST supplies every Job's description (the listing's own text can be silently
    #: truncated — module docstring); the ADR-0050 skip-list prunes it to new postings. True so
    #: the embed planner knows a zwayam vector can have been built before its text arrived.
    has_detail_pass = True
    #: A judgement call, not a measured optimum — say so plainly, because the two probe numbers
    #: it rests on are **not** a width sweep: 32-wide measured 9.3 responses/s and 16-wide 7.8,
    #: but against different Boards and row counts, so they bound the endpoint's throughput
    #: (~8-9/s per IP either way) without ranking the two widths. Doubling concurrency against a
    #: shared origin for at most ~19% is not a trade this repo makes on one unpaired pair of
    #: measurements, and the ADR-0050 skip-list makes the full-corpus pass a one-time cost
    #: anyway. Whatever the width, no async fan-out: multiplexing cannot raise a server ceiling.
    detail_workers = 16
    #: The thread path, which this pass has always taken. The transport itself is **not**
    #: measured: ADR-0167 asks for a measurement, and this records why there is none. An
    #: interleaved A/B at width 16 was tried on 2026-09-24 (impetus.openings.co,
    #: careers.practo.com), and the per-IP quota (`egress_fallback_on`) walled the detail path on
    #: both transports within ~15-20 requests. Multiplexed got 21 of 32 details, then 13 of 32,
    #: before HTTP 403; threads got 15 of 39. So the two are equally correct under the wall, but
    #: nothing shows which is faster. What is on record is the note on `detail_workers` above: a
    #: measured ~8-9 responses/s per-IP ceiling, which multiplexing cannot raise. Re-run the A/B
    #: from a fresh egress before moving this pass off threads. That move must also take
    #: `_config_once_per_board`'s blocking config call out of `detail_request`, which the
    #: multiplexed path runs inside its event loop.
    async_fanout = False
    #: Process-wide, shared by every instance (see `_CONFIG_SPACING_S`).
    config_pacer = _CONFIG_PACER

    def __init__(
        self, slug: str, company: str | None = None, fetcher: Fetcher | None = None
    ) -> None:
        super().__init__(slug, company, fetcher=fetcher)
        self._config_lock = threading.Lock()
        # Filled by `_config_once_per_board`; a failed config call leaves None and is not
        # asked again.
        self._config_asked = False
        self._resolved_config: tuple[int | None, str | None] = (None, None)

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

    def job_url(self, link_base: str, native_job_url: str) -> str:
        """``link_base`` is resolved once per Board by :meth:`_link_base` (a network fetch, so
        it is passed in rather than re-derived here); ``native_job_url`` is the listing's own
        vendor slug. Percent-encoded with ``safe=""`` — see the call site's own comment for why
        (ADR-0153)."""
        return f"{link_base}{quote(native_job_url, safe='')}"

    def _page(self, start: int) -> dict[str, Any]:
        url, headers, body = search_request(self.slug, start)
        response = self._fetch(
            "POST",
            url,
            data=body,
            headers=headers,
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json() or {}
        code = body_error_code(payload)
        if code is not None:
            # Without this raise a failing response reads exactly like a dead Board's
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
            response = self._fetch(
                "GET",
                self.url(),
                # `marks_wall=False`: this is the only request here that does NOT go to the
                # metered API — it GETs the Board's own customer domain
                # (`careers.persistent.com`, `adani.openings.co`). A WAF 403 from one customer
                # says nothing about zwayam's per-IP quota, and marking on it would wall the whole
                # ATS for the run off one tenant's edge — the exact shape of the personio revert
                # (#312/#313) that `egress_fallback_on`'s own docstring cites. Routing is kept,
                # marking is dropped (the eightfold precedent, `BoardFetcher.egress_binding`).
                marks_wall=False,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                timeout=30,
            )
            html = response.text or ""
        except Exception as exc:  # noqa: BLE001 - a link prefix must not fail the Board
            fallback = self._fallback_link_base()
            _log.info(
                f"{self.board_key()}: homepage unread ({classify_exception(exc)}), "
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
        fallback = self._fallback_link_base()
        # Said, because every Job of this Board now ships a guessed link shape.
        _log.info(
            f"{self.board_key()}: homepage answered {response.status_code} with no "
            f"base-href/_next/ng-view marker — assuming {fallback}"
        )
        return fallback

    def _config(self) -> tuple[int | None, str | None]:
        """The tenant's numeric id and stated name, from the config endpoint. The detail POST
        rejects any other id (measured: base64 400s, a wrong numeric id 404s); the name is
        ``company.companyName``. ``(None, None)`` on any failure: a Board whose config call breaks
        loses this run's detail fetches and name, never its Jobs."""
        self.config_pacer.wait()
        try:
            response = self._fetch(
                "POST",
                _CONFIG_API,
                data=_multipart({"companyUrl": self.slug}),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": f"multipart/form-data; boundary={_BOUNDARY}",
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json() or {}
            company = (payload.get("responseObject") or {}).get("company") or {}
            native, name = company.get("id"), company.get("companyName")
            return (
                native if isinstance(native, int) else None,
                name if isinstance(name, str) else None,
            )
        except Exception as exc:  # noqa: BLE001 - a lost detail pass must not fail the Board
            _log.info(
                f"{self.board_key()}: config call failed ({classify_exception(exc)})"
            )
            return None, None

    def _config_once_per_board(self) -> tuple[int | None, str | None]:
        """:meth:`_config`, called once per Board and only when something needs it — the Board's
        name, or the Detail pass's first request. The call is metered like every other
        (``egress_fallback_on``), so a named Board whose rows are all gated or already held never
        spends it. Locked because the thread transport (:attr:`async_fanout`) forms requests from
        several workers at once."""
        with self._config_lock:
            if not self._config_asked:
                self._resolved_config = self._config()
                self._config_asked = True
            return self._resolved_config

    def detail_request(self, row: dict) -> DetailRequest:
        """A JSON POST, unlike the multipart search, carrying the *real* numeric company id."""
        company_id, _ = self._config_once_per_board()
        if company_id is None:
            # The config call is per-Board, so its failure fails every detail on the Board — each
            # a loss like any other failed detail, so each retries next run.
            raise DetailLost("no company id")
        return DetailRequest(
            _DETAIL_API,
            method="POST",
            headers={
                "User-Agent": _DETAIL_USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
            },
            options={
                "json": {"jobUrl": row["jobUrl"].strip(), "companyId": company_id}
            },
        )

    def read_detail(self, row: dict, response: Any) -> str:
        """One Job's full posting text — the detail JSON's ``longDescription``, stripped.

        ``""``, never a loss, when the detail JSON carries no ``longDescription``: `fetch_raw`
        needs the two apart — a loss is transient and must be retried next run, the other is this
        posting's final answer. A body that is not JSON at all is a loss, labelled by its type."""
        detail = response.json() or {}
        return html_to_text(detail.get("longDescription")) or ""

    def fetch_raw(self) -> Any:
        """Walk the Board's pages, fetch each new Job's detail text, then read the homepage once
        for the Board's link shape.

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
                if not rows:
                    self.note_unreadable_board(
                        "a `data` object",
                        "code 200 with data: null — host not a registered Board",
                    )
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
        # Detail pass for every row the ADR-0050 store does not already hold text for: the
        # listing's own fields can be silently truncated (module docstring), so the detail is
        # the only text trusted as complete. Steady state, `needs_detail` prunes this to the
        # Board's new postings.
        # Two skips: the tech gate (ADR-0017) drops what `filter_tech` would drop anyway, and
        # `skip_held` (ADR-0048) drops what the description store already holds. The gate is
        # exact here — `parse` reads `jobTitle` and `departmentName` off this same listing row
        # and the detail supplies only text — so it cannot cost a Job the index would have kept.
        # A row with no `jobUrl` has no detail to ask for, and `parse` drops it anyway.
        if rows and self.wants_company_name():
            self.adopt_company(self._config_once_per_board()[1])
        linked = [row for row in rows if (row.get("jobUrl") or "").strip()]
        texts = self.run_detail_pass(
            linked,
            key_of=_native_id,
            what="descriptions",
            title_of=lambda row: row.get("jobTitle"),
            department_of=lambda row: (
                row.get("departmentName") or row.get("DepartmentName")
            ),
            skip_held=True,
        )
        bodyless = 0
        for row in linked:
            text = texts.get(_native_id(row))
            if text:
                row[_TEXT] = text
            elif text == "":
                # The detail answered with no body: this posting has no fuller text than the
                # listing's, so the listing's is final rather than provisional.
                row[_TEXT] = _listing_description(row)
                bodyless += 1
            # A *failed* detail (absent) records nothing, so the Job ships with no description
            # and `update_descriptions` stores none — leaving `needs_detail` true so the next
            # run retries it. Falling back to the listing text here would be a one-way door:
            # the store persists whatever the scrape emits, membership in it *is* the
            # skip-list, and a skip-listed Job never fetches a detail again — so one
            # transient failure would freeze text this module measured as possibly
            # truncated, permanently and invisibly.
        if bodyless:
            # Not losses, so the gap line never counted them — yet each is fetched again every
            # run when the listing text is empty too, since no description is then stored to
            # skip it by.
            _log.info(
                f"{self.board_key()}: {bodyless}/{len(texts)} details answered with no "
                "longDescription — the listing text is kept"
            )
        return {"rows": rows, "link_base": self._link_base() if rows else ""}

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        rows = (raw or {}).get("rows") or []
        link_base = (raw or {}).get("link_base") or self._fallback_link_base()
        jobs: list[Job] = []
        unlinked = 0
        unnamed = 0
        for source in rows:
            native_id = source.get("id")
            title = (source.get("jobTitle") or "").strip()
            if native_id is None or not title:
                unnamed += 1
                continue
            location = _location(source)
            job_url = (source.get("jobUrl") or "").strip()
            if not job_url:
                # Unobserved: 0 of 16,427 rows across 19 Boards. The alternative — falling back
                # to the Board root — would emit a link that no per-Job URL shape can match, so
                # the row is dropped and counted instead of shipping an unverifiable link.
                unlinked += 1
                continue
            jobs.append(
                Job(
                    id=self.job_id(native_id),
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
                    url=self.job_url(link_base, job_url),
                    posted_at=_posted_at(source),
                    scraped_at=scraped_at,
                    description=source.get(_TEXT),
                    experience=_experience(source),
                    # `jobType` is "J" on all 16,427 rows walked and `employeeType`/
                    # `jobTypeFieldDisplayName` are null, so the listing states no employment
                    # type. Left None rather than mapped from a constant that means nothing.
                    employment_type=None,
                    salary=self._salary_field(source),
                )
            )
        if unlinked:
            _log.info(f"{self.board_key()}: {unlinked} job(s) had no jobUrl, skipped")
        self.note_unread_rows(unnamed, len(rows), "with no id/title")
        return jobs

    def _salary_field(self, raw: dict) -> str | None:
        """Every posted range, whether or not the tenant flipped its display toggle.

        ``showSal`` is the tenant's own careers-page toggle, and it is off on most rows that
        nonetheless carry amounts (19 of 23 across four Boards). An earlier version honoured it,
        on the reasoning that publishing a withheld figure asserts something the employer chose
        not to. **That was overruled deliberately: a figure beats an empty column here.**

        What makes the trade different on this ATS than it looks: salary has no second path. The
        Tier-2 description mine that supplies most of the index's parsed salaries — 84% of rows
        with a figure have no raw field string — recovers **0 of 52** on Zwayam, because Indian
        postings do not state compensation in prose. So this field is the only source there will
        ever be, and the toggle was not one filter among several but the whole gate.

        The cost is accepted knowingly: some tenants leave a form default in place (one Board
        posts an identical ``100000-200000`` with no currency across ten unrelated roles), so a
        minority of published figures are placeholders rather than offers.
        """
        # A zero bound is an unfilled half of the form, not a stated floor or ceiling — the same
        # reading `_experience` gives an all-zero pair. Each side is blanked on its own rather
        # than the pair dropped: emitting "1000000-0" makes `salary.extract` reject the whole
        # row, losing a real 1,000,000 floor that parses fine alone (17 of 5,079 amount rows,
        # 2026-08-27).
        lo = _amount(raw.get("minJobSalary"))
        hi = _amount(raw.get("maxJobSalary"))
        currency = (raw.get("currencyType") or "").strip() or _DEFAULT_CURRENCY
        if lo:
            return salary.to_field(lo, hi or None, currency)
        if hi:
            # Ceiling-only (10 of 5,079 rows) must not be emitted *bare*: `salary.extract` reads
            # a lone figure as a floor (measured: "200000 INR" -> min_annual=200000), so a job
            # capped at 200k would be served as one paying at least that. "Upto" is the honest
            # rendering — `Job.salary` is a display column (README §"The served table": "raw,
            # for display"), so the reader sees the real bound, while `extract` measurably parses
            # it to None and the derived columns stay empty rather than inverted.
            return f"Upto {hi} {currency}"
        return None
