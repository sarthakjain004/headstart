#!/usr/bin/env python3
"""Recover a Wellfound tab fleet from a DataDome hard block by rotating the egress IP.

Before this, "Access is temporarily restricted" ended a run outright (``HardBlocked``'s own
docstring: *aborts the whole run*) and a human had to wait out the restriction and re-launch with
``--append``. That is the right call when there is only one IP — every further request re-signals
while the restriction is live. It is the wrong call when a fresh IP is one daemon restart away,
which is exactly what :func:`headstart.spare_egress.rotate` already does for the ATS scrapers.

**The block is bound to the IP, so recovery has to move all three things that identify us:**

1. **The egress IP** — ``spare_egress.rotate()``. It restarts the WARP daemon, because a
   ``warp-cli disconnect``/``connect`` pair is measurably a *no-op* for rotation: a registration
   is sticky to its edge node and returns the same IP. Needs passwordless sudo for that one
   command; without it this module degrades to the old behaviour rather than pretending.
2. **The DataDome cookie** — cleared. It was issued to the blocked IP, and presenting it from a
   fresh one is worse than arriving with none: it ties the new IP to the burnt session.
3. **The session's warmth** — one warm-up hop before work resumes, so the first request on the
   new IP is an in-site navigation rather than a cold deep link.

Rotation is **fleet-wide and serialized**: every tab shares one browser, one proxy and therefore
one IP, so the first tab to meet a block rotates while the others wait on the same event, and
none of them re-signals on the burnt IP in the meantime. A second tab arriving during a rotation
rides the one already in flight instead of kickstarting the daemon again.

Bounded on purpose. After :data:`MAX_ROTATIONS` blocks the run stops for a human: past that, the
pattern is no longer "this IP is spent" but "this behaviour is detected", and burning fresh IPs
against it spends the pool that the ATS scrapers also draw on.
"""

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
from headstart import spare_egress

#: How many hard blocks one run will try to rotate past before giving up to a human. Three is a
#: judgement, not a measurement: enough to ride out a single unlucky IP, few enough that a run
#: which is being detected rather than rate-limited stops spending the shared IP pool on it.
MAX_ROTATIONS = 3

#: Seconds a browser gets to stop politely before it is killed. Short on purpose: the graceful
#: path talks CDP, and the browser being replaced is usually the one that stopped answering it.
STOP_GRACE = 3.0

#: Wellfound's own front door — the same hop the drivers warm up on.
WARMUP_URL = "https://wellfound.com/jobs"

#: Seconds with **no page arriving anywhere in the fleet** before :class:`Watchdog` calls it
#: stalled. Tight on purpose, and only meaningful because the beat is per *page load* rather than
#: per finished item: a company takes a median 16.8s and sometimes 57s, so a timer this short on
#: item-completion would fire constantly during healthy work, while on page arrivals — across
#: every tab at once — it fires only when the whole fleet has stopped talking to the site.
#:
#: Sized against recovery, not against politeness: a stall now costs an *in-place* rotation and
#: re-warm (a few seconds, browser kept), so a false positive is cheap and a missed stall is not.
#: Below about this, healthy solves start being cancelled mid-flight — a page that would have
#: arrived is thrown away along with the tab that was earning it.
#:
#: What it catches is the failure that actually happened on 2026-08-27: eight tabs sat in a
#: Cloudflare 403 retry loop (`span.cb-i` timeouts, then a 60s CDP command timeout) for minutes.
#: That is not a DataDome hard block, so `is_hard_block` never returned True and rotation never
#: fired — the fleet re-signalled on a burnt IP instead. A stall is now itself a rotation
#: trigger, which is the general form of that lesson: react to *no progress*, not to one
#: vendor's specific denial page.
HANG_SECONDS = 8.0


