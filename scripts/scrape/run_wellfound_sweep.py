#!/usr/bin/env python3
"""Sweep all Wellfound target boards into one deduped CSV.

Walks every role x {india, remote} board from docs/wellfound/target-roles.md in a SINGLE
browser session (reusing the DataDome/Cloudflare cookies — far less anti-bot exposure than
relaunching per URL), dedupes jobs by id across all boards, and streams to
data/jobs/wellfound.csv with the 15-column schema. Reuses the per-board routine from
run_wellfound.py, so the anti-bot handling (slider solve, block-abort, pacing) stays in one
place.

Standing rule: only ever request Wellfound through Cloudflare WARP — this aborts if WARP is off
so it can't leak the residential IP.

Run:  python scripts/scrape/run_wellfound_sweep.py
          [--headless] [--max-pages N] [--delay S] [--no-warmup]
"""

import asyncio
import csv
import sys
import urllib.request
from datetime import datetime, timezone

from pydoll.browser import Chrome
from datadome_slider import audio_ready
from run_wellfound import COLS, EXP, OUT, _flag, _options, scrape_url

# Keep in sync with docs/wellfound/target-roles.md
ROLES = [
    "backend-engineer",
    "full-stack-engineer",
    "software-engineer",
    "software-architect",
    "artificial-intelligence-engineer",
    "frontend-engineer",
    "devops-engineer",
    "data-scientist",
    "machine-learning-engineer",
    "mobile-engineer",
    "product-designer",
]


def targets(roles=None) -> list[tuple[str, str]]:
    """(label, base_url) for every role x {india, remote} board (defaults to all ROLES)."""
    roles = roles if roles is not None else ROLES
    t = [(f"{r}/india", f"https://wellfound.com/role/l/{r}/india") for r in roles]
    t += [(f"{r}/remote", f"https://wellfound.com/role/r/{r}") for r in roles]
    return t


def warp_on() -> bool:
    try:
        with urllib.request.urlopen(
            "https://www.cloudflare.com/cdn-cgi/trace", timeout=10
        ) as r:
            return "warp=on" in r.read().decode("utf-8", "replace")
    except Exception:
        return False


async def main() -> int:
    if not warp_on():
        print(
            "ABORT: WARP is not on. Standing rule: never scrape Wellfound on the "
            "residential IP. Connect WARP and retry.",
            flush=True,
        )
        return 2
    ok, status = audio_ready()
    print(f"audio fallback: {'OK' if ok else 'MISSING'} — {status}", flush=True)
    headless = "--headless" in sys.argv
    warmup = "--no-warmup" not in sys.argv
    append = "--append" in sys.argv
    max_pages = _flag("--max-pages", 0)  # 0 = all pages per board
    delay = _flag("--delay", 4.0)
    start_board = _flag(
        "--start-board", 0
    )  # 0-based index into the (filtered) board list
    start_page = _flag("--start-page", 1)  # resume the start board from this page
    roles_arg = _flag("--roles", "")  # comma-sep role slugs; empty = all ROLES
    scraped_at = datetime.now(timezone.utc).isoformat()
    selected = (
        [s.strip() for s in roles_arg.split(",") if s.strip()] if roles_arg else ROLES
    )
    boards = targets(selected)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    EXP.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    per: dict[str, object] = {}

    # Resume: seed dedup from the existing CSV and append (don't clobber already-scraped jobs).
    write_header = not (append and OUT.exists())
    if append and OUT.exists():
        with OUT.open(encoding="utf-8") as fr:
            for row in csv.DictReader(fr):
                seen.add(row["id"])
        print(f"resume: seeded {len(seen)} ids from existing {OUT.name}", flush=True)
    f = OUT.open("a" if append else "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
    if write_header:
        writer.writeheader()

    print(
        f"WARP on. Sweeping boards {start_board}..{len(boards) - 1} "
        f"(start page {start_page}) -> {OUT}",
        flush=True,
    )
    async with Chrome(options=_options(headless, None)) as browser:
        tab = await browser.start()
        try:
            await tab.enable_auto_solve_cloudflare_captcha()
        except Exception:
            pass
        for i in range(start_board, len(boards)):
            label, url = boards[i]
            sp = start_page if i == start_board else 1
            print(
                f"\n=== [{i + 1}/{len(boards)}] {label}  ({url})  page>={sp} ===",
                flush=True,
            )
            # Warm-up only before the first board scraped; later boards are already in-session.
            added, ok = await scrape_url(
                tab,
                browser,
                url,
                scraped_at,
                writer,
                f,
                seen,
                max_pages,
                delay,
                warmup and i == start_board,
                start_page=sp,
            )
            per[label] = added if ok else "BLOCKED"
    f.close()

    print(f"\nDONE sweep: {len(seen)} unique jobs total -> {OUT}", flush=True)
    for i in range(start_board, len(boards)):
        print(f"  {boards[i][0]:<40} {per.get(boards[i][0])}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
