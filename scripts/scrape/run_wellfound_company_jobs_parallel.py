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

A hard block in ANY tab aborts the whole run: the IP is burnt, so the other tabs would only
deepen the hole. Everything already walked is on disk and in the done-file, so the next
``--append`` resumes from there.

Run:  python scripts/scrape/run_wellfound_company_jobs_parallel.py
          [--workers N] [--headless] [--append] [--limit N]
          [--proxy socks5://host:port] [--delay S] [--jitter S] [--no-scroll] [--audio-first]
"""

import asyncio
import csv
import sys
import time
from datetime import UTC, datetime

import datadome_slider
from datadome_slider import audio_ready
from pydoll.browser import Chrome
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

#: Tabs to fan out across. Measured on the same 25 companies (2026-08-27, 4s pacing, WARP proxy):
#: 1 tab 539s, 2 tabs 167s, 6 tabs 99s, 12 tabs 51s — every run returning the identical 402 jobs
#: with 0 incomplete, so concurrency changes only the clock. Note the curve had **not** flattened
#: by 12 (concurrency 1.9x / 4.2x / 7.4x), so this default is not the fast end of it: it is the
#: conservative one. Six holds the fleet near 1.5 requests a second, and a long unattended run
#: earns more from not being blocked than from finishing sooner. Raise it with ``--workers`` for
#: a supervised run — 12 drew no block over 25 companies, which is evidence, not a guarantee.
DEFAULT_WORKERS = 6


async def _new_ready_tab(browser, first: bool = False):
    """A tab with Cloudflare auto-solve armed, or the initial one when ``first``."""
    tab = await (browser.start() if first else browser.new_tab())
    try:
        await tab.enable_auto_solve_cloudflare_captcha()
    except Exception:  # noqa: BLE001, S110 - auto-solve is a bonus, the slider is the fallback
        pass
    return tab


async def main() -> int:
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
        return 2
    ok, status = audio_ready()
    print(f"audio fallback: {'OK' if ok else 'MISSING'} — {status}", flush=True)

    headless = "--headless" in sys.argv
    human_pause = "--no-scroll" not in sys.argv
    datadome_slider.AUDIO_FIRST = "--audio-first" in sys.argv
    append = "--append" in sys.argv
    limit = _flag("--limit", 0)
    delay = _flag("--delay", 4.0)
    jitter = _flag("--jitter", 2.0)
    workers = int(_flag("--workers", DEFAULT_WORKERS))

    scraped_at = datetime.now(UTC).isoformat()
    slugs = board_slugs()
    if not slugs:
        print("ABORT: no company slugs — run the board sweep first.", flush=True)
        return 2

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
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    EXP.mkdir(parents=True, exist_ok=True)
    write_header = not (append and OUT.exists())
    f = OUT.open("a" if append else "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
    if write_header:
        writer.writeheader()
    donef = DONE.open("a" if append else "w", encoding="utf-8")

    workers = max(1, min(workers, len(todo)))
    queue: asyncio.Queue[str] = asyncio.Queue()
    for slug in todo:
        queue.put_nowait(slug)

    blocked = asyncio.Event()  # a hard block anywhere stops everyone
    stats = {"companies": 0, "jobs": 0, "incomplete": 0, "seconds": 0.0}
    t0 = time.monotonic()

    print(
        f"WARP on. {len(todo)} companies across {workers} tabs -> {OUT}",
        flush=True,
    )

    async def worker(wid: int, tab) -> None:
        while not blocked.is_set():
            try:
                slug = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            started = time.monotonic()
            print(
                f"[t{wid}] {slug} ({queue.qsize()} left)",
                flush=True,
            )
            try:
                added, complete = await scrape_company(
                    tab,
                    browser,
                    slug,
                    scraped_at,
                    writer,
                    f,
                    seen,
                    delay,
                    jitter,
                    human_pause,
                )
            except HardBlocked as exc:
                # The IP is burnt — every other tab shares it, so stop the fleet rather than
                # let five more tabs confirm it. Resume is already on disk.
                print(f"\n[t{wid}] HARD BLOCK: {exc}", flush=True)
                blocked.set()
                return
            except Exception as exc:  # noqa: BLE001 - one company must not sink the fleet
                print(f"[t{wid}] {slug}: {type(exc).__name__}: {exc}", flush=True)
                stats["incomplete"] += 1
                continue
            took = time.monotonic() - started
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

    async with Chrome(options=_options(headless, proxy)) as browser:
        first = await _new_ready_tab(browser, first=True)
        # Warm up on ONE tab before fanning out, and only then open the rest: the cookie this
        # hop earns is shared by every tab opened after it, so the fleet starts already trusted.
        # Fanning out first would put N cold tabs against the challenge at once from one IP.
        print("warm-up hop on a single tab...", flush=True)
        try:
            await _load_page(first, "https://wellfound.com/jobs", browser)
        except Exception as exc:  # noqa: BLE001 - the walk itself will meet the wall if real
            print(f"  warm-up did not settle ({type(exc).__name__})", flush=True)

        tabs = [first]
        try:
            for _ in range(workers - 1):
                tabs.append(await _new_ready_tab(browser))
        except Exception as exc:  # noqa: BLE001 - fewer tabs is still a run
            print(
                f"  only {len(tabs)} tabs available ({type(exc).__name__})", flush=True
            )

        await asyncio.gather(*(worker(i, t) for i, t in enumerate(tabs)))

    f.close()
    donef.close()
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
    return 1 if blocked.is_set() else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
