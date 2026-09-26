#!/usr/bin/env python3
"""Zoho Recruit Board hosts from urlscan.io, every data centre, paged to exhaustion.

`urlscan_miner.py` slices each domain into half-year date windows and reads one 100-result page
per window, so any window holding more than 100 scans is silently cut: on 2026-09-26
`domain:zohorecruit.com` for 2024H1 alone held 165. urlscan's search does page past 100 with
`search_after` (the last hit's `sort` value), so this walks each data centre's whole result set
instead — no window, no cap.

It records two kinds of host, because `domain:` matches every domain a scanned page contacted:
  * a Board host, `{label}.zohorecruit.{tld}`, from the hit's page or task domain; and
  * an *embedding* page — a company site whose scan loaded a Zoho Recruit host (a careers widget
    or an iframe). Those go to a second file with the scan uuid, for a later pass that reads the
    scan's contacted domains (`/api/v1/result/{uuid}/`) to name the Board it embeds.

The unauthenticated search API allows 100 calls an hour per IP (429 with `retry-after`), so the
walk sleeps out the window rather than retrying into it. Resumable per data centre: a finished
data centre is recorded in `{OUT}.done` and skipped.

Run:  python -u scripts/discover/mine_zoho_urlscan.py OUT_FILE
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = "HeadStart-discovery/0.1 (ATS tenant discovery; polite)"
PACE = 3.0
DATA_CENTRES = (
    "zohorecruit.com",
    "zohorecruit.in",
    "zohorecruit.eu",
    "zohorecruit.com.au",
    "zohorecruit.ca",
    "zohorecruit.jp",
    "zohorecruit.sa",
    "zohorecruit.com.cn",
    "zohorecruit.uk",
    "zohorecruit.sg",
    "zohorecruit.ae",
)


def search(query: str, after: str | None) -> dict | None:
    """One page of hits; sleeps out a 429 window. None if the API keeps failing."""
    url = "https://urlscan.io/api/v1/search/?" + urllib.parse.urlencode(
        {"q": query, "size": 100, **({"search_after": after} if after else {})}
    )
    for _ in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code != 429:
                return None
            wait = int(e.headers.get("retry-after") or 600) + 5
            print(f"  429: sleeping {wait}s for the hourly window", flush=True)
            time.sleep(wait)
        except Exception:  # noqa: BLE001 — timeouts and resets: back off and retry
            time.sleep(30)
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(argv[1])
    embeds_out = out.with_suffix(".embeds.tsv")
    done_file = out.with_suffix(".done")
    seen = set(out.read_text().split()) if out.exists() else set()
    done = set(done_file.read_text().split()) if done_file.exists() else set()
    with out.open("a") as f, embeds_out.open("a") as ef:
        for dc in DATA_CENTRES:
            if dc in done:
                continue
            after, pages, new, embeds = None, 0, 0, 0
            while True:
                data = search(f"domain:{dc}", after)
                time.sleep(PACE)
                if not data or not data.get("results"):
                    break
                pages += 1
                for hit in data["results"]:
                    page_dom = ((hit.get("page") or {}).get("domain") or "").lower()
                    for h in {
                        page_dom,
                        ((hit.get("task") or {}).get("domain") or "").lower(),
                    }:
                        is_board = (
                            h.endswith("." + dc) and h.count(".") == dc.count(".") + 1
                        )
                        if is_board and h not in seen:
                            seen.add(h)
                            f.write(h + "\n")
                            new += 1
                    if page_dom and "zohorecruit." not in page_dom:
                        ef.write(f"{dc}\t{page_dom}\t{hit.get('_id', '')}\n")
                        embeds += 1
                f.flush()
                ef.flush()
                after = ",".join(str(v) for v in data["results"][-1].get("sort", []))
                if not data.get("has_more") and len(data["results"]) < 100:
                    break
            print(
                f"{dc}: {pages} pages, +{new} hosts, {embeds} embedding scans",
                flush=True,
            )
            with done_file.open("a") as d:
                d.write(dc + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
