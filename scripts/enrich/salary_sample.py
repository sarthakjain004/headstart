#!/usr/bin/env python3
"""Sample real boards to measure how one ATS shows salary (docs/salary-extraction/).

For up to N live boards of ``<ats>`` (``config.load_active_companies`` — the same liveness-ledger
source and dedup every other production consumer uses), fetches the listing response through the
*real* registered scraper (``registry.get_scraper``) and its real ``parse()``, then measures, per
Job: whether ``salary`` came back populated (a structured-field hit), and whether the description
text *looks* like it mentions a figure (``_SALARY_HINT_RE`` — a loose detector for this coarse
measurement pass only; it is not the extractor. ``headstart.salary`` is what actually parses a
figure out, built from what this script finds).

Listing-only ATSes (``has_detail_pass = False``) get one cheap request per board — the whole
sample. A detail-pass ATS needs its own adapter in ``_DETAIL_ADAPTERS``, built during that ATS's
own research pass — see docs/salary-extraction/README.md. Most cap detail fetches at
``_DETAIL_FETCH_CAP`` per board, calling the scraper's own endpoint methods directly rather than
its ``fetch_raw()`` (several detail-pass scrapers use that for a full per-board fan-out, expensive
across a large sample). **zoho is the deliberate exception**: its listing never paginates, so
calling ``fetch_raw()`` costs nothing beyond the detail fetches it needs anyway, and capping those
specifically was undercounting real coverage on boards with many missing-description jobs — see
``_fetch_zoho``'s own docstring. An ATS with neither the listing-only nor a built adapter shape
exits with a clear message rather than guessing.

**Spare egress is automatic, never hand-rolled.** Every fetch goes through the scraper's own
``_get()``/``_post()``/``_job_detail()``-style methods, which already carry
``**self._egress()`` — so an ATS with ``egress_fallback_on`` set (workday: ``{429}``) transparently
routes through `headstart.spare_egress`'s WARP fallback the same way the real pipeline does,
reactively, the first time this process meets a wall. No adapter here should ever call
``http.fetch`` directly; that would silently skip it. **Narrow, named exception:**
``_fetch_ripplehire`` calls ``http.fetch`` directly twice, mirroring ``ripplehire.py``'s own
production ``fetch_raw()`` exactly (which does the same for the identical reason — see that
adapter's own docstring) — harmless today only because ``RippleHireScraper.egress_fallback_on`` is
unset; re-check this exception if ripplehire is ever added to that set. One real local limitation,
verified 2026-08-21: rotation (`systemctl restart warp-svc`) needs systemd and doesn't exist on
macOS, so a local run gets one alternate route per process, not full adaptive rotation — CI still
gets that.

Raw captures land in ``experiment/salary-extraction/<ats>/artifacts/<slug>.json`` (the full parsed
Job list, for reading the real API shape) plus one ``coverage_summary_<n>_seed<seed>.json`` per
run (named by board count *and* seed, so neither a small live-verification run nor a same-size
re-verify with a different seed can clobber another run's summary in the same directory). Progress
prints one line per board as it completes (a bounded thread pool via ``as_completed``, never a
blocking map).

``--workers`` (default :data:`_DEFAULT_WORKERS`) bounds board-level concurrency. Higher than a
single ATS's own production per-*tenant* concurrency (e.g. workday's own ``detail_workers = 6``)
is safe here specifically because boards are sampled across many different companies/instances —
workday's own metering is per (source IP, instance host), and ``workday.py`` documents only 18
known ``wdN`` instances (not "hundreds" — corrected 2026-08-21 after code review), each shared by
many companies. 32 workers spread across 18 instances is under 2 concurrent requests per instance
*on average* — a calculated estimate from that documented count, not a measured per-instance load,
since board sampling order isn't guaranteed to spread evenly across instances.

    .venv/bin/python scripts/enrich/salary_sample.py workable
    .venv/bin/python scripts/enrich/salary_sample.py workday --n 500 --workers 48
    .venv/bin/python scripts/enrich/salary_sample.py workable --misses
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from headstart import http
from headstart.config import EXCLUDED_BOARDS, CompanyRef, load_active_companies
from headstart.models import Job
from headstart.scrapers import registry
from headstart.scrapers.base import USER_AGENT, BaseScraper
from headstart.scrapers.eightfold import _sitemap_position_id
from headstart.scrapers.ripplehire import _PAGE_SIZE as _RIPPLEHIRE_PAGE_SIZE
from headstart.scrapers.ripplehire import _TOKEN as _RIPPLEHIRE_TOKEN
from headstart.scrapers.successfactors import _job_urls_from
from headstart.scrapers.trakstar import _codes_from

ROOT = Path(__file__).resolve().parents[2]
LEDGER_DIR = ROOT / "data" / "validate" / "liveness"
ARTIFACTS_ROOT = ROOT / "experiment" / "salary-extraction"
#: Fallback candidate-tenant source for an ATS with no liveness ledger at all (oracle,
#: sensehq — see docs/salary-extraction/README.md's own processing-order note). Raw
#: discovery output (Common Crawl / fingerprint mining), never liveness-checked — dead
#: candidates simply error during the normal per-board fetch, the same as any other ATS's
#: occasional dead board.
CANDIDATES_DIR = ROOT / "data" / "ats-tenants-merged"

#: Default board-level concurrency. Deliberately well above any single ATS's own production
#: per-tenant bound (see the module docstring's concurrency note for why that's safe here — 32
#: workers over workday's documented 18 ``wdN`` instances is under 2 concurrent/instance on
#: average, a calculated estimate, not a measured one) — 3000 boards at the old default of 8 took
#: long enough to make a full pass impractical. Override with --workers for a specific ATS if its
#: own rate-limiting turns out to need something gentler.
_DEFAULT_WORKERS = 32

# A coarse "does this text look like it mentions a salary" detector for the measurement pass only
# — currency symbols/codes near digits, magnitude shorthand, and the region-specific phrasings
# this repo's India-strong-segment scope makes common (LPA, CTC, "per annum"). Deliberately loose:
# false positives get sorted out by reading the --misses/hit sample by hand, not by tightening this
# regex, since headstart.salary (not this script) is where real precision belongs.
_SALARY_HINT_RE = re.compile(
    r"""
    (?:[$€£₹]\s?\d[\d,]*\s?[kK]?)                       # $120,000  €50k  ₹8
    | (?:\d[\d,]*\s?[-–to]{1,3}\s?\d[\d,]*\s?[kK]?\s?(?:USD|EUR|GBP|INR|CAD|AUD))
    | (?:\bLPA\b)                                        # Lakhs Per Annum
    | (?:\bCTC\b)                                        # Cost To Company
    | (?:salary|compensation|remuneration)\s+(?:range|of|is|:|between)
    | (?:per\s+annum|annual\s+salary|base\s+salary)
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass
class BoardResult:
    ats: str
    slug: str
    company: str
    jobs: int
    jobs_with_salary_field: int
    jobs_with_desc_hint: int
    jobs_with_either: int
    error: str | None = None


