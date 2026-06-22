#!/usr/bin/env python3
"""Scrape one Wellfound role+location SEO page past its anti-bot, parse the jobs.

Wellfound (ex-AngelList Talent) renders its job list into server HTML at
/role/l/{role}/{location}, but the site is gated by DataDome (geo.captcha-delivery.com)
+ Cloudflare Turnstile, so a plain HTTP client gets a 403 JS-challenge. We drive a real
Chrome via pydoll (CDP), let the browser execute the challenge JS, then read the resolved
page's embedded __NEXT_DATA__ / Apollo cache and normalize JobListing entries to Jobs.

See docs/wellfound/traffic-analysis.md for the reverse-engineering this is based on.

Anti-bot hardening (from docs/wellfound/datadome-bypass.md), kept light so it stays fast:
stealth launch flags + a clean session, an optional residential proxy (the biggest lever —
zero cost unless used), a single warm-up hop, a light scroll, and small jittered pacing. The
one persistent browser reuses the DataDome cookie across all pages.

Location forms: a normal location scrapes /role/l/{role}/{location}; the special location
"remote" scrapes the location-agnostic remote board /role/r/{role}.

Usage:  python scripts/scrape/run_wellfound.py [role] [location]
            [--headless] [--proxy host:port] [--no-warmup] [--max-pages N] [--delay S]
        python scripts/scrape/run_wellfound.py software-engineer india
        python scripts/scrape/run_wellfound.py software-architect remote   # -> /role/r/...
"""
import asyncio
import csv
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions
from pydoll.interactions.scroll import ScrollPosition

from datadome_slider import solve_slider, _safe_source  # same dir; sys.path[0] as a script

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "data" / "jobs" / "wellfound.csv"  # pipeline output stays under data/jobs/
# Debug captures are experiment data — keep them under experiment/, not data/.
EXP = ROOT / "experiment" / "wellfound-datadome" / "artifacts"
DEBUG_HTML = EXP / "last-run_role-page.html"
DEBUG_JSON = EXP / "last-run_nextdata.json"

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)

# Stealth launch flags: drop the obvious automation tells without the cost of a heavier
# anti-detect browser. Headful (default) keeps the UA + client hints genuine and consistent.
# NB: pydoll already adds --no-first-run / --no-default-browser-check as CDP defaults, so we
# must not repeat them here (it raises ArgumentAlreadyExistsInOptions).
_STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--lang=en-US",
    "--window-size=1400,1000",
]


def _options(headless: bool, proxy: str | None) -> ChromiumOptions:
    opts = ChromiumOptions()
    for arg in _STEALTH_ARGS:
        opts.add_argument(arg)
    if headless:
        opts.add_argument("--headless=new")
    if proxy:  # residential/mobile proxy — the strongest trust lever (method #1)
        opts.add_argument(f"--proxy-server={proxy}")
    return opts


async def _human_pause(tab) -> None:
    """Emit real (trusted) mouse + wheel input, then settle, so behaviour reads as human.

    Uses pydoll's CDP input (``isTrusted: true``). A JS ``window.scrollBy`` would fire an
    untrusted scroll with no underlying wheel event — DataDome's behavioural layer hooks the
    wheel/pointer listeners and checks ``isTrusted``, so the JS version is worthless here (and
    an anomalous scroll-without-wheel is itself a tell).

    Only call this on a resolved app page: dispatching input while the challenge page is up or
    mid-navigation can leave the CDP command unacked until pydoll's 60s timeout. The wait_for
    is a backstop so a stalled dispatch can never block the scrape.
    """
    async def _act():
        await tab.mouse.move(300 + random.random() * 800,
                             250 + random.random() * 450, humanize=True)
        await tab.scroll.by(ScrollPosition.DOWN, 500 + int(random.random() * 900),
                            smooth=True, humanize=True)
    try:
        await asyncio.wait_for(_act(), 10)
        await asyncio.sleep(0.4 + random.random() * 0.8)
    except Exception:
        pass


