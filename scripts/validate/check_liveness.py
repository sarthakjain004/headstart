#!/usr/bin/env python3
"""Liveness checker for ATS tenant lists — loss-free three-state, multi-pass, ledger-backed.

Reads the candidate pool (a dir of {ats}.csv) and classifies each board into one of three states,
so a transient blip never gets mistaken for a dead board:

  LIVE     -> 200 with a parseable job list
  DEAD     -> definitive: 404/410, or DNS doesn't resolve (curl code 6)
  UNKNOWN  -> couldn't tell: timeout, reset, 5xx, 429, parse-fail  -> re-probed next pass

Verdicts land in the liveness ledger (ADR-0012), one CSV per ATS at
data/validate/liveness/{ats}.csv (ats,tenant,url,status,jobs,checked_at) — the single source of
truth. The Active list is just its status==live rows (scrapable_boards.load); dead is
status==dead; still-unknown is status==unknown.

Incremental + fresh: a board is re-probed only when it's new or past its per-status TTL (live 7d /
dead 90d / unknown 3d, env-tunable via HEADSTART_LIVE_TTL_DAYS / HEADSTART_DEAD_TTL_DAYS /
HEADSTART_UNKNOWN_TTL_DAYS or the flags below) — so adding companies to the pool never re-checks
the whole thing, yet settled boards still refresh on a cadence (catching a live-but-empty board
that opens roles, or a live board that has since died). UNKNOWN is re-probed across increasingly
patient passes within a run; whatever is still unknown after the last pass is recorded as
status=unknown and re-probed after its (short) TTL, never silently dropped. The ledger is
rewritten after each pass, so a crash keeps the verdicts already settled.

Every non-settling outcome records *why* (timeout, connect-refused, http-429, bot-wall challenge,
body-unparseable, ...), reported per ATS at the end of each pass — see the failure-accounting
block below. Without it UNKNOWN is a black hole, and the responses differ completely: a 429 means
back off, a parse failure means the probe itself is wrong, a timeout means retry more patiently.

A confirmed bot-wall challenge (see block-triage in .claude/skills/ats-gap-search/resilience.md)
gets one more attempt through `cloudscraper` — the legacy-JS-challenge solver — before the host's
gate trips its cooldown; curl_cffi's Chrome TLS impersonation clears most walls alone, so this only
fires on the ones it doesn't. Optional (`pip install cloudscraper`), kept out of pyproject's base
dependencies so CI's quality job (base deps only) stays green — the checker degrades to the
original curl_cffi-only behaviour when it's absent.

A 429 that would otherwise ban a host for the rest of the run tries a different egress address
first (`_ban_or_rotate`), through `headstart.network.spare_egress` — the same Cloudflare-WARP fallback the
scrape uses (ADR-0063), which already carries the per-platform daemon recipe and the coalescing.
Ordinary 429s still just ease the pace; only the bottom rung rotates, and only when the refusal
came from the host we actually asked. Degrades to the old ban when WARP isn't reachable.

Run:   python scripts/validate/check_liveness.py                       # all ATSes, respect TTLs
       python scripts/validate/check_liveness.py zoho workday          # only these ATSes
       python scripts/validate/check_liveness.py --force               # re-probe everything
       python scripts/validate/check_liveness.py --live-ttl 3 --limit 20  # smoke
"""

import csv
import html
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from itertools import zip_longest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
from headstart.boards import (  # needs src on sys.path first
    alias_ledger,
    liveness_ledger,
)
from headstart.network import http, spare_egress
from headstart.scrapers import (
    adp_recruiting as _adp_recruiting,  # request shapes + headers, single source
)
from headstart.scrapers import (
    jibe as _jibe,  # robots.txt rule + crawl delay, single source
)
from headstart.scrapers.adp import (  # request shapes + envelope parsers, single source
    is_published,
    languages_of,
    listing_total,
    listing_url,
    locales_url,
)
from headstart.scrapers.avature import (  # a sitemap's JobDetail ids, single source
    listing_rows as avature_listing_rows,
)
from headstart.scrapers.clearcompany import (  # feed decode + req grouping, single source
    decode_hrm_bytes,
    feed_reqs,
)
from headstart.scrapers.cornerstone import (  # the site walk + token, single source
    CornerstoneScraper,
)
from headstart.scrapers.darwinbox import (  # the data-centre TLDs, single source
    TLDS as _DARWINBOX_TLDS,
)
from headstart.scrapers.jobvite import (  # counter parse, single source
    total_of,
)
from headstart.scrapers.lever import (  # the two instances, single source
    API_HOSTS as _LEVER_API_HOSTS,
)
from headstart.scrapers.lever import (
    EU_API_HOST as _LEVER_EU_API_HOST,
)
from headstart.scrapers.lever import (
    GLOBAL_API_HOST as _LEVER_GLOBAL_API_HOST,
)
from headstart.scrapers.oracle import (  # the pod-host spelling, single source
    is_pod_host,
)
from headstart.scrapers.radancy import (  # the job-URL shape, single source
    sitemap_rows as _radancy_sitemap_rows,
)
from headstart.scrapers.registry import (  # the row-to-Board funnel, per ATS
    SCRAPERS,
    company_from_row,
)
from headstart.scrapers.ripplehire import (  # the careers redirect's token, single source
    CAREERS_TOKEN as _RIPPLEHIRE_TOKEN,
)
from headstart.scrapers.taleo_be import (  # the next-ten-rows link, single source
    NEXT_PAGE_LINK as _TALEO_NEXT,
)
from headstart.scrapers.workday import (  # careers-URL parts + the DC list, single source
    CAREERS_URL_PATTERN as _WD_URL,
)
from headstart.scrapers.workday import (
    INSTANCES as _WD_INSTANCES,
)
from headstart.scrapers.zoho import (  # the listing's jobs <input>, single source
    JOBS_INPUT as _ZOHO_JOBS,
)
from headstart.scrapers.zwayam import (  # request shape + dead-vs-failed line, single source
    body_error_code,
    search_request,
)

try:  # bot-wall fallback (see _cloudscraper_fetch below) — optional, not a base dependency
    import cloudscraper
except ImportError:
    cloudscraper = None

UA = "HeadStart-liveness/0.1 (careers-board liveness check)"
TIMEOUT = 12  # reassigned per pass by the runner
_DNS_ERR = 6  # curl CURLE_COULDNT_RESOLVE_HOST -> host doesn't exist -> DEAD
# One attempt per probe: the multi-pass retry below IS the retry mechanism, so http.fetch's own
# 3-attempt backoff would just tie a worker up in sleep() instead of probing the next board. A
# transient failure becomes UNKNOWN here and gets a more patient re-probe on the next pass.
_ATTEMPTS = 1
# Concurrency. The work is network-bound and curl_cffi drops the GIL on every request, so hundreds of
# threads run truly concurrently across all cores; processes wouldn't help (the only GIL-bound bit is
# the small parse) and can't share the ledger write. Default to cores*24; override with
# LIVENESS_WORKERS=N (the ceiling is host rate-limiting, not the machine — over-cranking just turns
# into UNKNOWNs the patient retry passes mop up).
_CORES = os.cpu_count() or 8
_W = int(os.environ.get("LIVENESS_WORKERS") or _CORES * 24)
# (timeout, worker cap) per pass. Only the TIMEOUT escalates — a board that was merely congested gets
# a more patient retry each round. Every pass may use the full pool; workers are sized to the actual
# tail in the loop (min(cap, remaining)). The earlier //4 / //12 / 4 fractions throttled a big
# --force tail (a 51k-board pass 2 crawling on 108 workers) — so cap, don't cut.
PASSES = [(12, _W), (25, _W), (45, _W), (70, _W)]


# --- shared-host gating -------------------------------------------------------------------------
# Some ATSes route every tenant through one host, so probing them at pool concurrency is
# self-inflicted contention (docs/learnings.md 2026-07-03): api.lever.co and join.com just get
# slower (timeouts -> UNKNOWN), and apply.workable.com hard-429s (Cloudflare per-IP, ~20h
# retry-after) — one careless --force run blanked all 16k workable boards to UNKNOWN for a day.
# Two per-netloc mechanisms at the _get/_post seam:
#   gate     cap in-flight requests and space request starts (excess workers queue on the gate);
#   breaker  a 429 with a long Retry-After marks the host banned, and its remaining probes
#            short-circuit to a transient failure (-> UNKNOWN, re-probed a later run) instead of
#            re-extending the ban with thousands more requests. Never DEAD, by construction.
#   egress   before the breaker trips, try the refusal from a different IP (_ban_or_rotate).
#            A ban costs every board behind the gate for the rest of the run; a fresh address
#            costs seconds, and on a per-IP limiter it is the only thing that actually clears.
class _HostGate:
    def __init__(self, cap, spacing, key=""):
        self.sem = threading.BoundedSemaphore(cap)
        self.spacing = spacing  # min seconds between request *starts*
        # What `recover` returns `spacing` to. Easing is tuned to the IP that was refusing us, so
        # on a fresh one it throttles against a limit that no longer applies — but the seed was
        # measured against a healthy origin, so recovery goes back to it and not to no pacing.
        self._seed_spacing = spacing
        # Egress accounting. `_address` is what is carrying this gate (None = the direct route),
        # and it is what makes `recover` idempotent: a herd coalesced onto one rotation arrives
        # holding the same address and spends one of the allowance between them. `_rotations`
        # counts addresses refused *in a row*; `_recovered_at` is what lets an address that did
        # its job clear that count. See `recover`.
        self._address = None
        self._rotations = (
            0  # addresses this gate has been moved to — reported, never bounded
        )
        self._banned_at = 0.0  # when the standing ban was set — see `recover`
        self.key = key  # what this gate governs — a shared host, or one tenant's host
        self._lock = threading.Lock()
        self._next = 0.0
        # time.monotonic() deadline; breaker open while now < banned_until. Every read and write
        # of it goes through _lock — hundreds of threads share one gate.
        self._banned_until = 0.0

    def wait_turn(self):
        with self._lock:
            start = max(time.monotonic(), self._next)
            self._next = start + self.spacing
        delay = start - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def blocked(self):
        with self._lock:
            return time.monotonic() < self._banned_until

    def trip(self, seconds, why):
        """Open the breaker for at least `seconds`. Prints only on the unbanned -> banned edge.

        Two earlier versions of this each fixed one failure mode and broke the other:

        - Comparing a freshly-computed deadline (`now + seconds` vs the stored one) lets the
          longer of two *concurrent* candidates win regardless of arrival order — but wall-clock
          time keeps advancing between calls, so a *repeated* trip with the SAME duration always
          computes a slightly later deadline too. Every 429 landing while a ban was already live
          re-printed and re-extended it — one host under sustained refusal logged dozens of
          "banned us" lines in the same second (2026-08-14).
        - Checking only current status (`now < banned_until`) fixed that flood, but a short ban
          and a long ban racing from the SAME unbanned instant are then resolved by "whichever
          thread wins the lock", not by duration — a 5s `Retry-After` trip and a concurrent 1800s
          challenge trip on the same host could settle on 5s, silently dropping the 1800s signal.

        Correct because it does both at once: `until > self._banned_until` always keeps the max
        of every candidate seen (fixes the race — order never matters, only magnitude), while
        `was_banned` gates the print to once per ban *episode* rather than once per call (fixes
        the flood — a repeat trip while already banned can still extend silently, but never
        re-announces). A gate stays banned only as long as real failures keep arriving: nothing
        new triggers `trip` once `blocked()` is short-circuiting requests before they're sent.
        """
        with self._lock:
            now = time.monotonic()
            was_banned = now < self._banned_until
            until = now + seconds
            self._banned_until = max(self._banned_until, until)
            self._banned_at = now
            first = not was_banned
        if first:
            print(
                f"  [gate] {self.key} banned us ({why}) — every board behind it "
                f"short-circuits to UNKNOWN for {seconds}s",
                flush=True,
            )

    def ease(self):
        """Halve the request rate. Returns False when already at the floor.

        A hand-tuned cap can only be right for the load it was measured at; letting the host's own
        429s widen the spacing means we settle at its real ceiling. The return value is what tells
        the caller easing has stopped helping — see _on_429.
        """
        with self._lock:
            if self.spacing >= _MAX_SPACING:
                return False
            self.spacing = min(_MAX_SPACING, max(_MIN_SPACING, self.spacing * 2))
            rate = 1 / self.spacing
            # Printed under the lock so concurrent easings can't report out of order.
            print(f"  [gate] {self.key} 429 — easing to {rate:.1f} req/s", flush=True)
        return True

    def address(self):
        """What is carrying this gate — None while it is still on the direct route."""
        with self._lock:
            return self._address

    def recover(self, why, address, since):
        """A fresh egress address is carrying this gate now: drop any ban and un-ease.

        Both halves matter and neither is enough alone. Leaving the ban up would make the rotation
        pointless — every board behind the gate still short-circuits to UNKNOWN. Leaving the eased
        spacing would throttle the new address against the old one's exhausted quota: 21,428
        workable boards held at the 1 req/s floor is ~6h against ~1.5h at the seeded 4 req/s.

        **Idempotent per address.** `spare_egress` coalesces a herd of walled workers onto a
        single daemon restart, so they all arrive here holding the same address between them —
        and a restart need not even land a new one (measured on the first workable sweep: two
        rotations, `1 moved, 1 repeated`). Recovering once per *address* rather than once per
        caller is what keeps the log honest about how far the run has actually moved, and stops
        sixteen threads each clearing the ban and re-announcing it.

        `since` is when this rotation began, and only a ban older than that is cleared. An earlier
        draft cleared unconditionally, defending it as "the branches that ban for reasons no
        address can fix return before reaching here" — per-thread reasoning about an object dozens
        of threads share, and false on a spanning gate. `jobs.personio.de` carries every tenant, so
        a departed tenant's off-host trip and a live tenant's recovery land on it concurrently, and
        `_fetch`'s non-429 bot-wall trip races the same field. A ban set *after* the rotation
        started knows something the rotation does not, so it stands.
        """
        with self._lock:
            if address == self._address:
                return  # a peer already moved us here; one address, one charge
            self._address = address
            self._rotations += 1
            if self._banned_at <= since:
                self._banned_until = 0.0
            self.spacing = self._seed_spacing
            pace = f"{1 / self.spacing:.1f} req/s" if self.spacing else "unpaced"
            # Printed under the lock, as `ease` is, so concurrent recoveries can't interleave.
            print(
                f"  [gate] {self.key} on a fresh egress address "
                f"(#{self._rotations}, was {why}) — "
                f"ban cleared, back to {pace}",
                flush=True,
            )

    def rest(self, seconds):
        """Hold every worker on this gate for `seconds`, without dropping a single board.

        Deliberately **not** `trip`. The breaker is how a gate gives up: `_through_gate` sees
        `blocked()` and short-circuits each board to UNKNOWN without sending a request. Resting is
        the opposite — nothing is abandoned, the queue simply waits. Conflating them cost a whole
        sweep: a `trip(60, ...)` meant as a pause discarded 19,117 boards in 72 seconds, because
        every worker behind the gate took the short-circuit instead of the wait.

        Implemented by pushing the gate's next permitted start, which is the same field `wait_turn`
        already paces on — so workers sleep in the queue they were in anyway.
        """
        with self._lock:
            self._next = max(self._next, time.monotonic() + seconds)

    def egress_account(self):
        """`(addresses moved to, banned now)` — what the pass report says about this gate."""
        with self._lock:
            return self._rotations, time.monotonic() < self._banned_until