def _sample_boards(ats: str, n: int, seed: int) -> list[CompanyRef]:
    all_companies = [c for c in load_active_companies(LEDGER_DIR) if c.ats == ats]
    if not all_companies and not (LEDGER_DIR / f"{ats}.csv").exists():
        all_companies = _candidates_without_ledger(ats)
    if len(all_companies) <= n:
        return all_companies
    return random.Random(seed).sample(all_companies, n)


def _candidates_without_ledger(ats: str) -> list[CompanyRef]:
    """Fallback for an ATS with no liveness ledger at all (:data:`CANDIDATES_DIR`'s own
    docstring) — reads the raw candidate-tenant discovery file directly, applying the same
    :data:`~headstart.config.EXCLUDED_BOARDS` filter :func:`load_active_companies` would.
    Unlike that function, this does NOT itself check liveness — for a population this small
    (dozens, not thousands), the caller's own per-board fetch during sampling already is
    the liveness check, so a candidate that turns out dead simply errors there, same as any
    ATS's occasional dead board."""
    path = CANDIDATES_DIR / f"{ats}.csv"
    if not path.exists():
        return []
    companies = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            slug = row["tenant"]
            if f"{ats}:{slug}".lower() in EXCLUDED_BOARDS:
                continue
            companies.append(CompanyRef(ats=ats, slug=slug, name=slug))
    return companies