def _is_blocked(html: str) -> bool:
    """True if we got an anti-bot page instead of the app (DataDome / Cloudflare)."""
    return ("captcha-delivery.com" in html or "Just a moment" in html
            or "__NEXT_DATA__" not in html)


async def _load_page(tab, url: str, browser=None) -> str:
    """Navigate to one URL, waiting out / solving the DataDome challenge.

    A distrusted (e.g. WARP) IP gets an interactive "slide right" slider that won't auto-clear;
    when we see it we attempt the humanized drag solver. A trusted IP just auto-passes.
    """
    await tab.go_to(url)
    html = await _safe_source(tab)
    if not _is_blocked(html):  # SSR page returns __NEXT_DATA__ at once when not challenged
        return html
    solve_tried = False
    for attempt in range(20):  # ~40s budget for the challenge to resolve
        await asyncio.sleep(2)
        html = await _safe_source(tab)
        if not _is_blocked(html):
            return html
        if browser is not None and not solve_tried and "captcha-delivery.com" in html:
            solve_tried = True
            print("  DataDome slider detected — attempting humanized solve...", flush=True)
            if await solve_slider(tab, browser, EXP):
                html = await _safe_source(tab)
                if not _is_blocked(html):
                    print("  slider solved — page resolved", flush=True)
                    return html
        if attempt == 9:
            await tab.refresh()
            solve_tried = False  # allow one more solve attempt after the reload
    return html


def _epoch_to_iso(secs) -> str | None:
    """Wellfound's liveStartAt is a Unix epoch in seconds."""
    if not isinstance(secs, (int, float)):
        return None
    return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()


def _yoe(lo, hi) -> str | None:
    """Format yearsExperienceMin/Max into a compact string (both are often null)."""
    if lo is not None and hi is not None:
        return f"{lo}-{hi}"
    if lo is not None:
        return f"{lo}+"
    if hi is not None:
        return f"<={hi}"
    return None


def _ats_provider(src) -> str | None:
    """`AtsIntegration::Greenhouse::Listing` -> `greenhouse` (the dedupe/shortcut signal)."""
    if not isinstance(src, str) or not src:
        return None
    parts = src.split("::")
    return (parts[1] if len(parts) >= 2 else parts[0]).lower()


_CURRENCY_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP",
                     "₦": "NGN"}


def _currency(comp) -> str | None:
    """Derive ISO currency from the leading symbol of the compensation string; None when there
    is no salary figure (equity-only / not disclosed). Wellfound shows '$' as USD for these."""
    if not isinstance(comp, str) or not comp.strip():
        return None
    return _CURRENCY_SYMBOLS.get(comp.strip()[0])


def _lf(s):
    """Normalise text newlines to LF so the CSV has one consistent line terminator (the
    description markdown carries its own newlines; mixing them with CRLF rows looks 'unusual')."""
    return s.replace("\r\n", "\n").replace("\r", "\n") if isinstance(s, str) else s


