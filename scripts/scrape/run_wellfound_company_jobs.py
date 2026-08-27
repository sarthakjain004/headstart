#!/usr/bin/env python3
"""Scrape every job of every discovered Wellfound company from its company jobs page.

Why this exists: the role boards run_wellfound_sweep.py walks cap `highlightedJobListings` at
**3 jobs per company**, and their `perPage: 20` counts *startups*, not jobs — so
`pageCount = ceil(totalStartupCount / 20)` and perfect pagination still only ever yields
`min(3, n)` jobs per company. Measured 2026-08-05: Deepgram 19 captured vs 78 real, Netskope 18
vs 139, and `devops-engineer/india` collected 200 against the board's own `totalJobCount` of 206.
The missing jobs were never in the board HTML, so no parser or paging fix can reach them.

`/company/{slug}/jobs?page=N` has no such cap: it is server-rendered, carries 20 full
`JobListing` nodes per page, exposes `jobListingsConnection.totalCount`, and a plain `?page=`
advances the base64 offset cursor (`after: "MA=="` -> `"MjA="`). It also reaches jobs in
locations and roles outside the board matrix (Deepgram's San Francisco listings, say), which the
role boards cannot see at all.

What it does NOT carry is the full description — only a ~300-char `descriptionSnippet` against a
board median of ~3,500 chars. Filling those in is run_wellfound_job_details.py's job; this stage
writes the snippet and marks `desc_source=snippet` so that stage knows what still needs fetching.

Standing rule: only ever request Wellfound through Cloudflare WARP — this aborts if WARP is off
so it can't leak the residential IP.

Resume: completed slugs are appended to a done-file as each company finishes, so a hard block
(or any interruption) resumes from the last fetched company rather than restarting. Re-running
with --append is always safe.

Run:  python scripts/scrape/run_wellfound_company_jobs.py
          [--headless] [--append] [--limit N] [--proxy socks5://host:port]
          [--delay S] [--jitter S] [--no-scroll] [--audio-first]
"""

import asyncio
import csv
import random
import sys
from datetime import UTC, datetime

import datadome_slider
from datadome_slider import audio_ready
from pydoll.browser import Chrome
from run_wellfound import (
    COLS as BOARD_COLS,
)
from run_wellfound import (
    EXP,
    ROOT,
    HardBlocked,
    _ats_provider,
    _captcha_frame_html,
    _currency,
    _epoch_to_iso,
    _flag,
    _human_pause,
    _is_blocked,
    _lf,
    _load_page,
    _options,
    _yoe,
    apollo_cache,
    is_hard_block,
)
from run_wellfound import (
    OUT as BOARD_CSV,
)
from run_wellfound_sweep import warp_on


def warp_on_via(proxy: str) -> bool:
    """Whether requests *through ``proxy``* egress on WARP.

    `warp_on` asks the machine's default route, which answers correctly only when WARP runs as
    a full tunnel. This repo's own spare-egress machinery instead configures WARP as a SOCKS
    proxy (`WarpProxy on port 40000`, what `spare_egress` dials), and on such a machine the
    default route is the residential IP while the proxy is the tunnel — so the default-route
    check reports "WARP off" for a setup that is entirely WARP-covered. Asking through the
    proxy is what the standing rule actually cares about: the requests this scrape makes.
    """
    try:
        from curl_cffi import requests as _cffi

        r = _cffi.get(
            "https://www.cloudflare.com/cdn-cgi/trace",
            proxies={"http": proxy, "https": proxy},
            timeout=15,
        )
        return "warp=on" in r.text
    except Exception:  # noqa: BLE001 - unreachable proxy is not WARP
        return False


OUT = ROOT / "data" / "jobs" / "wellfound_company_jobs.csv"
DONE = ROOT / "data" / "jobs" / "wellfound_company_jobs.done.txt"

PER_PAGE = 20  # what the company page actually returns per ?page=
PAGE_CEILING = 200  # runaway guard when totalCount is unknown (200 pages = 4000 jobs)

