"""Base scraper: shared fetching plus the parse contract each ATS implements."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import urllib.parse
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Awaitable, Callable, Container, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import cached_property
from types import MappingProxyType
from typing import Any, TypeVar

from headstart import log
from headstart.boards import company_name
from headstart.jobs.job import Job
from headstart.jobs.tech_filter import is_tech
from headstart.network import fanout_stats, http
from headstart.network.fetcher import BoardFetcher, Fetcher

#: The one User-Agent every scraper sends. Public because nine of them re-declared
#: this same literal locally, which is a set of strings that can silently disagree.
#:
#: This string is load-bearing, not cosmetic, and it is bare on purpose — two hosts have been
#: measured rejecting a *specific* shape of it, and the intersection of what they accept is
#: narrow. The policy is ADR-0115; the measurements behind it are
#: `docs/successfactors/2026-09-07_user-agent-denylist.md`.
#:
#: **SuccessFactors** denylists the previous value, ``headstart/0.1 (job-board reader)``, as an
#: **exact literal**. On `careers.te.com`, 2026-09-07: that string returns 403 (a 111-byte
#: ``{"error":{"status-code":"403","message":"Policy ID: ..."}}``) while
#: ``headstart/0.1 (job-board)``, ``headstart/0.1 (reader)``, ``curl/8.7.1`` and even
#: ``python-requests/2.32.3`` all return 200 on the same URL — a denylist entry, not a heuristic.
#: It cost 102 Boards their whole detail pass: 0 jobs each across five consecutive runs, 56,120
#: postings listed and none ingested, and the log could not say so because its detail reader
#: mapped a 403 and an unparseable 200 onto the same ``None``.
#:
#: **zwayam** rejects any User-Agent carrying a domain or an email — ``(+https://github.com/…)``,
#: ``(+github.com/…)``, ``(github.com/…)`` and an ``@``-address all fail with ``curl (92) HTTP/2
#: stream error``, 2 of 2 attempts each — so a contact URL cannot live here either.
#:
#: And it must stay **non-stock**: zwayam blackholes ``curl``'s and ``python-requests``'s own
#: defaults, which **time out** rather than answering, so a caller treating a timeout as transient
#: retries forever. ``headstart/0.1`` is the value measured clear of all three constraints.
USER_AGENT = "headstart/0.1"
_T = TypeVar("_T")
_R = TypeVar("_R")

#: The share of a Board's own stated total that must be read for its list to stay *authoritative*
#: (ADR-0053) — the one tolerance, so the gate cannot mean different things on different ATSes
#: (ADR-0121).
#:
#: One *policy*, but not yet every call site: `jobvite` and `zwayam` still report a measured
#: shortfall through the unconditional :meth:`BaseScraper.mark_truncated`. That is a measured scope
#: boundary, not an oversight — neither has had an excluded Board, so the gate would never fire for
#: them. Convert them when a Board of theirs is actually observed coming back marginally short, as
#: `icims` and `smartrecruiters` were (ADR-0121's 2026-09-25 amendment).
#:
#: ADR-0053 shipped before ADR-0083 and has no tolerance: a Board one page short of complete
#: leaves the eviction scope entirely, and that exclusion has **no drain**, so a Board short on
#: every run serves its closed postings forever. A 1-in-2,130 shortfall
#: (`successfactors:careers.te.com`, 99.953% read) is precisely what ADR-0083's per-Job grace
#: period exists to absorb: the missing id is withheld for one scrape and evicted only if the
#: *next* scrape of that Board misses it too. Excluding the whole Board to protect that one id is
#: the blunt instrument, and it is the blunt instrument that never releases.
#:
#: 0.99, not looser, on measurement rather than taste. Measured on run `34327339789`'s own
#: per-Board breakdown (3,055 shielded rows across the 44 Boards holding any), this releases
#: **1,204 rows — 39.4%**. Loosening to 0.95 releases 1,259, only **55 rows more (+1.8pp)** for
#: five times the tolerated loss: the 95–99% band is nearly empty, so there is almost nothing to
#: buy down there. Tightening to 0.995 costs 128 rows. 0.99 is the knee.
#:
#: It deliberately does **not** reach `successfactors:careers.hcltech.com`. On run `34327339789`
#: it read 91.20% (995 unreadable of 11,265) and was, alone, 1,533 rows — 50.2% of that run's
#: shielded set, and 1,533 of the 1,590 rows that separate this threshold from a 0.90 one. (Both
#: figures are that one run's: the Board reads 90.957% on the next run, and quoting one run's
#: ratio beside another's row count is a conflation this comment made once already.) Reaching it
#: is not a bargain: declaring a Board authoritative while one Job in eleven is missing feeds
#: those ids to eviction. That Board is a detail-fetch defect — 971 -> 985 -> 995 -> 1,022
#: unreadable pages across four consecutive runs against a flat ~11.3k board — not a
#: gate-calibration one, and it is out of scope here.
#:
#: Not a class attribute, so a scraper cannot quietly hold itself to a different bar — the gate
#: must not mean different things on different ATSes.
MIN_AUTHORITATIVE_SHARE = 0.99

#: The thread-pool width :meth:`BaseScraper.fan_out` uses unless told otherwise — for a listing
#: fan-out or a Detail pass alike — and the Detail pass's thread path uses when a Scraper declares
#: no :attr:`~BaseScraper.detail_workers` of its own.
_DEFAULT_FAN_OUT_WORKERS = 8

# Default HTTP/2 multiplexing width (concurrent streams per host) for fan_out_async — 100 is around
# the common server MAX_CONCURRENT_STREAMS. Override per-call, via HEADSTART_H2_STREAMS, or
# run_scrapers --streams N. Read at call time (below) so a CLI flag can set the env before the scrape.
_DEFAULT_H2_STREAMS = 100

#: A Detail pass that lands nothing for this long skips the rest of its items (ADR-0209). Run
#: 36003741124: `oracle:egud` finished its listing, then spent 56 min in its detail pass until the
#: shard's 60 min budget killed it, and the run took 91 min instead of ~47. A slow pass that still
#: lands details is left alone (`oracle:ejwl` legitimately takes ~26 min); only one that has
#: stopped landing any is cut. Above a single item's worst legitimate budget (~430 s: 5 attempts at
#: a 30 s timeout, capped 30 s waits, two egress rotations), so a stall this long is not one slow
#: request.
_DETAIL_STALL_S = 600.0
#: The most one multiplexed detail may take (ADR-0209). Every request carries a timeout, but a
#: stream can outlast it; without a bound one stuck item holds the pass, and so the shard, open.
#: Above the ~430 s worst legitimate budget of one walled item (5 timeouts, 4 capped waits, two
#: rotations).
_DETAIL_ITEM_TIMEOUT_S = 900.0
#: The loss labels the two bounds write, so the gap line names them.
DETAIL_STALLED = "skipped after the detail pass stalled"
DETAIL_TIMED_OUT = "timed out in the detail pass"
DETAIL_WALLED = "skipped after the origin walled the detail pass"
#: The clock the stall window reads; a module attribute so a test can drive it.
_detail_clock = time.monotonic

#: What a fan-out item or a detail read raising is *expected* to look like: a refused or failed
#: request (curl's errors and timeouts are ``OSError``\s) or a body that is not JSON. Anything
#: else reaching a catch-all is a bug in scraper code, reported through :data:`_UNEXPECTED`.
_ROUTINE_FAILURES = (OSError, json.JSONDecodeError)
#: The catch-alls below swallow a parse bug into a default or an ``unlabelled``/``KeyError xN``
#: count, which names neither file nor line. The first one per shard process warns with its
#: traceback; the rest inform. Module-level because every Board builds its own scraper.
_UNEXPECTED = log.FirstOnly(log.get(__name__))
#: Where a fan-out's tally of its unexpected items is said (the static :meth:`fan_out` has no
#: Board logger).
_log = log.get(__name__)


def classify_exception(exc: Exception) -> str:
    """A groupable label for one failed request — the status where the origin gave one, else
    the exception type. Deliberately coarse: a message carries per-request detail (offsets,
    hosts) that would never group, and what a loss tally needs is the *shape* of a failure, not
    one distinct string per request.

    Shared for the same reason `loss_breakdown` is: workday kept its own copy of this exact
    computation (`_failure_class`) purely because it needed a bare label to feed its own richer
    `classes: Counter[str]` rather than `note_detail_exception`'s side effect of recording
    straight into `self.detail_losses`. Two copies of one computation is the near-synonym
    failure CLAUDE.md §3 names, and this module already paid for that once — `loss_breakdown`
    itself was unified from two independently-drifted formatters (ADR-0088). Pulling this one
    level lower, to a bare `Exception -> str` function neither side owns, lets both keep their
    own recording behaviour without re-deriving the same classification.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return f"HTTP {status}" if status else type(exc).__name__