class Watchdog:
    """Detects a wedged browser: progress stops, but nothing raises and nothing exits.

    Written after a 15-tab sweep hung on 2026-08-27 — five deep boards left, every tab silent,
    the driver at 0.0% CPU, no CSV write for minutes, and 38 orphaned Chrome processes heavy
    enough that the machine's *own* browser stopped responding. Nothing in the stack noticed:
    a hang is not an exception, so the fleet neither failed nor finished, and only a human
    watching the file timestamps could tell. This is that human.

    Deliberately a *detector*, not a healer. Restarting a browser mid-flight means rebuilding
    every tab and re-solving the challenge, which is the driver's own business — so this reports
    and lets the driver decide, and its :meth:`beat` is the only thing keeping the run alive.

    Cheap by construction: one timestamp per completed item, and a single polling task per run.
    """

    def __init__(self, *, timeout: float = HANG_SECONDS) -> None:
        self._timeout = timeout
        self._last = time.monotonic()
        #: Set when nothing has beaten for ``timeout`` seconds. Drivers watch it like ``blocked``.
        self.hung = asyncio.Event()

    def reset(self) -> None:
        """Re-arm after an in-place recovery, so the next stall is judged on its own."""
        self.hung = asyncio.Event()
        self._last = time.monotonic()

    def beat(self) -> None:
        """Record progress. Any completed unit of work counts — a page, a company, a job."""
        self._last = time.monotonic()

    def since_beat(self) -> float:
        return time.monotonic() - self._last

    async def watch(self, stop: asyncio.Event) -> None:
        """Poll until ``stop`` is set or the fleet goes quiet for ``timeout``.

        Runs beside the workers (``asyncio.gather``), so a wedged fleet is noticed by the event
        loop that is otherwise doing nothing — which is exactly the state a hang leaves it in.
        """
        while not stop.is_set():
            await asyncio.sleep(max(1.0, min(15.0, self._timeout / 4)))
            if stop.is_set():
                return
            idle = self.since_beat()
            if idle >= self._timeout:
                print(
                    f"!! WATCHDOG: no page in {idle:.0f}s — the fleet has stalled.",
                    flush=True,
                )
                self.hung.set()
                return


