#!/usr/bin/env python3
"""Walk Wellfound company jobs pages across N browser tabs at once.

Same work as ``run_wellfound_company_jobs.py``, same output file and resume contract — this is
only a **driver**. The per-company page walk, the parser, the block handling and the schema are
imported from that module rather than copied, so the two can never disagree about what a job row
is; what lives here is the concurrency around it.

Why it helps: the sequential version spends nearly all its wall-clock waiting — a page load,
then ``delay + jitter`` (4-6s) before the next one, one company at a time. Those waits are
per-tab, so N tabs overlap N of them. The run prints its own ``concurrency Nx`` figure (in-tab
seconds over wall seconds) so the speedup is measured on the day rather than asserted here.

Three things make the fan-out safe rather than just fast:

* **One browser, N tabs — never N browsers.** ``new_tab()`` shares the default browser context,
  so every tab reuses the *one* DataDome/Cloudflare cookie the warm-up solved. N browsers would
  present N cold sessions to the anti-bot and each would have to solve its own challenge.
* **A warm-up before the fan-out.** Tab 0 loads one company page alone and solves whatever
  challenge appears. Only then do the workers start. Fanning out cold means N tabs hit the
  challenge simultaneously from one IP, which is the shape of an attack.
* **Per-tab pacing is preserved.** Each worker still sleeps ``delay + jitter`` between its own
  page loads, so a single tab looks exactly as human as before; concurrency raises the *fleet*
  rate, not any one tab's. Aggregate load is therefore ``workers / delay`` requests a second —
  at the defaults (6 workers, 4s) about 1.5/s, which this endpoint served without complaint.

Shared state (the ``seen`` id set, the CSV writer, the done-file) needs no lock: asyncio runs one
task at a time and every mutation of it happens in synchronous code with no ``await`` inside, so
a write can never interleave with another. That is a property of the imported ``emit`` closure,
not an accident — if a future change puts an ``await`` inside it, this comment stops being true.

A hard block in any tab rotates the shared egress IP (``wellfound_block_recovery``) rather than
ending the run — bounded, so a run that is genuinely being detected still stops for a human.
Everything already walked is on disk and in the done-file, so the next ``--append`` resumes from
there either way.

``--fast`` trades behavioural cover for speed: it drops the humanized scroll and dwell (the
single biggest per-page cost, per ``_human_pause``'s own docstring) and cuts pacing to a flat
1s with no jitter. That removes the only behavioural signal these scrapes emit, so expect more
challenges — the trade is deliberate, and it is survivable now only because a hard block
rotates the egress and a wedge restarts from checkpoint rather than ending the run. Explicit
``--delay``/``--jitter`` still win over the preset.

Run:  python scripts/scrape/run_wellfound_company_jobs_parallel.py
          [--workers N] [--fast] [--no-rotate] [--headless] [--append] [--limit N]
          [--proxy socks5://host:port] [--delay S] [--jitter S] [--no-scroll] [--audio-first]
"""

import asyncio
import csv
import sys
import time
from datetime import UTC, datetime

import datadome_slider
from datadome_slider import audio_ready
from run_wellfound import (
    EXP,
    HardBlocked,
    _flag,
    _load_page,
    _options,
)
from run_wellfound_company_jobs import (
    COLS,
    DONE,
    OUT,
    board_slugs,
    scrape_company,
    warp_on,
    warp_on_via,
)
from wellfound_block_recovery import (
    ITEM_STALL,
    WARMUP_URL,
    BlockRecovery,
    BrowserFleet,
    Watchdog,
    kill_orphan_browsers,
    new_ready_tab,
    recycle_tab,
    run_fleet,
    run_until_stalled,
    run_with_restarts,
)

#: Tabs to fan out across. Measured on the same 25 companies (2026-08-27, 4s pacing, WARP proxy):
#: 1 tab 539s, 2 tabs 167s, 6 tabs 99s, 12 tabs 51s — every run returning the identical 402 jobs
#: with 0 incomplete, so concurrency changes only the clock. Note the curve had **not** flattened
#: by 12 (concurrency 1.9x / 4.2x / 7.4x), so this default is not the fast end of it: it is the
#: conservative one. Six holds the fleet near 1.5 requests a second, and a long unattended run
#: earns more from not being blocked than from finishing sooner. Raise it with ``--workers`` for
#: a supervised run — 12 drew no block over 25 companies, which is evidence, not a guarantee.
DEFAULT_WORKERS = 6


