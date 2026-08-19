#!/usr/bin/env python3
"""Liveness checker for ATS tenant lists — loss-free three-state, multi-pass, ledger-backed.

Reads the candidate pool (a dir of {ats}.csv) and classifies each board into one of three states,
so a transient blip never gets mistaken for a dead board:

  LIVE     -> 200 with a parseable job list
  DEAD     -> definitive: 404/410, or DNS doesn't resolve (curl code 6)
  UNKNOWN  -> couldn't tell: timeout, reset, 5xx, 429, parse-fail  -> re-probed next pass

Verdicts land in the liveness ledger (ADR-0012), one CSV per ATS at
data/validate/liveness/{ats}.csv (ats,tenant,url,status,jobs,checked_at) — the single source of
truth. The Active list is just its status==live rows (config.load_active_companies); dead is
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

Run:   python scripts/validate/check_liveness.py                       # all ATSes, respect TTLs
       python scripts/validate/check_liveness.py zoho workday          # only these ATSes
       python scripts/validate/check_liveness.py --force               # re-probe everything
       python scripts/validate/check_liveness.py --live-ttl 3 --limit 20  # smoke
"""

import csv
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
from headstart import http, liveness  # needs src on sys.path first
from headstart.models import (  # one host rule, shared with the scrapers
    host_of,
)
from headstart.scrapers.workday import (  # the DC list, single source of truth
    INSTANCES as _WD_INSTANCES,
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
class _HostGate:
    def __init__(self, cap, spacing, key=""):
        self.sem = threading.BoundedSemaphore(cap)
        self.spacing = spacing  # min seconds between request *starts*
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
}
_gates_lock = threading.Lock()
# Auto-gate defaults for a host that starts refusing without a seeded entry, and the bounds
# ease() moves between (50 req/s down to 1 req/s, where it stops easing and trips instead).
_AUTO_CAP, _AUTO_SPACING = 16, 0.1
_MIN_SPACING, _MAX_SPACING = 0.02, 1.0


def _spanning_key(netloc):
    """The listed domain this host sits under, if any — never inferred, only matched."""
    return next(
        (d for d in _SPANNING if netloc == d or netloc.endswith("." + d)),
        None,
    )


def _gate_for(netloc):
    """The gate governing this host, or None if it has never pushed back."""
    with _gates_lock:
        gate = _GATES.get(netloc)
        if gate is None:
            span = _spanning_key(netloc)
            gate = _GATES.get(span) if span else None
        return gate