class BlockRecovery:
    """Fleet-wide, serialized recovery from a hard block. One instance per run."""

    def __init__(
        self, warmup, *, browser_ref=None, max_rotations: int = MAX_ROTATIONS
    ) -> None:
        #: ``async warmup(tab) -> None`` — how this driver re-warms a tab after rotation.
        self._warmup = warmup
        #: ``{"b": browser}`` when the driver has one, so cookies can be cleared over the
        #: browser's own CDP connection rather than a tab's (see :meth:`_rewarm`).
        self._browser_ref = browser_ref
        self._max = max_rotations
        self._lock = asyncio.Lock()
        self.rotations = 0
        #: Set once recovery has given up, so every worker stops rather than each discovering it.
        self.exhausted = asyncio.Event()
        #: Bumped on every successful rotation, so a tab that blocked on the OLD IP can tell that
        #: a peer already replaced it and skip straight back to work.
        self.generation = 0

    async def recover(self, tab, wid: int) -> bool:
        """Rotate, re-warm ``tab``, and report whether work may continue.

        Serialized: a tab that blocks while a peer is mid-rotation waits, then sees the bumped
        generation and returns True without rotating again — one kickstart per block, not one
        per tab.
        """
        seen = self.generation
        async with self._lock:
            if self.generation != seen:
                await self._rewarm(tab, wid)  # a peer moved us; just re-warm this tab
                return True
            if self.exhausted.is_set():
                return False
            if self.rotations >= self._max:
                print(
                    f"[t{wid}] hard block after {self.rotations} rotations — stopping. "
                    "Past this it is behaviour being detected, not an IP being spent; "
                    "let it lapse and resume with --append.",
                    flush=True,
                )
                self.exhausted.set()
                return False

            self.rotations += 1
            print(
                f"[t{wid}] hard block — rotating egress "
                f"({self.rotations}/{self._max})...",
                flush=True,
            )
            started = time.monotonic()
            # Blocking (daemon restart + cooldown), so keep the event loop free for the other
            # tabs to finish the page they are on rather than stalling the whole fleet.
            moved = await asyncio.to_thread(spare_egress.rotate, "wellfound")
            if not moved:
                print(
                    f"[t{wid}] rotation did not produce a fresh IP "
                    "(passwordless sudo for the WARP kickstart is what this needs) — stopping.",
                    flush=True,
                )
                self.exhausted.set()
                return False
            self.generation += 1
            print(
                f"[t{wid}] egress rotated in {time.monotonic() - started:.1f}s",
                flush=True,
            )

        return await self._rewarm(tab, wid)

    async def _rewarm(self, tab, wid: int) -> bool:
        """Drop the cookie the blocked IP earned, take one warm-up hop, and report whether the
        **browser** is still answering.

        Returns False when the tab cannot execute CDP at all — which is a different failure from
        a wall and needs a different fix. Measured 2026-08-27: a rotation succeeded in 2.2s onto
        a fresh IP, and the very next `CLEAR_COOKIES` timed out after 60s because Chrome had
        wedged. An earlier version swallowed that and returned True, so the workers were sent
        back to a dead browser and re-stalled, paying a 60s CDP timeout each cycle. A fresh IP
        cannot fix a browser that will not answer; only a new browser can.
        """
        # Prefer the BROWSER's connection. A Tab talks over `/devtools/page/{targetId}`, served
        # by that page's renderer; a Browser talks over `/devtools/browser/...`, served by the
        # browser process. `Storage.clearCookies` is a browser-domain command either way, so
        # routing it through a tab only adds a dependency on the one component most likely to be
        # broken when this is called. (Caveat, stated because it was measured: pinning a renderer
        # with an infinite JS loop did NOT reproduce the 60s production hang over either route,
        # so this is a strictly-better route rather than a proven fix for it.)
        browser = (self._browser_ref or {}).get("b")
        alive = True
        try:
            if browser is not None:
                await asyncio.wait_for(browser.delete_all_cookies(), 15)
            else:
                await asyncio.wait_for(tab.delete_all_cookies(), 15)
        except Exception as exc:  # noqa: BLE001 - classified below, not ignored
            print(f"[t{wid}] cookie clear failed ({type(exc).__name__})", flush=True)
            alive = False
        try:
            await self._warmup(tab)
        except Exception as exc:  # noqa: BLE001 - a wall here is fine; an unresponsive tab is not
            print(f"[t{wid}] re-warm did not settle ({type(exc).__name__})", flush=True)
        if not alive:
            print(
                f"[t{wid}] the browser is not answering CDP — rotating cannot fix that",
                flush=True,
            )
        return alive


def kill_orphan_browsers() -> int:
    """Kill Chrome processes **this process** started, and return how many.

    A driver that is killed, or that exits without unwinding its ``async with Chrome(...)``,
    strands every renderer it spawned. Measured on 2026-08-27: one interrupted 15-tab sweep left
    **38** of them alive, and together they were heavy enough to make the machine's own browser
    unresponsive — the symptom that got the run stopped, several minutes after the scrape had in
    fact stopped doing anything.

    **Scoped to our own descendants, deliberately.** An earlier version matched any Chrome whose
    ``--user-data-dir`` sat under the system temp dir, which is every pydoll browser on the
    machine — so a scrape reaping its strays also killed the browser of any *other* run happening
    at the same time. That is not hypothetical: it killed two separate diagnostic runs of mine on
    2026-08-28 before the pattern was obvious, each time looking like an unrelated
    ``WebSocketConnectionClosed``. Walking down from our own pid cannot reach another run's
    browser, and still reaches every process ours spawned.

    The user's real browser was never in scope under either rule (it has no ``--user-data-dir``),
    and still is not.
    """
    try:
        listing = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except OSError:
        return 0

    children: dict[int, list[int]] = {}
    command: dict[int, str] = {}
    for line in listing.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
        command[pid] = parts[2]

    # Breadth-first over our descendants: Chrome's own helpers hang off the browser process, not
    # off us, so stopping at direct children would leave the renderers that are the actual weight.
    seen: set[int] = set()
    frontier = [os.getpid()]
    while frontier:
        pid = frontier.pop()
        for child in children.get(pid, []):
            if child not in seen:
                seen.add(child)
                frontier.append(child)

    killed = 0
    for pid in seen:
        if "Google Chrome" not in command.get(pid, ""):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
        except (ProcessLookupError, PermissionError):
            continue
    if killed:
        print(f"cleaned up {killed} orphaned browser processes", flush=True)
    return killed


