#!/usr/bin/env python3
"""Sweep the Wellfound role boards across N browser tabs at once.

Same work as ``run_wellfound_sweep.py``, same output file and resume contract — a **driver**
only. The per-board page walk is ``run_wellfound.scrape_url``, the board matrix is that module's
``targets()``, and the schema is its ``COLS``; all imported rather than copied.

Stage 1 of ADR-0037's three surfaces. Its job is company *discovery* — the ≤3 jobs per company it
returns are a cap no paging can lift — but those few arrive with **full descriptions at no extra
cost**, which is why stage 3 later reuses them (``desc_source=board``) instead of re-fetching.

The unit of work here is a board, not a company or a job, and there are only 22 of them — so the
useful tab count is bounded by the matrix, and the driver clamps ``--workers`` to it rather than
opening tabs with nothing to do. Boards vary a lot in depth (a 21-page board beside a 2-page
one), so the queue is pulled rather than pre-split: a tab that finishes a shallow board takes the
next one instead of idling while a deep board finishes.

Everything else is the stage-2/3 drivers' shape, for the same reasons — one browser so every tab
shares the single solved DataDome cookie, a warm-up hop before the fan-out, per-tab pacing
preserved, and a hard block rotating the shared egress IP (``wellfound_block_recovery``) rather
than ending the run.

Resume differs from the sequential driver's and is *better*: that one resumes at a board index
(``--start-board N --start-page M``), which cannot express "boards 3, 7 and 12 are unfinished" —
a shape concurrency produces routinely. This one records finished boards in a done-file, so
``--append`` re-runs exactly the unfinished ones. Job-level dedup is unchanged: ``seen`` is
seeded from the existing CSV, so a re-walked board contributes only its new ids.

``--fast`` trades behavioural cover for speed: it drops the humanized scroll and dwell (the
single biggest per-page cost, per ``_human_pause``'s own docstring) and cuts pacing to a flat
1s with no jitter. That removes the only behavioural signal these scrapes emit, so expect more
challenges — the trade is deliberate, and it is survivable now only because a hard block
rotates the egress and a wedge restarts from checkpoint rather than ending the run. Explicit
``--delay``/``--jitter`` still win over the preset.

Run:  python scripts/scrape/run_wellfound_sweep_parallel.py
          [--workers N] [--fast] [--no-rotate] [--headless] [--append] [--max-pages N]
          [--proxy socks5://host:port] [--delay S] [--jitter S] [--no-scroll] [--audio-first]
"""

import asyncio
import csv
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import datadome_slider
from datadome_slider import audio_ready
from run_wellfound import (
    COLS,
    EXP,
    OUT,
    ROOT,
    HardBlocked,
    _flag,
    _load_page,
    _options,
    scrape_url,
)
from run_wellfound_company_jobs import warp_on_via
from run_wellfound_sweep import targets, warp_on
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

#: Boards finished by a previous run. Sits beside the sweep's CSV, named for it.
DONE: Path = ROOT / "data" / "jobs" / "wellfound_sweep.done.txt"