#: How many per-job detail fetches a BOUNDED detail-pass adapter may spend on one board — the
#: ~3/board cap the sampling design commits to (docs/salary-extraction/README.md). Only the
#: detailed jobs are counted toward coverage stats: an undetailed posting has no description to
#: mine, and lumping it in as a "no signal" job would dilute the measurement with jobs never
#: actually read, not jobs genuinely found to say nothing. NOT used by ``_fetch_zoho`` (PR #242):
#: zoho's listing never paginates, so capping its *detail* fetches was undercounting real
#: coverage for no corresponding cost saving — see that function's own docstring.
_DETAIL_FETCH_CAP = 3


def _fetch_workday(scraper: BaseScraper) -> list[Job]:
    """Bounded adapter for workday: one listing page via the scraper's own single-page primitive
    (``_post``, offset 0 — never ``fetch_raw()``, which recursively subdivides and paginates the
    *whole* board plus fans out detail fetches over *every* posting found). Detail-fetches only
    the first :data:`_DETAIL_FETCH_CAP` postings, via the scraper's own ``_job_detail``, then
    parses just those — so the returned Jobs are exactly the ones with a real description."""
    scraper._resolve_instance()
    page = scraper._post({}, offset=0)
    postings = (page or {}).get("jobPostings") or []
    sample = postings[:_DETAIL_FETCH_CAP]
    for item in sample:
        detail = scraper._job_detail(item.get("externalPath"))
        item["_detail"] = detail or {}
    return scraper.parse(sample, datetime.now(UTC).isoformat())


def _fetch_smartrecruiters(scraper: BaseScraper) -> list[Job]:
    """Bounded adapter for smartrecruiters: one listing page via the scraper's own single-page
    primitive (``_get()``, no offset — never ``fetch_raw()``, which pages the *whole* board plus
    fans out detail fetches over *every* posting found). Detail-fetches only the first
    :data:`_DETAIL_FETCH_CAP` postings via the scraper's own ``_job_detail``, then parses
    just those — so the returned Jobs are exactly the ones with a real description."""
    page = json.loads(scraper._get())
    postings = (page or {}).get("content") or []
    sample = postings[:_DETAIL_FETCH_CAP]
    for item in sample:
        item["_detail"] = scraper._job_detail(item.get("id")) or {}
    return scraper.parse({**page, "content": sample}, datetime.now(UTC).isoformat())