# A gate governs whatever shares one rate limit. Two shapes, and conflating them is a real hazard:
#
#   shared host   every tenant is served by ONE host (apply.workable.com) or sits under a domain
#                 the vendor rate-limits as a whole ({t}.recruitee.com, {t}.jobs.personio.de).
#                 The gate must span them, or each board backs off alone and the shared limit is
#                 never actually respected — measured 2026-08-14, personio answered 429 on all
#                 four passes and never once backed off.
#   per-tenant    the subdomain IS a separate customer instance, as with Workday's
#                 {tenant}.wdN.myworkdayjobs.com. One tenant's 429 says nothing about the others.
#
# So the SPANNING keys are an explicit, evidence-backed list, never inferred. An auto-gate (below)
# always keys on the exact netloc. Inferring the span by stripping a label looked tidy and was
# briefly live: it made one tenant's bot-wall 429 ban an entire Workday datacenter — `interoute`
# alone took out wd10, wd102, wd103, wd109, wd115, wd117, wd503 and wd504 in a single pass, which
# would have turned thousands of unrelated live boards UNKNOWN.
_SPANNING = (
    "recruitee.com",  # 44 429s in one measured pass, ungated
    "jobs.personio.de",
    "jobs.personio.com",
    # applytojob.com: every jazzhr tenant is `{slug}.applytojob.com` on one Cloudflare zone, and
    # the refusals really do span it — an ungated 18,299-board pass at 432 workers drew 12
    # refusals across **6 distinct tenants** under one-probe-per-tenant load. That is the shared
    # meter this list is for, and the opposite of Workday's per-datacenter case above. Leaving it
    # ungated writes false DEAD verdicts on real boards: that pass wrote 2,740 `dead` rows, and a
    # 10-tenant spot-check of live->dead flips found 6 of 10 still live (#463).
    #
    # The span is also the cost: one tenant's Cloudflare challenge now trips the gate for all
    # ~14.7k jazzhr rows, short-circuiting them to UNKNOWN. Accepted deliberately — UNKNOWN is
    # re-probed next run, whereas `dead` settles for DEAD_TTL_DAYS, so the failure this prevents
    # is the durable one. Full measurement: docs/jazzhr/2026-09-16_full-pool-measurement.md.
    "applytojob.com",
    # pinpointhq.com: every tenant is `{slug}.pinpointhq.com`, one origin. Measured 2026-09-23: a
    # burst of 256 concurrent requests over 800 distinct tenants drew 34 connection refusals, and
    # the refusal then held — the next 800 at concurrency 64 got 627, at 16 got 715 — clearing
    # after a few minutes. An ungated whole-pool pass (1,465 tenants at the default worker count)
    # drew 46 refusals and 71 timeouts. Paced, it is clean: 5, 10, 25 and 50 req/s for 60-120 s
    # each, zero refusals.
    "pinpointhq.com",
    # peoplestrong.com: every candidate portal is `{label}.peoplestrong.com` behind one Kong
    # gateway that meters 5,000 requests per calendar minute per client IP across every tenant
    # and endpoint — `X-RateLimit-Remaining-minute` fell across four different hosts, including an
    # invented one (2026-09-25). A refusal on one tenant is a refusal on all of them.
    "peoplestrong.com",
    # avature.net: every tenant is `{label}.avature.net`, and Avature's edge meters per client IP
    # across all of them — a ~350-request burst refilling at ~1.2 req/s (measured 2026-09-26: at 2
    # req/s the first refusal came at request 893, after 446 s). A spent budget answers 406 Not
    # Acceptable (a 172-byte nginx page) to every request on every tenant for ~3-4 minutes, while
    # a WARP address answers 200 meanwhile. `p_avature` reads that 406 as UNKNOWN and rests or
    # rotates this gate; it never settles a Board.
    "avature.net",
)
_GATES = {
    # host: (max in-flight, seconds between request starts)
    "apply.workable.com": _HostGate(8, 0.25, "apply.workable.com"),  # CF per-IP ban
    "api.lever.co": _HostGate(
        16, 0.0, "api.lever.co"
    ),  # p50 1.6s->9.2s at 8->120 workers
    "api.eu.lever.co": _HostGate(16, 0.0, "api.eu.lever.co"),
    "join.com": _HostGate(16, 0.05, "join.com"),  # 56 429s in one measured pass at 0.0
    "recruitee.com": _HostGate(16, 0.05, "recruitee.com"),
    # Personio's boards sit behind Vercel's bot wall, which answers 429 with a "Security
    # Checkpoint" interstitial and no Retry-After. Verified 2026-08-14 that it still fires at ONE
    # request every 3 seconds, so this pacing is about staying out of the wall's reputation
    # window, not about throughput — the wall is not a request-rate limit we can simply out-wait.
    "jobs.personio.de": _HostGate(16, 0.05, "jobs.personio.de"),
    "jobs.personio.com": _HostGate(16, 0.05, "jobs.personio.com"),
    # 16 is the width at which a whole-pool re-probe is known to settle: a full 18,299-board pass
    # at it drew zero refusals (see `_SPANNING` above for what 432 drew). It is not the throughput
    # ceiling — 350 distinct tenants at concurrency 200 came back clean — so this is sized for the
    # sustained whole-pool case, not for the wall. No spacing: the clean pass used none.
    "applytojob.com": _HostGate(16, 0.0, "applytojob.com"),
    # ADP Workforce Now: one fixed host, and F5 BigIP refuses the 201st request in a fixed
    # 60-second window across every tenant, with a bare 429 and no Retry-After (measured
    # 2026-09-23: rested runs at 5 and 8 req/s refused on exactly #201; 450 at 3 req/s clean).
    # The auto-gate would find it only by being refused, at 10 req/s; this starts at 2.5 req/s,
    # the scraper's own pacing. Not in `_SPANNING`: the host is exact, so the key already is.
    "workforcenow.adp.com": _HostGate(16, 0.4, "workforcenow.adp.com"),
    # pinpointhq.com: sized like applytojob.com above. Paced load across distinct tenants ran clean
    # at 50 req/s for 60 s, but a 256-wide burst drew connection refusals that then held against
    # every tenant for minutes (see `_SPANNING`).
    "pinpointhq.com": _HostGate(16, 0.0, "pinpointhq.com"),
    # peoplestrong.com: 50 req/s is 3,000 a minute, under the 5,000 the gateway allows per IP
    # across tenants (see `_SPANNING`); 10,555 requests at 81 req/s on average ran clean, so this
    # leaves room for a scrape sharing the address.
    "peoplestrong.com": _HostGate(16, 0.02, "peoplestrong.com"),
    # avature.net: 1 req/s, under the ~1.2 req/s refill (see `_SPANNING`), so a whole-pool pass
    # never spends the burst and loses every tenant for minutes. It makes a pass slow — a tenant
    # costs a request per portal sitemap and locale, ~9 on average over 25 measured — and that is
    # the price of never reading a 406.
    "avature.net": _HostGate(16, 1.0, "avature.net"),
    # `jobs.jobvite.com` has no entry on purpose: one fixed host rather than a subdomain per
    # tenant, so the auto-gate below already keys it exactly, and it drew zero refusals even at
    # 432. A seeded gate for it would be configuration with no measurement behind it.
}
_gates_lock = threading.Lock()
# Auto-gate defaults for a host that starts refusing without a seeded entry, and the bounds
# ease() moves between (50 req/s down to 1 req/s, where it stops easing and trips instead).
_AUTO_CAP, _AUTO_SPACING = 16, 0.1
_MIN_SPACING, _MAX_SPACING = 0.02, 1.0
#: How long a gate rests when rotation cannot produce an address it is not already on. Sized to
#: the measured refill: `apply.workable.com` answered 200 again within a minute of load stopping,
#: twice. Short enough that the run keeps moving, long enough that the address it returns to has
#: something left. Reached mainly on IPv4-only hosts now — see `_ban_or_rotate` and ADR-0092.
_EGRESS_REST_S = 60

#: There is deliberately **no cap on how many addresses a gate may be refused from.** An earlier
#: version stopped after three and banned, reasoning that a host still refusing from a third is
#: refusing us rather than the address. The first real sweep measured what that costs: workable
#: spent its three inside the first ~300 boards and the ban then short-circuited **20,916** of the
#: remaining 21,228 without a request being sent. Rotating on is degraded throughput; banning is
#: total loss of everything behind the gate, and the two are not close.
#:
#: The bound is `spare_egress`'s own rotation cooldown, which is a real one: at one restart per
#: five seconds a gate cannot spend the run bouncing the daemon, and between restarts it keeps
#: probing at its own pace. Nothing else is needed, which is the point — the cap needed a credit
#: rule to stay usable across a long run, the credit rule needed to be earned by answered traffic
#: rather than elapsed time to resist laundering, and none of that machinery has to exist.


def _spanning_key(netloc):
    """The listed domain this host sits under, if any — never inferred, only matched."""
    return next(
        (d for d in _SPANNING if netloc == d or netloc.endswith("." + d)),
        None,
    )


def _gate_key(netloc):
    """What a gate for this host is — or would be — filed under: the listed domain it sits under,
    else the exact host.

    One function because three things must agree on it: the gate lookup, the auto-gate that
    creates one, and the spare-egress group the same boards are routed under. A key computed
    separately in each place is a key that can drift, and a drifted egress group would route one
    spelling of the host while walling another.
    """
    return _spanning_key(netloc) or netloc


def _gate_for(netloc):
    """The gate governing this host, or None if it has never pushed back."""
    with _gates_lock:
        return _GATES.get(_gate_key(netloc))


def _ensure_gate(netloc, why):
    """A host that starts refusing with no gate gets one, filed under its `_gate_key`.

    Never *inferred* wider than the exact host: an unlisted host might be one tenant among
    thousands on a shared suffix (Workday), and throttling — or worse, banning — all of them off
    one tenant's refusal costs far more coverage than the extra requests ever save. A `_SPANNING`
    entry is not an inference, so a host under one is filed under that domain, which is the whole
    point of listing it.

    Storing under the key it was looked up by is what keeps the three views of a gate consistent —
    lookup, creation, and the egress group `_fetch` routes it under. Storing under `netloc` while
    looking up `_gate_key(netloc)` meant a `_SPANNING` domain with no seeded gate would rebuild a
    fresh gate on every request (pacing, ban and egress state lost each time) and route under a key
    that never matched. Latent only because all three listed domains are seeded today.
    """
    with _gates_lock:
        key = _gate_key(netloc)
        gate = _GATES.get(key)
        if gate is None:
            gate = _GATES[key] = _HostGate(_AUTO_CAP, _AUTO_SPACING, key)
            print(
                f"  [gate] {key} answered {why} unprompted — throttling it to "
                f"{1 / _AUTO_SPACING:.0f} req/s for the rest of the run",
                flush=True,
            )
        return gate


_BAN_RETRY_AFTER_S = (
    60  # a Retry-After beyond this is a ban, not per-request throttling
)
_CHALLENGE_COOLDOWN_S = 1800
# A bot wall does not only speak 429. Cloudflare serves "Just a moment..." behind 403 and 503, and
# only its 429s carry the cf-mitigated header — so a status-429-only check would miss two of the
# three markers we look for and file the response as a plain http-403. These are the statuses worth
# reading the body of; a wall behind any of them means stop, not retry.
_CHALLENGE_STATUSES = (403, 429, 503)


# --- why a probe didn't settle -------------------------------------------------------------------
# UNKNOWN is the checker's catch-all, and it was a black hole: a timeout, a 429, a bot-wall 403 and
# a JSON parse failure all landed in the same bucket, so a run that came back 81% unknown gave no
# clue which. The responses differ completely — a 429 means back off, a parse failure means the
# probe itself is wrong, a timeout means retry more patiently — so every non-settling outcome now
# records a reason, reported per ATS at the end of each pass.
_ctx = threading.local()  # the ATS the calling worker is probing
_reasons = Counter()  # (ats, reason) -> count
_reasons_lock = threading.Lock()
_cs_cleared = Counter()  # ats -> count of challenges cloudscraper cleared this pass

# curl error codes worth telling apart (libcurl's own numbering).
_CURL = {
    6: "dns",
    7: "connect-refused",
    28: "timeout",
    35: "tls-error",
    52: "empty-reply",
    56: "recv-error",
    60: "tls-cert",
}


def _note(reason):
    """Record why a probe didn't settle, against the ATS the worker is on."""
    with _reasons_lock:
        _reasons[(getattr(_ctx, "ats", "?"), reason)] += 1


def _note_cs_cleared():
    """Record that cloudscraper cleared a wall curl_cffi couldn't — a save, not a failure, so it
    is tracked separately from `_note` and never counted against the failed-requests report."""
    with _reasons_lock:
        _cs_cleared[getattr(_ctx, "ats", "?")] += 1


def _net_reason(exc):
    code = getattr(exc, "code", None)
    return _CURL.get(code, f"curl-{code}" if code else f"exc-{type(exc).__name__}")


def _report_reasons(unknown_boards):
    """Print the pass's failed *requests*, worst ATS first — empty when nothing failed.

    These count requests, not boards, and the two differ a lot: ``p_workday`` sweeps every
    datacenter, so one board can contribute ~30 notes, and a board that settles LIVE on its
    fourth DC still notes the three that refused. So the totals here routinely exceed the number
    of boards left unknown, and the header says which is which rather than implying they match.
    """
    with _reasons_lock:
        snapshot = dict(_reasons)
        _reasons.clear()
    if not snapshot:
        return
    per_ats = Counter()
    for (ats, _), n in snapshot.items():
        per_ats[ats] += n
    print(
        f"  failed requests this pass ({unknown_boards} boards ended unknown):",
        flush=True,
    )
    for ats, total in per_ats.most_common():
        detail = ", ".join(
            f"{reason}={n}"
            for (a, reason), n in sorted(snapshot.items(), key=lambda kv: -kv[1])
            if a == ats
        )
        print(f"    {ats:<16} {total:>6}  {detail}", flush=True)


def _report_cs_cleared():
    """Print how many bot-wall challenges cloudscraper cleared this pass, worst ATS first —
    silent when it never fired (not installed, or curl_cffi never hit a wall)."""
    with _reasons_lock:
        snapshot = dict(_cs_cleared)
        _cs_cleared.clear()
    if not snapshot:
        return
    detail = ", ".join(f"{ats}={n}" for ats, n in Counter(snapshot).most_common())
    print(f"  cloudscraper cleared a wall: {detail}", flush=True)


def _report_egress():
    """Print the spare egress's own account — silent when no gate ever needed one.

    Cumulative, not per-pass, and labelled so: a gate walled in pass 1 must stay routed in pass 4,
    so the state behind these numbers is process-global by design and resetting it between passes
    would cost the routing to make the report tidier.

    Deliberately **not** `spare_egress.report()`. That function's headline is
    `rescued / (rescued + walled)`, and `walled` counts settles whose status is in the caller's
    `egress_on` — empty here (see `_EGRESS_ON`), so the denominator can never grow and the rate
    reads 100% however badly rotation is going. A number that cannot come out low is not a
    measurement. The two that *are* falsifiable get printed instead: how many distinct addresses
    the run was actually given, and, per gate, how many refused it in a row and whether it ended
    up banned regardless — which is the outcome that matters.
    """
    spins = spare_egress.rotations()
    addresses = spare_egress.egress_ips()
    seen = {k[3:] for k in addresses if k.startswith("ip:")}
    if spins:
        print(
            "  [egress] rotations: "
            + ", ".join(f"{why} {n:,}" for why, n in sorted(spins.items()) if n),
            flush=True,
        )
    if seen:
        print(
            f"  [egress] {len(seen)} distinct address(es) across "
            f"{addresses['moved'] + addresses['repeat']} comparison(s) "
            f"({addresses['moved']} moved, {addresses['repeat']} repeated): "
            + ", ".join(sorted(seen)),
            flush=True,
        )
    with _gates_lock:
        gates = list(_GATES.values())
    for gate in gates:
        spent, banned = gate.egress_account()
        if not spent:
            continue
        print(
            f"  [egress] {gate.key}: moved across {spent} address(es); "
            + ("banned anyway" if banned else "probing"),
            flush=True,
        )


def _retry_after_s(value):
    """Retry-After header -> seconds (int form or HTTP-date form), None if absent/garbled."""
    if not value:
        return None
    try:
        return int(value.strip())
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        return max(0, int(parsedate_to_datetime(value).timestamp() - time.time()))
    except Exception:  # noqa: BLE001
        return None


def _through_gate(gate, fn):
    """Run `fn()` under `gate`'s discipline (in-flight cap + spacing), or ungated if `gate` is
    None. Returns None without calling `fn` when the gate's breaker is open — the same
    short-circuit `_fetch` already applies to its primary request, shared here with the
    cloudscraper fallback so it can never burst a walled host outside the gate's pacing.
    """
    if gate is None:
        return fn()
    if gate.blocked():
        return None
    with gate.sem:
        gate.wait_turn()
        return fn()


def _cs_session():
    """This thread's cloudscraper session — created once and reused, mirroring http.session()'s
    per-thread curl_cffi pool (a session isn't safe to share across threads)."""
    s = getattr(_ctx, "cs_session", None)
    if s is None:
        s = cloudscraper.create_scraper()
        _ctx.cs_session = s
    return s


def _cloudscraper_fetch(method, url, **kw):
    """One request through cloudscraper's legacy-JS-challenge solver — tried only after curl_cffi's
    Chrome impersonation has already hit a confirmed bot wall (resilience.md: "curl_cffi, then
    cloudscraper"). Returns the response, or None on any failure, so the caller falls back to the
    original walled response and its normal breaker handling.

    Strips our own User-Agent (it just announces "HeadStart-liveness" to the wall) so
    cloudscraper's browser-consistent UA/TLS pairing — the thing that actually lets it solve the
    challenge — isn't overridden by a request-level header.
    """
    headers = {
        k: v
        for k, v in (kw.pop("headers", None) or {}).items()
        if k.lower() != "user-agent"
    }
    try:
        return _cs_session().request(
            method, url, timeout=TIMEOUT, verify=False, headers=headers or None, **kw
        )
    except Exception as e:  # noqa: BLE001
        _note(f"cloudscraper-{type(e).__name__}")
        return None


#: Empty on purpose, and load-bearing rather than a default. `http.fetch` reads `egress_group` to
#: decide *routing* (send this request over the spare egress once its group is walled) and
#: `egress_on` to decide *marking* (these statuses, seen here, are what walls the group). Naming a
#: group with no marking statuses takes the first and declines the second — the shape ADR-0063
#: gives Eightfold's availability probe. The checker wants exactly that: a 429 here is not on its
#: own evidence that our IP is spent (`_ban_or_rotate` has the reasons), so the decision to wall a
#: gate stays in the ladder below where the Retry-After, the challenge markers and the redirect
#: chain are all in view, and never fires from inside a single request.
_EGRESS_ON = frozenset()


