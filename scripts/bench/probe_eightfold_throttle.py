"""Measure how much of an Eightfold board's detail pass survives, by fan-out width.

Runs the *real* scraper detail-fetch path (`fan_out_async` -> `http.fetch_async`,
identical retry policy) against one live board at several concurrency widths, and reports how many
descriptions come back None plus the settled HTTP status histogram.

Run it from a laptop and from a GitHub Actions runner and compare: same code, same widths, different
egress IP. A gap that appears only on the runner implicates the origin, not the fan-out width.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
from collections import Counter

from headstart import http
from headstart.scrapers.registry import get_scraper

SLUG = sys.argv[1] if len(sys.argv) > 1 else "nvidia.eightfold.ai"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 120
WIDTHS = [int(w) for w in sys.argv[3:]] or [6, 25, 100]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
GROUP = SLUG.split(".")[0] + ".com"

statuses: Counter = Counter()


# A 1500-item arm can run ~10 minutes; staying silent for that long loses everything on a crash.
_PROGRESS_EVERY = 100


async def _fetch(session, pid):
    r = await http.fetch_async(
        session,
        "GET",
        f"https://{SLUG}/api/pcsx/position_details?"
        + urllib.parse.urlencode({"position_id": pid, "domain": GROUP, "hl": "en"}),
        headers={"User-Agent": UA, "Accept": "application/json"},
        timeout=30,
    )
    statuses[r.status_code] += 1
    done = sum(v for k, v in statuses.items() if isinstance(k, int))
    if done % _PROGRESS_EVERY == 0:
        print(f"    ...{done} fetched, statuses so far {dict(statuses)}", flush=True)
    if r.status_code != 200:
        return None
    try:
        return (r.json().get("data") or {}).get("jobDescription") or None
    except ValueError:
        statuses["unparseable"] += 1
        return None


def main() -> int:
    ids: list[str] = []
    start = 0
    total = None
    while len(ids) < N:
        q = urllib.parse.urlencode(
            {"domain": GROUP, "query": "", "location": "", "start": start}
        )
        r = http.fetch(
            "GET",
            f"https://{SLUG}/api/pcsx/search?{q}",
            headers={"User-Agent": UA, "Accept": "application/json"},
            timeout=30,
        )
        print(f"search start={start} -> {r.status_code}", flush=True)
        if r.status_code != 200:
            break
        data = json.loads(r.text).get("data") or {}
        total = total if total is not None else int(data.get("count") or 0)
        batch = data.get("positions") or []
        if not batch:
            break
        ids.extend(str(p["id"]) for p in batch)
        start += 10
    ids = ids[:N]
    print(f"\nboard {SLUG}: API count={total}, probing {len(ids)} ids\n", flush=True)
    if not ids:
        print("no ids — search itself was blocked", flush=True)
        return 1

    # An instance, because fan_out_async falls back to the scraper's own bound — irrelevant here
    # since every call passes an explicit width, but it is the same object the scrape path uses.
    scraper = get_scraper("eightfold", SLUG)

    for width in WIDTHS:
        statuses.clear()
        http.reset_retry_stats()
        t0 = time.time()
        out = scraper.fan_out_async(ids, _fetch, concurrency=width)
        missing = sum(1 for o in out if o is None)
        print(
            f"width {width:>4}: {missing:>4}/{len(out)} missing ({100 * missing / len(out):5.1f}%) "
            f"in {time.time() - t0:5.1f}s | settled={dict(statuses)} "
            f"| retries={dict(http.retry_stats())}",
            flush=True,
        )
        time.sleep(5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
