#!/usr/bin/env python3
"""Zoho Recruit careers hosts from Common Crawl, across every data centre and many crawls.

`cc_miner.py` reads one crawl and only four Zoho data centres (.com .eu .in .ca), so hosts on
.com.au .jp .sa .com.cn .uk .sg .ae, and hosts first crawled in an older or newer crawl, never
reach it. This sweeps `*.zohorecruit.{tld}` for every data centre over the newest N crawls
straight off CC's CDX API, one page at a time, and appends each host the first time it is seen.

Why it works: a Zoho Board lives at `{label}.zohorecruit.{tld}` (the scraper's Slug is the full
host), so every captured URL on that domain names a Board host by its first label, whatever the
path — careers page, job page, RSS feed or apply form.

Output: one host per line, appended and resumable (hosts already in OUT are not re-emitted).
A 404 / "No Captures found" is an empty result, not a block; CC answers a page past the end with
400. Politeness: one request at a time, PACE seconds apart, backing off on 429/503.

Run:  python -u scripts/discover/mine_zoho_cc.py OUT_FILE [N_CRAWLS]   (default 12 crawls)
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

INDEX = "https://index.commoncrawl.org"
UA = "HeadStart-discovery/0.1 (ATS tenant discovery; polite)"
PACE = 1.5
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
NOT_A_BOARD = {"www", "recruit", "static", "js", "css", "img", "accounts", "insights"}


def get(url: str) -> str | None:
    """Body text; "" for an empty result; None when the index kept refusing."""
    delay = 10
    for _ in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return ""
            if e.code not in (429, 500, 502, 503, 504):
                return None
        except Exception as e:  # noqa: BLE001 — timeouts and resets are retried like a 503
            print(f"  retrying after {type(e).__name__}", flush=True)
        time.sleep(delay)
        delay = min(delay * 2, 240)
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(argv[1])
    n_crawls = int(argv[2]) if len(argv) > 2 else 12
    out.parent.mkdir(parents=True, exist_ok=True)
    seen = set(out.read_text().split()) if out.exists() else set()
    crawls = [c["id"] for c in json.loads(get(f"{INDEX}/collinfo.json") or "[]")]
    crawls = crawls[:n_crawls]
    print(f"{len(crawls)} crawls x {len(DATA_CENTRES)} data centres", flush=True)
    with out.open("a") as f:
        for crawl in crawls:
            for dc in DATA_CENTRES:
                pat = re.compile(
                    r"//([a-z0-9][a-z0-9-]*)\." + re.escape(dc) + r"[:/]", re.IGNORECASE
                )
                base = f"{INDEX}/{crawl}-index?url=*.{dc}&output=json&fl=url"
                meta = get(base + "&showNumPages=true")
                pages = json.loads(meta)["pages"] if meta else 0
                new = 0
                for page in range(pages):
                    body = get(f"{base}&page={page}")
                    time.sleep(PACE)
                    if body is None:
                        print(
                            f"  {crawl} {dc} page {page}: refused, skipped", flush=True
                        )
                        continue
                    for label in {m.lower() for m in pat.findall(body)}:
                        host = f"{label}.{dc}"
                        if label not in NOT_A_BOARD and host not in seen:
                            seen.add(host)
                            f.write(host + "\n")
                            new += 1
                    f.flush()
                print(
                    f"{crawl} {dc}: {pages} pages, +{new} (total {len(seen)})",
                    flush=True,
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