def _fetch_zoho(scraper: BaseScraper) -> list[Job]:
    """Uncapped adapter for zoho: calls the scraper's own ``fetch_raw()`` directly, unlike every
    other detail-pass adapter here. Zoho's listing is one page (``_get()``) with no *further*
    request needed to page through it — but that single response is not unbounded: it silently
    truncates at a hard ~750-job ceiling with no reachable pagination past it (confirmed by direct
    investigation, not assumed — full account in ``zoho.py``'s own module docstring and
    docs/salary-extraction/zoho.md's "Post-merge correction" section; user confirmed accepting
    this as a documented platform limit rather than pursuing a bigger architectural fix, PR #242).
    So "costs nothing extra to call in full" is true for the ≤750 jobs a response returns, not a
    claim every board's true population is ≤750 — this adapter and zoho.py's own production
    `fetch_raw()` make the identical single request, so both lose the same excess silently.

    Independent of that ceiling: the listing already carries ``Job_Description`` for most tenants
    among whichever jobs it does return; some tenants configure their careers site without that
    column (28/71 in the scraper's own docstring) and need a per-job detail fetch instead.

    This used to cap those detail fetches at :data:`_DETAIL_FETCH_CAP` (3/board, serial, its own
    small re-implementation of ``fetch_raw()``'s ``empty``-selection logic). A DIFFERENT, earlier
    zoho bug — every eligible record came back from ``parse()`` regardless of whether its
    description was ever fetched, so records past the cap silently became phantom "no signal"
    Jobs — was already fixed on zoho's original pass (PR #238) by post-filtering `parse()`'s
    output through ``keep_ids``, the same filter still below: 21,358 of 58,004 jobs (36.8%) were
    phantom before that fix (docs/salary-extraction/zoho.md's own numbers). That filter was
    correct as far as it went, but it only ever hid the cap's cost from the coverage percentage —
    it didn't remove the cost. A board with far more than 3 missing-description jobs still only
    ever got 3 of them genuinely read; `keep_ids` correctly excluded the rest from the denominator
    rather than mis-scoring them as "no signal," but excluding them is not the same as reading
    them — real coverage was still measured over a tiny, non-representative slice of any such
    board's true population. Removed here: this adapter now detail-fetches every missing-
    description record via the scraper's own concurrent ``fetch_raw()`` (fan_out/fan_out_async,
    spare-egress-aware, same path production uses), matching what a real scrape of the board
    already does — user-directed fix, PR #242, independently re-measured (not assumed) at 36,624
    to 45,855 genuinely-read jobs (+25.2%) and 9.2% to 10.0% overall coverage on a fresh seed=7
    resample; see docs/salary-extraction/zoho.md's correction section for the full account. The
    post-filter below still matters for a *genuine* detail-fetch failure (network error, 404) —
    ``parse()`` doesn't drop those either, and keeping one in the sample would count a job that
    was actually attempted-and-failed as if it had been read and found to say nothing; filtered
    here exactly as before, just over the now-complete ``details`` dict instead of one capped at
    3."""
    raw = scraper.fetch_raw()
    records = scraper._records(raw["page"])
    keep_ids = {
        r["id"]
        for r in records
        if r.get("id")
        and not r.get("Is_Locked")
        and r.get("Publish", True)
        and (r.get("Job_Description") or r["id"] in raw["details"])
    }
    jobs = scraper.parse(raw, datetime.now(UTC).isoformat())
    return [j for j in jobs if j.id.split(":", 2)[2] in keep_ids]


def _fetch_rippling(scraper: BaseScraper) -> list[Job]:
    """Bounded adapter for rippling: one listing GET via the scraper's own inherited ``_get()``
    primitive (``BaseScraper``'s, egress-wrapped — rippling.py doesn't override it), never
    ``fetch_raw()``, which bakes the FULL per-posting detail fan-out into that same call — every
    job on the board, not a capped subset. It's the *fan-out*, not the listing, that makes calling
    ``fetch_raw()`` directly unsafe for a sampling pass here. Detail-fetches only the first
    :data:`_DETAIL_FETCH_CAP` postings via the scraper's own ``_detail()``, then parses just
    those, mirroring workday's/smartrecruiters' capped shape rather than zoho's uncapped one."""
    data = json.loads(scraper._get())
    items = (
        data
        if isinstance(data, list)
        else (data.get("items") or data.get("jobs") or [])
    )
    sample = items[:_DETAIL_FETCH_CAP]
    for item in sample:
        item["_detail"] = scraper._detail(item.get("uuid")) or {}
    return scraper.parse(sample, datetime.now(UTC).isoformat())