def _fetch(method, url, **kw):
    """One request, paced by its host's gate. Returns the response, or None when the host's
    circuit breaker is open (callers treat None as a transient failure -> UNKNOWN).

    An ungated host still runs at full concurrency — but its 429, if one comes, now reaches the
    gate logic and creates a gate, so the limit is discovered rather than hand-maintained.

    A confirmed bot-wall challenge gets one more attempt through cloudscraper (still under the
    same gate) before the breaker trips: if it clears the wall, that response is returned and the
    gate is left alone — there is no wall left to cool down from. If cloudscraper can't clear it
    either (not installed, or itself walled), this falls through to the original trip-the-breaker
    behaviour, so the checker degrades gracefully whether cloudscraper is present or effective.
    """
    netloc = urllib.parse.urlsplit(url).netloc
    gate = _gate_for(netloc)
    key = _gate_key(netloc)
    r = _through_gate(
        gate,
        lambda: http.fetch(
            method,
            url,
            timeout=TIMEOUT,
            verify=False,
            attempts=_ATTEMPTS,
            egress_group=key,
            egress_on=_EGRESS_ON,
            **kw,
        ),
    )
    if r is None:
        return None
    challenged = r.status_code in _CHALLENGE_STATUSES and _is_challenge(r)
    if challenged and cloudscraper is not None:
        cs = _through_gate(gate, lambda: _cloudscraper_fetch(method, url, **kw))
        if cs is not None and not (
            cs.status_code in _CHALLENGE_STATUSES and _is_challenge(cs)
        ):
            _note_cs_cleared()
            return cs
    if r.status_code == 429:
        _on_429(gate or _ensure_gate(netloc, "429"), r, url)
    elif _is_quota_403(netloc, r) and not challenged:
        _on_quota_403(gate or _ensure_gate(netloc, "403"), r, url)
    elif challenged:
        _note(f"challenge-{r.status_code}")
        (gate or _ensure_gate(netloc, str(r.status_code))).trip(
            _CHALLENGE_COOLDOWN_S, f"{r.status_code}, bot-wall challenge"
        )
    return r


# Hosts that meter **per IP over a window** and refuse with a bare 403 — no `Retry-After`, no
# `cf-mitigated`, no interstitial body, so neither `_on_429` nor `_is_challenge` sees them and the
# refusal used to fall straight through to UNKNOWN with no gate trip and no rotation.
#
# Keyed by `_gate_key`, which is also the spare-egress group, so one entry covers every board on
# the host. zwayam qualifies on both counts: every tenant is probed through the one shared API
# (`search_request` sends `careers.infoedge.com`, `adani.openings.co` and `careers.practo.com`
# alike to `public.zwayam.com`), and the wall is a cumulative request quota, not a concurrency
# limit.
#
# The entry is the **exact host**, because `zwayam.com` is not in `_SPANNING` and `_gate_key`
# therefore returns `public.zwayam.com` unchanged. Writing the registrable domain here instead
# looks right and silently never matches — `test_the_quota_403_key_matches_the_gate_key` pins it.
#
# Read off the scraper's own request rather than spelled out, for the reason `p_zwayam` already
# imports `search_request`: a hardcoded copy is a copy that can drift, and this module has drifted
# on exactly that before (the User-Agent). `search_request` builds a request without sending one,
# so asking it where a probe would go is free, and if zwayam ever moves the API this follows it.
#
# Measured 2026-09-17, one IP, 16-wide (`experiment/zwayam-403-wall/LOG.md`):
#   - 100/200/300/400 cumulative requests -> 200 on every one;
#   - at 500 -> 23 of 100 refused; at 600 -> 100 of 100 refused. The wall is volume, not width.
#   - Against 25 slugs that had *just* been refused, interleaved: **WARP cleared 25/25 while the
#     direct route cleared 12/25.** Rotation is what clears it.
# Before this, a full 3,239-board sweep walled partway through its first quartile and returned
# 2,816 UNKNOWN — quartiles 2-4 were 809/809 unknown each.
_QUOTA_403 = frozenset({urllib.parse.urlsplit(search_request("probe")[0]).netloc})


# A bot wall's interstitial, served as 429. Cloudflare labels its own with a header; Vercel's
# "Security Checkpoint" (which fronts every personio board) carries no header at all, so the page
# body is the only signal. Verified 2026-08-14 that it still fires at one request per 3 seconds —
# a wall is not a rate limit, so easing the pace never clears it. Trip straight to a cooldown
# instead of spending five halvings and a few hundred requests learning that.
_CHALLENGE_BODIES = (
    b"Vercel Security Checkpoint",
    b"Just a moment...",
    b"cf-browser-verification",
)


def _is_challenge(r):
    if r.headers.get("cf-mitigated") == "challenge":
        return True
    body = r.content[:4096] if r.content else b""
    return any(m in body for m in _CHALLENGE_BODIES)


def _is_quota_403(netloc, r):
    """Is this the bare 403 of a host that meters per IP? See `_QUOTA_403`.

    "Bare" is the caller's job: `_fetch` tests `not challenged` alongside this, so a genuine
    bot-wall 403 from a metered host still takes the challenge arm and still logs
    `challenge-403`. Without that, the rung would shadow it and mislabel the mechanism in the
    `_note` counters — which is the whole thing this change exists to name correctly.
    """
    return r.status_code == 403 and _gate_key(netloc) in _QUOTA_403


def _on_quota_403(gate, r, url):
    """Move this gate to a different address; ban only if none can be had.

    The same rung `_on_429` reaches for a ban-length Retry-After, for the same reason — the limit
    *is* the address, so easing the pace cannot clear it — but reached directly, because a quota
    403 carries none of the signals the 429 ladder reads: no Retry-After to measure, and no
    challenge markers (`_is_challenge` returns False for it on purpose, and must keep doing so, or
    an ordinary "forbidden" would start rotating too).
    """
    _note("403-quota")
    # `_EGRESS_REST_S`, not `_CHALLENGE_COOLDOWN_S`: this only matters on the fall-through, where
    # no spare egress can be had, and then the honest wait is the quota's own refill. A bot wall
    # needs the 30-minute cooldown because nothing but time clears it; a quota refills in about a
    # minute of quiet — the same measurement `_EGRESS_REST_S` is already sized to.
    _ban_or_rotate(gate, r, url, _EGRESS_REST_S, "403, per-IP quota spent")


def _on_429(gate, r, url):
    """Back off this host: ban outright when it tells us to, else ease the rate and keep going.

    The last branch used to trip on a run of *consecutive* 429s. That cannot work on a gate many
    boards share: any one of hundreds of threads getting a 200 reset the counter, so reaching the
    threshold depended on luck — measured, 1,800 rejections in 2,000 requests left the counter at
    5. Easing to the floor is the honest signal instead: if we are already as slow as this gate
    goes and the host still refuses, slowing further cannot help, so stop asking.

    Every branch that would ban now offers the refusal a different IP first (`_ban_or_rotate`).
    Nothing about the pacing ladder changes — an ordinary 429 still eases, because easing is what
    a real rate limit responds to and rotation is not free. What changes is the bottom rung: a ban
    costs every board behind the gate for the rest of the run, and where the limiter is keyed on
    our address that price buys nothing a fresh address would not have avoided.
    """
    retry = _retry_after_s(r.headers.get("Retry-After"))
    if retry and retry > _BAN_RETRY_AFTER_S:
        _ban_or_rotate(gate, r, url, retry, f"429, retry-after {retry}s")
    elif _is_challenge(r):
        _ban_or_rotate(gate, r, url, _CHALLENGE_COOLDOWN_S, "429, bot-wall challenge")
    elif not gate.ease():
        _ban_or_rotate(
            gate,
            r,
            url,
            _CHALLENGE_COOLDOWN_S,
            f"still refusing at {1 / _MAX_SPACING:.1f} req/s",
        )


def _redirected_off_host(url, r):
    """Whether the refusal came from a host we were redirected to rather than the one we asked.

    A 429 served by another property is not this board's rate limiter and no egress IP touches it.
    Measured 2026-08-27: personio boards left `unknown` in the ledger 307 off
    `{tenant}.jobs.personio.de/xml` to `personio.com`, whose wall answered 429 over the direct
    route and over a WARP address alike — the dead-tenant tombstone of
    `docs/personio/2026-08-26_the-429-is-a-dead-tenant-tombstone.md`, and the false premise that
    got PR #312's rotate-on-429 reverted. That ledger holds 5,178 such rows, so without this guard
    the first `--force` personio pass would spend itself restarting the tunnel against boards no
    address can reach.

    Compares the settled `url` — curl_cffi reports the *final* hop — against what was asked for.
    A response with no readable url is treated as same-host: the guard exists to stop a rotation
    that cannot help, and refusing to rotate on missing evidence would instead stop ones that can.
    """
    final = getattr(r, "url", None)
    if not final:
        return False
    return urllib.parse.urlsplit(final).netloc != urllib.parse.urlsplit(url).netloc


def _addresses_seen():
    """How many distinct egress addresses this run has actually been given.

    The honest identity for the address a gate is on, and the one thing that tells "we moved" apart
    from "the daemon restarted onto the same IP". An address the tunnel could not report leaves
    this unchanged, so a genuinely new one occasionally goes uncharged — the safe direction to be
    wrong in, costing one extra rotation rather than an early ban.
    """
    return sum(1 for key in spare_egress.egress_ips() if key.startswith("ip:"))


def _fresh_egress(gate, status=429):
    """Move this gate onto a different egress address. Its identity, or None if none can be had.

    Two rungs, the same pair `http.fetch` climbs: a gate still on the direct route is *moved onto*
    the spare egress, which is already a different address; one refused there rotates the tunnel to
    another. `headstart.network.spare_egress` owns both, and owning them is the point — the daemon recipe
    (`launchctl kickstart -k` on macOS, `systemctl restart warp-svc` on Linux, each under
    `sudo -n`), the SOCKS5 readiness handshake, the cooldown, and the coalescing that keeps
    hundreds of liveness workers meeting one wall to a single restart are all already solved there.

    The rung is chosen from **this gate's** recorded address, not from whether a proxy exists. Read
    the other way round, every peer arriving just after the first thread dialled would see a proxy
    and take the rotate branch — bouncing the daemon, and severing its own in-flight requests, on
    an address that had not yet carried a single request.

    The identity returned counts **distinct addresses observed**, never rotations. Those differ: a
    daemon restart need not land a different IP, and the first real workable sweep rotated twice
    for `1 moved, 1 repeated`, so keying on the rotation counter charged the allowance for an
    address the run never got. Process-global on purpose — a rotation moves every group at once, so
    a gate whose address changed underneath it really is on a new one. `None` stands for the direct
    route, so the first dial always reads as a change.

    WARP is dialled *before* the group is marked walled, so a machine without it is never recorded
    as having spent an egress it never had. Every failure returns None rather than raising, and the
    caller then bans exactly as it did before this existed.
    """
    if gate.address() is not None:
        return _addresses_seen() if spare_egress.rotate(gate.key) else None
    if (
        spare_egress.proxy_url() is None
    ):  # no WARP here — cached, so only the first caller waits
        return None
    spare_egress.mark_walled(gate.key, status)
    return _addresses_seen()


def _ban_or_rotate(gate, r, url, seconds, why):
    """Try the refusal from a different address; ban the host only when none can be had.

    Two ways to end up banning anyway, and each is a case where an address cannot be the answer:
    the 429 came from a host we were redirected to, or no spare egress could be raised at all.
    Both fall through to the exact `trip` the ladder performed before, so the worst case here is
    the old behaviour plus one bounded attempt at a fresh address.

    There is no third arm and no allowance — see the block above `_HostGate`. A gate that keeps
    being refused keeps being moved, for as long as the refusals continue, because the ban it
    would otherwise take costs every board behind it and a rotation costs seconds.
    """
    if _redirected_off_host(url, r):
        _note(f"{r.status_code}-off-host")
        gate.trip(seconds, f"{why}, from a redirect off-host")
        return
    # Bans older than this are about the address we are leaving, so `recover` may clear them.
    since = time.monotonic()
    was = gate.address()
    address = _fresh_egress(gate, r.status_code)
    if address is None:
        gate.trip(seconds, why)
        return
    if address == was:
        # The daemon restarted and Cloudflare handed back the address we were already on, so
        # there is nothing to recover onto. Resting beats spinning: a spent address answers 200
        # again within a minute of going quiet, so the supply is time, and a restart that changes
        # nothing still costs ~7s of gate downtime.
        #
        # **This arm is inverted, not dead, and that is a known defect — see ADR-0092.**
        # `_addresses_seen()` counts distinct addresses `_observe_egress_ip()` has ever recorded,
        # and that traces `www.cloudflare.com`, which publishes AAAA. Since ADR-0092 put the spare
        # egress on `socks5h` the trace egresses IPv6 and almost always reports an address not seen
        # before, so the count moves and `address == was` almost never holds — 70 rotations, 70
        # distinct addresses, 0 rests on the sweep that prompted this.
        #
        # It does still fire, just for the wrong reasons: an unreadable trace records no `ip:` key
        # at all, and a recurrence of any previously-seen address leaves the count flat. Both mean
        # the surviving firings skew towards false positives — resting a gate that actually moved.
        # And the counter was already partly blind before `socks5h`, being process-global: a peer
        # gate's new address hides this gate's repeat.
        #
        # The cost of a *missed* rest is not the ~7s rotation. `recover()` also resets
        # `spacing` to the seed, and the branch that reaches here most often is `elif not
        # gate.ease()` — i.e. the gate had already eased to the 1 req/s floor. So on an IPv4-only
        # host (`api.lever.co`, Workday, Greenhouse — none publishes AAAA) a missed rest throws
        # the backoff away and returns to 4 req/s on an address the host just refused, which is
        # precisely the spin this was written to stop.
        #
        # A per-gate "consecutive rotations with no answered request" signal would fix it
        # generally. So would something much smaller: one cached AAAA lookup per gate, since a
        # host with no AAAA provably cannot have its egress moved by rotation, so it should rest
        # unconditionally. Neither is written yet.
        _note(f"{r.status_code}-same-address")
        gate.rest(_EGRESS_REST_S)
        return
    gate.recover(why, address, since)


LIVE, DEAD, UNKNOWN = "live", "dead", "unknown"

# Non-production boards are dead by convention, however alive their endpoint (ADR-0034).
# Probing cannot tell fabricated jobs from real ones — sandbox tenants 200 with
# production-looking counts (measured: eightfold amdocs-sandbox 2,099 "jobs" vs the real
# board's 5; citigroup-qa-sandbox 3,193; ripplehire hdfcbank-uat 28,886 vs 173) — so the
# HOST NAME is the signal: sandbox/uat/demo as a delimited token in tenant or url. Token-
# bounded on purpose: one-word names like sandboxvr or thesandbox must not match. A trailing
# instance number is still the token: `nvidia-sandbox2` mirrors jobs.nvidia.com (2,376 of its
# 2,408 title+location slugs, measured 2026-09-23), and `uat2` is RippleHire's own UAT tenant.
_NONPROD = re.compile(r"(?:^|[-./_])(?:sandbox|uat|demo)\d*(?:[-./_]|$)", re.IGNORECASE)
# Oracle names a tenant's non-production pods after its production one — `jpmc-dev9`, `jpmc-test`,
# `fa-exuf-test-saasfaprod1` — so `test`/`dev` are the vendor's environment names there, safe to
# read as tokens. Nowhere else: off Oracle they are customer names (`ashby:convex-dev`,
# `recruitee:test1234`). Measured 2026-09-23 on 15 pods: 86 of 328 sampled ids exist on the prod
# pod (a clone); the rest are closed there or synthetic ("Software Engineer 092 - enable auto
# approval for testing"). Neither is a Board of its own.
_ORACLE_NONPROD = re.compile(
    r"-(?:test|dev)\d*[-.][^/]*\.oraclecloud\.com", re.IGNORECASE
)
# Real companies whose *names* collide with the tokens (found by eyeballing every ledger
# match before the convention landed). A company here can still have a nonprod board — the
# exception is exact-tenant, not a pattern.
_NONPROD_EXCEPTIONS = {
    "sandbox-interactive-gmbh",  # Sandbox Interactive GmbH (Albion Online) — personio
    "demo-duck",  # Demo Duck, video production agency — workable
}
# The inverse list: KNOWN nonprod tenants the token rule cannot see because the marker is
# concatenated into the name — widening the regex to catch these would re-admit the
# sandboxvr/thesandbox false positives, so they are named exactly instead.
_NONPROD_TENANTS = {
    "stldemo",  # ripplehire — STL (Sterlite Technologies) demo instance, 137 mirrored jobs
}


def is_nonprod(tenant: str, url: str) -> bool:
    """A sandbox/UAT/demo board — marked dead before any probe is spent (ADR-0034)."""
    if (tenant or "") in _NONPROD_EXCEPTIONS:
        return False
    if (tenant or "") in _NONPROD_TENANTS:
        return True
    return any(
        p.search(s or "") for p in (_NONPROD, _ORACLE_NONPROD) for s in (tenant, url)
    )