def loss_breakdown(losses: Counter[str], missing: int) -> str:
    """`` (HTTP 403 x2114, no JSON-LD on a 200 x13)`` — one detail pass's losses, tallied.

    Public because it is shared: workday keeps its own richer loss `Counter` (ADR-0088) and
    formats it through here rather than beside it. The two were written separately and had
    already drifted inside one commit — one called the residual ``unlabelled`` and the other
    ``unclassified``, one stated the tail's size and the other printed a bare ``…`` — which is
    two spellings of one fact, the near-synonym failure CLAUDE.md §3 names.

    Every named cause is shown, largest first. A cap used to keep only the top 4 behind a sized
    ``…N more cause(s)`` tail, but it never bound in practice: 4 runs / ~2M detail attempts /
    747 loss lines measured only 9 distinct causes fleet-wide and ~1.2 per Board per run
    (``docs/workday/2026-09-09_parser-shaped-detail-losses.md``). The cause vocabulary is
    closed — status codes plus a handful of exception/parse-shape labels — so there is no
    board whose line would grow unreadable; the cap was complexity with nothing to show for it.

    Whatever reached no label is counted into ``unlabelled`` rather than dropped, so a partial
    tally cannot read as a full account of ``missing`` — this is not a coverage gap to close by
    naming more causes. Both callers classify deliberately narrowly (workday's
    ``_failure_class``, base's ``note_detail_loss``) and lean on the outer
    ``fan_out``/``fan_out_async`` catch-all as backstop for whatever they didn't anticipate — a
    malformed body an existing branch doesn't parse, a race in a non-atomic Counter update,
    anything new. ``unlabelled`` is that backstop's readout, and it stays near zero (measured
    ~1-in-40,000) precisely because the classification above it is doing its job; a spike in it
    is the signal to add a new named cause, not evidence this function should hide the gap.
    ``test_workday_detail_classes_always_account_for_every_loss`` pins the invariant directly:
    an uncaught path must surface as ``unlabelled``, never vanish.

    Accounts for the whole of ``missing`` even from an empty ``losses``. Whether a pass that
    labelled *nothing* deserves a breakdown at all is the caller's question, not this one's, and
    the two callers answer it differently — so it is asked at each call site instead.
    """
    tally = Counter(losses)
    unlabelled = missing - sum(tally.values())
    if unlabelled > 0:
        tally["unlabelled"] = unlabelled
    if not tally:
        return ""
    why = ", ".join(f"{cause} x{n}" for cause, n in tally.most_common())
    return f" ({why})"


#: The headers :meth:`BaseScraper._get` sends, and so the default for a :class:`DetailRequest` —
#: a Detail pass that used to ride ``_get``/``_get_async`` states nothing and sends exactly this.
DEFAULT_REQUEST_HEADERS: Mapping[str, str] = MappingProxyType(
    {"User-Agent": USER_AGENT, "Accept": "application/json, text/html"}
)


def _report_unexpected_tally(what: str, unexpected: Counter[str]) -> None:
    """One line for a fan-out's unexpected items past the first, which alone was reported."""
    if sum(unexpected.values()) > 1:
        _log.info(
            f"{what}: {sum(unexpected.values())} unexpected exception(s) in fan-out items "
            f"({', '.join(f'{name} x{n}' for name, n in unexpected.most_common())})"
        )


def _head_of(response: Any, size: int) -> str | None:
    """The first ``size`` bytes (or a little more — whole chunks) of a streamed 200, decoded as
    UTF-8, or None for any other status; the rest is never downloaded, and the stream is closed
    either way."""
    body = b""
    try:
        if response.status_code != 200:
            return None
        for chunk in response.iter_content():
            body += chunk
            if len(body) >= size:
                break
    finally:
        response.close()
    return body.decode("utf-8", "replace")


@dataclass(frozen=True)
class DetailRequest:
    """One Job's Detail pass request, stated as data (ADR-0201).

    A Scraper returns this from :meth:`BaseScraper.detail_request` instead of sending the request
    itself, so :meth:`BaseScraper.run_detail_pass` can send it over whichever transport is in
    force — the thread pool or the multiplexed session — from this one description. Before this
    existed every Scraper wrote each detail request twice, once per transport, and the copies could
    drift apart unseen — eightfold's sent its Referer on one path only until it moved onto this.

    ``options`` carries any further keyword for the fetch seam unchanged — ``json=``, ``data=``,
    ``allow_redirects=``, ``retry_on=``, ``marks_wall=``.
    """

    url: str
    method: str = "GET"
    headers: Mapping[str, str] = DEFAULT_REQUEST_HEADERS
    timeout: float = 30
    options: Mapping[str, Any] = field(default_factory=dict)


class BoardUnreadable(ValueError):
    """A Board answered, but not with anything its scraper can read — an outcome the scraper has
    named (Workday's non-JSON listing, a Taleo shell with no ``portalNo``), not a parse bug.

    ``harvest.scrape_all`` records it in the Board's error map like any failure but, like a
    transport error, without the traceback and annotation it keeps for the unclassified.
    A ``ValueError`` so the callers that already catch one still do.
    """


class DetailLost(Exception):
    """One Job's detail is lost, and ``cause`` names what lost it — the label
    :meth:`BaseScraper.report_detail_gaps` prints (ADR-0088's discipline).

    Where it is raised decides how it is counted. From :meth:`BaseScraper.detail_request` it means
    no request could be formed (a listing row with no native id) and is counted *unattempted*, so
    ``detail_attempted`` stays a count of requests actually made. From
    :meth:`BaseScraper.read_detail` it means a response arrived but carries no detail (``"no
    JSON-LD on a 200"``, ``"no posting"``). Transport failures and non-200 statuses are labelled by
    :meth:`BaseScraper.run_detail_pass` itself and never need raising.
    """

    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause = cause


class DetailBatchWalled(Exception):
    """A batch transport's origin refused it and no route is left (ADR-0228).

    Raised from :meth:`BaseScraper.fetch_detail_batch`. :meth:`BaseScraper.run_detail_pass` stops
    the pass on it and counts every detail not yet fetched as skipped, keeping what already landed:
    pressing on against a wall that blocks the whole origin would cost the listing too.
    """


def gone_board_error(detail: str) -> http.RequestsError:
    """A Board failure in the shape `board_failures.is_gone` matches, so the Board earns an
    ADR-0162 gone-strike. ``HTTP Error 410`` is that ledger's marker for "this Board no longer
    exists", not a claim about the status the host answered — ``detail`` says what it did."""
    return http.RequestsError(f"HTTP Error 410 (gone): {detail}")


@dataclass(frozen=True)
class DetailWithoutDescription:
    """What :meth:`BaseScraper.read_detail` returns for a detail that arrived without its
    description but whose other fields are real — kept for them, counted as a gap for it.

    The Detail pass exists for the description (CONTEXT.md §Detail pass), so a page that parsed
    but carries none is a loss on the gap line, labelled ``cause``; yet raising
    :class:`DetailLost` would also drop the other fields ``parse`` reads off the same page (a
    Taleo BE layout states a location and department on 128 of 128 pages with no body, measured
    2026-09-24). :meth:`BaseScraper.run_detail_pass` keeps ``fields`` in its mapping.
    """

    fields: Any
    cause: str


def _unwrapped(outcome: Any) -> Any:
    return outcome.fields if isinstance(outcome, DetailWithoutDescription) else outcome


class FetchedDetails(dict[str, Any]):
    """What :meth:`BaseScraper.run_detail_pass` returns: each detail that arrived, keyed by the
    Job's native id, plus how many of the requested ones are gaps (:attr:`missing`) — the count a
    load-bearing pass marks its Board truncated on (ADR-0053). A detail that arrived without its
    description (:class:`DetailWithoutDescription`) is both: kept for its other fields, and
    counted in :attr:`missing`.

    A Job the tech gate or the held-description skip left out is simply absent: never requested,
    so neither present nor missing.
    """

    def __init__(self, details: dict[str, Any], missing: int) -> None:
        super().__init__(details)
        self.missing = missing