#: How many times a run will rotate and rebuild after a stall before giving up. Raised from 2
#: once the watchdog became a 20s stall detector rather than a 5-minute hang detector: at that
#: sensitivity a restart is an ordinary event on a bad night, not a last resort. Still bounded —
#: a run that stalls six times is being detected, and more fresh IPs will not change that.
MAX_RESTARTS = 6


async def run_with_restarts(one_pass, *, max_restarts: int = MAX_RESTARTS) -> int:
    """Run ``one_pass(append)`` to completion, rebuilding the fleet from checkpoint on a hang.

    ``one_pass`` is a driver's whole fleet pass — build browser, fan out, write, tear down — and
    returns ``"hung"`` when :class:`Watchdog` fired, anything else when the pass ended on its own
    terms. This retries only the first case.

    **Restart is resume, and costs nothing extra, because every pass re-reads its own
    checkpoints.** A driver's pass begins by seeding its done-set and ``seen`` ids from the files
    on disk, so a second pass starts precisely where the wedged one stopped writing — the same
    mechanism ``--append`` already uses for a human re-launch, just invoked without the human.
    That is why retries force ``append=True`` regardless of what the operator passed: the first
    pass may legitimately truncate its output, but a *restart* that truncated would throw away
    the very work it is meant to resume from.

    Orphaned renderers are reaped between passes: a wedged fleet never unwinds its own
    ``async with Chrome(...)``, and stacking a second browser on top of the first one's corpses
    is how a scrape takes the machine down with it.

    **And the egress is rotated between passes**, because a stall is usually the IP rather than
    the browser: the measured failure was eight tabs looping on a Cloudflare 403, which no amount
    of rebuilding Chrome would clear while the same address kept asking. Rotating first means the
    rebuilt fleet arrives from somewhere new. If rotation is unavailable the restart still
    happens — a fresh browser is worth trying on its own — it just says so rather than pretending.
    """
    append = None  # first pass honours the operator's own flag
    for attempt in range(max_restarts + 1):
        status = await one_pass(append)
        if status != "hung":
            return 0 if status == "done" else 1
        kill_orphan_browsers()
        if attempt == max_restarts:
            print(
                f"!! hung {attempt + 1} times — giving up rather than rebuilding again. "
                "Everything finished is on disk; resume with --append.",
                flush=True,
            )
            return 1
        append = True  # never truncate what the previous pass already wrote
        moved = await asyncio.to_thread(spare_egress.rotate, "wellfound")
        print(
            f"restarting the fleet from the last checkpoint "
            f"({attempt + 1}/{max_restarts})"
            f"{' on a fresh egress IP' if moved else ' — egress did NOT rotate'}...",
            flush=True,
        )
    return 1


