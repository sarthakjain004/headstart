"""Base scraper: shared fetching plus the parse contract each ATS implements."""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.parse
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Awaitable, Callable, Container, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any, TypeVar

from headstart import company_name, fanout_stats, http, log, spare_egress
from headstart.fetcher import Fetcher
from headstart.models import Job
from headstart.tech_filter import is_tech

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
#: postings listed and none ingested, and the log could not say so because ``_job_fields`` maps a
#: 403 and an unparseable 200 onto the same ``None``.
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
#: One *policy*, but not yet every call site: `icims`, `jobvite`, `smartrecruiters` and `zwayam`
#: still report a measured shortfall through the unconditional :meth:`BaseScraper.mark_truncated`.
#: That is a measured scope boundary, not an oversight — on the live ledger the gate would never
#: fire for them. All 13 excluded `icims` Boards read **0.000%** (`1515/1515` job pages unreadable
#: on the worst): a total detail-pass failure, which is a broken scrape rather than a shortfall,
#: and 0% clears no threshold. The one `smartrecruiters` Board raised `HTTP 401` and states no
#: total at all, and `jobvite` and `zwayam` have no excluded Boards. Convert them when a Board of
#: theirs is actually observed coming back marginally short.
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

# Default HTTP/2 multiplexing width (concurrent streams per host) for fan_out_async — 100 is around
# the common server MAX_CONCURRENT_STREAMS. Override per-call, via HEADSTART_H2_STREAMS, or
# run_scrapers --streams N. Read at call time (below) so a CLI flag can set the env before the scrape.
_DEFAULT_H2_STREAMS = 100


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

    #: HTTP statuses at which this ATS should stop being requested over the shard's own egress IP
    #: and move to a spare one (see :mod:`headstart.spare_egress`). Empty — every scraper unless it
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
    #: Only requests made through :meth:`_get` carry the opt-in. A scraper that calls
    #: ``http.fetch`` or ``http.fetch_async`` directly (most of them do, for their detail passes) must pass
    #: ``**self._egress()`` itself, or setting this is silently inert.
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

    def __init__(
        self,
        slug: str,
        company: str | None = None,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.slug = slug
        self.company = company or slug
        # The Fetcher seam (ADR-0153): every method below that used to reach `headstart.http`
        # as a module global now goes through this instead. Defaulting to `http.DEFAULT_FETCHER`
        # — resolved here, not as the parameter's own default value — means a caller that never
        # passes `fetcher` gets exactly today's global-http behaviour, unchanged, while a test
        # (or a future second HTTP-shaped adapter) can inject a fake without monkeypatching
        # `headstart.http` itself. None of the nine scrapers that override `__init__` need any
        # change for this: they all call `super().__init__(slug, company)` positionally, which
        # still resolves to the same default.
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

        * **A hard cap.** Oracle's API serves no offset past 10,000 and a Workday query can cap at
          2,000 with no facet left to split. The unread remainder is genuinely unreachable, not
          noise, and it is unreachable identically on every run — so it calls
          :meth:`mark_truncated` directly however close to complete the read looks.
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
        if self.truncated is not None:
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

    def note_detail_loss(self, cause: str) -> None:
        """Record what one empty detail result was lost *to*, for :meth:`report_detail_gaps`.

        A bare count cannot separate a Board being refused from a Board whose pages arrived
        unreadable — the distinction that hid a User-Agent denylist for five consecutive runs
        while the only line on the subject read ``2127/2127 detail fields missing`` (see
        :data:`USER_AGENT`). Labels are deliberately coarse — a status, or an exception class —
        because what a gap needs is its *shape*, not one distinct string per request.

        Call it only where the same path returns ``None``, so the tally can never exceed the
        count it explains. Workday keeps a richer tally of its own and does not use this.
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

    def tech_wanted(
        self,
        items: Sequence[_T],
        title_of: Callable[[_T], str | None],
        department_of: Callable[[_T], str | None] | None = None,
    ) -> list[_T]:
        """The subset of ``items`` whose detail fetch is worth making (ADR-0017 + ADR-0048).

        A detail fetch costs one request against a per-origin budget; a posting the tech filter
        drops is never embedded, indexed or shown, so that request buys nothing that survives the
        run. This asks :func:`~headstart.tech_filter.is_tech` the same question ``filter_tech``
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
            self._log.info(
                f"{self.board_key()}: skipping {skipped}/{len(items)} non-tech detail "
                f"fetches (ADR-0017 gate)"
            )
        return kept

    @staticmethod
    def tech_gate_enabled() -> bool:
        """Whether the pre-detail tech gate runs. **On by default**, like
        :meth:`async_fanout_enabled`, with ``HEADSTART_TECH_GATE=0`` as the kill switch — so a
        tenant whose titles the gate misreads is one workflow variable away from the old
        behaviour rather than nine reverts and a deploy.

        On rather than off because two of the nine call sites shipped before this seam existed
        and were already gating in production (eightfold, ADR-0048's amendment; successfactors,
        #503). A default of off would have silently switched both back on the commit that
        routed them through here."""
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

        Override where an ATS serves its aliases independently instead of redirecting between them
        — Eightfold's ``nvidia.eightfold.ai`` and ``jobs.nvidia.com`` each answer for themselves, so
        the default finds nothing there and its tenant id is the key. This is the same
        default-here-override-there shape as :meth:`board_key` and :meth:`slug_from`.

        **The default's return value must be comparable to this ATS's own ``slug``, and for the
        default that means the slug has to BE a host.** ``board_aliases.resolve`` decides a Board
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
        ``board_aliases.resolve`` reports it rather than grouping it. Note that ``fetch`` settles
        4xx/5xx rather than raising (:class:`~headstart.fetcher.Fetcher`'s contract, kept from
        ``http.fetch``), so a Board whose own host answers 503 records itself, not
        None — which reads as "nothing points away from it" and leaves it unburied. That is the
        conservative direction: it can miss a duplicate, never invent one.

        Streamed and closed unread — only the redirect chain is wanted, and a SuccessFactors
        sitemap body runs to megabytes. Only the final host survives, not the chain that reached
        it, which is why the ledger records a destination rather than a route.
        """
        try:
            resp = self._fetcher.fetch(
                "GET",
                self.url(),
                headers={"User-Agent": USER_AGENT},
                timeout=30,
                allow_redirects=True,
                stream=True,
            )
            resp.close()
            return urllib.parse.urlsplit(resp.url).netloc.lower() or None
        except Exception:  # noqa: BLE001 - any failure to reach it is "no verdict", not a crash
            return None

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

        ``headstart.salary``'s Tier 1 (``from_field``) is what actually reads ``Job.salary`` back
        out at index time — this method is the other half of that contract, the one place each
        scraper states what it found. The expected shape is a bare string carrying whatever the
        native field states — a number or range, a currency code, and a period — space-separated,
        e.g. Lever's ``_salary_field`` returns ``"50000-70000 USD per-year-salary"`` from
        ``salaryRange``. An ATS with no calibrated ``salary.py`` parser still reaches
        ``_field_generic``, so any reasonable "AMOUNT[-AMOUNT] [CURRENCY] [PERIOD]" spelling is
        safe to emit even without adding a dedicated Tier-1 parser for it.

        ``raw`` is deliberately loose: every ATS's raw per-job record shape differs, so each
        scraper interprets it however its own :meth:`parse` already does (the whole record, or
        just the sub-field that carries compensation).

        ``@abstractmethod`` rather than a shared default — every concrete scraper must either
        return a real formatted string from a native field, or ``return None`` with a comment
        citing the measured evidence that this ATS's raw record carries no such field. This is
        what stops a future scraper from silently never addressing the salary question at all.
        """

    def _egress(self, *, marks_wall: bool = True) -> dict[str, Any]:
        """``http.fetch`` kwargs opting this scraper into the spare-egress fallback, plus board
        attribution for the retry log even when it doesn't.

        Routing (``egress_group``/``egress_on``) is empty for every scraper that leaves
        :attr:`egress_fallback_on` unset, so the *request* it feeds is identical to the one made
        before this existed — no scraper is routed or walled without opting in. Keyed on
        :attr:`ats` rather than the Board, because the metering that motivates it is per origin
        across all of an ATS's tenants.

        ``egress_board`` rides along unconditionally: it steers nothing, costs nothing, and exists
        only so the retry log (``http._note_retry``, DEBUG) and the shard report can name *which*
        Board spent a retry or the IP supply — a scraper with no wall configured used to retry in
        total silence, indistinguishable in the log from one that never needed to. Grouping is
        still per ATS.

        ``marks_wall=False`` keeps the **routing** and drops only the **marking**: the request still
        rides the spare egress once the ATS is walled, but its own failures can never be what walls
        it. That is for a request whose non-200 means something other than "this IP is refused" —
        see Eightfold's API-availability probe (ADR-0063). Dropping the routing too would send it
        over the spent IP on exactly the shard the fallback exists to rescue.
        """
        if not self.egress_fallback_on:
            return {"egress_board": self.board_key()}
        return {
            "egress_group": self.ats,
            "egress_on": self.egress_fallback_on if marks_wall else frozenset(),
            "egress_board": self.board_key(),
        }

    def _get(self, url: str | None = None) -> str:
        """GET a board URL as text via the reliable-fetch seam (retry lives there). Defaults to
        ``self.url()``; pass an explicit ``url`` to fetch a secondary endpoint (e.g. Keka's careers
        page for the tenant id). Raises on a definitive HTTP error so a dead board surfaces as a
        per-company failure."""
        response = self._fetcher.fetch(
            "GET",
            url or self.url(),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/html",
            },
            timeout=30,
            **self._egress(),
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
        response = await self._fetcher.fetch_async(
            session,
            "GET",
            url or self.url(),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/html",
            },
            timeout=30,
            **self._egress(),
        )
        response.raise_for_status()
        return response.text

    def _fetch(
        self, method: str, url: str, *, marks_wall: bool = True, **kwargs: Any
    ) -> Any:
        """Raw request via the reliable-fetch seam, with this scraper's egress opt-in and board
        attribution always applied — the counterpart to :meth:`_get` for a caller that needs a
        non-GET method, custom headers/timeout, or the raw ``Response`` rather than parsed text.
        ``marks_wall`` passes straight through to :meth:`_egress`.
        """
        return self._fetcher.fetch(
            method, url, **self._egress(marks_wall=marks_wall), **kwargs
        )

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
        return await self._fetcher.fetch_async(
            session, method, url, **self._egress(marks_wall=marks_wall), **kwargs
        )

    def fetch_raw(self) -> Any:
        return json.loads(self._get())

    def board_page(self) -> str | None:
        """The page whose ``<title>`` carries this Board's company name, or None for an ATS with
        no such page. Overridden by the seven scrapers `headstart.company_name` has evidence for;
        everything else keeps serving its slug, exactly as before."""
        return None

    def resolve_company(self) -> None:
        """Replace the slug standing in for this Board's company with its real name, if we can.

        Called from :meth:`fetch`, not :meth:`parse`, because it makes a request and ``parse`` is
        pure — that split is what lets the parse tests run against recorded fixtures.

        One request per Board, never per Job, and every failure path leaves ``self.company``
        exactly as it was: no ``board_page``, a request that raises, a title this ATS's patterns
        cannot read. What that guarantees is narrower than "only ever an upgrade": a slug is never
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
            response = self._fetch(
                "GET",
                page,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                timeout=30,
                # One attempt, and it can never wall the ATS. A display name is the most
                # optional thing this scrape fetches, so it must not spend the retry ladder
                # (three attempts against a walled origin is ~90s for a Board) and its own
                # non-200 must not be what routes every other Board of that ATS onto the spare
                # egress — the reason eightfold's own probe already passes `marks_wall=False`.
                attempts=1,
                marks_wall=False,
            )
            html_text = response.text if response.status_code == 200 else None
        except Exception:  # noqa: BLE001 - a display name is never worth failing a Board for
            return
        name = company_name.from_title(
            self.ats, company_name.title_of(html_text), self.slug
        )
        if name:
            self.company = name

    def fetch(self) -> list[Job]:
        scraped_at = datetime.now(UTC).isoformat()
        raw = self.fetch_raw()
        self.resolve_company()
        return self.parse(raw, scraped_at)

    @staticmethod
    def fan_out(
        items: Sequence[_T],
        fn: Callable[[_T], _R],
        *,
        workers: int = 8,
        default: _R | None = None,
    ) -> list[_R | None]:
        """Apply ``fn`` to each item across a bounded thread pool, isolating per-item failures.

        Returns results aligned to ``items`` — input order, not completion order — where each
        entry is ``fn(item)`` or ``default`` if that call raised. One item's failure never sinks
        the batch: the detail passes are network-bound, so a single 404 or timeout must not drop
        the rest of the Board's Jobs. ``workers`` bounds the pool; a scraper hammering one
        rate-limited host passes a smaller value (trakstar uses 4 under DataDome).
        """
        results: list[_R | None] = [default] * len(items)
        if not items:
            return results
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fn, item): i for i, item in enumerate(items)}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception:  # noqa: BLE001 - one item's failure must not sink the batch
                    results[index] = default
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
        one the origin is still serving. :func:`~headstart.spare_egress.stream_width` clamps the
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
        # `_egress()` names the group this scraper's traffic is metered under, and is empty for one
        # that never opted in — so the clamp keys on exactly what the requests themselves carry
        # rather than a second guess at it that could drift from `egress_fallback_on`.
        concurrency = spare_egress.stream_width(
            self._egress().get("egress_group"), concurrency
        )
        # Recorded against the width in force, not the ceiling above it, so the clamp's two
        # operating points stay comparable (`headstart.fanout_stats`).
        with fanout_stats.batch(f"{self.ats} details", concurrency) as item_done:
            return asyncio.run(
                BaseScraper._gather_async(items, fn, concurrency, default, item_done)
            )

    @staticmethod
    async def _gather_async(
        items: Sequence[_T],
        fn: Callable[[Any, _T], Awaitable[_R]],
        concurrency: int,
        default: _R | None,
        item_done: Callable[[float], None],
    ) -> list[_R | None]:
        from curl_cffi.requests import AsyncSession

        sem = asyncio.Semaphore(concurrency)
        results: list[_R | None] = [default] * len(items)
        async with AsyncSession(impersonate="chrome") as session:

            async def one(index: int, item: _T) -> None:
                async with sem:
                    # Timed from inside the semaphore: the wait for a slot is queueing, and
                    # counting it as busy time would report every width as fully occupied.
                    started = time.monotonic()
                    try:
                        results[index] = await fn(session, item)
                    except Exception:  # noqa: BLE001 - one item's failure must not sink the batch
                        results[index] = default
                    finally:
                        item_done(time.monotonic() - started)

            await asyncio.gather(*(one(i, item) for i, item in enumerate(items)))
        return results

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

    @staticmethod
    def async_fanout_enabled() -> bool:
        """Whether the detail pass uses the multiplexed async path (ADR-0015, default per ADR-0016).

        On by default; set ``HEADSTART_ASYNC_FANOUT=0`` to fall back to the sync thread-pool path.
        Centralised here so every detail-fetch scraper shares one policy.
        """
        return os.environ.get("HEADSTART_ASYNC_FANOUT", "1") != "0"