# The board's schema plus one column saying where each description came from. Derived rather
# than retyped so a change to the board's columns can't silently drift the two apart.
COLS = [*BOARD_COLS, "desc_source"]  # snippet | board | detail | gone


def _job_type(v) -> str | None:
    """The company page emits `full_time`; the board emits `full-time`. Normalize to the board's
    form so the two surfaces don't produce two spellings of the same value."""
    return v.replace("_", "-") if isinstance(v, str) else v


def parse_company_page(
    html: str, slug: str, scraped_at: str
) -> tuple[list[dict], int, bool]:
    """Normalize one /company/{slug}/jobs page to Jobs.

    Returns (jobs, totalCount, found). `found` says whether THIS company was actually rendered
    on the page, and it is not the same question as "were there jobs". `staple-3` is on its own
    page and legitimately has none (found=True, jobs=[]), whereas a redirect, a rename, or a
    garbled render yields found=False — which must be retried, not recorded as a company with
    zero openings. Collapsing the two is how a company gets retired permanently having never
    been read.

    totalCount is a *hint only* — it is 0 when it can't be resolved (some pages store the
    connection as a `__ref` into a separate cache entry, and a page also carries `Startup`
    entries for recommended companies whose counts must not be mistaken for this one's).
    Observed live on `staple-3`: 0 reported, 20 listings on the page. The caller must therefore
    paginate on what pages actually return, never on this number alone — see scrape_company.
    """
    cache = apollo_cache(html)
    if not cache:
        return [], 0, False

    def deref(v):
        return cache.get(v["__ref"], {}) if isinstance(v, dict) and "__ref" in v else v

    # Find the subject company. A company page also caches ~20 *recommended* startups, so this
    # must key on the slug we asked for — never "the Startup entries on the page".
    subject_key, subject = None, None
    for key, val in cache.items():
        if (
            isinstance(val, dict)
            and val.get("__typename") == "Startup"
            and val.get("slug") == slug
        ):
            subject_key, subject = key, val
            break
    if subject is None:
        # Page didn't render this company (redirect / dead slug / garbled): claim nothing, and
        # say so, so the caller retries instead of retiring it.
        return [], 0, False

    company = subject.get("name") or slug
    total = 0
    listing_keys: list[str] = []
    for key, sub in subject.items():
        if not key.startswith("jobListingsConnection"):
            continue
        sub = deref(sub)
        if not isinstance(sub, dict):
            continue
        if sub.get("totalCount") is not None:
            total = max(total, int(sub["totalCount"]))
        for edge in sub.get("edges") or []:
            node = (edge or {}).get("node") or {}
            if node.get("__ref"):
                listing_keys.append(node["__ref"])

    # Fall back to the startup backref every JobListing carries. Belt and braces: without this,
    # a page whose connection holds no edges would silently yield zero jobs.
    if not listing_keys:
        listing_keys = [
            key
            for key, val in cache.items()
            if isinstance(val, dict)
            and val.get("__typename") == "JobListing"
            and (val.get("startup") or {}).get("__ref") == subject_key
        ]

    # No within-page dedupe: the returned length IS the page's size, and the walk uses a full
    # page to decide there is another one. Silently collapsing a repeated __ref to 19 would end
    # the walk a page early; cross-page duplicates are the writer's job (`seen` in emit).
    jobs = []
    for key in listing_keys:
        val = cache.get(key)
        if not isinstance(val, dict):
            continue
        jid = val.get("id")
        jslug = val.get("slug") or ""
        locations = val.get("locationNames") or []
        remote_locs = val.get("acceptedRemoteLocationNames") or []
        jobs.append(
            {
                "id": f"wellfound:{slug}:{jid}",
                "ats": "wellfound",
                "company": company,
                "title": val.get("title") or "?",
                # Fall back to the accepted-remote list so a remote-only listing still says where.
                "location": ", ".join(locations or remote_locs) or None,
                "remote": val.get("remote"),
                "job_type": _job_type(val.get("jobType")),
                "years_experience": _yoe(
                    val.get("yearsExperienceMin"), val.get("yearsExperienceMax")
                ),
                "compensation": val.get("compensation"),
                "currency": _currency(val.get("compensation")),
                # The company page has no primaryRoleTitle; the board is the only source of it.
                "department": None,
                "ats_source": _ats_provider(val.get("atsSource")),
                "url": f"https://wellfound.com/jobs/{jid}-{jslug}".rstrip("-"),
                "posted_at": _epoch_to_iso(val.get("liveStartAt")),
                "scraped_at": scraped_at,
                "description": _lf(val.get("descriptionSnippet")),
                "desc_source": "snippet",
            }
        )
    return jobs, total, True