def _ensure_gate(netloc, why):
    """A host that starts refusing with no gate gets one, keyed on the exact host.

    Deliberately never widened to a parent domain: an unlisted host might be one tenant among
    thousands on a shared suffix (Workday), and throttling — or worse, banning — all of them off
    one tenant's refusal costs far more coverage than the extra requests ever save.
    """
    with _gates_lock:
        gate = _GATES.get(netloc)
        if gate is None:
            span = _spanning_key(netloc)
            gate = _GATES.get(span) if span else None
        if gate is None:
            gate = _GATES[netloc] = _HostGate(_AUTO_CAP, _AUTO_SPACING, netloc)
            print(
                f"  [gate] {netloc} answered {why} unprompted — throttling it to "
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
    r = _through_gate(
        gate,
        lambda: http.fetch(
            method, url, timeout=TIMEOUT, verify=False, attempts=_ATTEMPTS, **kw
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
        _on_429(gate or _ensure_gate(netloc, "429"), r)
    elif challenged:
        _note(f"challenge-{r.status_code}")
        (gate or _ensure_gate(netloc, str(r.status_code))).trip(
            _CHALLENGE_COOLDOWN_S, f"{r.status_code}, bot-wall challenge"
        )
    return r


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


def _on_429(gate, r):
    """Back off this host: ban outright when it tells us to, else ease the rate and keep going.

    The last branch used to trip on a run of *consecutive* 429s. That cannot work on a gate many
    boards share: any one of hundreds of threads getting a 200 reset the counter, so reaching the
    threshold depended on luck — measured, 1,800 rejections in 2,000 requests left the counter at
    5. Easing to the floor is the honest signal instead: if we are already as slow as this gate
    goes and the host still refuses, slowing further cannot help, so stop asking.
    """
    retry = _retry_after_s(r.headers.get("Retry-After"))
    if retry and retry > _BAN_RETRY_AFTER_S:
        gate.trip(retry, f"429, retry-after {retry}s")
    elif _is_challenge(r):
        gate.trip(_CHALLENGE_COOLDOWN_S, "429, bot-wall challenge")
    elif not gate.ease():
        gate.trip(
            _CHALLENGE_COOLDOWN_S, f"still refusing at {1 / _MAX_SPACING:.1f} req/s"
        )


LIVE, DEAD, UNKNOWN = "live", "dead", "unknown"

# Non-production boards are dead by convention, however alive their endpoint (ADR-0034).
# Probing cannot tell fabricated jobs from real ones — sandbox tenants 200 with
# production-looking counts (measured: eightfold amdocs-sandbox 2,099 "jobs" vs the real
# board's 5; citigroup-qa-sandbox 3,193; ripplehire hdfcbank-uat 28,886 vs 173) — so the
# HOST NAME is the signal: sandbox/uat/demo as a delimited token in tenant or url. Token-
# bounded on purpose: one-word names like sandboxvr or thesandbox must not match.
_NONPROD = re.compile(r"(?:^|[-./_])(?:sandbox|uat|demo)(?:[-./_]|$)", re.IGNORECASE)
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
    return bool(_NONPROD.search(tenant or "") or _NONPROD.search(url or ""))


_ZOHO_JOBS = re.compile(r'value="([^"]*)"\s+id="jobs"')
_TOKEN = re.compile(r"token=([A-Za-z0-9_-]+)")
_WD_URL = re.compile(r"^https://([^.]+)\.(wd\d+)\.myworkdayjobs\.com/([^/?#]+)")


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


# --- per-ATS probes: return (verdict, jobs) ---


def p_greenhouse(t, u):
    return _classify(
        f"https://boards-api.greenhouse.io/v1/boards/{t}/jobs",
        lambda b: _len_of(b, "jobs"),
    )


def p_lever(t, u):
    # Try the instance the discovered url hints at, then the other one — the slug alone doesn't
    # say which instance hosts the board, exactly as LeverScraper.fetch_raw already assumes.
    # Trusting the url's hint alone marks a board DEAD whenever discovery found it on the global
    # host but the company actually sits on EU: measured 2026-07-27, 13 boards the ledger called
    # dead answered live on api.eu.lever.co. Only a 404 from *both* instances is definitive.
    hinted = "api.eu" if "jobs.eu.lever.co" in u else "api"
    verdict = DEAD
    for host in (hinted, "api" if hinted == "api.eu" else "api.eu"):
        v, jobs = _classify(
            f"https://{host}.lever.co/v0/postings/{t}?mode=json", _len_of
        )
        if v == LIVE:
            return v, jobs
        if v == UNKNOWN:
            verdict = UNKNOWN  # inconclusive on one instance -> can't call it dead
    return verdict, None


def p_ashby(t, u):
    return _classify(
        f"https://api.ashbyhq.com/posting-api/job-board/{t}",
        lambda b: _len_of(b, "jobs"),
    )


def p_recruitee(t, u):
    return _classify(
        f"https://{t}.recruitee.com/api/offers/", lambda b: _len_of(b, "offers")
    )


def p_workable(t, u):
    return _classify(
        f"https://apply.workable.com/api/v1/widget/accounts/{t}",
        lambda b: _len_of(b, "jobs"),
    )


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
    # Host only, matching ZohoScraper.slug_from — same latent shape as the personio bug above.
    # Zoho's ledger carries 44 pathy / 19 query rows; none is live today, so this is not yet
    # costing coverage, but the identical `/jobs/Careers` suffix would land inside a query.
    host = host_of(u)
    status, body = _get(f"https://{host}/jobs/Careers")
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


def p_keka(t, u):
    # The careers SPA's own job call (read off cdn.keka.com/careers/v/2026/scripts/app/app.min.js:
    # `$.ajax('/api/jobs/${apiPortalName}/active')`, apiPortalName defaulting to "default"). It
    # needs no org UUID, which is what makes it strictly better than the embedjobs route this
    # probe used to take: that one had to recover the UUID from an /ats/documents/{uuid}/ asset
    # path — the careers background image, else the org logo on the /careers page — so a portal
    # with *neither* exposed no UUID at all and was recorded UNKNOWN despite serving jobs.
    # Measured 2026-07-27: 25 of 25 sampled ledger-UNKNOWNs answered LIVE here, with real count
    # variance; dead slugs and garbage controls were unchanged.
    status, body = _get(f"https://{t}.keka.com/careers/api/jobs/default/active")
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
    m = _WD_URL.match(u.rstrip("/"))
    if not m:
        return DEAD, None  # not a Workday URL -> can't be a board
    co, hinted, site = m.groups()

    def probe(inst):
        status, data = _post(
            f"https://{co}.{inst}.myworkdayjobs.com/wday/cxs/{co}/{site}/jobs",
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
    for inst in (hinted, *(i for i in _WD_INSTANCES if i != hinted)):
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
    headers = {"User-Agent": UA}
    try:
        r = http.fetch(
            "GET",
            f"https://{t}.ripplehire.com/candidate/careers",
            headers=headers,
            timeout=TIMEOUT,
            verify=False,
            attempts=_ATTEMPTS,
        )
    except http.RequestsError as e:
        return (DEAD, None) if _is_dns(e) else (UNKNOWN, None)
    m = _TOKEN.search(r.url)
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
            f"https://{t}.ripplehire.com/candidate/candidatejobsearch",
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
    host_tld = "com" if ".darwinbox.com" in u else "in"
    dns_fails = 0
    for tld in (host_tld, *[x for x in ("in", "com") if x != host_tld]):
        api = f"https://{t}.darwinbox.{tld}/ms/candidateapi/job/alljobs?companyId=main"
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
    return (DEAD, None) if dns_fails == 2 else (UNKNOWN, None)


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

    verdict, jobs = _classify(
        f"https://api.smartrecruiters.com/v1/companies/{t}/postings?limit=10", count
    )
    if verdict != LIVE or jobs:
        return verdict, jobs
    served = _sr_board_host(t)
    if served is None:
        return UNKNOWN, None  # couldn't tell -> re-probe rather than guess
    return (LIVE, 0) if served else (DEAD, None)


def p_teamtailor(t, u):
    return _classify(
        f"https://{t}.teamtailor.com/jobs.json", lambda b: _len_of(b, "items")
    )


def p_freshteam(t, u):
    # The public careers widget: a real board always returns JSON with a "jobs" key (0 == live but
    # empty). An unknown/parked slug soft-errors at HTTP 200 with an HTML 404 page off the
    # *.freshteam.com wildcard (so it never 404s / DNS-fails) — non-JSON at 200 is definitively DEAD.
    status, body = _get(f"https://{t}.freshteam.com/hire/widgets/jobs.json")
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    n = _len_of(body, "jobs")
    return (LIVE, n) if n is not None else (DEAD, None)


_SF_PROBE_CAP = 256 * 1024  # capped stream: RMK RSS feeds trickle at ~30 KB/s


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
            f"https://{t}/sitemap.xml",
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


def p_rippling(t, u):
    return _classify(
        f"https://api.rippling.com/platform/api/ats/v1/board/{t}/jobs",
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
    status, body = _get(f"https://{t}.hire.trakstar.com/")
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    n = len(body.decode("utf-8", "replace").split("js-careers-page-job-list-item")) - 1
    return LIVE, max(n, 0)


def p_personio(t, u):
    # Host only, matching PersonioScraper.slug_from. 634 rows in this ledger carry a job deep
    # link with tracking params in `url` (cc_miner stored the raw capture), and `rstrip("/")`
    # left the path and query in place: the probe then fetched `.../job/186062?language=de/xml`,
    # where the `/xml` lands INSIDE the query string, so Personio served the ordinary HTML job
    # page with a 200 and this counted zero `<position>` entries. Every one of the 312 such rows
    # is recorded live with jobs=0 — probed as alive while the scraper could never read them.
    host = host_of(u) or f"{t}.jobs.personio.de"
    status, body = _get(f"https://{host}/xml")
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    return LIVE, body.decode("utf-8", "replace").count("<position>")


def p_join(t, u):
    status, body = _get(f"https://join.com/companies/{t}")
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


PROBES = {
    "greenhouse": p_greenhouse,
    "lever": p_lever,
    "ashby": p_ashby,
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
    "personio": p_personio,
    "join": p_join,
    "freshteam": p_freshteam,
    "eightfold": p_eightfold,
    "successfactors": p_successfactors,
}


def _parse_args(args):
    indir = ROOT / "data" / "ats-tenants-merged"
    ledger_dir = liveness.dir_for(ROOT)
    limit = 0
    live_ttl, dead_ttl = liveness.LIVE_TTL_DAYS, liveness.DEAD_TTL_DAYS
    unknown_ttl = liveness.UNKNOWN_TTL_DAYS
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
    verdicts: dict[str, dict[str, liveness.Verdict]] = {}
    buckets = {}  # ats -> [(ats, tenant, url), ...] to probe this run
    for csvf in sorted(indir.glob("*.csv")):
        ats = csvf.stem
        if ats not in PROBES or (filt and ats not in filt):
            continue
        ledger = liveness.load(ledger_dir / f"{ats}.csv")
        verdicts[ats] = ledger
        rows = list(csv.DictReader(csvf.open(encoding="utf-8")))
        todo = [
            r
            for r in rows
            if force
            or liveness.needs_probe(
                ledger.get(r["tenant"]),
                today,
                live_ttl=live_ttl,
                dead_ttl=dead_ttl,
                unknown_ttl=unknown_ttl,
            )
        ]
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
            liveness.write(ledger_dir / f"{ats}.csv", rows.values())

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
            if is_nonprod(
                tenant, url
            ):  # dead by convention — no probe spent (ADR-0034)
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
                verdicts[ats][tenant] = liveness.Verdict(
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
    # never silently dropped.
    for ats, tenant, url in items:
        verdicts[ats][tenant] = liveness.Verdict(
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