async def run_fleet(
    tabs,
    worker,
    watchdog,
    recovery,
    *,
    requeue,
    rebuild=None,
    rotate_first: bool = True,
    max_stalls: int = 8,
) -> str:
    """Drive ``worker(wid, tab)`` across ``tabs``, recovering **in place** when the fleet stalls.

    Returns ``"done"`` when the workers finish their queue, or ``"hung"`` when a stall can no
    longer be cleared here — which is the caller's signal to rebuild the browser entirely
    (:func:`run_with_restarts`).

    **Why in place.** Measured 2026-08-27, the three costs of recovering from a stall are not
    equal: re-reading the checkpoints is 0.03s, rotating the egress is 2-7s, and rebuilding
    Chrome with its tabs and warm-up hop is the rest — by far the largest. But a stall caused by
    a *wall* has nothing wrong with the browser; only the address is spent. So this keeps the
    browser and its tabs, rotates, drops the cookie the burnt IP earned, re-warms, and restarts
    the worker tasks over the same tabs. Full teardown remains the fallback for the case where
    the browser really is wedged, which is the one thing a fresh IP cannot fix.

    ``requeue`` puts back whatever the cancelled workers were holding. Cancellation is not
    graceful by nature — a worker stalled inside a 60s CDP call is killed where it stands — so
    without this the items in flight at stall time would be silently dropped from the run.

    ``rebuild`` is the second tier: when rotating cannot clear a stall the browser itself is the
    problem, and this swaps it for a fresh one *without leaving the pass* — the queue, the seen
    set and the open output files all survive. Without it that case still escalates to a full
    pass restart, which works but re-does everything a pass does on the way in.
    """
    for stall in range(max_stalls + 1):
        finished = asyncio.Event()

        async def _run_workers(done: asyncio.Event = finished) -> None:
            # `done` is bound as a default so each loop iteration keeps its own Event rather
            # than closing over the name and setting whichever the next pass created.
            try:
                await asyncio.gather(*(worker(i, t) for i, t in enumerate(tabs)))
            finally:
                done.set()

        # The watchdog must be able to *cancel* the fleet, not merely ask it to stop: a stalled
        # worker is blocked inside a page load or a CDP call and never reaches the top of its
        # loop to notice a flag. Racing the two and cancelling the loser is what lets an 8s
        # detector end a 60s timeout.
        fleet = asyncio.create_task(_run_workers())
        sentry = asyncio.create_task(watchdog.watch(finished))
        await asyncio.wait({fleet, sentry}, return_when=asyncio.FIRST_COMPLETED)
        for task in (fleet, sentry):
            if not task.done():
                task.cancel()
        for task in (fleet, sentry):
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if not watchdog.hung.is_set():
            return "done"
        if stall == max_stalls:
            print(
                f"!! stalled {stall + 1} times in one fleet — handing back for a full rebuild.",
                flush=True,
            )
            return "hung"
        requeue()
        # `rotate_first=False` skips straight to a fresh browser. Worth having as its own path
        # because the two failures look identical from here and cost very differently: the
        # 2026-08-27 stall rotated onto a clean IP in 2.2s and stayed stalled, because Chrome
        # had stopped answering CDP. When the browser is the thing that breaks, rotating first
        # only spends an IP to learn that.
        recovered = rotate_first and await recovery.recover(tabs[0], 0)
        if not recovered:
            if rebuild is None:
                return "hung"
            try:
                tabs[
                    :
                ] = (
                    await rebuild()
                )  # in place, so the workers' closure sees the new tabs
            except Exception as exc:  # noqa: BLE001 - a failed rebuild is the caller's problem
                print(f"browser rebuild failed ({type(exc).__name__})", flush=True)
                return "hung"
        watchdog.reset()
        print(f"fleet resumed in place after stall {stall + 1}", flush=True)
    return "hung"