def board_slugs() -> list[str]:
    """Company slugs discovered by the board sweep, from `wellfound:{slug}:{id}` ids."""
    if not BOARD_CSV.exists():
        return []
    csv.field_size_limit(sys.maxsize)
    slugs: dict[str, None] = {}  # dict keeps first-seen order
    with BOARD_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            parts = (row.get("id") or "").split(":")
            if len(parts) >= 3 and parts[1]:
                slugs[parts[1]] = None
    return list(slugs)


async def scrape_company(
    tab,
    browser,
    slug: str,
    scraped_at: str,
    writer,
    f,
    seen: set[str],
    delay: float,
    jitter: float,
    human_pause: bool,
) -> tuple[int, bool]:
    """Walk every ?page= of one company's jobs page.

    Returns (jobs_added, walked_to_the_end). The second value is what makes resume safe: a
    company is only recorded as done when its walk actually reached the end. A soft block, or a
    page that doesn't render this company at all (redirect / rename / garbled), leaves it to be
    retried. A company that simply has no openings still counts as walked to the end.
    """
    added = 0

    def emit(jobs: list[dict], page: int, last) -> int:
        """Write the unseen rows of one page; returns how many the PAGE held (not how many were
        new). Pagination keys off the page's own size — using "new rows" would stop a *resumed*
        company at its first already-captured page and never reach the pages beyond it."""
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
        print(
            f"    page {page}/{last}: {len(jobs)} listings, +{fresh} new (company {added})",
            flush=True,
        )
        return len(jobs)

    url = f"https://wellfound.com/company/{slug}/jobs"
    html = await _load_page(tab, f"{url}?page=1", browser)
    if _is_blocked(html) and is_hard_block(await _captcha_frame_html(tab, browser)):
        raise HardBlocked(f"hard block on page 1 of {url}")
    if _is_blocked(html):
        print(
            f"  BLOCKED on {slug} page 1 — will retry on the next --append", flush=True
        )
        return 0, False

    jobs, total, found = parse_company_page(html, slug, scraped_at)
    if not found:
        print(
            f"  {slug}: page 1 didn't render this company — will retry on the next --append",
            flush=True,
        )
        return 0, False
    # `total` is display only. It reads 0 on some companies (staple-3) while the page serves 20
    # listings, and it disagreed with the live page on Deepgram (77 vs 78) — deriving a page cap
    # from it is the same trusted-but-wrong count that let the role board truncate silently.
    # The walk ends on what the pages themselves show: a short page is the last page.
    shown = (-(-total // PER_PAGE) if total else None) or "?"
    print(f"  {total or '?'} jobs / {shown} pages", flush=True)
    n = emit(jobs, 1, shown)
    if human_pause:
        await _human_pause(tab)

    prev_ids = {j["id"] for j in jobs}
    page = 1
    while n >= PER_PAGE:  # a full page means there is very likely another one
        page += 1
        if page > PAGE_CEILING:
            # Deliberate stop, not a failure: retrying would hit the same wall forever. Loud,
            # because at 4,000 jobs for one company something upstream is probably wrong.
            print(
                f"    !! {slug} hit the {PAGE_CEILING}-page ceiling — stopping this company",
                flush=True,
            )
            break
        await asyncio.sleep(delay + random.random() * jitter)
        html = await _load_page(tab, f"{url}?page={page}", browser)
        if _is_blocked(html) and is_hard_block(await _captcha_frame_html(tab, browser)):
            raise HardBlocked(f"hard block on page {page} of {url}")
        if _is_blocked(html):
            print(
                f"    page {page}/{shown}: blocked — will retry on the next --append",
                flush=True,
            )
            return added, False
        if human_pause:
            await _human_pause(tab)
        jobs, _, found = parse_company_page(html, slug, scraped_at)
        if not found:
            # Mid-walk the page stopped rendering this company: the tail is unread, so this is
            # a truncation, not an ending. Retry rather than bank a partial set.
            print(
                f"    page {page}/{shown}: didn't render {slug} — will retry on the next --append",
                flush=True,
            )
            return added, False
        ids = {j["id"] for j in jobs}
        if ids and ids == prev_ids:
            # Same page served again: past the end (the role board wraps this way). Not an error.
            print(
                f"    page {page}/{shown}: repeat of the previous page — end of list",
                flush=True,
            )
            break
        prev_ids = ids
        n = emit(jobs, page, shown)
    return added, True


async def main() -> int:
    # Chrome is pointed at this too, so the check and the scrape share one egress.
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

    scraped_at = datetime.now(UTC).isoformat()
    slugs = board_slugs()
    if not slugs:
        print(
            f"ABORT: no company slugs — run the board sweep first ({BOARD_CSV}).",
            flush=True,
        )
        return 2

    OUT.parent.mkdir(parents=True, exist_ok=True)
    EXP.mkdir(parents=True, exist_ok=True)

    # Resume: a company counts as done only once every page of it finished, so an interrupted
    # company is re-walked rather than left half-scraped.
    done: set[str] = set()
    seen: set[str] = set()
    if append and DONE.exists():
        done = {s.strip() for s in DONE.open(encoding="utf-8") if s.strip()}
    if append and OUT.exists():
        csv.field_size_limit(sys.maxsize)
        with OUT.open(encoding="utf-8") as fr:
            for row in csv.DictReader(fr):
                seen.add(row["id"])
        print(
            f"resume: {len(done)} companies done, {len(seen)} jobs already captured",
            flush=True,
        )

    todo = [s for s in slugs if s not in done]
    if limit:
        todo = todo[:limit]

    write_header = not (append and OUT.exists())
    f = OUT.open("a" if append else "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
    if write_header:
        writer.writeheader()
    donef = DONE.open("a" if append else "w", encoding="utf-8")

    incomplete = 0
    print(f"WARP on. {len(todo)} companies to scrape -> {OUT}", flush=True)
    async with Chrome(options=_options(headless, proxy)) as browser:
        tab = await browser.start()
        try:
            await tab.enable_auto_solve_cloudflare_captcha()
        except Exception:  # noqa: BLE001, S110
            pass
        for i, slug in enumerate(todo):
            print(f"\n=== [{i + 1}/{len(todo)}] {slug} ===", flush=True)
            try:
                _, complete = await scrape_company(
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
            except HardBlocked as e:
                print(
                    f"\n!! HARD BLOCK: {e}\n"
                    f"   DataDome denied access outright — not a solvable challenge.\n"
                    f"   Stopping. Let it lapse, then resume from this company with:\n"
                    f"     python scripts/scrape/run_wellfound_company_jobs.py --append",
                    flush=True,
                )
                break
            # Only a walk that reached the end counts as done. Recording a soft-blocked or
            # unparsable company would retire it permanently with whatever it happened to get.
            if complete:
                donef.write(slug + "\n")
                donef.flush()
            else:
                incomplete += 1
            await asyncio.sleep(delay + random.random() * jitter)
    f.close()
    donef.close()
    print(f"\nDONE: {len(seen)} jobs -> {OUT}", flush=True)
    if incomplete:
        print(
            f"  {incomplete} companies did not finish (blocked / capped) — rerun with "
            f"--append to pick them up",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