def parse_page(html: str, scraped_at: str) -> tuple[list[dict], int]:
    """Normalize one role page's Apollo cache to Jobs; also return its total page count.

    The role page groups jobs by company: each ``StartupResult`` carries the company
    name/slug plus ``highlightedJobListings`` refs into ``JobListingSearchResult`` entries.
    ``ROOT_QUERY.seoLandingPageJobSearchResults`` reports ``pageCount`` for pagination.
    """
    m = NEXT_DATA_RE.search(html)
    if not m:
        return [], 0
    data = json.loads(m.group(1))
    cache = data.get("props", {}).get("pageProps", {}).get("apolloState", {}).get("data", {})
    if not isinstance(cache, dict):
        return [], 0

    # pageCount lives at ROOT_QUERY.talent.seoLandingPageJobSearchResults({...}); `talent`
    # is sometimes an inline object, sometimes a __ref into another cache entry.
    page_count = 0
    talent = cache.get("ROOT_QUERY", {}).get("talent")
    if isinstance(talent, dict) and "__ref" in talent:
        talent = cache.get(talent["__ref"], {})
    if isinstance(talent, dict):
        for key, val in talent.items():
            if key.startswith("seoLandingPageJobSearchResults") and isinstance(val, dict):
                page_count = val.get("pageCount") or 0
                break

    jobs = []
    for key, startup in cache.items():
        if not (isinstance(startup, dict) and startup.get("__typename") == "StartupResult"):
            continue
        company = startup.get("name") or startup.get("slug") or "?"
        for ref in startup.get("highlightedJobListings") or []:
            entry = cache.get(ref.get("__ref"), {})
            if not entry:
                continue
            listing_id = entry.get("id")
            slug = entry.get("slug") or ""
            locations = entry.get("locationNames") or []
            jobs.append(
                {
                    "id": f"wellfound:{startup.get('slug') or 'unknown'}:{listing_id}",
                    "ats": "wellfound",
                    "company": company,
                    "title": entry.get("title") or "?",
                    "location": ", ".join(locations) if locations else None,
                    "remote": entry.get("remote"),
                    "job_type": entry.get("jobType"),
                    "years_experience": _yoe(entry.get("yearsExperienceMin"),
                                             entry.get("yearsExperienceMax")),
                    "compensation": entry.get("compensation"),
                    "currency": _currency(entry.get("compensation")),
                    "department": entry.get("primaryRoleTitle"),
                    "ats_source": _ats_provider(entry.get("atsSource")),
                    "url": f"https://wellfound.com/jobs/{listing_id}-{slug}".rstrip("-"),
                    "posted_at": _epoch_to_iso(entry.get("liveStartAt")),
                    "scraped_at": scraped_at,
                    "description": _lf(entry.get("description")),
                }
            )
    return jobs, page_count


COLS = ["id", "ats", "company", "title", "location", "remote", "job_type",
        "years_experience", "compensation", "currency", "department", "ats_source", "url",
        "posted_at", "scraped_at", "description"]

BLOCK_ABORT = 2  # consecutive blocked pages before we stop (the IP is hot — don't hammer it)


async def scrape_url(tab, browser, base_url: str, scraped_at: str, writer, f,
                     seen: set[str], max_pages: int, delay: float,
                     warmup: bool, start_page: int = 1) -> tuple[int, bool]:
    """Scrape `?page=N` of one board from `start_page` onward into `writer`, deduping via `seen`.

    Returns (jobs_added, started_ok). started_ok is False only when the first page is blocked,
    so a sweep can skip this board and move on. A mid-pagination block stops this board but
    still returns started_ok=True with whatever was collected. `start_page` enables resuming a
    board partway (e.g. after a crash). Reusable across single + sweep.
    """
    added = 0

    def emit(jobs: list[dict], page: int, last: int) -> None:
        nonlocal added
        fresh = 0
        for j in jobs:
            if j["id"] in seen:
                continue
            seen.add(j["id"])
            writer.writerow(j)
            fresh += 1
            added += 1
        f.flush()
        print(f"    page {page}/{last}: +{fresh} new (board {added}, run total {len(seen)})",
              flush=True)

    # Warm-up: land on the jobs hub first so the first deep-link reads as in-site navigation,
    # not a cold deep-link (DataDome's intent-based detection, method #6).
    if warmup:
        warm = await _load_page(tab, "https://wellfound.com/jobs", browser)
        if not _is_blocked(warm):  # only humanize on a resolved page, never the challenge
            await _human_pause(tab)

    # First page (start_page): clears the challenge and tells us how many pages exist.
    html = await _load_page(tab, f"{base_url}?page={start_page}", browser)
    DEBUG_HTML.write_text(html, encoding="utf-8")
    if _is_blocked(html):
        print(f"  BLOCKED on page {start_page}: DataDome served the challenge instead of the app."
              " Skipping this board (retry after a cooldown / cleaner IP).", flush=True)
        return 0, False
    DEBUG_JSON.write_text(json.dumps(json.loads(NEXT_DATA_RE.search(html).group(1)), indent=1),
                          encoding="utf-8")
    jobs, page_count = parse_page(html, scraped_at)
    last = min(page_count, max_pages) if max_pages else page_count
    last = max(last, start_page)
    print(f"  {page_count} pages available; scraping {start_page}..{last}", flush=True)
    emit(jobs, start_page, last)
    await _human_pause(tab)  # dwell on the resolved page before paginating

    consecutive_blocks = 0
    for page in range(start_page + 1, last + 1):
        # Jittered pacing — polite, and machine-precise intervals are themselves a tell.
        await asyncio.sleep(delay + random.random() * 2)
        html = await _load_page(tab, f"{base_url}?page={page}", browser)
        if _is_blocked(html):
            consecutive_blocks += 1
            print(f"    page {page}/{last}: blocked ({consecutive_blocks}/{BLOCK_ABORT})",
                  flush=True)
            # Once DataDome flips to the slider it challenges every request; grinding through
            # the rest only deepens the wait and burns the IP's trust further.
            if consecutive_blocks >= BLOCK_ABORT:
                print(f"  DataDome is now challenging every request — stopping this board "
                      f"with {added} jobs.", flush=True)
                break
            continue
        consecutive_blocks = 0
        await _human_pause(tab)
        jobs, _ = parse_page(html, scraped_at)
        emit(jobs, page, last)
    return added, True