class BrowserFleet:
    """Owns the browser and its tabs, and can replace them **without ending the run**.

    The escalation path used to be a full pass restart: return "hung", tear the pass down, and
    let :func:`run_with_restarts` build everything again — which also re-read the checkpoints,
    re-seeded the id set, and discarded the in-memory queue. All of that is wasted when the only
    broken thing is Chrome. This owns the browser instead, so a wedged one is swapped in place
    and the queue, the ``seen`` set and the done-file handles all survive it.

    ``build_tabs(browser, count)`` is the driver's own tab-and-warm-up routine, kept there
    because what a warm-up means differs per stage. Everything else — start, stop, reap the
    strays a stopped browser leaves — is the same for all three, so it lives here.

    ``make_options`` is a **factory, not an options object**: pydoll appends its arguments to
    whatever instance it is handed, so reusing one across builds raises
    ``ArgumentAlreadyExistsInOptions`` on the second. An earlier version stored the instance and
    every rebuild failed that way — seven for seven, silently turning the in-place swap back into
    a full pass restart while the log said it was rebuilding.
    """

    def __init__(self, make_options, build_tabs, workers: int) -> None:
        self._make_options = make_options
        self._build_tabs = build_tabs
        self._workers = workers
        self.browser = None
        self.tabs: list = []

    async def start(self) -> list:
        from pydoll.browser import Chrome  # local: only a fleet needs the browser class

        self.browser = Chrome(
            options=self._make_options()
        )  # fresh: see the class docstring
        self.tabs = await self._build_tabs(self.browser, self._workers)
        return self.tabs

    async def rebuild(self) -> list:
        """Swap in a fresh browser, keeping the caller's queue and counters.

        Reaps orphans between the two: a browser that stopped answering CDP often will not stop
        cleanly either, and stacking a second Chrome on the first one's renderers is what made
        the machine unusable on 2026-08-27.
        """
        started = time.monotonic()
        print("rebuilding the browser in place...", flush=True)
        await self.close()
        tabs = await self.start()
        print(f"browser rebuilt in {time.monotonic() - started:.1f}s", flush=True)
        return tabs

    async def close(self, *, grace: float = STOP_GRACE) -> None:
        """Stop the browser, but never wait on one that has stopped answering.

        ``browser.stop()`` talks CDP, so on a *wedged* browser it blocks until pydoll's own 60s
        command timeout — which was the whole cost of a rebuild, paid precisely when the browser
        is broken and a rebuild is most urgent. Measured 2026-08-27: a stall recovery spent 60s
        each on GET_DOCUMENT and CLEAR_COOKIES before anything else could happen. So the graceful
        stop gets `grace` seconds, and after that the process is killed outright — a browser being
        replaced has nothing worth saving, and SIGKILL is not slower than a timeout.
        """
        browser, self.browser, self.tabs = self.browser, None, []
        if browser is None:
            return
        with contextlib.suppress(Exception, asyncio.TimeoutError):
            await asyncio.wait_for(browser.stop(), grace)
        kill_orphan_browsers()  # whatever the graceful path did not take with it


#: Seconds **one tab may go without a page** before it is treated as stuck and recycled.
#:
#: This exists because :class:`Watchdog` is deliberately *fleet-wide*: it asks whether any page
#: arrived anywhere, so seven healthy tabs mask an eighth wedged on a verification page
#: indefinitely — the fleet keeps beating, the stall is never declared, and the item that tab was
#: holding is never requeued. Observed 2026-08-28. A fleet-wide detector cannot see this by
#: construction, so the bound has to be per tab.
#:
#: It measures *inactivity*, not the item's total duration, and that distinction is the whole
#: design. An earlier version capped the item at 90s outright, which quietly made deep companies
#: impossible: one seen on 2026-08-28 was still walking at page 43, and any cap would have
#: cancelled it, requeued it, and re-walked it from page 1 forever — a livelock that looks like
#: work. A company may take any length of time as long as pages keep arriving.
ITEM_STALL = 45.0


async def run_until_stalled(
    coro, last_beat: dict, wid: int, timeout: float = ITEM_STALL
):
    """Await ``coro``, cancelling it only once **this tab** has gone ``timeout`` without a page.

    ``last_beat[wid]`` is stamped by the worker's own ``on_progress`` hook, so a long item that
    keeps producing pages runs as long as it needs while a tab that has stopped producing them is
    cut loose. Raises :class:`TimeoutError` on the stall, so callers keep the shape they had when
    this was a plain ``asyncio.wait_for``.
    """
    task = asyncio.ensure_future(coro)
    last_beat[wid] = time.monotonic()
    while True:
        done, _ = await asyncio.wait({task}, timeout=min(5.0, timeout / 3))
        if done:
            return await task
        if time.monotonic() - last_beat.get(wid, 0) >= timeout:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            raise TimeoutError


async def recycle_tab(browser, old_tab, wid: int):
    """Replace one stuck tab, leaving the rest of the fleet untouched.

    Closing the old tab is best-effort for the same reason :meth:`BrowserFleet.close` bounds its
    stop: a tab wedged on a challenge is exactly the one that will not answer the CDP call asking
    it to close, so its refusal must not become the caller's problem.
    """
    print(f"[t{wid}] recycling this tab", flush=True)
    with contextlib.suppress(Exception, asyncio.TimeoutError):
        await asyncio.wait_for(old_tab.close(), 3.0)
    tab = await browser.new_tab()
    with contextlib.suppress(Exception):
        await tab.enable_auto_solve_cloudflare_captcha()
    return tab
