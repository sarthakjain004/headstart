"""Eightfold AI scraper (public PCSX career sites: careers.qualcomm.com, jobs.nvidia.com,
paypal.eightfold.ai, ...).

A tenant's ``slug`` is its board host. Three public surfaces, primary + two fallbacks (probed live
2026-07-21 and again 2026-09-11 — see ``docs/eightfold/smartapply-fallback.md`` for the second date):

**Primary — the PCSX JSON API** (robots allows ``/api/pcsx``). The board's careers page carries
``_EF_GROUP_ID = "{domain}"``, the API's ``domain`` param. Then:
  - ``GET /api/pcsx/search?domain={d}&start={n}`` — paginates 10 positions/page (``start`` += 10),
    each with ``name``/``department``/``locations``/``postedTs``/``workLocationOption``/``positionUrl``
    — every field but the description. ``data.count`` is the board total.
  - ``GET /api/pcsx/position_details?position_id={id}&domain={d}&hl=en`` — clean JSON per job with
    ``jobDescription``. ~15 KB JSON vs the ~280 KB HTML page. A failed detail leaves description None
    but the job is still kept — its metadata already came from the search list.
Proven on 40/50 live tenants; adds ``department`` (absent from the sitemap path).

**Fallback 1 — SmartApply**, ``/api/apply/v2/jobs``, for the ~20% of tenants whose PCSX search
403s with an explicit "PCSX is not enabled" body (English or localized — the check keys on the
substring ``"pcsx"`` in the message, not the English phrase, since the observed Spanish variant is
"PCSX no está habilitado para este usuario."). Verified live 2026-09-11 against all 23 tenants of
this class in the then-current liveness ledger: **23/23 (100%) recovered**, same ``domain``/
``query``/``location``/``start`` params (plus ``sort_by=timestamp``), same 10-per-page pagination,
and — unlike the primary search — **no replica disagreement measured** (8 repeated-offset probes
across 4 tenants, 0 ids differing; 2 tenants' full crawls matched ``count`` exactly with 0
duplicate ids), so this path does one straight pass, no re-sweep. It also recovers ``department``
(absent from fallback 2's JSON-LD): 191/230 sampled positions had it non-null. Its own detail
endpoint is the *same* ``position_details`` used by the primary path — every one of the 23
tenants' SmartApply-sourced ids resolved there with a real description. Full protocol, field-shape
mapping, and the tenants tested: ``docs/eightfold/smartapply-fallback.md``.

**Fallback 2 — sitemap → per-job JSON-LD**, for a generic (non-PCSX) 403 or a SmartApply failure:
``GET {host}/careers/sitemap.xml`` lists every job as ``/careers/job/{positionId}-{slug}?domain=
{co}.com`` (a ``sitemap_index`` of children is followed one level); each job page embeds a
schema.org ``JobPosting``. No ``department`` here.

Internal-mobility-only tenants (Infosys/Wipro/Walmart — ``{slug}.eightfold.ai`` behind SSO) expose
neither surface publicly, so they yield nothing — correct, they are not public boards.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import UTC, datetime
from typing import Any, NamedTuple

from headstart import http, log
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import USER_AGENT, BaseScraper, DetailLost, DetailRequest
from headstart.scrapers.job_posting_jsonld import (
    find_job_posting,
    job_location_text,
    job_posting_fields,
)

_log = log.get(__name__)

_DETAIL_WORKERS = 6  # sync-path detail fetches; bounded since they hit one host
# Async-path multiplexing width, below the shared default of 100 (ADR-0047). Eightfold's edge
# meters per origin across *all* tenants; measured against a live board, details lost 78.6% at
# width 100 and 49.9% at width 25, and the slice's ~3,400 Eightfold fetches per shard put width 25
# at ~7 min for a typical shard and ~10 min for the worst. Provisional: re-measure with
# scripts/bench/probe_eightfold_throttle.py, and note those rates predate the 405 retry, which
# trades wall-clock for recovered fetches in both directions.
_DETAIL_STREAMS = 25
_PAGE = 10  # PCSX search page size is fixed at 10 (num_items is ignored)
_MAX_PAGES = (
    2000  # fetch bound across all sweeps: 2000 x 10 = 20k jobs, above any real board
)
# Full re-crawls to reassemble a complete list when replica orderings disagree (#142). Two extra
# sweeps close a ~6% per-sweep miss almost surely; a board still short after three is reported.
_MAX_SWEEPS = 3
_MAX_INDEX_CHILDREN = 50  # sitemap-fallback: child sitemaps to follow from an index

# The discriminator between "this tenant needs SmartApply" and a generic WAF 403: measured live
# 2026-09-11, the 403 body's `message` always names the product, in whichever locale ("PCSX is not
# enabled for this user." / "PCSX no está habilitado para este usuario.") — so match the substring,
# not an English phrase, or the Spanish-locale tenants (2 of 23 measured) silently miss SmartApply
# and fall all the way to the weaker sitemap path instead.
_PCSX_DISABLED = re.compile(r"pcsx", re.IGNORECASE)

_EF_GROUP_ID = re.compile(r'_EF_GROUP_ID\s*=\s*"([^"]+)"')
# sitemap-fallback patterns
_JOB_LOC = re.compile(r"<loc>\s*([^<\s]*/careers/job/[^<\s]+?)\s*</loc>", re.IGNORECASE)
_CHILD_SITEMAP = re.compile(
    r"<loc>\s*([^<\s]*sitemap[^<\s]*\.xml[^<\s]*)\s*</loc>", re.IGNORECASE
)
_POSITION_ID = re.compile(r"/careers/job/(\d+)")

# workLocationOption -> remote. "hybrid" stays None (neither purely remote nor onsite). Live
# vocabulary measured 2026-08-25 across 44,215 jobs/62 boards is exactly onsite/hybrid/
# remote_local/remote_global — "remote"/"fully remote" never appear, and remote_local/
# remote_global previously matched nothing here, so 999 explicitly-remote jobs (2.26%) fell
# through to the location-string fallback and were served remote=False.
_REMOTE_OPTION = {
    "remote": True,
    "fully remote": True,
    "onsite": False,
    "in office": False,
    "remote_local": True,
    "remote_global": True,
}


class _PcsxPosition(NamedTuple):
    """One PCSX search position with the ``domain`` its ``position_details`` request names — an
    item of the API surface's Detail pass. The sitemap fallback's items are its job-page URLs."""

    group_id: str
    position: dict[str, Any]


class EightfoldScraper(BaseScraper):
    """Eightfold AI scraper — ``slug`` is the board host."""

    ats = "eightfold"
    # scraper: f"https://{slug}{path}" where the SLUG IS THE BOARD HOST — five live ledger rows
    # sit on custom domains (careers.micron.com, jobs.vodafone.com, portal.careers.hsbc.com…),
    # so anchoring on .eightfold.ai flagged real rows. Host-agnostic like recruitee/darwinbox;
    # verified live 2026-08-02 on both host kinds (amdocs-sandbox.eightfold.ai, careers.micron.com)
    url_shape = r"https://[^/]+/careers/job/\d+"
    detail_workers = _DETAIL_WORKERS
    detail_streams = _DETAIL_STREAMS
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050)

    #: 403/405 are the edge's per-origin wall shapes; 429 is also treated as an origin wall so a
    #: throttled detail request gets a fresh spare-egress address before exhausting its retries.
    #: The user-requested 429 arm is intentionally shared across API and detail surfaces.
    egress_fallback_on = frozenset({403, 405, 429})

    #: Why this Board fell back from the PCSX API — via SmartApply, when that's tried, or
    #: straight to the per-job sitemap walk otherwise — written by whichever branch actually gave
    #: up. A class-level default only so the attribute exists before the first assignment; every
    #: path that reaches the fallback line has replaced it.
    _fallback_reason = "the PCSX API did not answer"

    def url(self) -> str:
        return f"https://{self.slug}/careers/sitemap.xml"

    def board_page(self) -> str:
        """The careers landing page, whose ``<title>`` is ``"Careers at {Name}"``.

        Not `url`, which is the sitemap: the slug here is a hostname, so without this the served
        company reads "jobs.vodafone.com" (`headstart.company_name`)."""
        return f"https://{self.slug}/careers"

    def job_url(self, position_id: str, path: str | None = None) -> str:
        """The PCSX/SmartApply surface's job-detail URL: the API's own ``positionUrl`` when it
        states one, else the derived ``/careers/job/{id}`` route — both resolved against this
        Board's host (ADR-0153). The sitemap fallback surface (:meth:`_sitemap_records`) instead
        reads the ATS's own sitemap ``<loc>`` directly; there is nothing to build there."""
        path = path or f"/careers/job/{position_id}"
        return f"https://{self.slug}{path}" if path.startswith("/") else path

    def _get(
        self,
        url: str | None = None,
        accept: str = "application/json",
        marks_wall: bool = True,
    ) -> Any:
        """GET one Eightfold URL. ``marks_wall=False`` still routes over the spare egress once this
        ATS is walled, but stops *this* request's failures from being what walls it."""
        return self._fetch(
            "GET",
            url or self.url(),
            headers=self._headers(accept),
            timeout=30,
            marks_wall=marks_wall,
        )

    def _headers(self, accept: str) -> dict[str, str]:
        """What every Eightfold request sends, the careers-page Referer included — on both
        Detail-pass transports, where the multiplexed copy of each detail request used to drop it
        (ADR-0201). Measured 2026-09-24 over one ``AsyncSession``: 90 of 90 ``position_details``
        on three Boards and 80 of 80 job pages on four answered identically with and without it.
        """
        return {
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Referer": f"https://{self.slug}/careers",
        }

    # --- shared entry -------------------------------------------------------------------------

    def fetch_raw(self) -> Any:
        """Normalized job records via the PCSX API, or the sitemap fallback when the API 403s.
        Both paths yield ``{id, url, fields:{...}}`` so ``parse`` is uniform."""
        group_id = self._group_id()
        if group_id:
            positions = self._api_search(group_id)
            if positions is not None:
                return self._api_records(group_id, positions)
        # Which surface answered, and why the cheap one did not — the sibling successfactors
        # scraper logs exactly this and it is the first thing asked when a Board's cost or
        # completeness changes shape. The two paths have entirely different failure modes (the
        # API is replica-unstable and re-swept; the sitemap is batch-generated and stable but
        # can be a stale or wrong-tenant index), so reading a Board's numbers without knowing
        # which one produced them has repeatedly meant re-probing the host by hand to find out.
        #
        # The reason is the *branch actually taken*, not the step that returned None. Both
        # helpers below have three exits each — a transport failure, a non-200, and a body that
        # arrived and did not carry what was wanted — and collapsing them onto "no group id on
        # the careers page" / "the PCSX API did not answer" sends an operator hunting a page
        # rewrite for what was a connection error.
        _log.info(
            f"{self.board_key()}: falling back to the sitemap — {self._fallback_reason}"
        )
        return self._sitemap_records()

    def _group_id(self) -> str | None:
        """The board's ``_EF_GROUP_ID`` (the API ``domain`` param), read from its careers page.

        Sets :attr:`_fallback_reason` on the way out, so a None says which of its three exits
        produced it."""
        try:
            r = self._get(f"https://{self.slug}/careers", accept="text/html")
        except http.RequestsError as exc:
            self._fallback_reason = f"the careers page failed ({type(exc).__name__})"
            return None
        if r.status_code != 200:
            self._fallback_reason = f"the careers page returned {r.status_code}"
            return None
        m = _EF_GROUP_ID.search(r.text)
        if not m:
            self._fallback_reason = "no group id on the careers page"
            return None
        return m.group(1)

    # --- primary: PCSX JSON API ---------------------------------------------------------------

    def _search_url(self, group_id: str, start: int) -> str:
        q = urllib.parse.urlencode(
            {"domain": group_id, "query": "", "location": "", "start": start}
        )
        return f"https://{self.slug}/api/pcsx/search?{q}"

    def _api_search(self, group_id: str) -> list[dict[str, Any]] | None:
        """Paginate ``/api/pcsx/search`` to the full position list. None signals "API unavailable"
        (403/non-200 on the first page) so the caller falls back to the sitemap.

        The pages come from replicas whose orderings disagree — the default sort key
        (``postedTs``) has day resolution, so hundreds of postings tie and each replica breaks
        the ties its own way. One offset crawl can therefore return a posting at two offsets and
        another at none, and counting raw rows against ``data.count`` let such a crawl believe
        itself complete while silently missing jobs — which sync then evicted as delistings and
        the next run re-added, the #142 flap. Positions are deduped by id, completeness is judged
        on *distinct* postings, and a short sweep is re-crawled (up to :data:`_MAX_SWEEPS`) to
        pick up the offsets the next replica deals differently.

        A crawl that still comes up short keeps the positions it has and marks the Board
        truncated. The API hands back ``data.count``, so how short the list is comes out
        *exactly* rather than inferred — which is the whole point: ``index sync`` can then skip
        the Board instead of reading the gap as delistings and evicting them (ADR-0053)."""
        # This page decides "does this tenant expose the API at all?", so its non-200 must not
        # mark the ATS walled (ADR-0063): ~40% of tenants answer a steady 403 here and a healthy
        # 200 on the sitemap right after, which would dial the spare egress on nearly every shard,
        # on the normal path. It still *routes* over the spare egress once something else has
        # walled us — exempting it from the routing too would send it over the spent IP and drop
        # every remaining Board onto the far more expensive per-job sitemap path.
        first = self._get(self._search_url(group_id, 0), marks_wall=False)
        if first.status_code != 200:
            if first.status_code == 403 and _pcsx_disabled(first):
                positions = self._smartapply_search(group_id)
                if positions is not None:
                    return positions
                # _smartapply_search already set _fallback_reason on its own failure exits.
                return None
            self._fallback_reason = f"the PCSX API returned {first.status_code}"
            return None
        try:
            data = first.json().get("data") or {}
        except ValueError:
            self._fallback_reason = "the PCSX API answered 200 with an unparseable body"
            return None
        total = int(data.get("count") or 0)
        seen: dict[str, dict[str, Any]] = {}
        for pos in data.get("positions") or []:
            seen.setdefault(str(pos.get("id")), pos)
        pages = 1
        for sweep in range(_MAX_SWEEPS):
            # Sweep 1 continues from the first page already fetched; later sweeps restart, since
            # the point is to see the same offsets dealt by a differently-ordered replica.
            start = _PAGE if sweep == 0 else 0
            before = len(seen)
            while len(seen) < total and start < total and pages < _MAX_PAGES:
                r = self._get(self._search_url(group_id, start))
                if r.status_code != 200:
                    self.mark_truncated_unless_negligible(
                        len(seen),
                        total,
                        _short_reason(
                            f"HTTP {r.status_code} on page {pages + 1}",
                            len(seen),
                            total,
                        ),
                    )
                    return list(seen.values())
                batch = (r.json().get("data") or {}).get("positions") or []
                if not batch:
                    # The list ended early; whether that is a truncation is decided below, on
                    # what the sweeps collectively found — not per page.
                    break
                for pos in batch:
                    seen.setdefault(str(pos.get("id")), pos)
                start += _PAGE
                pages += 1
            if len(seen) >= total:
                # Only when a re-sweep was actually needed. Converging on sweep 1 is the ordinary
                # case across ~100 Boards a run and would be pure noise; needing a second or third
                # pass is the replica instability being *survived*, and it is otherwise invisible —
                # this path is silent success, indistinguishable in the log from a Board that
                # never wobbled. That distinction is the whole input to "would raising
                # _MAX_SWEEPS help?", which docs/eightfold/ could only reason about, not measure:
                # a fleet mostly converging on sweep 3 is one bad run away from falling short,
                # while one mostly converging on sweep 2 has real headroom.
                if sweep:
                    _log.info(
                        f"{self.board_key()}: converged on sweep {sweep + 1} of {_MAX_SWEEPS} "
                        f"({pages} page(s), {len(seen)} of {total}) — the earlier sweep(s) missed "
                        f"{total - before} posting(s) a differently-ordered replica then dealt"
                    )
                break
            if pages >= _MAX_PAGES:
                # Unconditional: a ceiling is a hard cap, so the unread remainder is
                # unreachable on every run and no share of it is negligible — the class
                # ADR-0121 keeps outside the tolerance.
                self.mark_truncated(
                    _short_reason(
                        f"hit the {_MAX_PAGES}-page ceiling", len(seen), total
                    )
                )
                break
            if sweep and len(seen) == before:
                # Another full pass found nothing new — more sweeps won't either.
                self.mark_truncated_unless_negligible(
                    len(seen),
                    total,
                    _short_reason(
                        f"no new postings on sweep {sweep + 1}", len(seen), total
                    ),
                )
                break
        else:
            if len(seen) < total:
                self.mark_truncated_unless_negligible(
                    len(seen),
                    total,
                    _short_reason(
                        f"still short after {_MAX_SWEEPS} sweeps", len(seen), total
                    ),
                )
        return list(seen.values())

    # --- fallback 1: SmartApply --------------------------------------------------------------

    def _smartapply_url(self, group_id: str, start: int) -> str:
        q = urllib.parse.urlencode(
            {
                "domain": group_id,
                "query": "",
                "location": "",
                "start": start,
                "sort_by": "timestamp",
            }
        )
        return f"https://{self.slug}/api/apply/v2/jobs?{q}"

    def _smartapply_search(self, group_id: str) -> list[dict[str, Any]] | None:
        """Paginate ``/api/apply/v2/jobs`` (the "SmartApply" surface) for tenants whose PCSX
        search 403s "not enabled". None signals SmartApply itself is unavailable, so the caller
        falls through to the sitemap.

        Unlike :meth:`_api_search`, one straight pass — no re-sweep. Measured live 2026-09-11
        (``docs/eightfold/smartapply-fallback.md``): 8 same-offset probes 3-6s apart across 4
        tenants found 0 ids disagreeing (``_api_search``'s replica-disagreement bug doesn't
        reproduce here), and two tenants' full crawls matched ``count`` exactly with 0 duplicate
        ids — so the dedupe below is a cheap safety net, not a load-bearing fix.
        """
        first = self._get(self._smartapply_url(group_id, 0), marks_wall=False)
        if first.status_code != 200:
            self._fallback_reason = f"the SmartApply API returned {first.status_code}"
            return None
        try:
            data = first.json()
        except ValueError:
            self._fallback_reason = (
                "the SmartApply API answered 200 with an unparseable body"
            )
            return None
        total = int(data.get("count") or 0)
        seen: dict[str, dict[str, Any]] = {}
        for pos in data.get("positions") or []:
            seen.setdefault(str(pos.get("id")), pos)
        start = _PAGE
        pages = 1
        while len(seen) < total and start < total and pages < _MAX_PAGES:
            r = self._get(self._smartapply_url(group_id, start))
            if r.status_code != 200:
                self.mark_truncated_unless_negligible(
                    len(seen),
                    total,
                    _short_reason(
                        f"SmartApply HTTP {r.status_code} on page {pages + 1}",
                        len(seen),
                        total,
                    ),
                )
                return [_smartapply_to_pcsx_shape(p) for p in seen.values()]
            batch = r.json().get("positions") or []
            if not batch:
                break
            for pos in batch:
                seen.setdefault(str(pos.get("id")), pos)
            start += _PAGE
            pages += 1
        if len(seen) < total:
            reason = (
                f"hit the {_MAX_PAGES}-page ceiling"
                if pages >= _MAX_PAGES
                else "SmartApply's list ended short"
            )
            if pages >= _MAX_PAGES:
                self.mark_truncated(_short_reason(reason, len(seen), total))
            else:
                self.mark_truncated_unless_negligible(
                    len(seen), total, _short_reason(reason, len(seen), total)
                )
        return [_smartapply_to_pcsx_shape(p) for p in seen.values()]

    def _details_url(self, group_id: str, position_id: str) -> str:
        q = urllib.parse.urlencode(
            {"position_id": position_id, "domain": group_id, "hl": "en"}
        )
        return f"https://{self.slug}/api/pcsx/position_details?{q}"

    def _api_records(
        self, group_id: str, positions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Merge search metadata with a per-job description from position_details (fanned out).
        A failed detail only drops the description — the metadata already came from the search.

        Two populations are skipped entirely, because nothing downstream will ever read what the
        fetch would return. Both spend this provider's per-origin rate budget, which is the one
        it runs out of (ADR-0047/ADR-0063).

        **Jobs whose detail we already hold** (ADR-0048): the description was read once, at embed
        time, and is never read again.

        **Jobs the tech gate will discard** (ADR-0017): only `data/jobs/tech` is embedded,
        indexed and shown, and `update_descriptions` stores only what is in it — so a non-tech
        posting never enters the store, never appears on the skip-list above, and was re-fetched
        on every run forever. Measured across five runs of 2026-09-16, eightfold's non-tech count
        tracked its detail `attempted` to within 0.3% every run (34,806 vs 34,736 on the first).

        :func:`~headstart.tech_filter.is_tech` reads `title` + `department` and nothing else, and
        the PCSX search already carries both — probed live 2026-09-16 over 6 boards / 2,929
        positions: `name` 2,929/2,929, `department` 2,843/2,929 — so the detail body was never an
        input to that decision. The 2.9% with no `department` are classified on the title alone
        here *and* by `filter_tech`, which sees the same `None` from `parse`; `department` goes
        through the one expression both read (:func:`_department_of`) and `classify` strips the
        title itself, so the two verdicts cannot drift.

        Both skips are conditional on ``have_details``, the pipeline's signal, which is ``None``
        for every caller outside it (ADR-0048). The tech gate does not *need* that knowledge —
        no pipeline reader opens a non-tech description either way — but eight scripts construct
        scrapers directly and three would read the hole as a defect:
        ``scripts/validate/verify_scraper.py`` reports "jobs-with-description" as its health
        metric, ``scripts/eval/audit_remote.py`` live-scrapes a Board and triangulates `remote`
        against the description text, and ``scripts/enrich/salary_sample.py`` measures
        `salary.extract` recall off it. Skipping unconditionally would hand all three
        ``description=None`` on ~59% of this ATS's postings and have them report a quality
        collapse that is not real. Honouring the documented default is not an extra branch, it is
        respecting one — and it costs nothing: every sharded run ships the list (`scrape_run`
        reads it whenever ``--assignment`` is set; the five runs of 2026-09-16 logged
        `detail skip-list: 671,630 / 671,833 / 672,468 Job details already held`)."""
        descriptions = self.run_detail_pass(
            [_PcsxPosition(group_id, position) for position in positions],
            key_of=lambda pcsx_position: str(pcsx_position.position.get("id")),
            what="descriptions",
            title_of=lambda pcsx_position: pcsx_position.position.get("name"),
            department_of=lambda pcsx_position: _department_of(pcsx_position.position),
            skip_held=True,
        )
        # Only the held-detail half: the tech half has its own line, from the gate
        # (`tech_detail_wanted`), and saying it in both double-counted every Board for anything
        # grepping these. Read back off the pass's own counters, since both skips happen inside
        # it: what it requested, against what the gate let through.
        requested = self.telemetry["detail_jobs"]
        tech = len(positions) - self.telemetry.get("tech_gated_details", 0)
        if requested < tech:
            _log.info(
                f"{self.board_key()}: fetched {requested}/{tech} descriptions "
                f"({tech - requested} already held)"
            )
        records = []
        for position in positions:
            position_id = str(position.get("id"))
            records.append(
                {
                    "id": position_id,
                    "url": self.job_url(position_id, position.get("positionUrl")),
                    "fields": {
                        "title": position.get("name"),
                        "description": descriptions.get(position_id) or None,
                        "location": _first_location(
                            position.get("locations"),
                            position.get("standardizedLocations"),
                        ),
                        "posted_at": _ts_to_iso(position.get("postedTs")),
                        "employment_type": None,  # not exposed by the PCSX API
                        "department": _department_of(position),
                        "remote": _remote_from(position.get("workLocationOption")),
                    },
                }
            )
        return records

    def detail_request(self, item: _PcsxPosition | str) -> DetailRequest:
        """A PCSX position's ``position_details``, or, on the sitemap fallback, whose items are
        job-page URLs, the page itself — each with the headers :meth:`_get` sends."""
        if isinstance(item, str):
            return DetailRequest(item, headers=self._headers("text/html"))
        return DetailRequest(
            self._details_url(item.group_id, str(item.position.get("id"))),
            headers=self._headers("application/json"),
        )

    def read_detail(
        self, item: _PcsxPosition | str, response: Any
    ) -> str | dict[str, Any]:
        """A position's description text, or a job page's JobPosting fields.

        This ATS's edge answers a spent per-origin budget with 403/405/429 (ADR-0063) and its API
        can answer 200 with a body that will not parse; the label on each loss is what tells the
        two apart. A ``position_details`` answer with no description is ``""``, not a loss — see
        :func:`_description_of` on why an empty description means only that the request
        completed. On the sitemap fallback the page *is* the Job, and that fallback is taken
        exactly when the API is refusing us — the run where "was it refused or was it
        unreadable?" is the whole question.
        """
        if isinstance(item, str):
            fields = _jobposting(response.text)
            if fields is None:
                raise DetailLost("no JobPosting JSON-LD on a 200")
            return fields
        description = _description_of(response)
        if description is None:
            raise DetailLost("unparseable body on a 200")
        return description

    # --- fallback 2: sitemap -> per-job JSON-LD -----------------------------------------------

    def _sitemap_records(self) -> list[dict[str, Any]]:
        listed = self._job_urls()
        # Keyed by the page's own URL, not its position id: `_job_urls` dedupes URLs, and two
        # URLs naming one position each keep the page they were read from.
        pages = self.run_detail_pass(
            listed, key_of=lambda job_url: job_url, what="detail fields"
        )
        lost = pages.missing
        if lost:
            # On this surface the per-job page *is* the Job — `parse` drops the ones that did
            # not arrive — so the list is knowingly short and must say so or `index sync` reads
            # the gap as a delisting (ADR-0053). It matters most here: this is the fallback
            # taken whenever the API 403s, i.e. exactly when fetches are most likely to fail.
            # The sitemap gives a real total to measure against, so a negligible loss is left to
            # ADR-0083 rather than costing the Board its whole eviction scope (ADR-0121).
            self.mark_truncated_unless_negligible(
                len(listed) - lost,
                len(listed),
                f"{lost}/{len(listed)} job pages unreadable — those Jobs are listed but unbuilt",
            )
        return [
            {
                "id": _sitemap_position_id(job_url),
                "url": job_url,
                "fields": pages.get(job_url),
            }
            for job_url in listed
        ]

    def _job_urls(self) -> list[str]:
        r = self._get(accept="application/xml")
        # The sitemap is the LAST surface — reaching it means the careers page or the API
        # already failed — so a non-200 here means the board went unread, and returning []
        # would present a dead board as alive-and-empty (invisible to ADR-0058's quarantine).
        r.raise_for_status()
        jobs = _dedupe(_JOB_LOC.findall(r.text))
        if jobs:
            return jobs
        children = [
            c
            for c in _dedupe(_CHILD_SITEMAP.findall(r.text))
            if "index" not in c.lower()
        ]
        if len(children) > _MAX_INDEX_CHILDREN:
            # Unconditional, and load-bearing since ADR-0121. The cap silently shortens `listed`,
            # which is the very denominator the detail pass measures its shortfall against — so
            # without this line a Board missing a third of its sitemap could still read "100% of
            # what we listed" and be declared authoritative. That is the one route by which a
            # hard cap could reach the tolerance, and the tolerance's whole contract is that none
            # can. Before the tolerance a stray unreadable detail page usually excluded such a
            # Board anyway; that accident is gone, so the cap has to speak for itself.
            self.mark_truncated(
                f"followed {_MAX_INDEX_CHILDREN} of {len(children)} child sitemaps — "
                "the rest of the index was not listed"
            )
        found: list[str] = []
        for child in children[:_MAX_INDEX_CHILDREN]:
            cr = self._get(child, accept="application/xml")
            if cr.status_code == 200:
                found.extend(_JOB_LOC.findall(cr.text))
            else:
                # One child of a live index failing is a partial read, not a dead board —
                # report it so sync excludes the Board from eviction instead of reading the
                # unread child's postings as delistings (ADR-0053).
                self.mark_truncated(
                    f"HTTP {cr.status_code} on child sitemap {child} — "
                    "its postings were not listed"
                )
        return _dedupe(found)

    # --- shared parse -------------------------------------------------------------------------

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        jobs: list[Job] = []
        for item in raw:
            fields = item.get("fields") or {}
            title = (fields.get("title") or "").strip()
            position_id = item.get("id")
            if not title or not position_id:
                continue  # unreadable / no id — nothing to key the job by
            location = fields.get("location")
            remote = fields.get("remote")
            if remote is None:
                remote = is_remote(location)
            jobs.append(
                Job(
                    id=self.job_id(position_id),
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=remote,
                    department=fields.get("department"),
                    url=item["url"],
                    posted_at=fields.get("posted_at"),
                    scraped_at=scraped_at,
                    description=html_to_text(fields.get("description")),
                    employment_type=fields.get("employment_type"),
                )
            )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        # Not yet measured: no structured compensation field has been looked for across any of
        # the three listing/detail surfaces this scraper reads. Needs its own measurement pass
        # before this can claim more.
        return None


# --- public helpers for callers outside a scrape (e.g. alias detection, #154) ------------------
# Thin wrappers over the scraper's own machinery, so a second caller reuses the real sitemap-index
# child-following logic (``_job_urls``) rather than a parallel, less complete reimplementation.


def group_id_for(slug: str) -> str | None:
    """The board's ``_EF_GROUP_ID`` — the tenant identity the PCSX API keys on, independent of
    which vanity hostname is asking. Two live hostnames sharing one group_id are the same board
    (#154)."""
    return EightfoldScraper(slug)._group_id()


def sitemap_ids_for(slug: str) -> set[str]:
    """Every job id in the board's sitemap, following index children exactly as a real scrape
    would (:meth:`EightfoldScraper._job_urls`) — not a bare top-level fetch, which is only
    ever right when a tenant has no index indirection."""
    scraper = EightfoldScraper(slug)
    return {
        pid
        for url in scraper._job_urls()
        if (pid := _sitemap_position_id(url)) is not None
    }


def _pcsx_disabled(response: Any) -> bool:
    """Whether a 403's body is PCSX explicitly refusing this tenant (any locale — see
    :data:`_PCSX_DISABLED`), the signal to try SmartApply, vs. a generic WAF 403 that should fall
    straight through to the sitemap as before."""
    try:
        message = response.json().get("message")
    except ValueError:
        return False
    return bool(message) and bool(_PCSX_DISABLED.search(str(message)))


def _smartapply_to_pcsx_shape(pos: dict[str, Any]) -> dict[str, Any]:
    """One SmartApply position (``/api/apply/v2/jobs``), normalized onto the PCSX search shape
    ``_api_records`` already reads — verified live 2026-09-11, ``docs/eightfold/
    smartapply-fallback.md``. ``name``/``locations``/``department`` are the same key names on both
    surfaces. ``work_location_option`` -> ``workLocationOption``: same value vocabulary (onsite/
    hybrid/remote_local/remote_global, all already in ``_REMOTE_OPTION``). ``t_create`` ->
    ``postedTs``: SmartApply carries no ``postedTs`` of its own, and ``t_create`` (when the
    posting was created) is the closer match than ``t_update`` (which moves on every edit).
    ``standardizedLocations`` is simply absent — ``_first_location``'s dirty-location repair tier
    is skipped, not broken, without it. ``positionUrl`` is deliberately left out too: SmartApply's
    own ``canonicalPositionUrl`` sometimes points at a *different* vanity host than ``self.slug``
    (e.g. bayer.eightfold.ai's is ``talent.bayer.com``), while the existing ``/careers/job/{id}``
    fallback in ``_api_records`` was confirmed live to resolve on every one of the tenants this was
    checked against — so that fallback is left to build the URL, not overridden. ``department``
    came back list-shaped on one measured tenant (fluor) instead of the usual string; joined here
    so ``_api_records``'s ``.strip()`` doesn't raise.
    """
    department = pos.get("department")
    if isinstance(department, list):
        department = ", ".join(str(d) for d in department if d)
    return {
        "id": pos.get("id"),
        "name": pos.get("name"),
        "locations": pos.get("locations"),
        "department": department,
        "postedTs": pos.get("t_create"),
        "workLocationOption": pos.get("work_location_option"),
    }


def _department_of(pos: dict[str, Any]) -> str | None:
    """One search position's ``department``, in the shape ``parse`` puts on the Job.

    One expression because two readers must agree on it: the tech gate in ``_api_records``
    decides whether to fetch a detail at all, and ``filter_tech`` re-runs that same gate later on
    the field emitted here. A drift between them would either fetch details for postings that are
    then dropped, or — the expensive direction — skip a posting the filter goes on to keep.
    ``title`` needs no such helper: ``classify`` strips it exactly as ``parse`` does.
    """
    return (pos.get("department") or "").strip() or None


def _short_reason(cause: str, got: int, total: int) -> str:
    """Why the crawl stopped, with exactly how short it left the list (ADR-0053). ``data.count``
    gives the board total, so every way ``_api_search`` can give up reports the same measured
    shortfall rather than each phrasing it its own way."""
    return f"{cause} — got {got} of {total} postings"


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


# Internal site-code shape some tenants ship instead of a place name, e.g. "US-CA-Fremont
# (1003)" or "TW-Hsinchu-01 (3103)" — a 2-letter prefix, a dash, and a trailing parenthesized
# numeric site id.
_SITE_CODE = re.compile(r"^[A-Za-z]{2}-\S.*\(\d+\)\s*$")
# "Riyadh, , Saudi Arabia" — a comma-joined value with a blank segment.
_EMPTY_SEGMENT = re.compile(r",\s*,")
_BARE_COUNTRY_CODE = re.compile(r"^[A-Za-z]{2}$")
_SITE_CODE_PREFIX = re.compile(r"^([A-Za-z]{2})-")


def _dirty_location(value: str) -> bool:
    """Values worth repairing from `standardizedLocations` — measured live 2026-08-25 on
    21.36% of the dirtiest boards' jobs, 99.5% of which come back clean from that field."""
    return bool(_SITE_CODE.match(value) or _EMPTY_SEGMENT.search(value))


def _repair_location(dirty_value: str, standardized_entry: Any) -> str | None:
    """The `standardizedLocations` entry paired with a dirty `locations` entry, repair-tier
    style — None when it isn't actually an improvement. Measured live 2026-08-25 (10,697 jobs,
    12 boards), three regressions a blanket swap would cause, none of which this repair commits:
    - **bare 2-letter country code** (3.4% of the code-shaped class): 'Singapore, Singapore' /
      'SG-Singapore (3301)' both collapse to 'SG' — a real loss of the only place name for a
      city-state (the same failure successfactors hit).
    - **still site-code-shaped** (3.3%): some tenants' own standardizedLocations never resolves
      the code — lamresearch's 'KR-Yongin-02 (3821)' comes back merely lowercased, not repaired.
    - **wrong country** (6.7%, all on one lamresearch site code): every 'MY-LMM KM [3620]
      (3832)' posting's standardizedLocations is 'Lancaster, VIC, AU' — Malaysia mapped to
      Australia, a bad tenant-side mapping, not a real repair.
    """
    if not isinstance(standardized_entry, str):
        return None
    candidate = standardized_entry.strip()
    if (
        not candidate
        or _BARE_COUNTRY_CODE.match(candidate)
        or _SITE_CODE.match(candidate)
    ):
        return None
    prefix_match = _SITE_CODE_PREFIX.match(dirty_value)
    if prefix_match:
        prefix = prefix_match.group(1).upper()
        suffix = candidate.rsplit(",", 1)[-1].strip().upper()
        if len(suffix) <= 3 and suffix != prefix:
            return None
    return candidate


def _first_location(locations: Any, standardized: Any = None) -> str | None:
    """The first non-empty place `locations` names (not always index 0 — some tenants ship a
    blank first entry with real ones after it, e.g. ascendion), repaired from the matching
    `standardizedLocations` entry when it's dirty (see `_dirty_location`/`_repair_location`).
    This is a repair tier, not a wholesale swap of `locations` for `standardizedLocations` — a
    clean `locations` entry is left exactly as it is."""
    if isinstance(locations, list):
        places = locations
    elif isinstance(locations, str):
        places = [locations]
    else:
        places = []
    std_places = standardized if isinstance(standardized, list) else []
    for i, raw in enumerate(places):
        value = str(raw).strip() if raw is not None else ""
        if not value:
            continue
        if _dirty_location(value):
            std_entry = std_places[i] if i < len(std_places) else None
            repaired = _repair_location(value, std_entry)
            if repaired is not None:
                return repaired
        return value
    return None


def _ts_to_iso(ts: Any) -> str | None:
    """PCSX ``postedTs`` (unix seconds, as int or str) -> ISO date. None if absent/garbled."""
    try:
        seconds = int(ts)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC).date().isoformat()


def _remote_from(option: Any) -> bool | None:
    if not isinstance(option, str):
        return None
    return _REMOTE_OPTION.get(option.strip().lower())


def _description_of(response: Any) -> str | None:
    """The posting's description; ``""`` when the payload carries no ``jobDescription``, and
    ``None`` only when the body could not be read at all.

    The distinction is no longer load-bearing (ADR-0089) — ``_api_records`` maps both to ``None``
    via ``desc or None`` — and it must not become so again. It looks like *this posting has no
    description* versus *we failed to find out*, but it is not: ``position_details`` answers 200
    with no ``jobDescription`` for postings whose pages carry full text (measured 5 of 5 on
    ``telekom-growthhub``), so the empty string means only that the request completed. That is
    what made the removed ``detail_fetched`` flag write a permanent falsehood into the store.
    """
    try:
        data = response.json().get("data") or {}
    except ValueError:
        return None
    return data.get("jobDescription") or ""


def _sitemap_position_id(url: str) -> str | None:
    m = _POSITION_ID.search(url)
    return m.group(1) if m else None


def _jobposting(page: str) -> dict[str, Any] | None:
    """The JobPosting fields from a job page's JSON-LD (sitemap fallback), or None without one.
    The region often already carries the country ("Hsinchu City,TW"), so a country it already
    holds is dropped from the location."""
    node = find_job_posting(page)
    if node is None:
        return None
    return {
        **job_posting_fields(node),
        "location": job_location_text(node.get("jobLocation"), drop_repeats=True),
        "department": None,  # not in the JSON-LD
    }