async def scrape(base_url: str, headless: bool, max_pages: int, delay: float,
                 proxy: str | None, warmup: bool) -> int:
    """Single-board scrape: open one Chrome + the CSV, scrape `base_url`, stream rows."""
    opts = _options(headless, proxy)
    scraped_at = datetime.now(timezone.utc).isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    EXP.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    # The CSV is a sync context manager, so it can't share the browser's `async with`; open
    # it here and close on each exit path. Per-row flush() means a crash loses nothing.
    f = OUT.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
    writer.writeheader()
    async with Chrome(options=opts) as browser:
        tab = await browser.start()
        try:
            await tab.enable_auto_solve_cloudflare_captcha()
        except Exception:  # non-fatal: DataDome is the real gate here
            pass
        added, ok = await scrape_url(tab, browser, base_url, scraped_at, writer, f, seen,
                                     max_pages, delay, warmup)
    f.close()
    print(f"DONE: {added} jobs -> data/jobs/wellfound.csv", flush=True)
    return 0 if ok else 1


def _flag(name: str, default):
    """Read `--name value` from argv, else default."""
    if name in sys.argv:
        return type(default)(sys.argv[sys.argv.index(name) + 1])
    return default


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    headless = "--headless" in sys.argv
    warmup = "--no-warmup" not in sys.argv
    proxy = _flag("--proxy", "") or None  # e.g. http://user:pass@host:port
    max_pages = _flag("--max-pages", 0)   # 0 = all pages
    delay = _flag("--delay", 4.0)         # seconds between pages
    role = args[0] if args else "software-engineer"
    location = args[1] if len(args) > 1 else "india"
    # location "remote" → the location-agnostic remote board /role/r/{role};
    # any other location → the location-scoped board /role/l/{role}/{location}.
    if location == "remote":
        base_url = f"https://wellfound.com/role/r/{role}"
    else:
        base_url = f"https://wellfound.com/role/l/{role}/{location}"
    print(f"GET {base_url}  (headless={headless}, max_pages={max_pages or 'all'},"
          f" proxy={'yes' if proxy else 'no'}, warmup={warmup})", flush=True)
    return asyncio.run(scrape(base_url, headless, max_pages, delay, proxy, warmup))


if __name__ == "__main__":
    raise SystemExit(main())