#: See ``run_wellfound_company_jobs_parallel.DEFAULT_WORKERS``. Clamped to the board count below,
#: which is 22 — so this is a ceiling that the matrix itself usually lowers.
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
    max_pages = _flag("--max-pages", 0)  # 0 = all pages per board
    delay = _flag("--delay", 1.0 if fast else 4.0)
    jitter = _flag("--jitter", 0.0 if fast else 2.0)
    workers = int(_flag("--workers", DEFAULT_WORKERS))

    scraped_at = datetime.now(UTC).isoformat()
    boards = targets()

    done: set[str] = set()
    seen: set[str] = set()
    if append and DONE.exists():
        done = {s.strip() for s in DONE.open(encoding="utf-8") if s.strip()}
    if append and OUT.exists():
        csv.field_size_limit(sys.maxsize)
        with OUT.open(encoding="utf-8") as fr:
            seen = {r["id"] for r in csv.DictReader(fr) if r.get("id")}
        print(f"resume: {len(done)} boards done, {len(seen)} ids seeded", flush=True)

    todo = [(label, url) for label, url in boards if label not in done]
    if not todo:
        print("nothing to do — every board is already in the done-file.", flush=True)
        return "done"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    EXP.mkdir(parents=True, exist_ok=True)
    write_header = not (append and OUT.exists())
    f = OUT.open("a" if append else "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
    if write_header:
        writer.writeheader()
    donef = DONE.open("a" if append else "w", encoding="utf-8")

    # More tabs than boards would just idle: the matrix is the ceiling here, unlike stages 2/3
    # where the work list is thousands long.
    workers = max(1, min(workers, len(todo)))
    queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
    for board in todo:
        queue.put_nowait(board)

    inflight: dict[int, object] = {}  # wid -> the item that tab is mid-way through
    last_beat: dict[int, float] = {}  # wid -> when THIS tab last saw a page
    blocked = asyncio.Event()  # set only once recovery has given up
    browser_ref: dict = {}  # the fleet's browser, needed by the recovery warm-up closure
    watchdog = Watchdog()
    recovery = BlockRecovery(
        lambda tab: _load_page(tab, WARMUP_URL, browser_ref["b"]),
        browser_ref=browser_ref,
    )
    per: dict[str, object] = {}
    t0 = time.monotonic()

    print(
        f"WARP on. {len(todo)} boards across {workers} tabs -> {OUT}",
        flush=True,
    )

    def _beat(wid: int) -> None:
        """One page arrived: keep the fleet alive AND this tab's own clock."""
        watchdog.beat()
        last_beat[wid] = time.monotonic()

    async def worker(wid: int, tab) -> None:
        while not (blocked.is_set() or watchdog.hung.is_set()):
            try:
                label, url = queue.get_nowait()
                inflight[wid] = (label, url)
            except asyncio.QueueEmpty:
                return
            print(f"[t{wid}] {label} ({queue.qsize()} left)", flush=True)
            try:
                added, finished = await run_until_stalled(
                    scrape_url(
                        tab,
                        browser_ref["b"],
                        url,
                        scraped_at,
                        writer,
                        f,
                        seen,
                        max_pages,
                        delay,
                        jitter,
                        False,  # the fleet warmed up once before fanning out
                        human_pause,
                        on_progress=lambda: _beat(wid),
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
                queue.put_nowait((label, url))
                inflight.pop(wid, None)
                tab = await recycle_tab(browser_ref["b"], tab, wid)
                continue
            except HardBlocked as exc:
                # Rotate the shared egress rather than end the sweep, and put the board back.
                print(f"[t{wid}] hard block on {label}: {exc}", flush=True)
                queue.put_nowait((label, url))
                if not await recovery.recover(tab, wid):
                    per[label] = "HARD-BLOCKED"
                    blocked.set()
                    return
                continue
            except Exception as exc:  # noqa: BLE001 - one board must not sink the fleet
                print(f"[t{wid}] {label}: {type(exc).__name__}: {exc}", flush=True)
                per[label] = "ERROR"
                continue
            inflight.pop(wid, None)
            watchdog.beat()
            per[label] = added if finished else "BLOCKED"
            if finished:
                donef.write(label + "\n")
                donef.flush()
            print(
                f"[t{wid}] {label}: +{added} jobs"
                f"{'' if finished else ' (incomplete — will retry)'}",
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
    wall = time.monotonic() - t0
    print(f"\nDONE sweep: {len(seen)} unique jobs total -> {OUT}", flush=True)
    for label, _ in boards:
        if label in per:
            print(f"  {label:<40} {per[label]}", flush=True)
    print(
        f"wall {wall:.1f}s across {len(tabs)} tabs | "
        f"{wall / max(len(per), 1):.1f} s/board",
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
