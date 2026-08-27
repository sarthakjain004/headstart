#!/usr/bin/env python3
"""Fill Wellfound job descriptions across N browser tabs at once.

Same work as ``run_wellfound_job_details.py``, same output file and resume contract — a
**driver** only. The per-job decision (board reuse / tech gate / detail fetch / challenge /
mismatch / gone) is ``resolve_row`` in that module, imported rather than copied, because those
branches are subtle enough that a second copy would drift: a posting has to be proven to belong
to *this* job, and a challenged page must be left unwritten rather than freezing a snippet.

This is the stage that most needs it. ADR-0037 sizes a full pass at **roughly a day for ~17k
tech-titled jobs**, and says why: the page load is 0.2s and the pacing is 4-6s, so the run is
almost entirely sleeping. Those sleeps are per-tab, so N tabs overlap N of them.

Measured 2026-08-27 against the sequential driver on the same 40 roster rows (8 of which needed
a live fetch; the rest were free board reuse or title-gated): **45.6s -> 14.7s at 6 tabs, 3.1x**,
with byte-identical output — same 40 ids, and zero differences across every field including the
description text. The speedup understates a real pass, because the one-off warm-up hop is a
fixed cost here amortised over only 40 rows. Extrapolating the *fetch-bound* rate (~5.7s a fetch
sequential) to the ADR's ~17k tech-titled jobs puts the sequential pass near a day, as it says,
and a 6-tab pass in the several-hours range — an estimate from this sample, not a measurement of
a full run.

The safety shape is the stage-2 driver's, for the same reasons — one browser so every tab shares
the single solved DataDome cookie, a warm-up hop before the fan-out so N cold tabs don't meet the
challenge together, per-tab pacing preserved so no individual tab looks faster than a human, and
a hard block in any tab stopping the fleet because they all share the one IP.

Ordering caveat, deliberate: rows are written as they finish, so the output is in completion
order rather than roster order. Nothing downstream reads it positionally (the id is the key, and
``--append`` seeds its done-set from the id column), and the sequential driver's order was itself
only an artifact of doing one at a time.

Run:  python scripts/scrape/run_wellfound_job_details_parallel.py
          [--workers N] [--headless] [--append] [--limit N] [--all-titles]
          [--proxy socks5://host:port] [--delay S] [--jitter S] [--audio-first]
"""

import asyncio
import csv
import sys
import time

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
from run_wellfound_company_jobs import warp_on_via
from run_wellfound_job_details import (
    COLS,
    OUT,
    ROSTER_CSV,
    board_descriptions,
    needs_fetch,
    read_roster,
    resolve_row,
    warp_on,
)

#: See ``run_wellfound_company_jobs_parallel.DEFAULT_WORKERS`` — same trade, same starting point.
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
    datadome_slider.AUDIO_FIRST = "--audio-first" in sys.argv
    append = "--append" in sys.argv
    all_titles = "--all-titles" in sys.argv
    limit = _flag("--limit", 0)
    delay = _flag("--delay", 4.0)
    jitter = _flag("--jitter", 2.0)
    workers = int(_flag("--workers", DEFAULT_WORKERS))

    roster = read_roster()
    if not roster:
        print(
            f"ABORT: no roster — run run_wellfound_company_jobs.py first ({ROSTER_CSV}).",
            flush=True,
        )
        return 2
    board = board_descriptions()
    print(
        f"roster: {len(roster)} jobs | board already has {len(board)} full descriptions",
        flush=True,
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    EXP.mkdir(parents=True, exist_ok=True)

    done: set[str] = set()
    if append and OUT.exists():
        csv.field_size_limit(sys.maxsize)
        widened = 0
        with OUT.open(encoding="utf-8") as fr:
            for row in csv.DictReader(fr):
                # Same widening rule as the sequential driver: --all-titles re-attempts anything
                # still lacking a full description, so those ids must NOT seed the done-set.
                if all_titles and row.get("desc_source") in ("snippet", "gone"):
                    widened += 1
                    continue
                done.add(row["id"])
        print(f"resume: {len(done)} rows already written", flush=True)
        if widened:
            print(
                f"  --all-titles: re-fetching {widened} rows left at snippet",
                flush=True,
            )

    write_header = not (append and OUT.exists())
    f = OUT.open("a" if append else "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
    if write_header:
        writer.writeheader()

    todo = [r for r in roster if r["id"] not in done]
    if limit:
        todo = todo[: int(limit)]
    if not todo:
        print("nothing to do — every roster row is already written.", flush=True)
        f.close()
        return 0

    n_fetch = sum(1 for r in todo if needs_fetch(r, board, all_titles))
    workers = max(1, min(workers, len(todo)))
    print(
        f"to write {len(todo)} rows; {n_fetch} need a detail fetch "
        f"({'all titles' if all_titles else 'tech-titled only'}) "
        f"across {workers} tabs -> {OUT}",
        flush=True,
    )

    queue: asyncio.Queue[dict] = asyncio.Queue()
    for row in todo:
        queue.put_nowait(row)

    blocked = asyncio.Event()
    stats = {
        "board": 0,  # description reused from the role-board sweep
        "detail": 0,  # fetched from the job page's JSON-LD
        "snippet": 0,  # non-tech title: not worth a fetch
        "gone": 0,  # listing closed/404 — no posting to fetch
        "challenged": 0,  # unwritten, retried by the next --append
        "mismatch": 0,  # unwritten, retried by the next --append
    }
    written = 0
    t0 = time.monotonic()

    async def worker(wid: int, tab) -> None:
        nonlocal written
        while not blocked.is_set():
            try:
                row = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                out = await resolve_row(
                    tab,
                    browser,
                    row,
                    board,
                    stats,
                    all_titles=all_titles,
                    delay=delay,
                    jitter=jitter,
                )
            except HardBlocked as exc:
                # Every tab shares the one IP, so five more would only confirm it. Everything
                # written is already flushed; the next --append resumes from there.
                print(f"\n[t{wid}] !! HARD BLOCK on {exc}", flush=True)
                blocked.set()
                return
            except Exception as exc:  # noqa: BLE001 - one job must not sink the fleet
                print(f"[t{wid}] {row['id']}: {type(exc).__name__}: {exc}", flush=True)
                continue
            if out is None:
                continue  # challenged or mismatched: left for the next --append
            writer.writerow(out)
            f.flush()
            written += 1
            if written % 25 == 0:
                rate = written / max(time.monotonic() - t0, 1e-9)
                print(
                    f"  [{written}/{len(todo)}] {rate * 60:.0f} rows/min {stats}",
                    flush=True,
                )

    async with Chrome(options=_options(headless, proxy)) as browser:
        first = await _new_ready_tab(browser, first=True)
        print("warm-up hop on a single tab...", flush=True)
        try:
            await _load_page(first, "https://wellfound.com/jobs", browser)
        except Exception as exc:  # noqa: BLE001 - the fetches themselves will meet a real wall
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
    wall = time.monotonic() - t0
    print(f"\nDONE: {written} rows -> {OUT}  {stats}", flush=True)
    print(
        f"wall {wall:.1f}s across {len(tabs)} tabs | "
        f"{written / max(wall, 1e-9) * 60:.0f} rows/min",
        flush=True,
    )
    return 1 if blocked.is_set() else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