async def _one_pass(append_override: bool | None) -> str:
    """One whole fleet pass: build the browser, fan out, write, tear down.

    Returns "done", "blocked" or "hung" — `run_with_restarts` retries only the last, and passes
    `append_override=True` when it does so the restart resumes rather than truncates.
    """
    proxy = (
        _flag("--proxy", "") or None
    )  # e.g. socks5://127.0.0.1:40000 (WARP proxy mode)
    if not (warp_on_via(proxy) if proxy else warp_on()):
        where = f"through {proxy}" if proxy else "on the default route"
        print(
            f"ABORT: WARP is not on {where}. Standing rule: never scrape Wellfound on the "
            "residential IP. Connect WARP and retry — or pass --proxy for WARP proxy mode.",
            flush=True,
        )
        return "blocked"
    ok, status = audio_ready()
    print(f"audio fallback: {'OK' if ok else 'MISSING'} — {status}", flush=True)

    fast = "--fast" in sys.argv  # no scroll, flat 1s pacing
    # A stall then goes straight to a fresh browser instead of spending an IP first.
    rotate_first = "--no-rotate" not in sys.argv
    headless = "--headless" in sys.argv
    human_pause = "--no-scroll" not in sys.argv and not fast
    datadome_slider.AUDIO_FIRST = "--audio-first" in sys.argv
    append = "--append" in sys.argv if append_override is None else append_override
    limit = _flag("--limit", 0)
    delay = _flag("--delay", 1.0 if fast else 4.0)
    jitter = _flag("--jitter", 0.0 if fast else 2.0)
    workers = int(_flag("--workers", DEFAULT_WORKERS))

    scraped_at = datetime.now(UTC).isoformat()
    slugs = board_slugs()
    if not slugs:
        print("ABORT: no company slugs — run the board sweep first.", flush=True)
        return "blocked"

    done: set[str] = set()
    seen: set[str] = set()
    if append and DONE.exists():
        done = {s.strip() for s in DONE.open(encoding="utf-8") if s.strip()}
    if append and OUT.exists():
        csv.field_size_limit(sys.maxsize)
        with OUT.open(encoding="utf-8") as fr:
            seen = {r["id"] for r in csv.DictReader(fr) if r.get("id")}

    todo = [s for s in slugs if s not in done]
    if limit:
        todo = todo[: int(limit)]
    if not todo:
        print("nothing to do — every company is already in the done-file.", flush=True)
        return "done"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    EXP.mkdir(parents=True, exist_ok=True)
    write_header = not (append and OUT.exists())
    f = OUT.open("a" if append else "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
    if write_header:
        writer.writeheader()
    donef = DONE.open("a" if append else "w", encoding="utf-8")

    workers = max(1, min(workers, len(todo)))
    browser_ref: dict = {}  # the fleet's browser, needed by the recovery warm-up closure
    queue: asyncio.Queue[str] = asyncio.Queue()
    for slug in todo:
        queue.put_nowait(slug)

    # Page-level checkpoint. The done-file records finished *companies*, which cannot express
    # "google is 40 pages in" — so before this, every restart sent a deep company back to page 1
    # and re-walked pages it had already emitted. Dedup made that harmless but not free.
    progress_path = OUT.with_suffix(".pages.tsv")
    page_done: dict[str, int] = {}
    if append and progress_path.exists():
        for line in progress_path.open(encoding="utf-8"):
            slug_, _, page_ = line.strip().partition("\t")
            if slug_ and page_.isdigit():
                page_done[slug_] = max(page_done.get(slug_, 0), int(page_))
        if page_done:
            print(
                f"resume: {len(page_done)} companies carry a page checkpoint",
                flush=True,
            )
    progress_f = progress_path.open("a" if append else "w", encoding="utf-8")

    inflight: dict[int, object] = {}  # wid -> the item that tab is mid-way through
    last_beat: dict[int, float] = {}  # wid -> when THIS tab last saw a page
    blocked = asyncio.Event()  # set only once recovery has given up
    watchdog = Watchdog()
    recovery = BlockRecovery(
        lambda tab: _load_page(tab, WARMUP_URL, browser_ref["b"]),
        browser_ref=browser_ref,
    )
    stats = {"companies": 0, "jobs": 0, "incomplete": 0, "seconds": 0.0}
    t0 = time.monotonic()

    print(
        f"WARP on. {len(todo)} companies across {workers} tabs -> {OUT}",
        flush=True,
    )

    def _note_page(slug: str, page: int) -> None:
        """Record that this company's page finished, so a restart resumes after it."""
        page_done[slug] = max(page_done.get(slug, 0), page)
        progress_f.write(f"{slug}\t{page}\n")
        progress_f.flush()

    def _beat(wid: int) -> None:
        """One page arrived: keep the fleet alive AND this tab's own clock."""
        watchdog.beat()
        last_beat[wid] = time.monotonic()

    async def worker(wid: int, tab) -> None:
        while not (blocked.is_set() or watchdog.hung.is_set()):
            try:
                slug = queue.get_nowait()
                inflight[wid] = slug
            except asyncio.QueueEmpty:
                return
            started = time.monotonic()
            print(
                f"[t{wid}] {slug} ({queue.qsize()} left)",
                flush=True,
            )
            try:
                added, complete = await run_until_stalled(
                    scrape_company(
                        tab,
                        browser_ref["b"],  # live: a rebuild swaps this
                        slug,
                        scraped_at,
                        writer,
                        f,
                        seen,
                        delay,
                        jitter,
                        human_pause,
                        on_progress=lambda: _beat(wid),
                        start_page=page_done.get(slug, 0) + 1,
                        on_page_done=lambda pg, sl=slug: _note_page(sl, pg),
                    ),
                    last_beat,
                    wid,
                )
            except TimeoutError:
                # This tab alone is stuck — the fleet-wide watchdog cannot see that while its
                # peers keep beating, so bound the item and give this tab a fresh one.
                print(
                    f"[t{wid}] no page in {ITEM_STALL:.0f}s — requeueing and recycling",
                    flush=True,
                )
                queue.put_nowait(slug)
                inflight.pop(wid, None)
                tab = await recycle_tab(browser_ref["b"], tab, wid)
                continue
            except HardBlocked as exc:
                # The IP is burnt and every tab shares it — so rotate to a fresh one rather
                # than end the run, and put this company back for whoever picks it up next.
                # Recovery is serialized fleet-wide: peers blocking meanwhile ride this
                # rotation instead of kickstarting the daemon again.
                print(f"[t{wid}] hard block on {slug}: {exc}", flush=True)
                queue.put_nowait(slug)
                if not await recovery.recover(tab, wid):
                    blocked.set()
                    return
                continue
            except Exception as exc:  # noqa: BLE001 - one company must not sink the fleet
                print(f"[t{wid}] {slug}: {type(exc).__name__}: {exc}", flush=True)
                stats["incomplete"] += 1
                continue
            took = time.monotonic() - started
            inflight.pop(wid, None)
            watchdog.beat()
            stats["companies"] += 1
            stats["jobs"] += added
            stats["seconds"] += took
            if complete:
                donef.write(slug + "\n")
                donef.flush()
            else:
                stats["incomplete"] += 1
            print(
                f"[t{wid}] {slug}: +{added} jobs in {took:.1f}s"
                f"{'' if complete else ' (incomplete — will retry)'}",
                flush=True,
            )

    async def _build_tabs(browser, count: int) -> list:
        """One warmed tab, then the rest — the fleet's whole build, reused on every rebuild.

        Warm up on ONE tab before fanning out, and only then open the rest: the cookie this hop
        earns is shared by every tab opened after it, so the fleet starts already trusted.
        Fanning out first would put N cold tabs against the challenge at once from one IP.
        """
        browser_ref["b"] = browser
        first = await new_ready_tab(browser, first=True)
        print("warm-up hop on a single tab...", flush=True)
        try:
            await _load_page(first, WARMUP_URL, browser)
        except Exception as exc:  # noqa: BLE001 - the walk itself will meet the wall if real
            print(f"  warm-up did not settle ({type(exc).__name__})", flush=True)
        built = [first]
        try:
            for _ in range(count - 1):
                built.append(await new_ready_tab(browser))
        except Exception as exc:  # noqa: BLE001 - fewer tabs is still a run
            print(
                f"  only {len(built)} tabs available ({type(exc).__name__})", flush=True
            )
        return built

    def _requeue() -> None:
        """Put back what the cancelled workers were holding, so a stall drops nothing."""
        for item in list(inflight.values()):
            queue.put_nowait(item)
        inflight.clear()

    fleet = BrowserFleet(lambda: _options(headless, proxy), _build_tabs, workers)
    tabs = await fleet.start()
    try:
        outcome = await run_fleet(
            tabs,
            worker,
            watchdog,
            recovery,
            requeue=_requeue,
            rebuild=fleet.rebuild,
            rotate_first=rotate_first,
        )
    finally:
        await fleet.close()

    if watchdog.hung.is_set():
        # A wedged fleet does not unwind its own `async with Chrome(...)`, so its renderers
        # outlive the driver — 38 of them after the 2026-08-27 hang, heavy enough to make the
        # machine's own browser unresponsive. Reap them before returning.
        kill_orphan_browsers()
    f.close()
    donef.close()
    progress_f.close()
    wall = time.monotonic() - t0
    n = stats["companies"] or 1
    print(
        f"\nDONE: {stats['jobs']} jobs from {stats['companies']} companies "
        f"({stats['incomplete']} incomplete) -> {OUT}",
        flush=True,
    )
    print(
        f"wall {wall:.1f}s | {wall / n:.1f} s/company across {len(tabs)} tabs | "
        f"in-tab {stats['seconds'] / n:.1f} s/company | "
        f"concurrency {stats['seconds'] / wall:.1f}x",
        flush=True,
    )
    if outcome == "hung":
        return "hung"
    return "blocked" if blocked.is_set() else "done"


async def main() -> int:
    """Supervise the pass: a hang rebuilds the fleet and resumes from the checkpoint."""
    return await run_with_restarts(_one_pass)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