# Eightfold tenants publishing the same board as another live tenant under a second vanity
# hostname — same _EF_GROUP_ID, byte-identical job-id set, so both get indexed under different
# Board keys and ~10,240 rows are served twice (#154). Being genuinely alive is precisely the
# problem: `dedupe_eightfold_aliases.py --apply` buried these six, but a plain dead-TTL re-probe
# would find them answering (measured 2026-08-20: all six still 200 their sitemap, 423-2,666 job
# URLs each) and resurrect the duplicate. So the burial has to hold without asking the host —
# the same pre-probe skip ADR-0034 gives non-prod boards, re-asserted free on every check (#157).
# The winner named against each is the live tenant it duplicates.
# The six are also candidates of `eightfold_backing_boards.py` (ADR-0205), which buries each in
# `data/validate/aliases/eightfold.csv` whenever it can read the pair whole, so
# `_drop_alias_duplicates` below skips them too. This set stays because that verdict needs a read and this one
# does not: in both runs of 2026-09-24 the writer could not read careers.qualcomm.com whole (a
# short sweep, then a failed connection), and a loser out of the ledger would be re-probed live
# after its dead TTL and scraped as a duplicate until the next clean run.
_EIGHTFOLD_ALIAS_LOSERS = {
    "nvidia.eightfold.ai",  # jobs.nvidia.com
    "qualcomm.eightfold.ai",  # careers.qualcomm.com
    "micron.eightfold.ai",  # careers.micron.com
    "hsbc.eightfold.ai",  # portal.careers.hsbc.com
    "vodafone.eightfold.ai",  # jobs.vodafone.com
    "dsm.eightfold.ai",  # dsm-firmenich.eightfold.ai
}


def _is_eightfold_alias_loser(ats: str, tenant: str) -> bool:
    """A duplicate-hostname eightfold board — dead before any probe is spent (#157)."""
    return ats == "eightfold" and (tenant or "") in _EIGHTFOLD_ALIAS_LOSERS


def _drop_alias_duplicates(ats: str, rows: list[dict], ledger_dir: Path) -> list[dict]:
    """Rows naming a Board buried as another Board's duplicate (ADR-0111), removed unprobed.

    The alias ledger is keyed on the scraper's **slug**, which is the bare tenant for most ATSes
    but not all (Workday's is the whole careers URL, Zoho's the careers host). Going through
    ``slug_from`` rather than assuming ``tenant`` is what keeps this correct when the framework
    reaches those — assuming it would fail silently, skipping nothing, which is the least
    detectable way for this to be wrong."""
    aliases = alias_ledger.load_for(ledger_dir, ats)
    if not aliases or ats not in SCRAPERS:
        return rows
    return [
        r
        for r in rows
        if company_from_row(ats, r["tenant"], r["url"]).slug.lower() not in aliases
    ]


#: Any posting link on a jobvite board page. Deliberately not slug-anchored: the probe only
#: needs to know whether the page lists anything, and five row templates put the link in
#: different elements (JobviteScraper's module docstring).
_JOBVITE_JOB = re.compile(r"/job/[A-Za-z0-9]+")
_TALEO_JOB = re.compile(r"viewRequisition[^\"\s>]*\brid=(\d+)", re.IGNORECASE)
_TALEO_GONE = "attempted to reach a url that no longer exists"


def _is_dns(exc):
    return getattr(exc, "code", None) == _DNS_ERR


def _get(url, headers=None):
    """GET via the reliable-fetch seam (shared-host gated). Returns (status, body): the HTTP code,
    "dns" if the host can't resolve (definitive), or None for an exhausted transient failure.
    Reads the full body (Zoho parks its jobs <input> at the end of a ~1.7MB page)."""
    h = {"User-Agent": UA, **(headers or {})}
    try:
        r = _fetch("GET", url, headers=h)
    except http.RequestsError as e:
        if _is_dns(e):
            return "dns", b""
        _note(_net_reason(e))
        return None, b""
    if r is None:  # circuit breaker open -> transient
        _note("breaker-open")
        return None, b""
    if r.status_code not in (200, 404, 410):  # 404/410 settle as DEAD, not a failure
        _note(f"http-{r.status_code}")
    return r.status_code, r.content


def _post(url, json_body, headers):
    """POST JSON via the reliable-fetch seam (shared-host gated). Returns (status,
    parsed_json|None); status is the code, "dns", or None."""
    try:
        r = _fetch("POST", url, json=json_body, headers=headers)
    except http.RequestsError as e:
        if _is_dns(e):
            return "dns", None
        _note(_net_reason(e))
        return None, None
    if r is None:  # circuit breaker open -> transient
        _note("breaker-open")
        return None, None
    if r.status_code == 200:
        try:
            return 200, r.json()
        except Exception:  # noqa: BLE001
            _note("json-parse-fail")
            return None, None  # 200 but unparseable -> treat as transient
    # Only ``p_workday`` posts, and its conclusive "not here" set is _WD_GONE (404/410/422) — those
    # settle the board, so they are not failures. 422 is the bulk of them: it is what a live
    # datacenter answers for a tenant/site it doesn't host, once per DC in the sweep.
    if r.status_code not in _WD_GONE:
        _note(f"http-{r.status_code}")
    return r.status_code, None


def _verdict(status, jobs):
    """Map an HTTP status + parsed count into (verdict, jobs)."""
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status == 200:
        if jobs is not None:
            return LIVE, jobs
        _note("body-unparseable")  # 200, but the count parser couldn't read it
        return UNKNOWN, None
    return UNKNOWN, None  # timeout / 5xx / 403 / 429-exhausted / other -> retry


def _len_of(body, *keys):
    """JSON list length: top-level list, or the first of `keys`. None if body won't parse."""
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return len(data[k])
        return 0
    return None


def _classify(url, count):
    """GET `url`, run `count(body) -> int|None`, return (verdict, jobs)."""
    status, body = _get(url)
    return _verdict(status, count(body) if status == 200 else None)


def _slug_of(ats, tenant, url):
    """The Board's slug, read off this row by its own Scraper's ``slug_from`` (ADR-0203).

    A probe that reads the raw ``tenant`` instead can ask a different host than the scrape reads:
    a Personio row whose ``url`` is a vanity host, an Oracle row whose ``tenant`` is a bare label."""
    return company_from_row(ats, tenant, url).slug


def _respell_pool_rows(ats, rows):
    """Oracle pool rows respelled as the ledger holds a Board: tenant the pod host its Scraper
    reads, url `https://{host}`, one row per host (#627).

    The harvest writes a bare label (`bun`) with the host only in `url`, and the ledger is keyed on
    the raw tenant, so 439 such rows had landed beside the host row already holding their Board.
    A row whose slug is no pod host names no Board and is dropped (`oracle.is_pod_host`)."""
    if ats != "oracle":
        return rows
    by_host = {}
    for r in rows:
        host = _slug_of(ats, r["tenant"], r["url"])
        if is_pod_host(host):
            by_host.setdefault(host, {**r, "tenant": host, "url": f"https://{host}"})
    return list(by_host.values())


def _scraper_for_row(ats, tenant, url):
    """The Scraper for the Board this row names, so a probe asks the very URL the scrape reads
    (its ``url()``) rather than a copy of it (ADR-0203)."""
    return SCRAPERS[ats](_slug_of(ats, tenant, url))


def _hinted_first(hinted, choices):
    """``choices`` reordered to ask ``hinted`` first: the instance, data centre or TLD a row's url
    names, before the others a Board may have moved to."""
    return (hinted, *(choice for choice in choices if choice != hinted))


# --- per-ATS probes: return (verdict, jobs) ---
# Each reads the Board through `_scraper_for_row`/`_slug_of`, except `p_eightfold`, which ADR-0203
# left as it was. Where a probe asks a different URL than the scraper's `url()` (a smaller page,
# no descriptions), it says why beside it.


def p_greenhouse(t, u):
    # Not `url()`: its `content=true` carries every description, and a count needs none.
    slug = _slug_of("greenhouse", t, u)
    return _classify(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        lambda b: _len_of(b, "jobs"),
    )


def p_lever(t, u):
    # Try the instance the discovered url hints at, then the other one — the slug alone doesn't
    # say which instance hosts the board, exactly as LeverScraper.fetch_raw already assumes.
    # Trusting the url's hint alone marks a board DEAD whenever discovery found it on the global
    # host but the company actually sits on EU: measured 2026-07-27, 13 boards the ledger called
    # dead answered live on api.eu.lever.co. Only a 404 from *both* instances is definitive.
    scraper = _scraper_for_row("lever", t, u)
    hinted = _LEVER_EU_API_HOST if "jobs.eu.lever.co" in u else _LEVER_GLOBAL_API_HOST
    verdict = DEAD
    for api_host in _hinted_first(hinted, _LEVER_API_HOSTS):
        v, jobs = _classify(scraper.listing_url_on(api_host), _len_of)
        if v == LIVE:
            return v, jobs
        if v == UNKNOWN:
            verdict = UNKNOWN  # inconclusive on one instance -> can't call it dead
    return verdict, None


def p_ashby(t, u):
    # Not `url()`: its `includeCompensation=true` adds a block per posting a count never reads.
    slug = _slug_of("ashby", t, u)
    return _classify(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        lambda b: _len_of(b, "jobs"),
    )


_AVATURE_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_AVATURE_SITEMAP = re.compile(r"(?im)^sitemap:\s*(\S+)")
_AVATURE_REDIRECTS = (301, 302, 303, 307, 308)
#: Avature names a customer's non-production instance by prefixing its label, undelimited, so
#: ADR-0034's token rule misses it: `sandboxtql` lists 434 JobDetail ids, 329 of them `tql`'s own
#: (measured 2026-09-26) — a stale copy of the production Board. 20 such labels probed live with
#: 3,700 postings between them (sandbox3uskpmg 990, sandboxfonterrakf 921, uatauspost 5).
_AVATURE_NONPROD = re.compile(r"(?:sandbox|uat)", re.IGNORECASE)
#: How long the avature.net gate rests when a request is answered 406 and no spare egress can be
#: had: the spent budget answered 406 to every tenant for ~3-4 minutes (see `_SPANNING`).
_AVATURE_REFILL_S = 240


def _avature_get(url):
    """(status, location, text) for one request, redirects unfollowed. Status is "dns" when the
    host does not resolve and None for a transient failure. A 406 is Avature's spent per-IP
    budget, never an answer about the Board: it rests the avature.net gate (or moves it onto a
    spare egress) and reads as None."""
    try:
        r = _fetch("GET", url, headers={"User-Agent": UA}, allow_redirects=False)
    except http.RequestsError as e:
        if _is_dns(e):
            return "dns", "", ""
        _note(_net_reason(e))
        return None, "", ""
    if r is None:
        _note("breaker-open")
        return None, "", ""
    if r.status_code == 406:
        _note("406-budget")
        netloc = urllib.parse.urlsplit(url).netloc
        _ban_or_rotate(
            _gate_for(netloc) or _ensure_gate(netloc, "406"),
            r,
            url,
            _AVATURE_REFILL_S,
            "406, per-IP budget spent",
        )
        return None, "", ""
    return r.status_code, r.headers.get("location") or "", r.text


def _avature_owner_label(host):
    """The avature.net tenant label `host` serves, read off its CNAME on a public resolver:
    `jobs.opptly.com` -> `opptly`, `rohde-schwarz.avature.net` -> `rohdeschwarz`. A host whose
    CNAME names no tenant (bloomberg's names its app server, `iatsapp-prod-en19`) serves itself
    when it is under avature.net. None when that cannot be read."""
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers, resolver.lifetime = ["1.1.1.1", "8.8.8.8"], 5
    try:
        target = str(resolver.resolve(host, "CNAME")[0].target).rstrip(".")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        target = ""
    except dns.exception.Timeout:
        _note("dns-unconfirmed")
        return None
    label = target.removesuffix(".avature.net")
    # `iatsapp-*` is an app server, and a dotted name (`…-prod-en01.integrations`) infrastructure
    if (
        target.endswith(".avature.net")
        and "." not in label
        and not label.startswith("iatsapp")
    ):
        return label
    return host.removesuffix(".avature.net") if host.endswith(".avature.net") else None


def p_avature(t, u):
    """A Board is the tenant label, `{label}.avature.net` (owner decision 2026-09-26).

    robots.txt names every portal as `Sitemap: https://{host}/{portal}/sitemap_index.xml`, each
    index names per-locale child sitemaps, and the count is the distinct `JobDetail` ids across
    every portal a visitor can open — `listing_rows`, the scraper's own parse, so the two read
    one set of ids (the id is tenant-wide: bloomberg's `careers` and `internalcareers` share 257,
    same titles). The root `/sitemap.xml` is skipped (favicon only), and so is a
    portal whose first posting redirects to a `/Login` path: its ids are the same postings, or ones
    no visitor can read. That is one request per job portal rather than a name rule, because the
    name misleads: `internalcareers` 302s to `/Login` on bloomberg and broadinstitute (6 of 6
    each) and mgl, three vanity-hosted internal portals (tennet, unifi, ucsf) answer the label
    host with a 301 whose Location is already `…/Login/`, but dbgroup's `internaljobsde` is
    public. Most internal portals list no `JobDetail` at all (14 of the 19 sampled that answered). Portals that are
    not job portals (events, timeslots, referrals) list none either and add nothing.

    The count is an upper bound: a sitemap can keep closed postings (bupaanz `careersau` listed
    1,701 ids against a SearchJobs total of 999, and 6 of 6 sampled ids 302 to `/Error`). That is
    enough for liveness and the jobs >= 1 hiring cut, and the scrape reads the real set.

    Every request goes to the host robots.txt was read on, `{label}.avature.net`, which serves a
    vanity host's portals byte for byte (arcbest, auspost, bain) — so the pass stays behind the one
    avature.net gate. The exception is a label that redirects to its own vanity host (four in the
    pool), read on that host ungated: a few dozen requests a pass. Measured 2026-09-26 over the 1,008-label pool
    (`experiment/avature-liveness/LOG.md`):

    - `avature.net` has no wildcard A record (an invented label, and 605 pool labels, get NOERROR
      with no A from the zone's own name servers), so a label no public resolver can find is DEAD
      — asked before any request, so the 605 cost the gate nothing. A local "no such host" for a
      label a public resolver does find is the resolver under load: UNKNOWN.
    - A resolving label whose robots.txt names no portal (amazon, ally, abbgb) is a live instance
      with no public career site — LIVE with 0, like intuit, whose listing is a Radancy front.
    - A label can be a second name for another tenant, and then it is DEAD, so no posting is
      served twice under two labels. Two ways, both read off DNS before any Avature request: the
      label's own CNAME names another tenant label (13 of the 403 resolving pool labels:
      `rohde-schwarz` -> `rohdeschwarz` and `smurfitwestrockta` -> `westrockta` serve the same
      portals, `portalciscojobs` -> `cisco` answers nothing of its own), or its robots.txt
      redirects to a host whose CNAME does (`genesys` -> `jobs.opptly.com` -> `opptly`). Four
      labels redirect to their own vanity host instead (deloitteglobal, dttl, opptly, unops) and
      are read there.
    - A `sandbox…`/`uat…` label is a non-production copy of a tenant (`_AVATURE_NONPROD`): DEAD.
    - 403, 202, 5xx, timeouts and refused connections are unexplained -> UNKNOWN.
    """
    label = _slug_of("avature", t, u)
    if _AVATURE_NONPROD.match(label):
        return DEAD, None
    host = f"{label}.avature.net"
    if _public_resolver_has_no_a_record(host):
        return DEAD, None
    owner = _avature_owner_label(host)
    if owner is None:
        return UNKNOWN, None
    if owner.lower() != label:
        return DEAD, None  # another tenant's Board under a second name
    status, location, robots = _avature_get(f"https://{host}/robots.txt")
    if status in _AVATURE_REDIRECTS:
        target = urllib.parse.urlsplit(
            urllib.parse.urljoin(f"https://{host}/", location)
        )
        owner = _avature_owner_label(target.netloc)
        if owner is None:
            _note("redirect-unresolved")
            return UNKNOWN, None
        if owner.lower() != label:
            return DEAD, None
        host = target.netloc
        status, location, robots = _avature_get(f"https://{host}/robots.txt")
    if status != 200:
        if status not in (None, "dns"):
            _note(f"http-{status}")
        return UNKNOWN, None
    public_ids = set()
    for index_url in _AVATURE_SITEMAP.findall(robots):
        index = urllib.parse.urlsplit(index_url)
        if index.path == "/sitemap.xml":
            continue
        postings = {}  # id -> one posting URL, for the login check
        status, _, body = _avature_get(index._replace(netloc=host).geturl())
        if status != 200:
            return UNKNOWN, None
        for child_url in _AVATURE_LOC.findall(body):
            child = urllib.parse.urlsplit(child_url)._replace(netloc=host).geturl()
            status, _, body = _avature_get(child)
            if status != 200:
                return UNKNOWN, None
            for row in avature_listing_rows(body):
                postings.setdefault(row["id"], row["url"])
        if not postings:
            continue
        first = urllib.parse.urlsplit(next(iter(postings.values())))
        status, location, _ = _avature_get(first._replace(netloc=host).geturl())
        if status is None or status == "dns":
            return UNKNOWN, None
        if status in _AVATURE_REDIRECTS and "/login" in location.lower():
            continue  # a login-walled portal (internalcareers)
        public_ids |= postings.keys()
    return LIVE, len(public_ids)


def p_recruitee(t, u):
    return _classify(
        _scraper_for_row("recruitee", t, u).url(), lambda b: _len_of(b, "offers")
    )


def p_workable(t, u):
    # Not `url()`: its `details=true` carries every description, and a count needs none.
    slug = _slug_of("workable", t, u)
    return _classify(
        f"https://apply.workable.com/api/v1/widget/accounts/{slug}",
        lambda b: _len_of(b, "jobs"),
    )


_GEM_LIST_QUERY = """query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) { jobPostings { id } }
}"""


