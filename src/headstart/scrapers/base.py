"""Base scraper: shared fetching plus the parse contract each ATS implements."""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.parse
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Container, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any, TypeVar

from headstart import fanout_stats, http, log, spare_egress
from headstart.models import Job

#: The one User-Agent every scraper sends. Public because nine of them re-declared
#: this same literal locally, which is a set of strings that can silently disagree.
USER_AGENT = "headstart/0.1 (job-board reader)"
_T = TypeVar("_T")
_R = TypeVar("_R")

# Default HTTP/2 multiplexing width (concurrent streams per host) for fan_out_async — 100 is around
# the common server MAX_CONCURRENT_STREAMS. Override per-call, via HEADSTART_H2_STREAMS, or
# run_scrapers --streams N. Read at call time (below) so a CLI flag can set the env before the scrape.
_DEFAULT_H2_STREAMS = 100


class BaseScraper(ABC):
    """Fetch one company's postings from an ATS and normalize them to Jobs.

    Network and parsing are split on purpose: ``parse`` is pure and is what the
    tests exercise against recorded fixtures, while ``_get`` is the only part that
    touches the network. JSON boards use the default ``fetch_raw`` (decode + parse);
    HTML boards (Zoho) override ``fetch_raw`` to keep the raw text.
    """

    ats: str  # set by each subclass

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

    def __init__(self, slug: str, company: str | None = None) -> None:
        self.slug = slug
        self.company = company or slug
        # Why this Board's list is incomplete, or None when it is whole (ADR-0053).
        #
        # A scraper that gives up mid-pagination and returns what it has is the flap's root cause:
        # it looks to `harvest` exactly like a Board that finished, so `index sync` reads the
        # missing postings as delisted and evicts them. Raising instead is not an option — the
        # partial Jobs are real and worth keeping — so the truncation travels beside them:
        # `scrape_all` reads this after a successful fetch and reports the Board as unfinished.
        self.truncated: str | None = None

    def mark_truncated(self, why: str) -> None:
        """Record ``why`` this Board's list came back short, keeping the *first* reason.

        A crawl that has already given up once tends to give up again, and the later reasons are
        consequences of the first — so the thing that cut it short is the one worth reporting.
        Every scraper that can detect its own truncation goes through here, so ``harvest`` reads
        one attribute and never learns how many ways a crawl can end (ADR-0053).
        """
        if self.truncated is None:
            self.truncated = why

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
        hit this. Today only SuccessFactors uses the default, and its slug is exactly the vanity
        host.

        None when the probe failed: an unreachable Board has earned no verdict, and
        ``board_aliases.resolve`` reports it rather than grouping it. Note ``http.fetch`` settles
        4xx/5xx rather than raising, so a Board whose own host answers 503 records itself, not
        None — which reads as "nothing points away from it" and leaves it unburied. That is the
        conservative direction: it can miss a duplicate, never invent one.

        Streamed and closed unread — only the redirect chain is wanted, and a SuccessFactors
        sitemap body runs to megabytes. Only the final host survives, not the chain that reached
        it, which is why the ledger records a destination rather than a route.
        """
        try:
            resp = http.fetch(
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

    def _egress(self, *, marks_wall: bool = True) -> dict[str, Any]:
        """``http.fetch`` kwargs opting this scraper into the spare-egress fallback, or ``{}``.

        Empty for every scraper that leaves :attr:`egress_fallback_on` unset, so the call it feeds
        is identical to the one made before this existed. Keyed on :attr:`ats` rather than the
        Board, because the metering that motivates it is per origin across all of an ATS's tenants.

        The Board rides along anyway as ``egress_board``: it steers nothing, and exists only so the
        shard report can name *which* Boards spent the IP supply. Grouping is still per ATS.

        ``marks_wall=False`` keeps the **routing** and drops only the **marking**: the request still
        rides the spare egress once the ATS is walled, but its own failures can never be what walls
        it. That is for a request whose non-200 means something other than "this IP is refused" —
        see Eightfold's API-availability probe (ADR-0063). Dropping the routing too would send it
        over the spent IP on exactly the shard the fallback exists to rescue.
        """
        if not self.egress_fallback_on:
            return {}
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
        response = http.fetch(
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
        response = await http.fetch_async(
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

    def fetch_raw(self) -> Any:
        return json.loads(self._get())

    def fetch(self) -> list[Job]:
        scraped_at = datetime.now(UTC).isoformat()
        return self.parse(self.fetch_raw(), scraped_at)

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
        the same numbers, so anything grepping them double-counts the Board."""
        missing = sum(1 for r in results if r is None)
        if missing:
            log.get(f"headstart.scrapers.{self.ats}").info(
                f"{self.board_key()}: {missing}/{len(results)} {what} missing"
            )
        return missing

    @staticmethod
    def async_fanout_enabled() -> bool:
        """Whether the detail pass uses the multiplexed async path (ADR-0015, default per ADR-0016).

        On by default; set ``HEADSTART_ASYNC_FANOUT=0`` to fall back to the sync thread-pool path.
        Centralised here so every detail-fetch scraper shares one policy.
        """
        return os.environ.get("HEADSTART_ASYNC_FANOUT", "1") != "0"