def _fetch_ripplehire(scraper: BaseScraper) -> list[Job]:
    """Bounded adapter for ripplehire: one listing POST (page 0 only, at production's own
    ``_PAGE_SIZE``) via the scraper's own token handshake, never ``fetch_raw()``, which loops the
    search POST across every page AND bakes an uncapped per-posting detail fan-out into the same
    call — every job on the board, not a capped subset. The token step calls ``http.fetch``
    directly (matching ``ripplehire.py``'s own ``fetch_raw()``, not ``BaseScraper._get()``): it
    needs the redirected response's own ``.url`` to pull the token from, which ``_get()`` doesn't
    expose (it returns only ``.text``) — this isn't a missed egress-wrapped primitive (lesson 40),
    it's the one step that structurally can't use it. The search POST that follows ALSO calls
    ``http.fetch`` directly, for a different reason: unlike workday (``_post()``) or smartrecruiters/
    rippling (``_get()``), ``RippleHireScraper`` exposes no single-page search primitive to
    delegate to — ``fetch_raw()`` builds the same request inline too, so this mirrors production's
    own necessary duplication rather than introducing a new one. Both calls are genuinely inert
    here (see the module docstring's own named exception for this adapter) since ripplehire has no
    ``egress_fallback_on`` set; revisit if that ever changes.

    Deliberately asymmetric, unlike every other capped adapter here: ``compensationRange``/
    ``compensationInfo`` (Tier 1) are already on the LISTING response — real, found while building
    this adapter (an initial version requested only :data:`_DETAIL_FETCH_CAP` listing rows via
    ``pagesize``, silently starving field coverage to 3 jobs/board on boards documented at
    hundreds — LTIMindtree, ~937 jobs, `ripplehire.py`'s own module docstring — with no network-
    cost reason to, since the field costs nothing extra to read). So the FULL page (up to
    ``_PAGE_SIZE`` real listing rows) is kept for field coverage; only the expensive per-job
    detail fetch (``jobDesc``, never on the listing — confirmed in ``ripplehire.py``'s own
    docstring) is capped at :data:`_DETAIL_FETCH_CAP`, via the scraper's own ``_job_description()``.
    Consequence, stated plainly rather than silently absorbed: on any board with more than
    :data:`_DETAIL_FETCH_CAP` jobs, the description-hint measurement undercounts (rows past the
    cap keep an empty ``description``, correctly read as "no hint" rather than fabricating one) —
    a conservative bias on the coarser Tier-2 signal only, never on Tier 1's own field coverage,
    which is exactly what this fix exists to make accurate."""
    response = http.fetch(
        "GET", scraper.url(), headers={"User-Agent": USER_AGENT}, timeout=30
    )
    response.raise_for_status()
    match = _RIPPLEHIRE_TOKEN.search(response.url)
    if not match:
        return []
    token = match.group(1)
    api = f"https://{scraper.slug}.ripplehire.com/candidate/candidatejobsearch"
    params = json.dumps(
        {
            "page": 0,
            "search": "*:*",
            "token": token,
            "source": "CAREERSITE",
            "pagesize": _RIPPLEHIRE_PAGE_SIZE,
        }
    )
    body = urllib.parse.urlencode({"careerSiteUrlParams": params, "lang": "en"})
    data = http.fetch(
        "POST",
        api,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
        timeout=30,
    ).json()
    items = data.get("jobVoList") or []
    for item in items[:_DETAIL_FETCH_CAP]:
        item["jobDesc"] = scraper._job_description(token, item.get("jobSeq"))
    return scraper.parse(items, datetime.now(UTC).isoformat())


def _fetch_successfactors(scraper: BaseScraper) -> list[Job]:
    """Bounded adapter for successfactors: one sitemap fetch via the scraper's own
    ``_fetch_sitemap()``, never ``fetch_raw()``, which fans out a per-job detail fetch over EVERY
    listed posting (workers=6, one host) — every job on the board, not a capped subset.
    ``_fetch_sitemap()`` is already cheap for a sampling pass by construction, not just here: it
    reads a urlset to completion (compact — the scraper's own module docstring puts the largest
    observed at ~3 MB) but *classifies and abandons* an RSS feed after ~64 KB
    (`_CLASSIFY_BYTES`), specifically so the trickling generator (jobs.sap.com's real feed is
    16.6 MB, confirmed live) never stalls a caller — confirmed this abandoned-early prefix still
    carries real job links (4 found in SAP's own ~75 KB prefix). Job URLs come from the module's
    own ``_job_urls_from()`` (shared across all three listing surfaces by design — the underlying
    regex just looks for ``/job/.../{id}/``, independent of urlset/RSS/search-page markup), so
    this needs no format-specific branching. Deliberately does NOT replicate ``fetch_raw()``'s
    `_search_job_urls()`/`_rss_job_urls()` fallbacks (used in production when the sitemap surface
    alone comes up empty) — for a bounded sample, a board with nothing in its sitemap contributes
    zero jobs rather than paying for the full fallback chain, an acceptable sampling loss given
    2000+ other boards. Detail-fetches only the first :data:`_DETAIL_FETCH_CAP` postings via the
    scraper's own ``_job_fields()``, then parses just those."""
    kind, text, _cut_short = scraper._fetch_sitemap()
    if kind not in ("urlset", "rss"):
        return []
    urls = _job_urls_from(text, scraper.slug)[:_DETAIL_FETCH_CAP]
    raw = [
        {"url": url, "id": job_id, "fields": scraper._job_fields(url) or {}}
        for url, job_id in urls
    ]
    return scraper.parse(raw, datetime.now(UTC).isoformat())