def p_gem(t, u):
    # gem's GraphQL listing answers 200 with an empty jobPostings list for a board that never
    # existed at all, indistinguishable from a live board with zero current openings (measured
    # 2026-09-16 — see gem.py's module docstring). The board page IS a real 404 for a nonexistent
    # tenant, so it settles DEAD; only once it says the tenant exists does the API's job count mean
    # anything.
    scraper = _scraper_for_row("gem", t, u)
    status, _ = _get(scraper.url())
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    status, body = _post(
        "https://jobs.gem.com/api/public/graphql/batch",
        [
            {
                "operationName": "JobBoardList",
                "variables": {"boardId": scraper.slug},
                "query": _GEM_LIST_QUERY,
            }
        ],
        {"User-Agent": UA, "Content-Type": "application/json", "Accept": "*/*"},
    )
    if status != 200 or not body:
        return UNKNOWN, None
    try:
        jobs = body[0]["data"]["oatsExternalJobPostings"]["jobPostings"]
    except (KeyError, IndexError, TypeError):
        return UNKNOWN, None
    return LIVE, len(jobs)


def _zoho_count(text):
    import html as _html

    m = _ZOHO_JOBS.search(text)
    if not m:
        return None
    try:
        return len(json.loads(_html.unescape(m.group(1))))
    except Exception:  # noqa: BLE001
        return None


def p_zoho(t, u):
    # The scraper's own careers page, on the host its `slug_from` reads off the row: Zoho's
    # ledger carries 44 pathy / 19 query rows, where appending `/jobs/Careers` to the raw url
    # would land inside a path or a query.
    status, body = _get(_scraper_for_row("zoho", t, u).url())
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    text = body.decode("utf-8", "replace")
    # Zoho serves a 200 "Page does not exist" error page (marked by cl-error-block) for a gone tenant
    # or an unpublished careers site — a soft-404, so definitively DEAD (not the transient UNKNOWN a
    # missing jobs <input> would otherwise imply).
    if "cl-error-block" in text:
        return DEAD, None
    count = _zoho_count(text)
    return (LIVE, count) if count is not None else (UNKNOWN, None)


class _BreakerOpen(Exception):
    """`_fetch` answered None: the host's circuit breaker is open, a transient."""


class _GatedFetcher:
    """A scraper Fetcher (ADR-0153) that sends every request through `_fetch`, so a probe
    reusing a scraper's own walk still rides this module's gates, breaker and egress. `_fetch`
    sets `timeout` itself, so the scraper's is dropped."""

    def fetch(self, method, url, **kw):
        kw.pop("timeout", None)
        r = _fetch(method, url, **kw)
        if r is None:
            raise _BreakerOpen
        return r

    def clear_cookies(self, domain=None):
        """`_fetch` rides the pooled session, so its jar is the one to clear (ADR-0199)."""
        http.DEFAULT_FETCHER.clear_cookies(domain)


def p_cornerstone(t, u):
    """The Board's whole listing, read by the scraper's own walk (`CornerstoneScraper.listing`).

    A Board is the tenant, but its postings sit on several career sites and one requisition is
    often on more than one (12,798 of 42,534 postings on 119 of 368 hiring seed tenants), so a
    count is only honest as the union the scraper builds: summing each site's `totalCount` would
    have read 58,083. The walk needs the page's JWT, its API pod and the US-pod session cookie,
    all of which live in the scraper; re-declaring them here is the drift `p_zwayam` warns of.

    DEAD on the two answers measured on real dead tenants (docs/cornerstone/): the host does not
    resolve (no wildcard DNS — 6 of 404 seed tenants, 15 of a 30-label Wayback sample), or every
    career-site page on ids 1-3 redirects to `/ui/error` (an LMS-only corp: 12 of that sample,
    and 5 tenants x ids 1-6). Anything else unexplained is UNKNOWN.
    """
    scraper = CornerstoneScraper(_slug_of("cornerstone", t, u), fetcher=_GatedFetcher())
    try:
        rows = scraper.listing()
    except _BreakerOpen:
        _note("breaker-open")
        return UNKNOWN, None
    except http.RequestsError as e:
        if _is_dns(e):
            return DEAD, None
        status = getattr(getattr(e, "response", None), "status_code", None)
        _note(f"http-{status}" if status else _net_reason(e))
        return UNKNOWN, None
    except (ValueError, KeyError, TypeError):  # a page or body that did not parse
        _note("body-unparseable")
        return UNKNOWN, None
    if rows is None:
        return DEAD, None
    return LIVE, len(rows)


def p_bamboohr(t, u):
    # Verified live 2026-09-16 (5 fabricated slugs + 3 confirmed-live-but-jobless tenants): a
    # dead tenant's widget answers 200 with an EMPTY body, while a live tenant — jobs or not —
    # always serves the BambooHR-ATS-board wrapper (a "no open positions" blank state when
    # empty). DNS/404 never happen here (the *.bamboohr.com wildcard resolves for anything), so
    # the wrapper's presence, not the status code, is the real signal. See bamboohr.py's module
    # docstring for the full measurement.
    status, body = _get(_scraper_for_row("bamboohr", t, u).url())
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    text = body.decode("utf-8", "replace")
    if "BambooHR-ATS-board" not in text:
        return DEAD, None
    return LIVE, len(set(re.findall(r"bhrPositionID_(\d+)", text)))


def p_breezy(t, u):
    """One GET of the Board's plain ``/json`` listing, redirects NOT followed.

    The status settles it — measured 2026-09-23 on every one of the 4,794 pool tenants
    (``docs/breezy/2026-09-23_json-api-measurement.md``): a JSON list is a live Board (2,174
    non-empty, 1,703 exactly ``[]``), and a 404 is a departed tenant (917, each the same 3,265-byte
    "Career portal not found" page an invented label also gets). Nothing else came back — no 3xx,
    no 403, no 5xx. Upstream expects a departed tenant to 302 to the marketing site; none did, so
    redirects are not followed (a followed one would read as that site's 200) and a 3xx stays
    UNKNOWN until one is seen. ``verbose`` is left off: the count needs no descriptions, and they
    are ~83% of the listing's bytes. No rate limit was found (up to 94 req/s across tenants, zero
    refusals), so no gate is seeded. That leaves a platform-wide wall ungated: the auto-gate keys the
    exact host and every Board is its own host, so only a `_SPANNING` entry would pace one. It fails
    safe meanwhile — a 403 or 429 reads UNKNOWN, never DEAD.

    **A DNS failure is not a dead tenant here.** ``*.breezy.hr`` is a wildcard record — an
    invented label resolves and gets the 404 — so no tenant is ever NXDOMAIN. What does fail to
    resolve is the local resolver under this prober's 432 workers, every Board being its own
    hostname: the first pass wrote 41 Boards the census had just read live
    (``kimmel-associates``, 445 postings) as dead that way, and a replay at 432-wide against
    1,500 live-verdict Boards drew 100 curl code-6 errors.
    So it is UNKNOWN, retried on the next pass, like any other network failure.
    """
    slug = _slug_of("breezy", t, u)
    try:
        r = _fetch(
            "GET",
            f"https://{slug}.breezy.hr/json",  # `url()` less `verbose`, as above
            headers={"User-Agent": UA, "Accept": "application/json"},
            allow_redirects=False,
        )
    except http.RequestsError as e:
        _note("dns" if _is_dns(e) else _net_reason(e))
        return UNKNOWN, None
    if r is None:  # breaker open -> transient
        _note("breaker-open")
        return UNKNOWN, None
    if r.status_code in (404, 410):
        return DEAD, None
    if r.status_code != 200:
        _note(f"http-{r.status_code}")
        return UNKNOWN, None
    try:
        rows = json.loads(r.content)
    except ValueError:
        rows = None
    if not isinstance(rows, list):
        _note("body-unparseable")
        return UNKNOWN, None
    return LIVE, len(rows)


def p_clearcompany(t, u):
    # ClearCompany's public Board is HRM Direct, and its whole-account feed `xml.php` settles the
    # verdict in one request (measured 2026-09-23, docs/clearcompany/): 404 for an unknown or
    # departed tenant (51 of 51 in the sample also had a 404 Board page; 2,473 of the ledger's
    # 2,474 dead rows re-read 404), and 200 with a `<source>` for every live Board, with no
    # `<job>` when nothing is open. ClearCompany's own JSON feed cannot make this call: it answers
    # 200 for 48 of those 51 dead tenants, 14 of them with postings. The count is distinct reqs,
    # not rows — a req repeats once per location (434 of 6,377). Everything else stays UNKNOWN,
    # including a refused connection (126 of 256 in one unexplained cross-tenant burst) and the
    # 500 the largest account's feed returns after ~104 s. So does a DNS failure: `*.hrmdirect.com`
    # answers almost any label (the one real NXDOMAIN in the pool is the vendor's `preview`), and
    # under a wide pass the local resolver fails first — breezy's wildcard wrote 41 live Boards
    # dead that way (`p_breezy`).
    status, body = _get(_scraper_for_row("clearcompany", t, u).url())
    if status == 404:
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    text = decode_hrm_bytes(body)
    if "<source>" not in text:
        _note("body-unparseable")
        return UNKNOWN, None
    return LIVE, len(feed_reqs(text))


def p_keka(t, u):
    # The careers SPA's own job call (read off cdn.keka.com/careers/v/2026/scripts/app/app.min.js:
    # `$.ajax('/api/jobs/${apiPortalName}/active')`, apiPortalName defaulting to "default"). It
    # needs no org UUID, which is what makes it strictly better than the embedjobs route this
    # probe used to take: that one had to recover the UUID from an /ats/documents/{uuid}/ asset
    # path — the careers background image, else the org logo on the /careers page — so a portal
    # with *neither* exposed no UUID at all and was recorded UNKNOWN despite serving jobs.
    # Measured 2026-07-27: 25 of 25 sampled ledger-UNKNOWNs answered LIVE here, with real count
    # variance; dead slugs and garbage controls were unchanged.
    status, body = _get(_scraper_for_row("keka", t, u).url())
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    text = body.decode("utf-8", "replace")
    # Keka soft-errors at 200 with an HTML page: "Invalid Tenant" (unknown slug) or "Forbidden
    # Access" (disabled portal) — both mean no public board, so definitively DEAD.
    if "Invalid Tenant" in text or "Forbidden Access" in text:
        return DEAD, None
    n = _len_of(body)
    return (LIVE, n) if n is not None else (UNKNOWN, None)


# A data center conclusively says a Workday tenant/site is "not here" via 404/410/422. Everything
# else — dns, timeout, 5xx, 429 — is inconclusive (dns included: the *.wdN wildcard resolves for
# every active DC, so a dns failure is OUR network, not a dead board).
_WD_GONE = {404, 410, 422}