class BaseScraper(ABC):
    """Fetch one company's postings from an ATS and normalize them to Jobs.

    Network and parsing are split on purpose: ``parse`` is pure and is what the
    tests exercise against recorded fixtures, while ``_get`` is the only part that
    touches the network. JSON boards use the default ``fetch_raw`` (decode + parse);
    HTML boards (Zoho) override ``fetch_raw`` to keep the raw text.
    """

    ats: str  # set by each subclass

    #: Regex the URL :meth:`job_url` produces must always match for this ATS — the single
    #: declared shape of a job-detail link, so ``scripts/eval/verify_filters.py``'s
    #: coverage-gated ``URL_SHAPES`` is *generated from* this rather than a second,
    #: hand-maintained copy that can silently disagree with what the scraper actually emits
    #: (ADR-0157). No shared default: every concrete scraper states its own, the same
    #: no-default-here contract :attr:`ats` already uses.
    url_shape: str

    #: This scraper's politeness bound for its detail pass, as **thread-pool workers** — what
    #: :meth:`fan_out` is called with. Declared on the class rather than kept as a module constant
    #: so :meth:`fan_out_async` can fall back to it: a scraper that bounds its sync path to 6
    #: because "they hit one host" means that about the host, not about the thread pool, and the
    #: async path had been silently taking :data:`_DEFAULT_H2_STREAMS` (100) instead. That
    #: divergence is what ADR-0047 found for Eightfold and this cost Workday too.
    detail_workers: int | None = None

    #: Whether this scraper's detail pass may use the multiplexed async path at all (ADR-0167).
    #: True for everything unless an origin has been *measured* to meter per **connection** rather
    #: than per stream — where it does, one shared ``AsyncSession`` is a single connection and
    #: widening :attr:`detail_streams` buys nothing, so the sync thread path (one connection per
    #: worker, at :attr:`detail_workers`) is the faster transport. Apple is the measured case.
    #: Read by :meth:`async_fanout_enabled`; a scraper declares it rather than overriding that.
    async_fanout: bool = True

    #: Optional async-only override of :attr:`detail_workers`, as HTTP/2 **streams**. Set it only
    #: where a wider multiplexing width has been *measured* to be safe (Eightfold's 25, ADR-0047);
    #: leaving it None keeps the async path as polite as the sync one.
    detail_streams: int | None = None

    #: Whether this scraper makes a per-Job **detail pass** — a second fetch after the listing,
    #: usually for ``description``. False means every field a Job carries came from the listing
    #: response, so its description can never go missing; True means it can (ADR-0050). Read by
    #: the embed planner to decide whether a pre-ADR-0050 vector might have been built without
    #: one. Set it when you add a detail pass, or that ATS's degraded vectors go unrepaired.
    has_detail_pass: bool = False

    #: How many details :meth:`run_detail_pass` sends per :meth:`fetch_detail_batch` call, for a
    #: Scraper whose origin admits one warmed browser tab and no other client (ADR-0228). None —
    #: every Scraper but Tesla — keeps the per-item HTTP transports. A batch is fetched one at a
    #: time, in order, so the bound is the origin's politeness bound, not a width to tune up.
    detail_batch_size: int | None = None

    #: HTTP statuses at which this ATS should stop being requested over the shard's own egress IP
    #: and move to a spare one (see :mod:`headstart.network.spare_egress`). Empty — every scraper unless it
    #: says otherwise — keeps the direct route no matter what comes back, which is the behaviour
    #: every ATS had before this existed.
    #:
    #: Set it only for an ATS measured to meter **per origin**, where a wall is about the shard's
    #: IP rather than about the request: Eightfold's edge answers 403/405 once a shard's budget is
    #: spent, and the same Boards serve 200 from a different IP moments later (ADR-0063). A status
    #: that means "this request is wrong" — a 401, a 404 — must never appear here; rotating egress
    #: would not fix it and would spend a second budget learning that.
    #:
    #: **Trace the status to its actual origin before reaching for this.** An aggregate count of
    #: one status across runs is a symptom, not a mechanism, and two ATSes have now been opted in
    #: on exactly that and reverted: freshteam's 429s were 502s from a down origin (#311), and
    #: personio's were a departed tenant's redirect to a bot-walled marketing site (#312, reverted
    #: by #313 — ADR-0063's 2026-08-26 amendment). Note also that the shard report's `recovered`
    #: rate cannot adjudicate this: it buckets every request the spare egress carries once the
    #: group is walled, so it sits ~95% whether or not the fallback bought anything. Only a
    #: per-Board outcome can.
    #:
    #: Every request made through :attr:`board_fetcher` carries the opt-in — :meth:`_get`,
    #: :meth:`_fetch` and their async twins all go through it (ADR-0204). Workday's listing POST
    #: drops it on purpose, with ``direct=True``, for its one direct-egress retry.
    egress_fallback_on: frozenset[int] = frozenset()

    #: Hosts this ATS parks a decommissioned tenant on — its own marketing pages. A Board whose
    #: :meth:`alias_key` lands on one of these has not moved and is not a duplicate: the tenant is
    #: gone (ADR-0111). Reporting-only, but the label is the difference between "go find where this
    #: board went" and "mark it dead", so the two are not collapsed. Measured for SuccessFactors
    #: 2026-09-06: `careers.toagroup.com` and `jobs.bhs-world.com` both land on `www.sap.com`.
    alias_vendor_hosts: frozenset[str] = frozenset()

    #: Job ids whose per-job detail fetch can be skipped because we already hold it (ADR-0048;
    #: re-keyed onto the description store by ADR-0050). ``None`` means fetch every detail. The
    #: pipeline's scrape stage sets this via :func:`~headstart.scrapers.registry.get_scraper`;
    #: every other caller leaves it alone.
    have_details: Container[str] | None = None

    #: The one company this Board belongs to, for a **Single source scraper** (ADR-0139) —
    #: declared here rather than in the ledger, and left ``None`` by every multi-tenant ATS
    #: (ADR-0172).
    #:
    #: The ledger cannot carry it. `scrapable_boards.load` builds
    #: ``ScrapableBoard(slug=scraper.slug_from(tenant, url), name=tenant)`` — so ``name`` is the
    #: raw ``tenant`` column, whatever that happens to be, while ``slug`` goes through
    #: :meth:`slug_from`. A Board whose tenant was recorded as a hostname therefore *displays* the
    #: hostname: ``amazon``/``apple``/``google``/``tiktok``/``bytedance`` served
    #: ``www.amazon.jobs`` and friends to the UI, 17,587 served tech rows between them.
    #:
    #: Renaming the tenant is not the fix. :meth:`slug_from` defaults to returning the tenant, and
    #: those five do not override it, so a tenant of ``Amazon`` makes the slug ``Amazon``, changes
    #: every :meth:`job_id`, and `index prune` evicts the Board's whole population as off-Board
    #: (ADR-0023). ``meta``/``tesla`` could hold a name in that column only because they *do*
    #: override :meth:`slug_from` to read the host out of ``url``.
    #:
    #: A declared name also needs no request, which is the point `meta` made in declining a
    #: `company_name` pattern: :meth:`resolve_company` costs a page fetch per Board and buys
    #: nothing where there is exactly one, known company.
    COMPANY: str | None = None

    def __init__(
        self,
        slug: str,
        company: str | None = None,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.slug = slug
        # `COMPANY` outranks the ledger's name because a Single source scraper knows its own
        # company at authorship time and the ledger's does not: that column doubles as the slug,
        # so a Board discovered by hostname carries the hostname as its display name.
        self.company = self.COMPANY or company or slug
        # The Fetcher seam (ADR-0153): every method below that used to reach `headstart.network.http`
        # as a module global now goes through this instead. Defaulting to `http.DEFAULT_FETCHER`
        # — resolved here, not as the parameter's own default value — means a caller that never
        # passes `fetcher` gets exactly today's global-http behaviour, unchanged, while a test
        # (or a future second HTTP-shaped adapter) can inject a fake without monkeypatching
        # `headstart.network.http` itself. Every scraper that overrides `__init__` passes `fetcher` on to
        # here, and `registry.get_scraper` takes one too, so a fake reaches any Scraper (ADR-0199).
        self._fetcher: Fetcher = (
            fetcher if fetcher is not None else http.DEFAULT_FETCHER
        )
        # Why this Board's list is incomplete, or None when it is whole (ADR-0053).
        #
        # A scraper that gives up mid-pagination and returns what it has is the flap's root cause:
        # it looks to `harvest` exactly like a Board that finished, so `index sync` reads the
        # missing postings as delisted and evicts them. Raising instead is not an option — the
        # partial Jobs are real and worth keeping — so the truncation travels beside them:
        # `scrape_all` reads this after a successful fetch and reports the Board as unfinished.
        self.truncated: str | None = None
        # This Board's own logger, tagged with the ATS rather than with `base`, so a merged CI
        # log still says which scraper spoke (ADR-0039's whole reason for the tag). Built once
        # here rather than per call — `report_detail_gaps` used to re-derive it on every line,
        # an idiom nothing else in the repo uses.
        self._log = log.get(f"headstart.scrapers.{self.ats}")
        # What this Board's detail fetches came back empty *for*, tallied by cause. Filled by
        # `note_detail_loss`, named by `report_detail_gaps`; empty for the scrapers that have
        # not opted in, whose line then reads exactly as it always did.
        self.detail_losses: Counter[str] = Counter()
        # The subset that returned no detail because no request could be formed (for example, a
        # listing with no native id). Kept apart so `detail_attempted` remains a count of Jobs for
        # which the scraper actually reached its request seam, not merely a count of result slots.
        self.detail_unattempted: Counter[str] = Counter()
        # Small per-Board counters that survive the runner in the shard report. This is telemetry,
        # not a second outcome channel: ``truncated`` and raised errors still decide authority.
        self.telemetry: dict[str, Any] = {}

    def mark_truncated(self, why: str) -> None:
        """Record ``why`` this Board's list came back short, keeping the *first* reason.

        A crawl that has already given up once tends to give up again, and the later reasons are
        consequences of the first — so the thing that cut it short is the one worth reporting.
        Every scraper that can detect its own truncation goes through here, so ``harvest`` reads
        one attribute and never learns how many ways a crawl can end (ADR-0053).

        This is the **unconditional** verdict, for a shortfall that is unreachable (a hard cap) or
        unmeasurable (no stated total). A shortfall you can measure against the Board's own total
        goes to :meth:`mark_truncated_unless_negligible`, which tolerates a negligible one (ADR-0121).

        **Logged, because this is the branch that costs something.** The observability was
        inverted until 2026-09-16: the sibling logged the shortfall it *tolerated* while this one
        — where the Board leaves ADR-0053's eviction scope and goes on serving postings that may
        already be closed — said nothing. 74-98 Boards a run took it unnamed.

        INFO for the same reason the sibling is (ADR-0039's annotation quota; ``index sync``
        already emits one aggregate warning). Only the first call logs, matching which reason is
        kept.
        """
        if self.truncated is None:
            self.truncated = why
            self._log.info(
                f"{self.board_key()}: {why} — Board unauthoritative this run, so its missing "
                f"ids are unscraped rather than closed (ADR-0053)"
            )

    def mark_truncated_unless_negligible(
        self, read: int, expected: int, why: str
    ) -> None:
        """Report a shortfall you can *measure*, and truncate only if it is too big to absorb.

        Use this wherever the Board states its own total and the crawl came back under it. Below
        :data:`MIN_AUTHORITATIVE_SHARE` the list is not this Board's set of openings and the Board
        leaves the eviction scope as before; at or above it the list stays authoritative and the
        few missing ids are left to ADR-0083, which withholds each one for a scrape and evicts it
        only on a second consecutive absence. That is the same protection ADR-0053 was giving,
        applied per-Job — and unlike the scope exclusion it drains (ADR-0121).

        Two shapes must **not** come through here, because no share makes them tolerable:

        * **A hard cap.** A Workday query can cap at 2,000 with no facet left to split. The unread
          remainder is genuinely unreachable, not noise, and it is unreachable identically on
          every run — so it calls :meth:`mark_truncated` directly however close to complete the
          read looks. Oracle's 10,000-offset ceiling was this class until ADR-0239: a Board past
          it is now read from both ends, and the union comes through here, since only the
          middle between the two ends is out of reach.
        * **A shortfall with no total to measure against.** A raised scrape, or a surface that
          could not say how much it was missing, has no ``expected`` — the ratio would be
          fabricated. Those call :meth:`mark_truncated` too.

        ``why`` is the caller's own wording, unchanged, so the reason a Board lands in
        ``unauthoritative_boards.json`` reads exactly as it did before this tolerance existed.
        """
        # A total of zero is no total, which is the second excluded shape above — so fail closed
        # rather than divide by it. This is the same direction ADR-0053 chose for an unresolvable
        # key: a shortfall we cannot measure is Unauthoritative, never silently tolerated.
        if expected <= 0 or read < expected * MIN_AUTHORITATIVE_SHARE:
            self.mark_truncated(why)
            return
        # Not logged when a hard cap already spoke: `mark_truncated` keeps the first reason, so
        # the Board is Unauthoritative whatever this call decides, and saying "stays
        # authoritative" about it would be flatly wrong. Reachable — Oracle's page cap runs
        # before this branch, and SuccessFactors' listing cut-short before its detail pass.
        # Nor when nothing is missing: a complete read has no shortfall to tolerate, and phenom,
        # which routes every Board through here, printed ~70 "0 missing id(s)" lines a run.
        if self.truncated is not None or read >= expected:
            return
        # INFO, not WARNING: this fires once per tolerated Board per run and a WARNING is an
        # Actions annotation against a hard quota (ADR-0039). Logged at all because the
        # alternative is a tolerance nobody can see working — the exact blindness ADR-0053's
        # own row-count line had to be added to fix.
        self._log.info(
            f"{self.board_key()}: read {read} of {expected} "
            f"({read / expected:.3%}) — within tolerance, so the list stays authoritative "
            f"and ADR-0083 carries the {expected - read} missing id(s): {why}"
        )

    def note_unreadable_board(self, expected: str, got: str) -> None:
        """Say, before returning nothing, that this Board could not be *read* — which is not
        the same fact as this Board having nothing open, though downstream they are identical.

        A scraper that answers ``[]`` for a listing surface it never managed to parse lands the
        Board in ``boards_ok``, clears its ADR-0058 gone-streak, and leaves every row it already
        has indexed unconfirmed — evicted on the next run under ADR-0083's grace period. The
        company's postings leave search and nothing in the run says why. Five such exits existed
        across four scrapers and none of them was readable from a log.

        ``expected``/``got`` are the discriminator the code actually branched on (the shape asked
        for, and the shape that came back), because "no jobs" alone cannot be acted on. This does
        not mark the Board truncated: whether an unread Board is a *departed* one is per-ATS and
        measured per-ATS, so the scraper that knows makes that call beside this line.

        INFO, not WARNING, and named ``note_`` rather than ``warn_`` to say so. An unreadable
        Board is routine at this scale, not exceptional — the committed liveness ledgers carry
        423 live freshteam rows and 95 live keka rows at ``jobs=0`` — and under Actions a
        WARNING is an annotation against a hard quota (10 per step, 50 per run), so a line that
        can fire once per Board spends the run's whole budget on the routine case and displaces
        the aborts the quota exists for (ADR-0039's 2026-09-08 amendment).
        """
        self._log.info(
            f"{self.board_key()}: read no jobs — expected {expected}, got {got}"
        )

    def note_unread_rows(self, unread: int, listed: int, why: str) -> None:
        """Say how many listed rows the parse skipped — a row with no id or title cannot become a
        Job, and a bare ``continue`` leaves no trace that the listing held more than was read.

        Logging only: the skipped rows stay out of any truncation measure, as before. INFO, one
        line per Board and only when non-zero, for :meth:`note_unreadable_board`'s reason.
        """
        if unread > 0:
            self._log.info(
                f"{self.board_key()}: {unread} of {listed} listed row(s) {why} — "
                "those postings are listed but unread"
            )

    def note_detail_loss(self, cause: str) -> None:
        """Record what one empty detail result was lost *to*, for :meth:`report_detail_gaps`.

        A bare count cannot separate a Board being refused from a Board whose pages arrived
        unreadable — the distinction that hid a User-Agent denylist for five consecutive runs
        while the only line on the subject read ``2127/2127 detail fields missing`` (see
        :data:`USER_AGENT`). Labels are deliberately coarse — a status, or an exception class —
        because what a gap needs is its *shape*, not one distinct string per request.

        Call it only for a detail that will count as a gap under :meth:`run_detail_pass` — one
        whose path returns ``None``, or a :class:`DetailWithoutDescription` — so the tally can
        never exceed the count it explains. Workday keeps a richer tally of its own and does not
        use this.
        """
        self.detail_losses[cause] += 1

    def note_detail_unattempted(self, cause: str) -> None:
        """Record an empty detail result for which no HTTP request could be made."""
        self.note_detail_loss(cause)
        self.detail_unattempted[cause] += 1

    def note_detail_exception(self, exc: Exception) -> None:
        """Record a detail request exception, retaining its settled HTTP status when present."""
        self.note_detail_loss(classify_exception(exc))

    def needs_detail(self, native_id: str) -> bool:
        """Whether this Job still needs its per-job detail fetch (ADR-0048).

        Takes the ATS's **native** id and composes the composite key with :meth:`board_key` —
        ``personio`` and ``workday`` override that, so a caller building ``{ats}:{slug}:{id}``
        itself would miss every entry on those Boards, and miss it *silently* as "fetch
        everything". ``have_details`` is None for every caller outside the pipeline, which means
        fetch everything; the scrape layer is never told *why* a Job is covered.
        """
        if self.have_details is None:
            return True
        return f"{self.board_key()}:{native_id}" not in self.have_details

    def tech_detail_wanted(
        self,
        items: Sequence[_T],
        title_of: Callable[[_T], str | None],
        department_of: Callable[[_T], str | None] | None = None,
    ) -> list[_T]:
        """The subset of ``items`` whose detail fetch is worth making (ADR-0017 + ADR-0048).

        A detail fetch costs one request against a per-origin budget; a posting the tech filter
        drops is never embedded, indexed or shown, so that request buys nothing that survives the
        run. This asks :func:`~headstart.jobs.tech_filter.is_tech` the same question ``filter_tech``
        will ask downstream, with the fields the *listing* already carries, and returns only the
        items still worth fetching.

        **The accessors must read what ``parse`` reads.** Where they do — the listing states both
        fields and no detail overrides either — the gate is exact by construction: the same two
        strings reach the same predicate, so no posting can be gated out that the filter would
        have kept. Where the detail can supply or override ``department``, the gate is an
        *approximation* and must be measured against the real verdict before it ships: on Oracle,
        measured live 2026-09-17, a title-only gate dropped 61.5% of one board's tech postings,
        because ``tech_filter``'s rule 4 promotes a vague title on a technical department and the
        gate never sees that department.

        **Off for every caller that is not the pipeline** (``have_details is None``): a
        directly-constructed scraper keeps the whole Board, which is what
        ``scripts/validate/verify_scraper.py`` and the enrichment samplers measure against, and
        what makes an ATS's real tech share readable at all.
        """
        if not self.tech_gate_enabled() or self.have_details is None:
            return list(items)
        kept = [
            item
            for item in items
            if is_tech(title_of(item), department_of(item) if department_of else None)
        ]
        skipped = len(items) - len(kept)
        if skipped:
            self.telemetry["tech_gated_details"] = skipped
            # DEBUG: routine on every gated Board, and the shard report already carries the count.
            self._log.debug(
                f"{self.board_key()}: skipping {skipped}/{len(items)} non-tech detail "
                f"fetches (ADR-0017 gate)"
            )
        return kept

    @staticmethod
    def attach_details(
        items: Sequence[dict[str, Any]],
        fetched: Sequence[dict[str, Any]],
        results: Sequence[Any],
    ) -> None:
        """Hang each detail on the item it was fetched for, and an empty one on the rest.

        ADR-0048's alignment trap, in one place. Once a gate makes the fan-out cover a *subset*
        of the listing, ``zip(items, results)`` pairs each result with the wrong item — silently,
        because both are lists of the right shape. Pairing against ``fetched`` is the fix, and
        the items that were never fetched must still be given an empty detail rather than left
        carrying a previous run's or another item's.

        Written once rather than at each call site because three scrapers had hand-written the
        same three lines, and three copies of an alignment trap is where the fourth one gets it
        wrong.
        """
        for item in items:
            item["_detail"] = {}
        for item, result in zip(fetched, results):
            item["_detail"] = result or {}

    @staticmethod
    def tech_gate_enabled() -> bool:
        """Whether the pre-detail tech gate runs. **On by default**, like
        :meth:`async_fanout_enabled`, with ``HEADSTART_TECH_GATE=0`` as the kill switch — so a
        tenant whose titles the gate misreads is one workflow variable away from the old
        behaviour rather than a revert per call site and a deploy.

        On rather than off because two of the call sites shipped before this seam existed
        and were already gating in production (eightfold, ADR-0048's amendment; successfactors,
        #503). A default of off would have silently switched both back off in the commit
        that routed them through here."""
        return os.environ.get("HEADSTART_TECH_GATE", "1") != "0"

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """Derive this scraper's ``slug`` from a discovered (tenant, url) row.

        Most ATSes are keyed by the bare tenant label, which is the default. Override only
        where the scraper interprets its slug differently (zoho wants the careers host,
        workday the full careers URL) — the inverse of what ``url()`` does with the slug.
        """
        return tenant

    def board_key(self) -> str:
        """This board's ``{ats}:{slug}`` key — the ``board_of`` prefix its job ids carry.

        Override where the id's Board segment isn't the bare slug (Workday derives ``{company}/{site}``
        from its careers-URL slug). Lets index maintenance (eviction / dead-Board prune, ADR-0023)
        map a ledger entry to the exact key its rows use."""
        return f"{self.ats}:{self.slug}"

    def job_id(self, native_id: str) -> str:
        """This Job's composite id: ``{board_key()}:{native_id}`` (ADR-0157).

        Not overridden by any scraper, including the four whose :meth:`board_key` itself
        departs from the bare ``{ats}:{slug}`` (workday's ``{company}/{site}``, personio's
        ``{tenant}``, taleo_be's and taleo_enterprise's own canonicalized key) — each of
        those composes correctly through here because the deviation lives in
        :meth:`board_key` alone, and this method only ever appends ``:{native_id}`` to
        whatever that returns.
        """
        return f"{self.board_key()}:{native_id}"

    @abstractmethod
    def job_url(self, native_id: str) -> str:
        """This Job's public detail URL (ADR-0157) — the one place this scraper states how a
        posting's link is built, read by both :meth:`parse` and this ATS's :attr:`url_shape`.

        ``@abstractmethod`` rather than a shared default, the same shape as
        :meth:`_salary_field`: every concrete scraper must either return a URL built from a
        formula (most — usually just ``native_id``), or read the link straight off the ATS's
        own listing record when it supplies one directly (ashby, greenhouse, lever, workable,
        teamtailor all do — there ``job_url`` is a documented pass-through, not a formula).

        The parameter is deliberately not always a bare id: a handful of ATSes need more to
        build or recognize their own link (a title slug, a relative path the API already
        gives, the whole raw record) — those scrapers override with whatever signature they
        actually need, the same default-here-override-there latitude ``_detail_url`` already
        had informally per scraper. Each scraper's own :meth:`parse` is the only caller of its
        own ``job_url``, so there is no shared call site that requires one shared signature.
        """

    def alias_key(self) -> str | None:
        """What this Board resolves to, for finding two Boards that are the same one (ADR-0111).

        Two Boards sharing an ``alias_key`` are the same Board. The default reads the answer the
        site owner already published: fetch the Board surface, follow the redirects, and return the
        host it lands on. A Board nothing points away from resolves to its own host, which is what
        makes the shared key meaningful rather than merely equal.

        Override where the redirect off :meth:`url` is not the signal: Workday follows its public
        careers page instead, and each single source scraper (``google``, ``apple``, ``meta``, …)
        is its own key without a request. Where the redirect is the signal but the landing host is
        the wrong key, override only :meth:`alias_key_of_landing` (both Taleo editions). This is
        the same default-here-override-there shape as :meth:`board_key` and :meth:`slug_from`.

        **The default's return value must be comparable to this ATS's own ``slug``, and for the
        default that means the slug has to BE a host.** ``alias_ledger.resolve`` decides a Board
        is a duplicate by asking whether the key is itself a live slug, so on an ATS whose slug is
        not a hostname — Workday's is a whole careers URL, Zoho's a careers host with a path —
        every key falls outside the live set and the entire ledger comes back labelled
        ``migrated``: an empty result, not an error. Those ATSes need an override that returns
        something in their own slug space, and `dedupe_boards.py` warns when a run looks like it
        hit this. SuccessFactors uses the default because its slug is exactly the vanity host;
        so does `amazon` (ADR-0139) — its slug is a fixed hostname with no vanity alias measured
        against it (live-checked 2026-09-11: no redirect on either the careers page or the search
        endpoint), so the inherited redirect-following default is the right call, not an
        unexamined one.

        None when the probe failed: an unreachable Board has earned no verdict, and
        ``alias_ledger.resolve`` reports it rather than grouping it. Note that ``fetch`` settles
        4xx/5xx rather than raising (:class:`~headstart.network.fetcher.Fetcher`'s contract, kept from
        ``http.fetch``), so a Board whose own host answers 503 records itself, not
        None — which reads as "nothing points away from it" and leaves it unburied. That is the
        conservative direction: it can miss a duplicate, never invent one.

        Streamed and closed unread — only the redirect chain is wanted, and a SuccessFactors
        sitemap body runs to megabytes. Only the final host survives, not the chain that reached
        it, which is why the ledger records a destination rather than a route.

        Names its Board in the retry log (``egress_board``) and does no more: it neither routes
        nor walls the spare egress, which is exactly what both Taleo editions' own copies of this
        fetch did before they came to share it (ADR-0203).
        """
        try:
            resp = self.board_fetcher.fetch(
                "GET",
                self.url(),
                direct=True,
                headers={"User-Agent": USER_AGENT},
                timeout=30,
                allow_redirects=True,
                stream=True,
                egress_board=self.board_key(),
            )
            resp.close()
            return self.alias_key_of_landing(resp.url)
        except Exception:  # noqa: BLE001 - any failure to reach it is "no verdict", not a crash
            return None

    @staticmethod
    def alias_key_of_landing(landing_url: str) -> str | None:
        """The alias key a Board's surface names by landing on ``landing_url`` — its host.

        The one step of :meth:`alias_key` an ATS may need to change without re-implementing the
        fetch around it: a Taleo Board shares its regional host with every other customer, so its
        key is the whole canonical career-section URL instead (ADR-0203). Raising is "no verdict",
        the same as a failed fetch."""
        return urllib.parse.urlsplit(landing_url).netloc.lower() or None

    @abstractmethod
    def url(self) -> str:
        """The public endpoint for this company's board."""

    @abstractmethod
    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        """Turn a raw API/page response into normalized Jobs."""

    @abstractmethod
    def _salary_field(self, raw: Any) -> str | None:
        """Format this ATS's native structured compensation field into ``Job.salary``, or
        ``None`` if this ATS has no such field.

        ``headstart.jobs.salary``'s Tier 1 (``from_field``) is what actually reads ``Job.salary`` back
        out at index time — this method is the other half of that contract, the one place each
        scraper states what it found. The expected shape is a bare string carrying whatever the
        native field states — a number or range, a currency code, and a period — space-separated,
        e.g. Lever's ``_salary_field`` returns ``"50000-70000 USD per-year-salary"`` from
        ``salaryRange``. Build that shape with ``headstart.jobs.salary.to_field``, the encoder paired
        with ``from_field`` (ADR-0197), rather than by hand. An ATS with no calibrated
        ``salary.py`` parser still reaches ``_field_generic``, so any reasonable "AMOUNT[-AMOUNT] [CURRENCY] [PERIOD]" spelling is
        safe to emit even without adding a dedicated Tier-1 parser for it.

        ``raw`` is deliberately loose: every ATS's raw per-job record shape differs, so each
        scraper interprets it however its own :meth:`parse` already does (the whole record, or
        just the sub-field that carries compensation).

        ``@abstractmethod`` rather than a shared default — every concrete scraper must either
        return a real formatted string from a native field, or ``return None`` with a comment
        citing the measured evidence that this ATS's raw record carries no such field. This is
        what stops a future scraper from silently never addressing the salary question at all.
        """

    @property
    def egress_group(self) -> str:
        """The origin this Board's spare-egress fallback is metered and walled under: the ATS,
        unless one ATS serves from origins that wall independently (Zoho's data centres)."""
        return self.ats

    @cached_property
    def board_fetcher(self) -> BoardFetcher:
        """This Board's fetcher: the injected fetcher, bound to the Board's spare-egress opt-in
        and its attribution, so every request made through it carries both (ADR-0204).

        Routing (``egress_group``/``egress_on``) is empty for every scraper that leaves
        :attr:`egress_fallback_on` unset, so its requests are identical to the ones made before
        the fallback existed — no scraper is routed or walled without opting in. Keyed on
        :attr:`egress_group` rather than the Board, because the metering that motivates it is per
        origin across all of an ATS's tenants. ``egress_board`` rides along unconditionally: it steers
        nothing, and exists so the retry log (``http._note_retry``, DEBUG) and the shard report
        can name *which* Board spent a retry or the IP supply.

        Bound on first use, not in ``__init__``: :meth:`board_key` is not computable until a
        subclass's own ``__init__`` has run (Workday's reads ``_instance``, set after
        ``super().__init__``), and a malformed slug must fail the Board's first request, as it
        always has, rather than the scraper's construction.
        """
        return BoardFetcher(
            self._fetcher,
            board_key=self.board_key(),
            egress_group=self.egress_group if self.egress_fallback_on else None,
            wall_statuses=self.egress_fallback_on,
        )

    def _get(self, url: str | None = None) -> str:
        """GET a board URL as text via the reliable-fetch seam (retry lives there). Defaults to
        ``self.url()``; pass an explicit ``url`` to fetch a secondary endpoint (e.g. a per-job detail
        page). Raises on a definitive HTTP error so a dead board surfaces as a
        per-company failure."""
        response = self.board_fetcher.fetch(
            "GET",
            url or self.url(),
            headers=dict(DEFAULT_REQUEST_HEADERS),
            timeout=30,
        )
        response.raise_for_status()
        return response.text

    async def _get_async(self, session: Any, url: str | None = None) -> str:
        """Async counterpart to :meth:`_get` over the shared multiplexed ``AsyncSession``.

        Carries :meth:`_get`'s headers, egress marking and raise-on-definitive-error, and pairs
        with it the way :meth:`fan_out_async` pairs with :meth:`fan_out`. **A subclass that
        overrides `_get` must override this too** — eightfold's adds a Referer and marks the
        wall, so it cannot ride this one.
        """
        response = await self.board_fetcher.fetch_async(
            session,
            "GET",
            url or self.url(),
            headers=dict(DEFAULT_REQUEST_HEADERS),
            timeout=30,
        )
        response.raise_for_status()
        return response.text

    def _fetch(
        self, method: str, url: str, *, marks_wall: bool = True, **kwargs: Any
    ) -> Any:
        """Raw request via the reliable-fetch seam, with this scraper's egress opt-in and board
        attribution always applied — the counterpart to :meth:`_get` for a caller that needs a
        non-GET method, custom headers/timeout, or the raw ``Response`` rather than parsed text.
        ``marks_wall`` passes straight through to :meth:`BoardFetcher.egress_binding`.
        """
        return self.board_fetcher.fetch(method, url, marks_wall=marks_wall, **kwargs)

    async def _fetch_async(
        self,
        session: Any,
        method: str,
        url: str,
        *,
        marks_wall: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Async counterpart to :meth:`_fetch`, over the shared multiplexed ``AsyncSession``."""
        return await self.board_fetcher.fetch_async(
            session, method, url, marks_wall=marks_wall, **kwargs
        )

    def fetch_raw(self) -> Any:
        return json.loads(self._get())

    #: When set, :meth:`resolve_company` streams the board page and reads only its first this
    #: many bytes — for a page whose name sits in the ``<head>`` of a body that can run to
    #: megabytes (freshteam's ``/jobs`` is 1.7 MB on ``abnhire``). None reads the whole page.
    board_page_head: int | None = None

    def board_page(self) -> str | None:
        """The page that states this Board's company name — its ``<title>`` unless
        :meth:`company_from_page` reads it elsewhere — or None for an ATS with no such page.
        Overridden by the scrapers `headstart.boards.company_name` has evidence for; for everything else
        :meth:`fetch` serves the Board's humanised tenant (ADR-0212)."""
        return None

    def resolve_company(self) -> None:
        """Replace the slug standing in for this Board's company with its real name, if we can.

        Called from :meth:`fetch`, not :meth:`parse`, because it makes a request and ``parse`` is
        pure — that split is what lets the parse tests run against recorded fixtures.

        One request per Board, never per Job (a :meth:`company_from_page` override may make one
        more, for a second source), and every failure path leaves ``self.company`` exactly as it
        was: no ``board_page``, a request that raises, a page this ATS cannot read, a reader that
        raises. What that guarantees is narrower than "only ever an upgrade": a slug is never
        replaced by a *non-name*, but a Board can state a name less recognisable than its own slug
        (`ripplehire:ltimindtree` serves "LTM"). ADR-0114 §Consequences has the measured cases.
        """
        # A real name outranks a page title — but "different from the slug" is not the same
        # question. The ledger itself holds "wipro" and "citi", so the first draft's
        # `self.company != self.slug` refused to improve exactly the rows this exists to fix.
        if not company_name.looks_like_slug(self.company):
            return
        page = self.board_page()
        if not page:
            return
        try:
            # `self._fetch` directly, not `self._get`: that method's return type is not the same
            # across subclasses — eightfold's override hands back the `Response` where the base
            # returns `.text` — and going through it fed a `Response` to the title parser and
            # broke every eightfold Board. Caught end to end against live boards, not by the
            # suite, which passed throughout.
            response = self._fetch_once(
                "GET", page, stream=self.board_page_head is not None
            )
            if self.board_page_head is not None:
                html_text = _head_of(response, self.board_page_head)
            else:
                html_text = response.text if response.status_code == 200 else None
        except Exception as exc:  # noqa: BLE001 - a display name is never worth failing a Board for
            self._log.info(
                f"{self.board_key()}: no company name — {page} raised {type(exc).__name__}"
            )
            return
        try:
            name = self.company_from_page(html_text)
        except Exception as exc:  # noqa: BLE001 - a reader's surprise is never worth the Board
            self._log.info(
                f"{self.board_key()}: no company name — reading {page} raised "
                f"{type(exc).__name__}"
            )
            return
        if name:
            self.company = name
        else:
            # INFO, like every per-Board line (ADR-0039). Until 2026-09-24 this path was silent,
            # so a Board serving its slug could not be told apart from one whose page was
            # never asked, refused, or read and not recognised.
            self._log.info(
                f"{self.board_key()}: no company name — {page} answered "
                f"{response.status_code} and stated none this ATS accepts"
            )

    def _fetch_once(
        self, method: str, url: str, *, accept: str = "text/html", **kwargs: Any
    ) -> Any:
        """One request for something only the company name needs, sent through the shared fetch
        seam — `_fetch` directly, not `_get`, whose return type differs across subclasses
        (eightfold's hands back the `Response`).

        One attempt, and it can never wall the ATS. A display name is the most optional thing
        a scrape fetches, so it must not spend the retry ladder (three attempts against a walled
        origin is ~90s for a Board) and its own non-200 must not be what routes every other
        Board of that ATS onto the spare egress — the reason eightfold's own probe already
        passes `marks_wall=False`.
        """
        return self._fetch(
            method,
            url,
            headers={"User-Agent": USER_AGENT, "Accept": accept},
            timeout=30,
            attempts=1,
            marks_wall=False,
            **kwargs,
        )

    def company_from_page(self, page: str | None) -> str | None:
        """The company name :meth:`board_page`'s HTML states, or None — its ``<title>`` read
        through this ATS's `company_name` patterns. Overridden where the page states the name
        somewhere else as well, or needs decoding first."""
        return company_name.from_title(self.ats, company_name.title_of(page), self.slug)

    def wants_company_name(self) -> bool:
        """Whether a stated name would change what this Board is served under: it has no real
        name yet, and no curated one overrides every source (ADR-0212). A scraper asks before
        spending a request on a name source."""
        return company_name.looks_like_slug(self.company) and not company_name.curated(
            self.board_key()
        )

    def adopt_company(self, stated: str | None) -> None:
        """Serve the name a structured field states as this Board's company.

        For a name a scraper reads off a response it fetched anyway — a detail record, a config
        call, a posting's JSON-LD — read by `company_name.from_field`, which takes it as the
        company typed it (ADR-0212) and refuses this ATS's vendor aliases. A real name already on
        the Board is kept, as in :meth:`resolve_company`.
        """
        if not self.wants_company_name():
            return
        name = company_name.from_field(self.ats, stated)
        if name:
            self.company = name
        elif stated:
            self._log.info(
                f"{self.board_key()}: no company name — a field stated {stated!r}, which "
                "the guards refuse"
            )

    def fetch(self) -> list[Job]:
        scraped_at = datetime.now(UTC).isoformat()
        # What the constructor left, before any source runs: anything that differs afterwards
        # was stated during this fetch, by `resolve_company` or a `fetch_raw` of its own.
        unresolved = self.company
        raw = self.fetch_raw()
        curated = company_name.curated(self.board_key())
        if not curated:
            self.resolve_company()
        # Before `parse`, so a posting with no name of its own falls back to a name, never to
        # the slug (ADR-0212).
        # None, where the tenant is only a code, is served as an empty company.
        self.company = (
            company_name.settled(self.company, unresolved, self.board_key()) or ""
        )
        jobs = self.parse(raw, scraped_at)
        for i, job in enumerate(jobs):
            # A curated name overrides a posting's own too; an all-caps legal name a posting
            # states is title-cased; and padding, which `field or self.company` let through as
            # truthy, falls back to the Board's name.
            name = curated or company_name.title_cased(job.company) or self.company
            if name != job.company:
                jobs[i] = replace(job, company=name)
        return jobs

    @staticmethod
    def fan_out(
        items: Sequence[_T],
        fn: Callable[[_T], _R],
        *,
        workers: int = _DEFAULT_FAN_OUT_WORKERS,
        default: _R | None = None,
        what: str = "fan_out",
    ) -> list[_R | None]:
        """Apply ``fn`` to each item across a bounded thread pool, isolating per-item failures.

        Returns results aligned to ``items`` — input order, not completion order — where each
        entry is ``fn(item)`` or ``default`` if that call raised. One item's failure never sinks
        the batch: the detail passes are network-bound, so a single 404 or timeout must not drop
        the rest of the Board's Jobs. ``workers`` bounds the pool; a scraper hammering one
        rate-limited host passes a smaller value (trakstar uses 4 under DataDome). ``what``
        (usually the Board key) prefixes the line an unexpected exception is reported on.
        """
        results: list[_R | None] = [default] * len(items)
        if not items:
            return results
        unexpected: Counter[str] = Counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fn, item): i for i, item in enumerate(items)}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception as exc:  # noqa: BLE001 - one item's failure must not sink the batch
                    if not isinstance(exc, _ROUTINE_FAILURES):
                        # The call's first only: a systemic bug raises on every item, and the
                        # rest are tallied onto one line below.
                        if not unexpected:
                            _UNEXPECTED.report(
                                f"{what}: unexpected {type(exc).__name__} in a fan-out item"
                            )
                        unexpected[type(exc).__name__] += 1
                    results[index] = default
        _report_unexpected_tally(what, unexpected)
        return results

    def fan_out_async(
        self,
        items: Sequence[_T],
        fn: Callable[[Any, _T], Awaitable[_R]],
        *,
        concurrency: int | None = None,
        default: _R | None = None,
    ) -> list[_R | None]:
        """HTTP/2-multiplexed counterpart to :meth:`fan_out` (ADR-0015).

        ``fn(session, item)`` returns an awaitable. One ``curl_cffi`` ``AsyncSession`` is shared across
        every item, so same-host requests ride as concurrent **streams over one HTTP/2 connection**
        instead of one connection per thread. Results are input-aligned and a raising item becomes
        ``default`` — same contract as :meth:`fan_out`. Runs its own event loop, so a sync
        thread-pool caller (one board per company thread) can invoke it directly.

        ``concurrency`` bounds the in-flight streams (the multiplexing width), resolved at call
        time as: the explicit argument, then ``HEADSTART_H2_STREAMS`` (the operator's
        ``run_scrapers --streams`` escape hatch), then this scraper's own :attr:`detail_streams`
        or :attr:`detail_workers`, and only then :data:`_DEFAULT_H2_STREAMS`. The scraper's own
        bound comes before the global default so a detail pass cannot be polite on the sync path
        and 100-wide on the async one — the divergence that had Workday fetching 100 streams
        against a host its sync path deliberately held to 6.

        Whatever that chain settles on is a **ceiling**, not the width: every step of it is static,
        so a shard whose origin has already refused it would otherwise fan out exactly as wide as
        one the origin is still serving. :func:`~headstart.network.spare_egress.stream_width` clamps the
        resolved number once this scraper's egress group has walled (#195).

        The clamp only ever narrows, and it outranks **every** step above it, the operator's
        ``--streams`` included — so that flag now widens an unwalled group and not a walled one.
        That is deliberate: ``--streams`` exists to pace an origin, and a walled origin has
        already said what pace it will take. The kill switch for the async path itself is
        ``HEADSTART_ASYNC_FANOUT=0`` (ADR-0016), which this does not touch.
        """
        if not items:
            return [default] * len(items)
        if concurrency is None:
            env = os.environ.get("HEADSTART_H2_STREAMS")
            concurrency = int(
                env or self.detail_streams or self.detail_workers or _DEFAULT_H2_STREAMS
            )
        # The board fetcher holds the group this scraper's traffic is metered under, and none for
        # one that never opted in — so the clamp keys on exactly what the requests themselves
        # carry rather than a second guess at it that could drift from `egress_fallback_on`.
        concurrency = self.board_fetcher.stream_width(concurrency)
        # Recorded against the width in force, not the ceiling above it, so the clamp's two
        # operating points stay comparable (`headstart.network.fanout_stats`).
        with fanout_stats.batch(f"{self.ats} details", concurrency) as item_done:
            return asyncio.run(
                BaseScraper._gather_async(
                    items, fn, concurrency, default, item_done, self.board_key()
                )
            )

    @staticmethod
    async def _gather_async(
        items: Sequence[_T],
        fn: Callable[[Any, _T], Awaitable[_R]],
        concurrency: int,
        default: _R | None,
        item_done: Callable[[float], None],
        what: str,
    ) -> list[_R | None]:
        from curl_cffi.requests import AsyncSession

        sem = asyncio.Semaphore(concurrency)
        results: list[_R | None] = [default] * len(items)
        # One event-loop thread writes this, so it needs no lock.
        unexpected: Counter[str] = Counter()
        async with AsyncSession(impersonate="chrome") as session:

            async def one(index: int, item: _T) -> None:
                async with sem:
                    # Timed from inside the semaphore: the wait for a slot is queueing, and
                    # counting it as busy time would report every width as fully occupied.
                    started = time.monotonic()
                    try:
                        results[index] = await fn(session, item)
                    except Exception as exc:  # noqa: BLE001 - one item's failure must not sink the batch
                        if not isinstance(exc, _ROUTINE_FAILURES):
                            # As in `fan_out`: the call's first only, the rest tallied below.
                            if not unexpected:
                                _UNEXPECTED.report(
                                    f"{what}: unexpected {type(exc).__name__} in a fan-out item"
                                )
                            unexpected[type(exc).__name__] += 1
                        results[index] = default
                    finally:
                        item_done(time.monotonic() - started)

            await asyncio.gather(*(one(i, item) for i, item in enumerate(items)))
        _report_unexpected_tally(what, unexpected)
        return results

    def detail_request(self, item: Any) -> DetailRequest:
        """The request that fetches ``item``'s detail — the one place a Scraper states it, for
        :meth:`run_detail_pass` to send on either transport (ADR-0201).

        Raise :class:`DetailLost` when no request can be formed; it is counted unattempted. Only a
        Scraper that calls :meth:`run_detail_pass` implements this.
        """
        raise NotImplementedError(f"{type(self).__name__} has no detail_request")

    def read_detail(self, item: Any, response: Any) -> Any:
        """``item``'s detail out of its 200 ``response``, or raise :class:`DetailLost` naming
        why the page carries none — or return :class:`DetailWithoutDescription` for a page whose
        other fields are real but whose description is missing.

        Only ever handed a 200: a non-200 is labelled by :meth:`detail_status_loss` before this is called,
        and anything it raises other than :class:`DetailLost` is labelled by its exception type
        rather than lost unlabelled. Keep it free of I/O — it runs inside the event loop on the
        multiplexed path.
        """
        raise NotImplementedError(f"{type(self).__name__} has no read_detail")

    def fetch_detail_batch(self, requests: Sequence[DetailRequest]) -> Sequence[Any]:
        """The responses to ``requests``, one per request and in order — the transport of a
        Scraper that sets :attr:`detail_batch_size` (ADR-0228).

        Each element is an object with ``status_code``, ``text`` and ``json()`` (what
        :meth:`read_detail` reads), or an ``Exception`` for a request that never got an answer.
        Raise :class:`DetailBatchWalled` when the origin refuses the batch and no route is left.
        """
        raise NotImplementedError(f"{type(self).__name__} has no fetch_detail_batch")

    def run_detail_pass(
        self,
        items: Sequence[_T],
        *,
        key_of: Callable[[_T], str | None],
        what: str,
        title_of: Callable[[_T], str | None] | None = None,
        department_of: Callable[[_T], str | None] | None = None,
        skip_held: bool = False,
        concurrency: int | None = None,
    ) -> FetchedDetails:
        """Fetch the detail of each of ``items`` worth fetching, and return them by native id.

        The whole **Detail pass** a Scraper used to compose by hand, behind one call (ADR-0201):

        * ``title_of`` (with ``department_of``, if the listing states one) arms the ADR-0166 tech
          gate, :meth:`tech_detail_wanted`. Omit it where the gate was measured unsafe.
        * ``skip_held`` skips a Job whose description the store already holds (ADR-0048,
          :meth:`needs_detail`). Leave it off where the detail supplies more than the description.
        * Each remaining item goes through :meth:`detail_request`, the fetch seam and
          :meth:`read_detail`, on the multiplexed path (ADR-0016) unless it is off
          (:meth:`async_fanout_enabled`), in which case on :attr:`detail_workers` threads. Both
          transports record their width and throughput (``fanout_stats``).
        * Every loss is labelled — a transport exception, a non-200 status, a
          :class:`DetailLost`, a :class:`DetailWithoutDescription`'s cause, or an unexpected
          parse error by its type — and the pass ends in one :meth:`report_detail_gaps` line
          titled ``what``.

        ``key_of`` gives an item's native id: the key of the returned mapping, and what
        :meth:`needs_detail` is asked about. It may answer None for a row with no id, which is
        never held and never keyed — its :meth:`detail_request` says why it was not fetched.

        ``concurrency`` pins the multiplexed width over every other source, for a host whose
        politeness bound must not be widened even by the operator (Trakstar under DataDome,
        ADR-0016); leave it None otherwise.
        """
        wanted: Sequence[_T] = items
        if title_of is not None:
            wanted = self.tech_detail_wanted(wanted, title_of, department_of)
        if skip_held:
            wanted = [
                item
                for item in wanted
                if (native_id := key_of(item)) is None or self.needs_detail(native_id)
            ]
        # ADR-0209: the last time a detail landed. Read before each item starts, so once nothing
        # has landed for `_DETAIL_STALL_S` every item not yet started is skipped, labelled.
        landed = _detail_clock()

        def skipped_as_stalled() -> bool:
            # A float read and a float write, each atomic under the GIL: no lock on either path.
            if _detail_clock() - landed <= _DETAIL_STALL_S:
                return False
            self.note_detail_unattempted(DETAIL_STALLED)
            return True

        def note(outcome: Any) -> Any:
            nonlocal landed
            if _unwrapped(outcome) is not None:
                landed = _detail_clock()
            return outcome

        async def watched_async(session: Any, item: _T) -> Any:
            if skipped_as_stalled():
                return None
            try:
                outcome = await asyncio.wait_for(
                    self._fetch_detail_outcome_async(session, item),
                    _DETAIL_ITEM_TIMEOUT_S,
                )
            except TimeoutError:
                self.note_detail_loss(DETAIL_TIMED_OUT)
                return None
            return note(outcome)

        def watched(item: _T) -> Any:
            if skipped_as_stalled():
                return None
            return note(self._fetch_detail_outcome(item))

        if self.detail_batch_size:
            results = self._fetch_detail_batches(
                wanted, self.detail_batch_size, skipped_as_stalled, note
            )
        elif self.async_fanout_enabled():
            results = self.fan_out_async(wanted, watched_async, concurrency=concurrency)
        else:
            results = self._fan_out_timed(
                wanted,
                watched,
                self.detail_workers or _DEFAULT_FAN_OUT_WORKERS,
            )
        described_details: list[Any] = []
        details: dict[str, Any] = {}
        for item, outcome in zip(wanted, results):
            fields = _unwrapped(outcome)
            # A detail kept without its description is still a gap on the line.
            described_details.append(outcome if fields is outcome else None)
            if fields is not None and (native_id := key_of(item)) is not None:
                details[native_id] = fields
        return FetchedDetails(details, self.report_detail_gaps(described_details, what))

    def _fetch_detail_batches(
        self,
        items: Sequence[_T],
        size: int,
        skipped_as_stalled: Callable[[], bool],
        note: Callable[[Any], Any],
    ) -> list[Any]:
        """:meth:`run_detail_pass`'s transport for a Scraper with :attr:`detail_batch_size`: the
        items in order, ``size`` at a time, each batch through :meth:`fetch_detail_batch`.

        A :class:`DetailBatchWalled` ends the pass: this batch and every later one is skipped,
        labelled :data:`DETAIL_WALLED`, and the details already landed are kept.
        """
        results: list[Any] = [None] * len(items)
        walled = False
        for start in range(0, len(items), size):
            batch = range(start, min(start + size, len(items)))
            if walled:
                for _ in batch:
                    self.note_detail_unattempted(DETAIL_WALLED)
                continue
            if skipped_as_stalled():
                for _ in batch[
                    1:
                ]:  # one label per skipped item, as the other transports write
                    skipped_as_stalled()
                continue
            formed = [
                (i, request)
                for i in batch
                if (request := self._detail_request_or_none(items[i])) is not None
            ]
            if not formed:
                continue
            try:
                responses = self.fetch_detail_batch([request for _, request in formed])
            except DetailBatchWalled as wall:
                self._log.info(f"{self.board_key()}: detail pass stopped — {wall}")
                walled = True
                for _ in formed:
                    self.note_detail_unattempted(DETAIL_WALLED)
                continue
            except Exception as exc:  # noqa: BLE001 - one batch's failure must not sink the Board
                # As in `_read_detail_outcome`: the Board's first of each type only.
                if (
                    not isinstance(exc, _ROUTINE_FAILURES)
                    and not self.detail_losses[classify_exception(exc)]
                ):
                    _UNEXPECTED.report(
                        f"{self.board_key()}: unexpected {type(exc).__name__} in a detail batch"
                    )
                for _ in formed:
                    self.note_detail_exception(exc)
                continue
            for (i, _), response in zip(formed, responses, strict=True):
                if isinstance(response, Exception):
                    self.note_detail_exception(response)
                else:
                    results[i] = note(self._read_detail_outcome(items[i], response))
        return results

    def _fan_out_timed(
        self, items: Sequence[_T], fetch_one: Callable[[_T], _R], workers: int
    ) -> list[_R | None]:
        """:meth:`fan_out` recording its operating point, as :meth:`fan_out_async` does.

        ``fan_out`` is a staticmethod with no Scraper to name, so a Board on the thread path used
        to drop its ``concurrency {ats} details @N`` line — the line ADR-0167's transport
        decision was read from. The lock is needed here and not on the async path:
        ``fanout_stats.batch``'s callback accumulates into an unsynchronised dict, safe from one
        event-loop thread but not from ``workers`` threads at once.
        """
        if not items:
            return []
        timing_lock = threading.Lock()
        with fanout_stats.batch(f"{self.ats} details", workers) as item_done:

            def timed(item: _T) -> _R:
                started = time.monotonic()
                try:
                    return fetch_one(item)
                finally:
                    with timing_lock:
                        item_done(time.monotonic() - started)

            return self.fan_out(items, timed, workers=workers, what=self.board_key())

    def fetch_detail(self, item: Any) -> Any:
        """One Job's detail over the thread-path transport, every loss labelled — the per-item
        step of :meth:`run_detail_pass`, public so a sampler can fetch a handful of details
        without running a whole pass (``scripts/enrich/salary_sample.py``). None when lost; a
        :class:`DetailWithoutDescription` comes back unwrapped, its loss still labelled."""
        return _unwrapped(self._fetch_detail_outcome(item))

    def _fetch_detail_outcome(self, item: Any) -> Any:
        request = self._detail_request_or_none(item)
        if request is None:
            return None
        try:
            response = self._fetch(
                request.method,
                request.url,
                headers=dict(request.headers),
                timeout=request.timeout,
                **request.options,
            )
        except Exception as exc:  # noqa: BLE001 - labelled here, not lost to fan_out's catch-all
            self.note_detail_exception(exc)
            return None
        return self._read_detail_outcome(item, response)

    async def _fetch_detail_outcome_async(self, session: Any, item: Any) -> Any:
        request = self._detail_request_or_none(item)
        if request is None:
            return None
        try:
            response = await self._fetch_async(
                session,
                request.method,
                request.url,
                headers=dict(request.headers),
                timeout=request.timeout,
                **request.options,
            )
        except Exception as exc:  # noqa: BLE001 - labelled here, not lost to fan_out's catch-all
            self.note_detail_exception(exc)
            return None
        return self._read_detail_outcome(item, response)

    def _detail_request_or_none(self, item: Any) -> DetailRequest | None:
        try:
            return self.detail_request(item)
        except DetailLost as lost:
            self.note_detail_unattempted(lost.cause)
            return None

    def _read_detail_outcome(self, item: Any, response: Any) -> Any:
        if response.status_code != 200:
            self.note_detail_loss(self.detail_status_loss(response))
            return None
        try:
            detail = self.read_detail(item, response)
        except DetailLost as lost:
            self.note_detail_loss(lost.cause)
        except Exception as exc:  # noqa: BLE001 - an unreadable body is a labelled loss
            # The Board's first of each type only: the gap line counts the rest by type.
            if (
                not isinstance(exc, _ROUTINE_FAILURES)
                and not self.detail_losses[classify_exception(exc)]
            ):
                _UNEXPECTED.report(
                    f"{self.board_key()}: unexpected {type(exc).__name__} reading a detail"
                )
            self.note_detail_exception(exc)
        else:
            if isinstance(detail, DetailWithoutDescription):
                self.note_detail_loss(detail.cause)
            return detail
        return None

    def detail_status_loss(self, response: Any) -> str:
        """The loss label for a detail that settled on a non-200 status (:meth:`run_detail_pass`,
        either transport; the response is already read, so no I/O here).

        The status itself by default. Override where a status means something specific on the
        host: Zoho's unfollowed 302 to ``/html/portal.html`` is its throttle (ADR-0226), and a bare
        ``HTTP 302`` would hide that."""
        return f"HTTP {response.status_code}"

    def report_detail_gaps(self, results: Sequence[Any], what: str) -> int:
        """Log how many of a detail pass's results came back empty (None) — the gaps behind
        ADR-0021's null fields. One INFO line per Board, only when something is missing; the
        failure was isolated per item (the fan_out contract), so this line is usually the
        only trace the gap leaves.

        Returns how many were missing, so a scraper whose detail pass is *load-bearing* — one
        where `parse` drops the Job without it — can mark the Board truncated on the same count
        (ADR-0053). Most callers only enrich a field and rightly ignore it.

        A scraper that needs more than a count may **replace** this line rather than add to it —
        workday reports its own gaps classified by cause (ADR-0088) and so does not call this at
        all. Adding a second line beside this one instead is the thing to avoid: the two carry
        the same numbers, so anything grepping them double-counts the Board.

        Where the scraper labelled its losses through :meth:`note_detail_loss`, the causes are
        appended to this same line rather than to a second one, for that reason. The leading
        ``N/M {what} missing`` is unchanged either way — several docs and probes quote it."""
        missing = sum(1 for r in results if r is None)
        unattempted = sum(self.detail_unattempted.values())
        self.telemetry.update(
            {
                "detail_jobs": len(results),
                "detail_attempted": max(0, len(results) - unattempted),
                "detail_losses": missing,
                "detail_http_failures": sum(
                    n
                    for cause, n in self.detail_losses.items()
                    if cause.startswith("HTTP ")
                ),
                "detail_breaker_skips": 0,
                # ADR-0209: read by `harvest`, which then keeps this Board's cost row unchanged.
                "detail_stalled": self.detail_losses[DETAIL_STALLED],
                "detail_loss_causes": dict(self.detail_losses),
            }
        )
        if missing:
            self._log.info(
                f"{self.board_key()}: {missing}/{len(results)} {what} missing"
                # A scraper that never called `note_detail_loss` gets nothing appended, which is
                # why every unmigrated scraper's line stays byte-identical to before. The check
                # lives here rather than inside `loss_breakdown` because workday's caller answers
                # it the other way round: its detail pass always passes a Counter, so a Board that
                # labelled nothing there is a hole in the labelling worth naming, not a scraper
                # that opted out.
                + (
                    loss_breakdown(self.detail_losses, missing)
                    if self.detail_losses
                    else ""
                )
            )
        return missing

    @classmethod
    def async_fanout_enabled(cls) -> bool:
        """Whether the detail pass uses the multiplexed async path (ADR-0015, default per ADR-0016).

        On by default; ``HEADSTART_ASYNC_FANOUT=0`` falls back to the sync thread-pool path for
        every scraper at once. The policy still lives here — a scraper does not reimplement it,
        it only declares :attr:`async_fanout` (ADR-0167), and the env switch still overrides that
        in the *off* direction. There is deliberately no on-switch: a scraper sets the attribute
        False only on a measurement, and an operator flag that could override the measurement
        would make the slow path reachable by accident.
        """
        if os.environ.get("HEADSTART_ASYNC_FANOUT", "1") == "0":
            return False
        return cls.async_fanout