def _fetch_trakstar(scraper: BaseScraper) -> list[Job]:
    """Bounded adapter for trakstar: one listing GET via the scraper's own inherited ``_get()``
    primitive, never ``fetch_raw()``, which bakes the FULL per-posting JSON-LD detail fan-out into
    that same call (workers=4, DataDome-fronted) — every job on the board, not a capped subset.
    Detail-fetches only the first :data:`_DETAIL_FETCH_CAP` postings via the scraper's own
    ``_job_posting()``. Unlike workday/rippling/smartrecruiters (which slice the raw item list
    itself before calling ``parse()``), trakstar's ``parse()`` walks every job card found in the
    FULL listing HTML regardless of which postings were fetched — so the result is post-filtered
    down to just the detail-fetched codes afterward, the same "don't count a never-read job as a
    no-signal one" discipline zoho's adapter already established via its own ``keep_ids`` filter."""
    html = scraper._get()
    sample = _codes_from(html)[:_DETAIL_FETCH_CAP]
    postings = {code: scraper._job_posting(code) for code in sample}
    jobs = scraper.parse(
        {"html": html, "postings": postings}, datetime.now(UTC).isoformat()
    )
    return [j for j in jobs if j.id.split(":", 2)[2] in sample]


def _fetch_eightfold(scraper: BaseScraper) -> list[Job]:
    """Bounded adapter for eightfold: mirrors ``fetch_raw()``'s own primary/fallback branching,
    but never calls ``fetch_raw()`` (via ``_api_search()``) itself — that method runs a
    multi-sweep, replica-disagreement-tolerant crawl to reconstruct the FULL position list
    (see its own docstring, #142), expensive and unnecessary for a bounded sample. Instead:
    one raw ``_search_url(group_id, 0)`` GET via the scraper's own ``_get()`` gets the first
    page (up to 10 positions) directly, capped to :data:`_DETAIL_FETCH_CAP` before calling the
    scraper's own ``_api_records()`` (which fans out ``_description()`` only over the positions
    it's given, not the whole board). Falls back to a capped slice of ``_job_urls()`` (the
    sitemap listing, itself already cheap and fan-out-free) plus the scraper's own ``_jsonld()``
    per-job detail fetch when the API 403s — matching ``fetch_raw()``'s own fallback branch."""
    group_id = scraper._group_id()
    if group_id:
        first = scraper._get(scraper._search_url(group_id, 0), marks_wall=False)
        # None = API unavailable; [] = API works, board genuinely empty
        positions = None
        if first.status_code == 200:
            try:
                positions = (first.json().get("data") or {}).get("positions") or []
            except ValueError:
                positions = None
        if positions is not None:
            sample = positions[:_DETAIL_FETCH_CAP]
            records = scraper._api_records(group_id, sample)
            return scraper.parse(records, datetime.now(UTC).isoformat())
    urls = scraper._job_urls()[:_DETAIL_FETCH_CAP]
    records = []
    for u in urls:
        fields = scraper._jsonld(u)
        records.append(
            {
                "id": _sitemap_position_id(u),
                "url": u,
                "fields": fields,
                "detail_fetched": fields is not None,
            }
        )
    return scraper.parse(records, datetime.now(UTC).isoformat())