def p_workday(t, u):
    slug = _slug_of("workday", t, u)
    m = _WD_URL.match(slug)
    if not m:
        return DEAD, None  # not a Workday URL -> can't be a board
    scraper = SCRAPERS["workday"](slug)

    def probe(inst):
        status, data = _post(
            scraper.listing_url_on(inst),
            {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
            {
                "User-Agent": UA,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        total = int(data.get("total", 0)) if status == 200 and data else None
        return total, status

    # Probe the hinted DC, then sweep the rest (tenant may have migrated). Any 200 -> LIVE, found.
    statuses = []
    for inst in _hinted_first(m.group("instance"), _WD_INSTANCES):
        total, status = probe(inst)
        if total is not None:
            return LIVE, total
        statuses.append(status)
    # No DC served it live. DEAD only if *every* probe conclusively said "not here"; a single
    # inconclusive probe (timeout/dns/5xx) leaves a live instance unruled-out -> UNKNOWN. This makes
    # a false-dead impossible: a migrated board whose live DC merely timed out stays UNKNOWN, never
    # DEAD (and an outage, which fails every probe, yields UNKNOWN, not a wave of false-deads).
    return (DEAD, None) if all(s in _WD_GONE for s in statuses) else (UNKNOWN, None)


def p_ripplehire(t, u):
    scraper = _scraper_for_row("ripplehire", t, u)
    headers = {"User-Agent": UA}
    try:
        r = http.fetch(
            "GET",
            scraper.url(),
            headers=headers,
            timeout=TIMEOUT,
            verify=False,
            attempts=_ATTEMPTS,
        )
    except http.RequestsError as e:
        return (DEAD, None) if _is_dns(e) else (UNKNOWN, None)
    m = _RIPPLEHIRE_TOKEN.search(r.url)
    if not m:
        return UNKNOWN, None
    params = json.dumps(
        {
            "page": 0,
            "search": "*:*",
            "token": m.group(1),
            "source": "CAREERSITE",
            "pagesize": 1,
        }
    )
    data = urllib.parse.urlencode({"careerSiteUrlParams": params, "lang": "en"})
    try:
        r2 = http.fetch(
            "POST",
            scraper.search_url(),
            data=data,
            headers={
                "User-Agent": UA,
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            timeout=TIMEOUT,
            verify=False,
            attempts=_ATTEMPTS,
        )
    except http.RequestsError:
        return UNKNOWN, None
    if r2.status_code != 200:
        return UNKNOWN, None
    try:
        return LIVE, int(r2.json().get("totalJobCount", 0))
    except Exception:  # noqa: BLE001
        return UNKNOWN, None


def p_darwinbox(t, u):
    # The scraper tries `TLDS` in its own order; the probe starts from the one the row's url
    # names, and asks each host the same listing the scrape pages through.
    scraper = _scraper_for_row("darwinbox", t, u)
    hinted = next(
        (tld for tld in _DARWINBOX_TLDS if f".darwinbox.{tld}" in u), _DARWINBOX_TLDS[0]
    )
    dns_fails = 0
    for tld in _hinted_first(hinted, _DARWINBOX_TLDS):
        api = scraper.listing_url_on(tld)
        try:
            r = http.fetch(
                "POST",
                api,
                json={
                    "companyId": "main",
                    "page": 1,
                    "sort_option": "new",
                    "limit": 100,
                },
                headers={"Accept": "application/json", "User-Agent": UA},
                timeout=TIMEOUT,
                verify=False,
                attempts=_ATTEMPTS,
            )
        except http.RequestsError as e:
            if _is_dns(e):
                dns_fails += 1
            continue
        if r.status_code == 200:
            try:
                return LIVE, len(r.json().get("data") or [])
            except Exception:  # noqa: BLE001
                return UNKNOWN, None
        # 404/other on this tld -> try the other tld
    return (DEAD, None) if dns_fails == len(_DARWINBOX_TLDS) else (UNKNOWN, None)


# The Board host's redirect target, for slugs the posting API can't settle (see p_smartrecruiters).
_SR_NOT_A_BOARD = "jobs.smartrecruiters.com"


def _sr_board_host(slug):
    """Does the Board host serve this slug? True / False / None when it can't be told.

    Redirects are deliberately NOT followed: every slug 200s once you chase them, which is what
    makes the naive check useless. The *target* is the signal — a slug that was never a Board
    bounces to the generic job search, while a customer running its career site on its own domain
    bounces to that domain (``Accor`` -> ``careers.accor.com``) and is perfectly real.
    """
    try:
        r = _fetch(
            "GET",
            f"https://careers.smartrecruiters.com/{urllib.parse.quote(slug)}",
            headers={"User-Agent": UA},
            allow_redirects=False,
        )
    except http.RequestsError as e:
        return False if _is_dns(e) else None
    if r is None:
        return None
    if r.status_code == 200:
        return True
    if r.status_code in (301, 302, 303, 307, 308):
        return _SR_NOT_A_BOARD not in (r.headers.get("location") or "")
    if r.status_code in (400, 404, 410):
        return False
    return None


def p_smartrecruiters(t, u):
    # The posting API answers 200 {"totalFound": 0} for a slug that never existed, identically to a
    # real Board with nothing open — so the count alone cannot separate them, and taking it at face
    # value files phantoms as live (121 such rows were found in the ledger, 2026-07-27).
    #
    # Only a ZERO count is ambiguous: totalFound > 0 is proof on its own. So settle zeroes on the
    # Board host, and *only* zeroes — a real Board whose customer runs its career site on its own
    # domain also redirects away from the Board host, so consulting the host first would discard
    # the largest Boards in the set (88 of them, 45,134 postings).
    def count(b):
        try:
            d = json.loads(b)
        except Exception:  # noqa: BLE001
            return None
        tf = d.get("totalFound")
        return tf if isinstance(tf, int) else len(d.get("content") or [])

    # `limit=10`, not `url()`'s 100: only `totalFound` is read.
    slug = _slug_of("smartrecruiters", t, u)
    verdict, jobs = _classify(
        f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=10", count
    )
    if verdict != LIVE or jobs:
        return verdict, jobs
    served = _sr_board_host(slug)
    if served is None:
        return UNKNOWN, None  # couldn't tell -> re-probe rather than guess
    return (LIVE, 0) if served else (DEAD, None)


def p_teamtailor(t, u):
    return _classify(
        _scraper_for_row("teamtailor", t, u).url(), lambda b: _len_of(b, "items")
    )


def p_freshteam(t, u):
    # The public careers widget: a real board always returns JSON with a "jobs" key (0 == live but
    # empty). An unknown/parked slug soft-errors at HTTP 200 with an HTML 404 page off the
    # *.freshteam.com wildcard (so it never 404s / DNS-fails) — non-JSON at 200 is definitively DEAD.
    status, body = _get(_scraper_for_row("freshteam", t, u).url())
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    n = _len_of(body, "jobs")
    return (LIVE, n) if n is not None else (DEAD, None)


def _pyjamahr_count(body):
    """The envelope's `count` — the Board's whole total, whatever `limit` the page asked for.
    None if the body is not that envelope."""
    try:
        count = json.loads(body).get("count")
    except Exception:  # noqa: BLE001
        return None
    return count if isinstance(count, int) else None


_PINPOINT_RENAMED = re.compile(
    r"^https://[a-z0-9-]+\.pinpointhq\.com/postings\.json$", re.IGNORECASE
)


def p_pinpoint(t, u):
    """The listing without following redirects, then one page asked the way a browser asks.

    Measured 2026-09-23 over the 1,465-slug pool
    (`docs/pinpoint/2026-09-23_postings-api-measurement.md`):

    * A slug that never existed is a real **404** on the listing (the vendor's 11,684-byte page).
    * A renamed tenant **301s the listing to another `{label}.pinpointhq.com/postings.json`** —
      63 of 63 redirects, none anywhere else. Followed, it would read as a second live row for a
      Board the target label already is (57 of the 63 targets are live slugs), so the old label
      is dead. A redirect anywhere else has never been seen and stays UNKNOWN.
    * The listing's count is not the whole answer: the JSON is served whether or not a user can
      open the postings, and the pages **content-negotiate** — asked with `Accept: */*` they
      render, asked as a browser asks (`text/html`) they can 404 or redirect away. So a Board
      with postings is settled on its **first and last postings' pages**, the links a user would
      click — two, because one posting can close between the listing and its page (`freeagent`
      flipped dead once that way and re-probed live 3 of 3); it is live if either lands. Asking
      each of the 692 such Boards for its *first* posting only (the measurement this rule was
      built on): 514 render it; 158 301 it to their vanity host at the same path, which serves
      it; 17 answer 404 (1,200 postings, `10kbi-23` alone 638); 3 redirect it to a company page
      that is not the posting (`10kai` -> `/our-programmes/`, 73 postings); 1 unparseable. `/` is the wrong page to ask here: `kharon` 404s a browser on `/`
      while its postings render.
    * An empty Board has no posting to ask about, so `/` decides: of 534 asked, 145 render (live,
      nothing open), 282 are 404 and 105 redirect to the tenant's own or another ATS's site
      (greenhouse, linkedin) — nothing is published here, so both are dead; 2 timed out or
      answered unparseably.

    `pinpointhq.com` is a spanning gate (`_SPANNING`): the refusals it answers overload with span
    tenants and persist for minutes.
    """
    scraper = _scraper_for_row("pinpoint", t, u)
    base = scraper.board_page().rstrip("/")

    def ask_listing():
        return _fetch(
            "GET",
            scraper.url(),
            headers={"User-Agent": UA},
            allow_redirects=False,
        )

    try:
        listing = ask_listing()
        if listing is None:  # breaker open -> transient
            _note("breaker-open")
            return UNKNOWN, None
        if listing.status_code in (301, 302, 303, 307, 308):
            if _PINPOINT_RENAMED.match(listing.headers.get("location") or ""):
                return DEAD, None
            _note(f"redirect-{listing.status_code}")
            return UNKNOWN, None
        if listing.status_code in (404, 410):
            return DEAD, None
        if listing.status_code != 200:
            _note(f"http-{listing.status_code}")
            return UNKNOWN, None
        if listing.content.replace(b" ", b"") == b'{"data":[]}':
            # A spurious empty answer for a Board with postings (12 of 6,030 fetches) would send
            # this to `/`, where a vanity host's redirect reads as dead (`jec`, once). Ask again.
            listing = ask_listing()
            if listing is None or listing.status_code != 200:
                _note(
                    "breaker-open" if listing is None else f"http-{listing.status_code}"
                )
                return UNKNOWN, None
        try:
            postings = json.loads(listing.content)["data"]
            paths = [p["path"] for p in (postings[:1] + postings[1:][-1:])] or ["/"]
        except (ValueError, KeyError, TypeError):
            _note("body-unparseable")
            return UNKNOWN, None
        lands = [_pinpoint_lands(base, path, bool(postings)) for path in paths]
    except http.RequestsError as e:
        if _is_dns(e):
            return DEAD, None
        _note(_net_reason(e))
        return UNKNOWN, None
    if True in lands:
        return LIVE, len(postings)
    if None in lands:
        return UNKNOWN, None
    return DEAD, None


def _pinpoint_lands(base, path, has_postings):
    """Does a browser asking for `path` get the page? True / False / None when it can't be told.

    Asked as `text/html` without following redirects. A redirect lands only for a posting, and
    only when it keeps the posting's path (the vanity host serves the same page); an empty
    Board's `/` redirecting anywhere is a site published elsewhere.
    """
    page = _fetch(
        "GET",
        f"{base}{path}",
        headers={"User-Agent": UA, "Accept": "text/html"},
        allow_redirects=False,
    )
    if page is None:
        _note("breaker-open")
        return None
    if page.status_code == 200:
        return True
    if page.status_code in (404, 410):
        return False
    if page.status_code in (301, 302, 303, 307, 308):
        return has_postings and (page.headers.get("location") or "").endswith(path)
    _note(f"http-{page.status_code}")
    return None


#: HAProxy's deny page, the whole body a departed PeopleStrong tenant's host answers.
_PEOPLESTRONG_DENIED = b"Request forbidden by administrative rules."


def p_peoplestrong(t, u):
    """One POST of the portal's listing at `limit=1`; the platform's own answers settle it.

    Measured 2026-09-25 over 423 pool labels on the wildcard `*.peoplestrong.com` zone:
    a registered candidate portal states `totalRecords` (the whole Board's count — 0 on 47 empty
    portals); a host that is not one answers 200 with `response: null` and code 201
    "Inside getTpUrl(...)" (192 labels: HRMS logins, support hosts, an invented label); and a
    departed tenant's host answers HAProxy's 93-byte deny page on every path and User-Agent
    (65 labels, none of them among the 103 registered portals — CitiusTech's is on RippleHire
    now). Anything else — PeopleStrong's LMS, helpdesk and alumni hosts answering HTML, another
    403 body, the shared 429 — is not one of those and stays UNKNOWN. So does a DNS failure: on a
    wildcard zone every label resolves, so a failed lookup is the resolver's.

    The deny page is a bare 403, which trips no gate, so the same page served to our *address*
    would read exactly like a departed tenant and write every Board dead. It is settled only when
    two named addresses — the direct route and the spare egress, each pinned so that nothing can
    re-route it — both get it; the first ask cannot be one of them, because once a 429 walls the
    group it rides the spare egress already. A real answer from either is read instead, and with
    no spare egress, or no answer from either, the row stays UNKNOWN.
    """
    url = _scraper_for_row("peoplestrong", t, u).url(limit=1)
    r = _peoplestrong_ask(url)
    if r is None or not _peoplestrong_denied(r):
        return _peoplestrong_verdict(r)
    proxy = spare_egress.proxy_url()
    if proxy is None:  # no second address: nothing another ask could settle
        _note("deny-unconfirmed")
        return UNKNOWN, None
    direct = _peoplestrong_ask_pinned(url, None)
    other = _peoplestrong_ask_pinned(url, proxy)
    if direct is None or other is None:
        _note("deny-unconfirmed")
        return UNKNOWN, None
    if _peoplestrong_denied(direct) and _peoplestrong_denied(other):
        return DEAD, None
    _note("deny-one-address-only")
    return _peoplestrong_verdict(other if _peoplestrong_denied(direct) else direct)


def _peoplestrong_ask(url):
    """The listing's response over the group's own route, or None (noted) when none came back."""
    try:
        r = _fetch("POST", url, json={}, headers={"User-Agent": UA})
    except http.RequestsError as e:
        _note("dns-wildcard" if _is_dns(e) else _net_reason(e))
        return None
    if r is None:  # breaker open -> transient
        _note("breaker-open")
    return r


def _peoplestrong_ask_pinned(url, proxy):
    """The listing's response over exactly one route — `proxy`, or direct when None — paced by the
    host's gate but outside its egress group, so a walled group cannot move it."""
    routed = {"proxies": {"http": proxy, "https": proxy}} if proxy else {}
    gate = _gate_for(urllib.parse.urlsplit(url).netloc)
    try:
        return _through_gate(
            gate,
            lambda: http.fetch(
                "POST",
                url,
                timeout=TIMEOUT,
                verify=False,
                attempts=_ATTEMPTS,
                json={},
                headers={"User-Agent": UA},
                **routed,
            ),
        )
    except http.RequestsError as e:
        _note(_net_reason(e))
        return None


def _peoplestrong_denied(r):
    return r.status_code == 403 and _PEOPLESTRONG_DENIED in r.content


def _peoplestrong_verdict(r):
    """What a listing response that is not the deny page says."""
    if r is None:
        return UNKNOWN, None
    if r.status_code != 200:
        _note(f"http-{r.status_code}")
        return UNKNOWN, None
    try:
        body = json.loads(r.content)
    except ValueError:
        _note("body-unparseable")
        return UNKNOWN, None
    if isinstance(body, dict) and isinstance(body.get("totalRecords"), int):
        return LIVE, body["totalRecords"]
    code = (body.get("messageCode") or {}) if isinstance(body, dict) else {}
    if code.get("code") == 201 and "getTpUrl" in str(code.get("messages")):
        return DEAD, None
    _note("body-unparseable")
    return UNKNOWN, None


def p_pyjamahr(t, u):
    # Two questions, cheapest first. The listing (`limit=1`, a ~200-byte envelope) says how many
    # postings the Board has, and a non-zero count is proof of a tenant. A zero is NOT proof of
    # anything: an unknown slug answers HTTP 200 with `count: 0`, byte-identical to a live Board
    # with nothing open — measured 2026-09-22 on `notacompany123` against 75 real empty Boards.
    # The board page tells those apart: `jobs.pyjamahr.com/{slug}` is a real 404 for a slug that
    # is not a tenant and a 200 for every tenant, empty or not (757 of 757 census tenants 200;
    # the three 404s in the Wayback roster were `images`, `&` and a `.js` asset, not tenants).
    # No rate limit was found (~3,800 requests, up to 84 req/s, zero non-200s), so neither host
    # is seeded in `_GATES`; the auto-gate covers a wall that appears later.
    # `limit=1`, not `url()`'s 1,000: the envelope's `count` is the whole total either way.
    scraper = _scraper_for_row("pyjamahr", t, u)
    status, body = _get(
        f"https://api.pyjamahr.com/api/career/jobs/?company_slug={scraper.slug}&limit=1"
    )
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    n = _pyjamahr_count(body)
    if n is None:
        _note("body-unparseable")
        return UNKNOWN, None
    if n:
        return LIVE, n
    status, _ = _get(scraper.board_page())
    if status == 200:
        return LIVE, 0
    if status == "dns" or status in (404, 410):
        return DEAD, None
    return UNKNOWN, None


def _jibe_get(url, follow=False):
    """(status, body) for one request to a Jibe client host, redirects left unfollowed so no
    request reaches a host whose robots.txt was not read — except robots.txt's own (`follow`),
    which RFC 9309 asks a crawler to follow. "dns" when the label does not resolve."""
    try:
        r = _fetch(
            "GET",
            url,
            headers={"User-Agent": UA},
            allow_redirects=follow,
            max_redirects=5,
        )
    except http.RequestsError as e:
        if _is_dns(e):
            return "dns", ""
        _note(_net_reason(e))
        return None, ""
    if r is None:
        _note("breaker-open")
        return None, ""
    return r.status_code, r.text


def _public_resolver_has_no_a_record(hostname):
    """True when a public resolver — the first of 1.1.1.1 and 8.8.8.8 that answers at all — says
    `hostname` has no A record: an unknown Jibe or Avature label answers NOERROR with an empty
    answer, not NXDOMAIN, on both alike. Neither answering is not an answer: False, so the Board stays
    UNKNOWN."""
    import dns.exception
    import dns.resolver

    for nameserver in ("1.1.1.1", "8.8.8.8"):
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers, resolver.lifetime = [nameserver], 5
        try:
            resolver.resolve(hostname, "A")
            return False
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return True
        except (dns.resolver.NoNameservers, dns.exception.Timeout):
            continue
    _note("dns-unconfirmed")
    return False


def p_jibe(t, u):
    # The Board is the client, read at `{client}.jibeapply.com`; `jibeapply.com` has no wildcard
    # DNS, so a label that does not resolve is a departed or invented client (101 of 1,244 pool
    # labels; `zzzzqqq`). robots.txt is read first and honoured (ADR-0189): a disallowing host
    # (carrefour) and an unreachable file are UNKNOWN, never read. The listing follows at the
    # host's `crawl-delay: 5`, and its `totalCount` is the count — it counts one row per
    # (requisition, language), so it can exceed the postings the scraper keeps. A 404 there is not
    # a departed client: 21 resolving labels answer it (dycom's board lives under `/dycom/`), so it
    # is UNKNOWN too. Measured 2026-09-24, docs/jibe/2026-09-24_api-jobs-measurement.md.
    hostname = _scraper_for_row("jibe", t, u).host
    host = f"https://{hostname}"
    status, body = _jibe_get(f"{host}/robots.txt", follow=True)
    if status == "dns":
        # Only a public resolver's "no A record" is dead: the macOS system resolver answered "no
        # such host" for live clients (uhs) under a 64-thread sweep on 2026-09-24.
        if _public_resolver_has_no_a_record(hostname):
            return DEAD, None
        return UNKNOWN, None
    verdict = _jibe.robots_verdict(status, body, _jibe.API_PATH, UA)
    if verdict != _jibe.ALLOW:
        _note(
            "robots-unreachable" if verdict == _jibe.UNREACHABLE else "robots-disallow"
        )
        return UNKNOWN, None
    time.sleep(_jibe.CRAWL_DELAY)
    status, body = _jibe_get(f"{host}{_jibe.API_PATH}?page=1&limit=1&internal=false")
    if status != 200:
        _note(f"http-{status}")
        return UNKNOWN, None
    try:
        total = json.loads(body).get("totalCount")
    except (ValueError, AttributeError):
        total = None
    if not isinstance(total, int):
        _note("body-unparseable")
        return UNKNOWN, None
    return LIVE, total


_SF_PROBE_CAP = 256 * 1024  # capped stream: RMK RSS feeds trickle at ~30 KB/s


def p_adp(t, u):
    # A Board is a career center, `{cid}/{ccId}`. content-links answers first: a `cid` ADP does
    # not know is a 404 (2 random GUIDs, a malformed one, a real one uppercased — measured
    # 2026-09-23), and a `ccId` the client does not have is a 200 with `PublishedIndicator`
    # false, where every real center measured (hiring or empty) states true. It also names the
    # center's languages, and `lang` is a filter: a center posting only in `en_CA` lists nothing
    # under `en_US`, in the same metaless envelope an empty Board returns. So the count is the
    # sum of each language's `totalNumber` — a posting translated into two languages counts
    # twice, which the `jobs >= 1` hiring cut does not mind. ~2 requests per live Board, paced
    # by the seeded gate above.
    #
    # A DNS failure is UNKNOWN, never DEAD: every Board is on the one fixed host, so an
    # unresolvable name says nothing about a tenant. Measured 2026-09-24: the local resolver
    # failed `workforcenow.adp.com` mid-pass while the host kept answering, which the generic
    # `status == "dns"` rule wrote down as dead Boards (breezy's lesson, ADR-0181).
    scraper = _scraper_for_row("adp", t, u)  # every ledger row is `{cid}/{ccId}`
    cid, cc = scraper.cid, scraper.cc_id
    status, body = _get(locales_url(cid, cc))
    if status == "dns":
        _note("dns-on-fixed-host")
        return UNKNOWN, None
    if status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    try:
        links = json.loads(body)
    except ValueError:
        _note("body-unparseable")
        return UNKNOWN, None
    if not is_published(links):
        return DEAD, None
    total = 0
    for lang in languages_of(links):
        status, body = _get(listing_url(cid, cc, lang, skip=1, top=1))
        # A center its client has closed to outsiders answers the listing with this 403 on
        # every language — measured 2026-09-24 on 8 of 40 sampled pool centers the first pass
        # left unknown (the other 32 were ADP-side 500s on both calls, genuinely unsettled).
        # Nothing on it is public, so there is no Board to read.
        if status == 403 and b"not allowed for external candidates" in body:
            return DEAD, None
        if status != 200:
            if status in (404, 410):  # `_get` notes every other non-200 itself
                _note(f"listing-http-{status}")
            return UNKNOWN, None
        try:
            total += listing_total(json.loads(body))
        except ValueError:
            _note("body-unparseable")
            return UNKNOWN, None
    return LIVE, total


#: What the site record answers for a site that is gone — a 400, not a 404. Both measured
#: 2026-09-24 on real departed sites from the seed lists: "not found" on 5 (`bastiansolutions`,
#: `carolinapowerscareers`, ...), "not active" on 6 (`cityofpeoriaaz`, `lkqexternalcareersite`,
#: ...). An invented slug answers "not found" too.
_ADP_RECRUITING_GONE = (b"Careersite not found", b"Careersite is not active")


def p_adp_recruiting(t, u):
    # A Board is a career site, `myjobs.adp.com/{slug}/cx`. Its record answers first: a 400
    # naming the site gone is DEAD, and a 200 carries the token the listing wants. The listing
    # (`$top=1`) states the count; a zero is a real empty site, because only a site the record
    # knows reaches it. Every request sends `Accept-Language: en-US` — a filter, and curl_cffi's
    # own default reads every site as empty (`adp_recruiting.request_headers`).
    #
    # An employee-only site (`careerSiteType` "Internal", 15 of the 681 seed-census sites) is DEAD
    # by policy, not by absence. Of the 14 hiring ones' 3,924 postings, 3,526 are on an external
    # site of the same client, which serves them; the other 398 are for the client's own staff
    # (ADR-0202).
    #
    # No rate limit was found (2,500 requests at 128-wide), so the host is not seeded in
    # `_GATES`. A DNS failure is UNKNOWN: every site is on the one fixed host.
    slug = _slug_of("adp_recruiting", t, u)
    status, body = _get(
        _adp_recruiting.site_url(slug), headers=_adp_recruiting.request_headers()
    )
    if status == "dns":
        _note("dns-on-fixed-host")
        return UNKNOWN, None
    if status == 400 and any(gone in body for gone in _ADP_RECRUITING_GONE):
        return DEAD, None
    if status != 200:
        # `_get` notes every other non-200 itself; neither was seen on this host.
        if status in (404, 410):
            _note(f"site-http-{status}")
        return UNKNOWN, None
    try:
        site = json.loads(body)
    except ValueError:
        _note("body-unparseable")
        return UNKNOWN, None
    if (site.get("settings") or {}).get("careerSiteType") == "Internal":
        return DEAD, None
    token = site.get("myJobsToken")
    if not token:
        _note("no-token")
        return UNKNOWN, None
    status, body = _get(
        _adp_recruiting.listing_url(skip=0, top=1),
        headers=_adp_recruiting.request_headers(token),
    )
    if status != 200:
        if status in (404, 410):
            _note(f"listing-http-{status}")
        return UNKNOWN, None
    try:
        return LIVE, int(json.loads(body)["count"])
    except (ValueError, KeyError, TypeError):
        _note("body-unparseable")
        return UNKNOWN, None


def p_successfactors(t, u):
    # RMK vanity-domain board: /sitemap.xml is either a compact urlset of /job/ URLs or the
    # Google-jobs RSS feed. The read is a capped stream (the RSS generator trickles, and big
    # urlsets don't need reading whole), so the jobs count is a lower bound on big boards —
    # enough for the jobs>=1 "hiring" cut. A 200 that is neither sitemap shape (parked host,
    # bot-wall, replatformed careers site) means no public RMK board -> DEAD, the
    # freshteam/zoho soft-404 precedent.
    try:
        r = http.session().request(
            "GET",
            _scraper_for_row("successfactors", t, u).url(),
            headers={"User-Agent": UA},
            timeout=TIMEOUT,
            verify=False,
            stream=True,
        )
    except http.RequestsError as e:
        return (DEAD, None) if _is_dns(e) else (UNKNOWN, None)
    chunks, size = [], 0
    try:
        for chunk in r.iter_content():
            chunks.append(chunk)
            size += len(chunk)
            if size >= _SF_PROBE_CAP:
                break
    except http.RequestsError:
        return (
            UNKNOWN,
            None,
        )  # torn mid-stream (timeout on a trickling feed) -> retry later
    finally:
        r.close()
    if r.status_code in (404, 410):
        return DEAD, None
    if r.status_code != 200:
        return UNKNOWN, None
    text = b"".join(chunks).decode("utf-8", "replace")
    if "base.google.com/ns/1.0" in text or "<rss" in text:
        return LIVE, text.count("<item>")
    if "<urlset" in text:
        return LIVE, text.count("/job/")
    return DEAD, None


#: What a TalentBrew results page states its own count as. On the 17 fronts measured 2026-09-26 it
#: equalled the sitemap's job count on 16 and was 2 more on the 17th.
_RADANCY_TOTAL = re.compile(rb'data-total-job-results="(\d+)"')


def _radancy_get(url):
    """`_get`, plus the host the redirects ended on: (status, final host, body)."""
    try:
        r = _fetch("GET", url, headers={"User-Agent": UA})
    except http.RequestsError as e:
        if _is_dns(e):
            return "dns", "", b""
        _note(_net_reason(e))
        return None, "", b""
    if r is None:
        _note("breaker-open")
        return None, "", b""
    if r.status_code not in (200, 404, 410):
        _note(f"http-{r.status_code}")
    return (
        r.status_code,
        (urllib.parse.urlsplit(r.url).hostname or "").lower(),
        r.content,
    )


def p_radancy(t, u):
    """The front's `/sitemap.xml`, counted by the scraper's own job-URL shape on the front's own
    host; `/search-jobs` only to tell an empty front from a host that is not one.

    A sitemap with no job URL is both: three live fronts with nothing open serve a `<urlset>` of
    content pages, and so do the 285 other hosts with a `/sitemap.xml` measured 2026-09-26. The
    three state `data-total-job-results="0"` on their own `/search-jobs`; of the 285, the 14 that
    state the attribute at all redirect it to another front's filtered results
    (`disneytech.com` to `www.disneycareers.com/en/search-jobs?acm=…`) — a view of a Board held
    under that host, not a Board. So the count is read only where the page stays on the host.
    robots.txt disallows `/search-jobs/` (the results endpoint beneath it) on every front
    measured, not this page. A front a customer has left redirects to `www.radancy.com`
    (`www.tmp.com`, `www.aia.co.uk`) or its new site and reads the same way: DEAD. So does an
    alias host whose sitemap lists another host's jobs (`www.takedajobs.com`, `jobs.takeda.com`'s
    Board). A DNS failure is DEAD: fronts sit on the customer's own hosts, not a wildcard zone.
    """
    slug = _slug_of("radancy", t, u)
    status, _, body = _radancy_get(f"https://{slug}/sitemap.xml")
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    jobs = len(_radancy_sitemap_rows(body.decode("utf-8-sig", "replace"), slug))
    if jobs:
        return LIVE, jobs
    status, landed, page = _radancy_get(f"https://{slug}/search-jobs")
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    stated = _RADANCY_TOTAL.search(page) if landed == slug else None
    return (LIVE, int(stated.group(1))) if stated else (DEAD, None)


def p_rippling(t, u):
    return _classify(
        _scraper_for_row("rippling", t, u).url(),
        lambda b: _len_of(b, "items", "jobs"),
    )


# Eightfold's own careers board, served as the fallthrough for any {slug}.eightfold.ai host that
# resolves but has no tenant board behind it. Its job URLs carry `?domain=eightfold.ai`; a real
# tenant's carry its own (`domain=micron.com`, `domain=kering.com`, ...). Verified 2026-07-27:
# accenture / adp / walmart all returned the *byte-identical* 14,403-byte sitemap — same SHA-256,
# 63 jobs each — while 24 sampled known-good Boards every one carried their own domain. Without
# this check those hosts read as LIVE with a plausible-looking 63 jobs and the scrape would file
# Eightfold's own postings under the wrong company.
_EF_VENDOR_DOMAIN = re.compile(
    r"/careers/job/[^<\s]*?[?&]domain=eightfold\.ai\b", re.IGNORECASE
)
_EF_TENANT_DOMAIN = re.compile(
    r"/careers/job/[^<\s]*?[?&]domain=([^&\"'<\s]+)", re.IGNORECASE
)


def _eightfold_is_vendor_board(text):
    """True when this sitemap is Eightfold's own board rather than the queried tenant's."""
    domains = {d.lower() for d in _EF_TENANT_DOMAIN.findall(text)}
    if not domains:
        return False  # no domain= to judge by (older sitemaps) — leave the verdict to the caller
    return domains == {"eightfold.ai"} or bool(
        _EF_VENDOR_DOMAIN.search(text) and len(domains) == 1
    )


_EF_GROUP_ID = re.compile(r'_EF_GROUP_ID\s*=\s*"([^"]+)"')
# The vendor's own group ids. A host serving these has no tenant board of its own, whichever
# surface you ask — `volkscience.com` is Eightfold's pre-rename identity and the portal default.
_EF_VENDOR_GROUPS = {"volkscience.com", "eightfold.ai"}


def _eightfold_pcsx(t):
    """The Board's PCSX total, the surface ``EightfoldScraper`` actually prefers.

    Always returns a (verdict, jobs) pair. DEAD only where a surface *definitively* says "not
    here" (404/410, or no board app on the page); everything inconclusive — 403, 429, 5xx,
    timeout — is UNKNOWN, never DEAD. That distinction is the whole point: this runs when the
    sitemap already 404'd, so a transient failure here would otherwise manufacture a definitive
    DEAD out of two non-answers.

    Measured 2026-07-27: ``bms.eightfold.ai`` 404s its sitemap yet serves 772 jobs here, so the
    sitemap-only DEAD was wrong for it. The group id doubles as the fallthrough check —
    accenture/adp/walmart/target all report ``volkscience.com``.
    """
    status, body = _get(f"https://{t}/careers")
    if status in (404, 410):
        return DEAD, None  # both surfaces say there is nothing here
    if status != 200:
        return UNKNOWN, None
    m = _EF_GROUP_ID.search(body.decode("utf-8", "replace"))
    if not m:
        return DEAD, None  # a page, but not an Eightfold board app
    group = m.group(1)
    if group.lower() in _EF_VENDOR_GROUPS:
        return DEAD, None  # vendor fallthrough, not this company's Board
    query = urllib.parse.urlencode(
        {"domain": group, "query": "", "location": "", "start": 0}
    )
    status, body = _get(f"https://{t}/api/pcsx/search?{query}")
    if status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    try:
        data = json.loads(body.decode("utf-8", "replace")).get("data") or {}
    except ValueError:
        return UNKNOWN, None
    if "count" not in data:
        return UNKNOWN, None
    return LIVE, int(data.get("count") or 0)


_EF_JOB_LOC = re.compile(
    r"<loc>\s*([^<\s]*/careers/job/[^<\s]+?)\s*</loc>", re.IGNORECASE
)
# A confirming fetch is one cheap job URL, not every one — this predicate runs across hundreds of
# boards a pass. 3 protects against a single already-filled posting false-negativing an otherwise
# healthy, high-churn board (a thousands-of-postings retail tenant like Starbucks turns over daily)
# without meaningfully adding to the request budget.
_EF_CONFIRM_SAMPLE = 3


def _eightfold_confirm_live(text):
    """True once one of the sitemap's own listed job URLs actually resolves.

    The sitemap 200ing with a plausible ``/careers/job/`` count is not proof the board still
    exists: a tenant that has fully migrated off Eightfold can leave the sitemap serving 200 with
    thousands of now-dead job URLs behind. Measured 2026-08-19: ``nttdata.eightfold.ai``'s sitemap
    is a 200, 3.9MB of ``/careers/job/...`` URLs — every one sampled 404s, and its ``/careers``
    page 307s to ``careers.services.global.ntt``, i.e. the tenant isn't on Eightfold anymore. A
    live job page carries its own ``_EF_GROUP_ID`` — the same marker ``_eightfold_pcsx`` reads off
    the careers page — so a 200 that doesn't carry it, or that carries a vendor group id
    (``_EF_VENDOR_GROUPS``, the same fallthrough ``_eightfold_pcsx`` guards against), doesn't count
    as confirmed either.
    """
    for job_url in _EF_JOB_LOC.findall(text)[:_EF_CONFIRM_SAMPLE]:
        status, body = _get(job_url)
        if status != 200:
            continue
        m = _EF_GROUP_ID.search(body.decode("utf-8", "replace"))
        if m and m.group(1).lower() not in _EF_VENDOR_GROUPS:
            return True
    return False


def p_eightfold(t, u):
    # The public PCSX board: /careers/sitemap.xml lists every job as a /careers/job/ URL (or a
    # sitemap_index of child sitemaps — for the count we just probe the first child). A 200 that
    # is neither shape (internal-mobility-only tenant behind SSO, or a non-EF host) means no public
    # board -> DEAD. Jobs count is a lower bound on index-split tenants; enough for the hiring cut.
    status, body = _get(f"https://{t}/careers/sitemap.xml")
    if status == "dns":
        return DEAD, None
    if status in (404, 410):
        # One surface saying "not here" isn't the whole board. The scraper *prefers* the PCSX
        # API, so ask it before calling this dead.
        return _eightfold_pcsx(t)
    if status != 200:
        return UNKNOWN, None
    text = body.decode("utf-8", "replace")
    if _eightfold_is_vendor_board(text):
        return DEAD, None
    n = text.count("/careers/job/")
    if n:
        # The count alone isn't proof the board still resolves (see _eightfold_confirm_live) — a
        # stale sitemap 200s exactly like a live one. If none of the sample confirms, this surface
        # is as good as gone: fall through the same as a 404/410 sitemap already does.
        if _eightfold_confirm_live(text):
            return LIVE, n
        return _eightfold_pcsx(t)
    child = re.search(
        r"<loc>\s*([^<\s]*sitemap[^<\s]*\.xml[^<\s]*)\s*</loc>", text, re.IGNORECASE
    )
    if child and "index" not in child.group(1).lower():
        cs, cbody = _get(child.group(1))
        if cs == 200:
            ctext = cbody.decode("utf-8", "replace")
            if _eightfold_is_vendor_board(ctext):
                return DEAD, None
            cn = ctext.count("/careers/job/")
            if not cn or _eightfold_confirm_live(ctext):
                return LIVE, cn
            return _eightfold_pcsx(t)
    if "<urlset" in text or "<sitemapindex" in text:
        return LIVE, 0  # valid but empty board
    return DEAD, None


def p_trakstar(t, u):
    status, body = _get(_scraper_for_row("trakstar", t, u).url())
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    n = len(body.decode("utf-8", "replace").split("js-careers-page-job-list-item")) - 1
    return LIVE, max(n, 0)


def p_personio(t, u):
    # The scraper's own feed, on the host its `slug_from` reads. 634 rows in this ledger carry a
    # job deep link with tracking params in `url` (cc_miner stored the raw capture), and
    # `rstrip("/")` left the path and query in place: the probe then fetched
    # `.../job/186062?language=de/xml`, where the `/xml` lands INSIDE the query string, so
    # Personio served the ordinary HTML job page with a 200 and this counted zero `<position>`
    # entries. Every one of the 312 such rows was recorded live with jobs=0 — probed as alive
    # while the scraper could never read them.
    status, body = _get(_scraper_for_row("personio", t, u).url())
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    return LIVE, body.decode("utf-8", "replace").count("<position>")


def p_join(t, u):
    status, body = _get(_scraper_for_row("join", t, u).url())
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    m = re.search(rb'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', body, re.DOTALL)
    if not m:
        return UNKNOWN, None
    try:
        state = (json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}
    except Exception:  # noqa: BLE001
        return UNKNOWN, None
    # join.com serves a 200 Next.js error page (pageProps.statusCode 404 "Entity not found" / 410
    # "Resource deleted") for a company that's gone — a soft-404, so DEAD.
    if state.get("statusCode") in (404, 410):
        return DEAD, None
    cid = ((state.get("initialState") or {}).get("company") or {}).get("id")
    if not cid:
        return UNKNOWN, None
    return _classify(
        f"https://join.com/api/public/companies/{cid}/jobs?locale=en&page=1&pageSize=1",
        lambda b: (lambda d: (d.get("pagination") or {}).get("rowCount"))(  # noqa: PLC3002
            json.loads(b)
        ),
    )


def p_jobvite(t, u):
    """One GET of the board, deliberately NOT following redirects.

    Jobvite answers a departed tenant with ``302 Location: http://search.jobvite.com?invalid=1``,
    and that chain ends on a 174 KB marketing page with a **200** — so a redirect-following probe
    records a dead tenant as LIVE with zero jobs, which is the shape that keeps a gone Board in
    the scrape list forever. Measured across the whole 517-tenant pool 2026-09-07: 83 tenants
    redirect (78 ``invalid=1``, one to an ``app.jobvite.com`` login wall, four to the customer's
    own domain) and **not one of the 434 live boards does**, so on this ATS any 3xx is definitive.

    The count is the board's own pagination counter (``1-50 of 2,831``), which page 0 already
    carries — so a full board costs exactly one request. A live board with no counter at all is
    the empty board: 33 of the 434 serve that, a 200 with no postings and no counter, so it is
    LIVE with 0 rather than unparseable. The URL and the counter parse are imported from
    ``JobviteScraper`` so the probe and the scrape cannot drift.
    """
    try:
        r = _fetch(
            "GET",
            _scraper_for_row("jobvite", t, u).url(),
            headers={"Accept": "text/html"},
            allow_redirects=False,
        )
    except http.RequestsError as e:
        if _is_dns(e):
            return DEAD, None
        _note(_net_reason(e))
        return UNKNOWN, None
    if r is None:  # breaker open -> transient
        _note("breaker-open")
        return UNKNOWN, None
    if r.status_code in (301, 302, 303, 307, 308):
        # The tenant is gone, login-walled, or has moved its career site off the Jobvite-hosted
        # surface. All three mean there is no public board here to read.
        return DEAD, None
    if r.status_code in (404, 410):
        return DEAD, None
    if r.status_code != 200:
        _note(f"http-{r.status_code}")
        return UNKNOWN, None
    page = r.content.decode("utf-8", "replace")
    if _JOBVITE_JOB.search(page):
        return LIVE, total_of(page) or len(set(_JOBVITE_JOB.findall(page)))
    # No posting links: an empty board, which is live and hiring nobody. Guarded on the job-link
    # regex rather than on the counter alone so a template that ever drops the counter still
    # reports its postings.
    return LIVE, 0


def p_zwayam(t, u):
    """One POST to the shared API, which selects the Board by hostname — the slug.

    Read the BODY, never the status: a hostname that is no longer a registered Board answers
    HTTP 200 with `"data": null`, identical in every other respect to a live one — and a
    *failing* request answers HTTP 200 with `data: null` too, distinguished only by its body
    `code` of 500, so DEAD additionally requires `body_error_code` to be quiet. The whole
    request — url, headers and body — comes from the scraper's `search_request`, and the
    dead-vs-failed line from its `body_error_code`, so neither can drift; an earlier version
    imported only the body helpers and re-declared the headers, and had already drifted on the
    User-Agent.
    """
    url, headers, body = search_request(_slug_of("zwayam", t, u))
    try:
        r = _fetch("POST", url, data=body, headers=headers)
    except http.RequestsError as e:
        if _is_dns(e):
            return DEAD, None
        _note(_net_reason(e))
        return UNKNOWN, None
    if r is None:  # breaker open -> transient
        _note("breaker-open")
        return UNKNOWN, None
    if r.status_code != 200:
        # Never DEAD on a status alone here: a real absence is a 200 with `data: null`, so any
        # non-200 is about the request or the edge, not the Board. (A 403 from the Akamai front
        # has been seen once, in 2026-08 discovery; ~2,160 requests of deliberate load-testing on
        # 2026-08-27 — 34 req/s sustained, 32-wide concurrency, 60 distinct domains — could not
        # reproduce it.) **That "rare and transient" reading is now falsified**: measured
        # 2026-09-17, the 403 is a cumulative per-IP request quota and the earlier sweep simply
        # stayed under it — 100/200/300/400 requests all answered 200, 23 of 100 were refused at
        # 500 and 100 of 100 at 600. It is volume, not width, which is why a 32-wide burst missed
        # it. `_QUOTA_403` now rotates the egress on it; see `experiment/zwayam-403-wall/LOG.md`.
        return UNKNOWN, None
    try:
        payload = r.json() or {}
    except ValueError:
        return UNKNOWN, None
    failed = body_error_code(payload)
    if failed is not None:
        # A failing request carries the same `data: null` a dead Board answers (measured
        # 2026-08-27 by sending malformed input); only a quiet body code makes the null
        # authoritative. Anything else is the request or the service, not the Board.
        _note(f"body-code-{failed}")
        return UNKNOWN, None
    data = payload.get("data")
    if not data:
        return DEAD, None
    return LIVE, data.get("totalCount", 0)


_JAZZHR_ROW = re.compile(rb'id="row_job_(\w+)"')


def p_jazzhr(t, u):
    """The embed listing, the same surface `JazzHRScraper.url()` reads.

    Read the SHAPE, never the status: a departed tenant answers **200**, not 404. Measured over
    a 1,000-tenant random sample of the pool, 2026-09-07 — every one of the 1,000 answered 200
    at the transport layer, and the 75 dead ones split into the vendor's own
    "JazzHR - Inactive Career Page" on the wildcard host (66) and a 302 to its job-seeker
    marketing page (9, followed by `_get` into another 200). Neither renders the `jobs_table`
    shell, and a slug that was never a tenant behaves identically (wildcard DNS resolves, then
    302s), so a 200 without the shell is the only definitive DEAD signal here — the freshteam
    and zoho soft-404 precedent.

    The count is the number of DISTINCT `row_job` ids, not the raw match count: the same page
    renders every posting twice, once in a desktop `<tr>` table and once in a mobile `<div>`
    list (60 elements for 30 postings on `10pearls`).

    Cross-checked against an independent career-page (`/apply/`) harvest of the same 1,000
    tenants: 923 live/live, 75 dead/dead, zero contradictions. The two rows that differed were
    one transport timeout (correctly UNKNOWN here) and one empty board the career-page harvest
    miscounted.
    """
    status, body = _get(_scraper_for_row("jazzhr", t, u).url())
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    if b'id="jobs_table"' not in body:
        return (
            DEAD,
            None,
        )  # a 200 that is not a board: inactive tenant, or the vendor's page
    return LIVE, len(set(_JAZZHR_ROW.findall(body)))


def p_oracle(t, u):
    """The requisition listing, the same surface `OracleScraper.url()` reads.

    **No `siteNumber`.** It is a filter, not an address: a site number narrows the board to one
    of the tenant's sites, and omitting it returns the whole host — the exact union of every
    site, verified across all 596 hosts with a hiring board (docs/oracle/). Probing with the
    scraper's old hardcoded `CX_1` would have under-counted 929 of 1,331 hiring boards, and
    under-counted *silently*, since a wrong site still answers 200 with a valid envelope.

    `TotalJobsCount` is the count rather than `len(requisitionList)`: the page is capped at 200
    and 199 boards exceed it, so the list length would record every large board as exactly 200.

    **There is no definitive DEAD signal beyond DNS and 404/410**, so nothing else is treated as
    one. Measured on four hosts that are certainly not tenants: one answered 503 and three timed
    out — both classic transients, and a wildcard DNS record means the host resolves either way.
    Settling either as DEAD would bury real boards on a bad afternoon, so a nonexistent tenant
    stays UNKNOWN here and is re-probed. That is the status-is-not-a-mechanism rule; a 503 from
    this API means "ask again", not "gone".
    """
    # `limit=1`, not `url()`'s page of 200: `TotalJobsCount` is the whole total either way.
    host = _slug_of("oracle", t, u)
    return _classify(
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        f"?onlyData=true&expand=requisitionList&finder=findReqs;limit=1,offset=0",
        _oracle_total,
    )


def _oracle_total(body):
    try:
        items = json.loads(body).get("items") or []
    except Exception:  # noqa: BLE001 — an unparseable body is UNKNOWN, not a verdict
        return None
    return (items[0].get("TotalJobsCount") or 0) if items else None


def p_phenom(t, u):
    """One POST to the tenant's own `/widgets`, the same surface `PhenomScraper._listing` reads.

    The payload comes from the scraper's own `_search_payload`, not a copy of it here, for the
    reason `p_zwayam` gives: a re-declared request drifts, and this one has a discriminator
    (`ddoKey`) and fourteen other keys to drift on.

    `totalHits` is the count rather than `len(jobs)`: the page size is asked for as 1, so the list
    length would record every live Board as exactly 1.

    **The locale prefix is not probed.** It decides which *links* resolve, not which postings
    come back — measured on all seven tenants the seed marks `global`, whose totals are identical
    read as `us` or as `global` — so the probe asks with the default and the scraper learns the
    real prefix at scrape time from the redirect.

    **Nothing but DNS and 404/410 settles as DEAD.** A non-tenant host usually fails DNS outright;
    what a 403 or a timeout means here is "ask again" (the seed carries one host behind a bot wall
    that answered 403 to every probe while serving a real board in a browser), so those stay
    UNKNOWN and are re-probed rather than buried.
    """
    scraper = _scraper_for_row("phenom", t, u)
    status, body = _post(
        scraper.widgets_url(),
        scraper._search_payload(0, 1),
        {"User-Agent": UA, "Accept": "*/*", "Content-Type": "application/json"},
    )
    total = None
    if status == 200 and isinstance(body, dict):
        refine = body.get("refineSearch")
        if isinstance(refine, dict):
            total = refine.get("totalHits")
    return _verdict(status, total if isinstance(total, int) else None)


def p_taleo_be(t, u):
    """Walk TBE's cookie-backed ten-row pages and return the actual Board count."""
    from urllib.parse import urljoin

    page_url, seen_pages, ids = _scraper_for_row("taleo_be", t, u).url(), set(), set()
    for _ in range(1_000):
        if page_url in seen_pages:
            return UNKNOWN, None
        seen_pages.add(page_url)
        status, body = _get(page_url)
        if status == "dns" or status in (404, 410):
            return DEAD, None
        if status != 200:
            return UNKNOWN, None
        text = body.decode("utf-8", "replace")
        if _TALEO_GONE in text.lower():
            return DEAD, None
        if "oracletaleocwsv2" not in text:
            return UNKNOWN, None
        ids.update(_TALEO_JOB.findall(text))
        next_match = _TALEO_NEXT.search(text)
        if not next_match:
            return LIVE, len(ids)
        page_url = urljoin(page_url, html.unescape(next_match.group("href")))
    return UNKNOWN, None


def p_taleo_enterprise(t, u):
    """Count a public Career Section through its measured JSON listing surface."""
    try:
        scraper = _scraper_for_row("taleo_enterprise", t, u)
        response = http.fetch(
            "GET",
            scraper.url(),
            headers={"User-Agent": UA, "Accept": "application/json, text/html"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return LIVE, len(scraper._listing(response.text, timeout=TIMEOUT))
    except Exception as exc:  # noqa: BLE001 - network/shape failure stays retryable
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return (DEAD, None) if status in (404, 410) else (UNKNOWN, None)


PROBES = {
    "greenhouse": p_greenhouse,
    "lever": p_lever,
    "adp": p_adp,
    "adp_recruiting": p_adp_recruiting,
    "ashby": p_ashby,
    "avature": p_avature,
    "bamboohr": p_bamboohr,
    "breezy": p_breezy,
    "clearcompany": p_clearcompany,
    "cornerstone": p_cornerstone,
    "recruitee": p_recruitee,
    "workable": p_workable,
    "zoho": p_zoho,
    "workday": p_workday,
    "keka": p_keka,
    "ripplehire": p_ripplehire,
    "darwinbox": p_darwinbox,
    "smartrecruiters": p_smartrecruiters,
    "teamtailor": p_teamtailor,
    "rippling": p_rippling,
    "trakstar": p_trakstar,
    "peoplestrong": p_peoplestrong,
    "personio": p_personio,
    "join": p_join,
    "freshteam": p_freshteam,
    "eightfold": p_eightfold,
    "successfactors": p_successfactors,
    "zwayam": p_zwayam,
    "jazzhr": p_jazzhr,
    "jibe": p_jibe,
    "jobvite": p_jobvite,
    "oracle": p_oracle,
    "phenom": p_phenom,
    "pinpoint": p_pinpoint,
    "pyjamahr": p_pyjamahr,
    "radancy": p_radancy,
    "taleo_be": p_taleo_be,
    "taleo_enterprise": p_taleo_enterprise,
    "gem": p_gem,
}


def _parse_args(args):
    indir = ROOT / "data" / "ats-tenants-merged"
    ledger_dir = liveness_ledger.dir_for(ROOT)
    limit = 0
    live_ttl, dead_ttl = liveness_ledger.LIVE_TTL_DAYS, liveness_ledger.DEAD_TTL_DAYS
    unknown_ttl = liveness_ledger.UNKNOWN_TTL_DAYS
    force = False
    rest = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--dir":
            indir = Path(args[i + 1])
            i += 2
        elif a == "--ledger-dir":
            ledger_dir = Path(args[i + 1])
            i += 2
        elif a == "--limit":
            limit = int(args[i + 1])
            i += 2
        elif a == "--live-ttl":
            live_ttl = int(args[i + 1])
            i += 2
        elif a == "--dead-ttl":
            dead_ttl = int(args[i + 1])
            i += 2
        elif a == "--unknown-ttl":
            unknown_ttl = int(args[i + 1])
            i += 2
        elif a == "--force":
            force = True
            i += 1
        else:
            rest.append(a)
            i += 1
    return indir, ledger_dir, limit, live_ttl, dead_ttl, unknown_ttl, force, set(rest)


def main():
    indir, ledger_dir, limit, live_ttl, dead_ttl, unknown_ttl, force, filt = (
        _parse_args(sys.argv[1:])
    )
    ledger_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).date()
    today_iso = today.isoformat()

    # Per ATS: carry the whole ledger forward (verdicts[ats]: tenant -> Verdict), and re-probe only
    # the pool rows that are new, unknown, or past their TTL (ADR-0012). The rest stay untouched.
    verdicts: dict[str, dict[str, liveness_ledger.Verdict]] = {}
    buckets = {}  # ats -> [(ats, tenant, url), ...] to probe this run
    for csvf in sorted(indir.glob("*.csv")):
        ats = csvf.stem
        if ats not in PROBES or (filt and ats not in filt):
            continue
        ledger = liveness_ledger.load(ledger_dir / f"{ats}.csv")
        verdicts[ats] = ledger
        rows = _respell_pool_rows(
            ats, list(csv.DictReader(csvf.open(encoding="utf-8")))
        )
        todo = [
            r
            for r in rows
            if force
            or liveness_ledger.needs_probe(
                ledger.get(r["tenant"]),
                today,
                live_ttl=live_ttl,
                dead_ttl=dead_ttl,
                unknown_ttl=unknown_ttl,
            )
        ]
        # Boards buried as duplicates of another live Board (ADR-0111). Dropped from the work list
        # rather than probed-and-marked: they answer 200 and serve a full board, so any verdict
        # this could reach would be `live` — which is true, and beside the point. Their ledger row
        # carries forward untouched, and `scrapable_boards.load` is what keeps them out of
        # the scrape. This is the free half of #157's problem, without the frozen set.
        todo = _drop_alias_duplicates(ats, todo, ledger_dir)
        if limit:
            todo = todo[:limit]
        if todo:
            buckets[ats] = [(ats, r["tenant"], r["url"]) for r in todo]

    items = [x for row in zip_longest(*buckets.values()) for x in row if x is not None]
    print(
        f"to probe: {len(items)} of {sum(len(v) for v in verdicts.values())} known "
        f"(live_ttl={live_ttl}d, dead_ttl={dead_ttl}d, unknown_ttl={unknown_ttl}d"
        f"{', force' if force else ''})",
        flush=True,
    )
    lock = threading.Lock()

    def flush_ledger():
        for ats, rows in verdicts.items():
            liveness_ledger.write(ledger_dir / f"{ats}.csv", rows.values())

    def run_pass(work, timeout, workers):
        global TIMEOUT
        TIMEOUT = timeout
        unknowns = []
        ulock = threading.Lock()
        probed = 0

        def do(item):
            nonlocal probed
            ats, tenant, url = item
            _ctx.ats = (
                ats  # so _note attributes this worker's failures to the right ATS
            )
            # dead by convention — no probe spent (ADR-0034 nonprod, #157 alias losers)
            if is_nonprod(tenant, url) or _is_eightfold_alias_loser(ats, tenant):
                verdict, jobs = DEAD, None
            else:
                try:
                    verdict, jobs = PROBES[ats](tenant, url)
                except Exception as e:  # noqa: BLE001
                    _note(f"probe-raised-{type(e).__name__}")
                    verdict, jobs = UNKNOWN, None
            if verdict == UNKNOWN:
                with ulock:
                    unknowns.append(item)
                    probed += 1
                return
            with lock:  # settle it in the ledger (url refreshed from the pool)
                verdicts[ats][tenant] = liveness_ledger.Verdict(
                    ats, tenant, url, verdict, jobs, today_iso
                )
            with ulock:
                probed += 1

        # Checkpoint the ledger every 60s so a crash or network drop mid-pass keeps everything
        # settled so far (a gated single-host pass can run for an hour) — and heartbeat progress.
        stop = threading.Event()

        def checkpoint():
            while not stop.wait(60):
                with lock:
                    flush_ledger()
                print(
                    f"  [checkpoint] {probed}/{len(work)} probed, {len(unknowns)} unknown so far",
                    flush=True,
                )

        ticker = threading.Thread(target=checkpoint, daemon=True)
        ticker.start()
        start = time.monotonic()
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(do, work))
        finally:
            stop.set()
            ticker.join()
        elapsed = time.monotonic() - start
        print(
            f"  pass done in {elapsed:.1f}s ({len(work) / elapsed:.0f} boards/s)",
            flush=True,
        )
        _report_reasons(len(unknowns))
        _report_cs_cleared()
        _report_egress()
        return unknowns

    for n, (timeout, cap) in enumerate(PASSES, 1):
        if not items:
            break
        workers = min(
            cap, len(items)
        )  # never more workers than boards left in the tail
        print(
            f"pass {n}: {len(items)} to probe (timeout={timeout}s, workers={workers})",
            flush=True,
        )
        items = run_pass(items, timeout, workers)
        flush_ledger()  # checkpoint per pass so a crash keeps settled verdicts
        settled = sum(
            1
            for rows in verdicts.values()
            for v in rows.values()
            if v.checked_at == today_iso
        )
        print(
            f"  -> {settled} settled this run, still unknown {len(items)}", flush=True
        )

    # Whatever is still unknown after the last pass is recorded as such (re-probed next run),
    # never silently dropped — except over a `live` verdict, which is kept as-is (ADR-0177):
    # `unknown` leaves the Scrapable set and `index prune` would evict the Board's rows outright.
    # Its stale `checked_at` still puts it back in the next run's probe list.
    for ats, tenant, url in items:
        prior = verdicts[ats].get(tenant)
        if prior is not None and prior.status == LIVE:
            continue
        verdicts[ats][tenant] = liveness_ledger.Verdict(
            ats, tenant, url, UNKNOWN, None, today_iso
        )
    flush_ledger()

    for ats in sorted(verdicts):
        rows = verdicts[ats].values()
        live = sum(1 for v in rows if v.status == LIVE)
        dead = sum(1 for v in rows if v.status == DEAD)
        unknown = sum(1 for v in rows if v.status == UNKNOWN)
        print(f"{ats}: live {live}, dead {dead}, unknown {unknown}", flush=True)


if __name__ == "__main__":
    main()
