"""Measure how much of a Workday board's detail pass survives, by fan-out width.

The Workday counterpart to ``probe_eightfold_throttle.py``, and it exists for the same reason: the
scraper's ``_job_detail_async`` maps *every* failure — any non-200 status, any ``RequestsError`` —
onto the same ``None``, so ``report_detail_gaps``'s "N/M details missing" line cannot tell a 429
from a 404 from a timeout. A fix aimed at the wrong one of those is wasted, so measure first.

Runs the *real* detail-fetch path (``fan_out_async`` -> ``http.fetch_async``, same retry ladder,
same headers, same URL construction) against one live board at several concurrency widths, and
reports the settled-status histogram, the exception histogram, and the retry classes each arm
spent. Also probes the **listing** endpoint at the same width, because the two passes differ in
method (POST vs GET) and path shape and the scrape logs show them failing at very different rates.

Run it from a laptop and from a GitHub Actions runner and compare: same code, same widths,
different egress IP. A gap that appears only on the runner implicates the origin, not the width.
"""

from __future__ import annotations

import sys
import time
from collections import Counter

from headstart import http
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.workday import WorkdayScraper

SLUG = (
    sys.argv[1]
    if len(sys.argv) > 1
    else "https://ngc.wd1.myworkdayjobs.com/Northrop_Grumman_External_Site"
)
N = int(sys.argv[2]) if len(sys.argv) > 2 else 200
WIDTHS = [int(w) for w in sys.argv[3:]] or [6, 25]

_PROGRESS_EVERY = 50


def _outcome(exc: Exception) -> str:
    """The groupable label for a raised request: its curl code where it has one, else its type."""
    code = getattr(exc, "code", None)
    return f"{type(exc).__name__}(code={code})" if code else type(exc).__name__


def collect_paths(scraper: WorkdayScraper, want: int) -> list[str]:
    """``externalPath`` for up to ``want`` postings, off the plain unfaceted listing query."""
    paths: list[str] = []
    offset = 0
    while len(paths) < want:
        page = scraper._post({}, offset=offset)
        if not page:
            break
        postings = page.get("jobPostings") or []
        if not postings:
            break
        for posting in postings:
            path = posting.get("externalPath")
            if path:
                paths.append(path)
        print(
            f"  listing offset {offset}: {len(postings)} postings "
            f"(total reported {page.get('total')})",
            flush=True,
        )
        offset += len(postings)
    return paths[:want]


def run_arm(scraper: WorkdayScraper, paths: list[str], width: int) -> None:
    """One fan-out arm: fetch every path at ``width`` and print what actually came back."""
    statuses: Counter[str] = Counter()
    done = 0

    async def one(session, path: str):
        try:
            response = await http.fetch_async(
                session,
                "GET",
                scraper._detail_url(path),
                timeout=30,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                **scraper._egress(),
            )
        except Exception as exc:  # noqa: BLE001 - classifying the failure is the point
            statuses[_outcome(exc)] += 1
            return None
        statuses[f"HTTP {response.status_code}"] += 1
        if response.status_code != 200:
            return None
        try:
            info = response.json().get("jobPostingInfo") or {}
        except ValueError:
            statuses["unparseable"] += 1
            return None
        return info.get("jobDescription") or None

    # `one` is wrapped so progress prints as work completes rather than at the end.

    async def counted(session, path: str):
        nonlocal done
        result = await one(session, path)
        done += 1
        if done % _PROGRESS_EVERY == 0:
            print(f"    ...{done}/{len(paths)} {dict(statuses)}", flush=True)
        return result

    http.reset_retry_stats()
    started = time.monotonic()
    results = scraper.fan_out_async(paths, counted, concurrency=width)
    elapsed = time.monotonic() - started
    missing = sum(1 for r in results if r is None)
    print(
        f"  width {width}: {missing}/{len(paths)} missing "
        f"({100 * missing / len(paths):.1f}%) in {elapsed:.0f}s",
        flush=True,
    )
    print(f"    settled: {dict(statuses)}", flush=True)
    print(f"    retries: {dict(http.retry_stats())}", flush=True)


def probe_listing(scraper: WorkdayScraper, pages: int, width: int) -> None:
    """The same width against the *listing* endpoint — the pass that keeps succeeding."""
    statuses: Counter[str] = Counter()

    async def one(session, offset: int):
        try:
            page = await scraper._post_async(session, {}, offset)
        except Exception as exc:  # noqa: BLE001 - classifying the failure is the point
            statuses[_outcome(exc)] += 1
            return None
        statuses["HTTP 200" if page else "empty/404"] += 1
        return page

    http.reset_retry_stats()
    started = time.monotonic()
    offsets = [20 * i for i in range(1, pages + 1)]
    results = scraper.fan_out_async(offsets, one, concurrency=width)
    elapsed = time.monotonic() - started
    missing = sum(1 for r in results if r is None)
    print(
        f"  LISTING width {width}: {missing}/{len(offsets)} pages missing in {elapsed:.0f}s",
        flush=True,
    )
    print(f"    settled: {dict(statuses)}", flush=True)
    print(f"    retries: {dict(http.retry_stats())}", flush=True)


def main() -> int:
    scraper = WorkdayScraper(SLUG)
    scraper._resolve_instance()
    print(f"board {scraper.board_key()} -> {scraper.url()}", flush=True)
    paths = collect_paths(scraper, N)
    print(f"collected {len(paths)} externalPaths", flush=True)
    if not paths:
        return 1
    for width in WIDTHS:
        probe_listing(scraper, min(len(paths) // 20 or 1, 30), width)
        run_arm(scraper, paths, width)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