#: ATS -> detail-pass adapter, built per-ATS as that ATS is reached (never assumed for one not yet
#: researched — see the module docstring). workday/smartrecruiters/rippling/ripplehire/
#: successfactors/trakstar cap detail fetches at `_DETAIL_FETCH_CAP` (workday/smartrecruiters'
#: listings paginate too, so `fetch_raw()` itself is expensive to call from a sampling script;
#: rippling's/ripplehire's/successfactors'/trakstar's listings are cheap but their `fetch_raw()`
#: bakes in an uncapped detail fan-out — see each adapter's own docstring). eightfold caps the
#: same way but for a different reason: its own listing/search call, not a downstream detail
#: fan-out, is the expensive part — `fetch_raw()`'s primary path calls `_api_search()`, a
#: multi-sweep, replica-disagreement-tolerant crawl of the FULL board (#142) — so the adapter
#: issues one raw single-page search request directly instead, then caps *that* page's positions
#: before the detail fetch (see `_fetch_eightfold`'s own docstring). zoho is uncapped — its
#: listing never paginates, so `fetch_raw()` costs nothing extra there, and capping its *detail*
#: fetches was undercounting real coverage (see `_fetch_zoho`'s own docstring).
_DETAIL_ADAPTERS = {
    "workday": _fetch_workday,
    "smartrecruiters": _fetch_smartrecruiters,
    "zoho": _fetch_zoho,
    "rippling": _fetch_rippling,
    "ripplehire": _fetch_ripplehire,
    "successfactors": _fetch_successfactors,
    "trakstar": _fetch_trakstar,
    "eightfold": _fetch_eightfold,
}


def _fetch_one(company: CompanyRef, artifacts_dir: Path) -> BoardResult:
    scraper = registry.get_scraper(company.ats, company.slug, company.name)
    adapter = _DETAIL_ADAPTERS.get(company.ats)
    if scraper.has_detail_pass and adapter is None:
        return BoardResult(
            ats=company.ats,
            slug=company.slug,
            company=company.name or company.slug,
            jobs=0,
            jobs_with_salary_field=0,
            jobs_with_desc_hint=0,
            jobs_with_either=0,
            error="detail-pass ATS: no bounded adapter built yet for this ATS — see module docstring",
        )
    try:
        jobs = adapter(scraper) if adapter else scraper.fetch()
    except Exception as exc:  # noqa: BLE001 - one board's failure must not sink the sample
        return BoardResult(
            ats=company.ats,
            slug=company.slug,
            company=company.name or company.slug,
            jobs=0,
            jobs_with_salary_field=0,
            jobs_with_desc_hint=0,
            jobs_with_either=0,
            error=f"{type(exc).__name__}: {exc}",
        )
    with_field = sum(1 for j in jobs if (j.salary or "").strip())
    with_hint = sum(
        1
        for j in jobs
        if not (j.salary or "").strip() and _SALARY_HINT_RE.search(j.description or "")
    )
    either = sum(
        1
        for j in jobs
        if (j.salary or "").strip() or _SALARY_HINT_RE.search(j.description or "")
    )
    safe_slug = company.slug.replace("/", "_")
    (artifacts_dir / f"{safe_slug}.json").write_text(
        json.dumps([asdict(j) for j in jobs], indent=2, default=str)
    )
    return BoardResult(
        ats=company.ats,
        slug=company.slug,
        company=company.name or company.slug,
        jobs=len(jobs),
        jobs_with_salary_field=with_field,
        jobs_with_desc_hint=with_hint,
        jobs_with_either=either,
    )


def _run_sample(ats: str, n: int, seed: int, workers: int) -> list[BoardResult]:
    boards = _sample_boards(ats, n, seed)
    print(
        f"sampling {len(boards)} live board(s) for {ats} ({workers} workers)",
        flush=True,
    )
    artifacts_dir = ARTIFACTS_ROOT / ats / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    results: list[BoardResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_fetch_one, company, artifacts_dir): company
            for company in boards
        }
        for i, future in enumerate(as_completed(futures), 1):
            r = future.result()
            results.append(r)
            status = (
                r.error
                or f"{r.jobs} jobs, {r.jobs_with_salary_field} field, {r.jobs_with_desc_hint} desc-hint"
            )
            print(f"  [{i}/{len(boards)}] {r.slug}: {status}", flush=True)
    return results


def _summarize(ats: str, results: list[BoardResult], seed: int) -> None:
    ok = [r for r in results if r.error is None]
    errored = [r for r in results if r.error is not None]
    total_jobs = sum(r.jobs for r in ok)
    total_field = sum(r.jobs_with_salary_field for r in ok)
    total_hint = sum(r.jobs_with_desc_hint for r in ok)
    total_either = sum(r.jobs_with_either for r in ok)
    boards_with_any = sum(1 for r in ok if r.jobs_with_either > 0)

    print(f"\n===== {ats} coverage summary =====")
    print(f"boards sampled: {len(results)}  ({len(errored)} errored, {len(ok)} ok)")
    if not ok:
        print("no successful boards — nothing to summarize")
        return
    print(f"jobs seen: {total_jobs}")
    if total_jobs:
        print(
            f"  jobs with a structured salary field: {total_field} ({100 * total_field / total_jobs:.1f}%)"
        )
        print(
            f"  jobs with only a description hint:   {total_hint} ({100 * total_hint / total_jobs:.1f}%)"
        )
        print(
            f"  jobs with either:                     {total_either} ({100 * total_either / total_jobs:.1f}%)"
        )
    print(
        f"boards with >=1 job showing either: {boards_with_any}/{len(ok)} ({100 * boards_with_any / len(ok):.1f}%)"
    )

    # Named per run size AND seed (not a fixed "coverage_summary.json") so a small live-
    # verification sample can't silently clobber a large main run's summary, or another
    # differently-seeded verification run of the same size, in the same artifacts directory —
    # all are real, worth keeping (code review finding, PR #235; size-only collided on a same-N
    # re-verify before this fix, caught live while applying it).
    summary_path = (
        ARTIFACTS_ROOT
        / ats
        / "artifacts"
        / f"coverage_summary_{len(results)}_seed{seed}.json"
    )
    summary_path.write_text(
        json.dumps(
            {
                "ats": ats,
                "boards_sampled": len(results),
                "boards_ok": len(ok),
                "boards_errored": len(errored),
                "jobs_seen": total_jobs,
                "jobs_with_salary_field": total_field,
                "jobs_with_desc_hint_only": total_hint,
                "jobs_with_either": total_either,
                "boards_with_any_salary_signal": boards_with_any,
                "results": [asdict(r) for r in results],
            },
            indent=2,
        )
    )
    print(f"\nwrote {summary_path.relative_to(ROOT)}")


def _misses(ats: str, n: int, seed: int) -> None:
    """Re-read already-captured artifacts (no network) and sample jobs with NO salary signal at
    all, for manual reading — the read-then-widen half of the loop, mirroring
    experience_coverage.py --misses."""
    artifacts_dir = ARTIFACTS_ROOT / ats / "artifacts"
    if not artifacts_dir.exists():
        sys.exit(f"no captures at {artifacts_dir} — run a sample first")
    misses: list[dict] = []
    for f in sorted(artifacts_dir.glob("*.json")):
        if f.name.startswith("coverage_summary"):
            continue
        for j in json.loads(f.read_text()):
            has_field = bool((j.get("salary") or "").strip())
            desc = j.get("description") or ""
            has_hint = bool(_SALARY_HINT_RE.search(desc))
            if not has_field and not has_hint and len(desc) >= 200:
                misses.append(j)
    if not misses:
        print("no substantial misses found in captured artifacts")
        return
    sample = random.Random(seed).sample(misses, min(n, len(misses)))
    print(f"{len(misses)} substantial misses — sampling {len(sample)}\n")
    for j in sample:
        print(f"--- {j.get('id')} — {j.get('title')} ---")
        print((j.get("description") or "")[:1200].replace("\n\n", "\n"))
        print()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("ats")
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument(
        "--workers",
        type=int,
        default=_DEFAULT_WORKERS,
        help=f"concurrent board fetches (default {_DEFAULT_WORKERS})",
    )
    ap.add_argument(
        "--misses", action="store_true", help="read miss samples from prior captures"
    )
    args = ap.parse_args()

    if args.ats not in registry.SCRAPERS:
        sys.exit(f"unknown ats {args.ats!r}; known: {sorted(registry.SCRAPERS)}")

    if args.misses:
        _misses(args.ats, n=8, seed=args.seed)
        return 0

    results = _run_sample(args.ats, n=args.n, seed=args.seed, workers=args.workers)
    _summarize(args.ats, results, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
