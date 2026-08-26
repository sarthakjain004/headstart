#!/usr/bin/env python3
"""Common Crawl CDX sweep for Zwayam's two mineable namespaces, across every crawl.

Zwayam's customer boards sit on customer domains, but two provider-side namespaces leak the tenant
list anyway: ``{tenant}.openings.co`` (Zwayam's own multi-tenant board hosting, including its
``cluster2``/``cluster3``/``preprod1``/``dev1`` pods) and ``zwayam.com`` itself. Every crawled URL
in either namespace names a tenant, and a crawled *customer* page that references them names the
board host — which is the only way to find boards that live on customer domains.

Sweeps every crawl in collinfo (newest first), paging the CDX API and checkpointing per
``(crawl, target)`` — a throttled page is retried next run, never silently recorded as empty.
Reuses :func:`cc_miner.curl` for CC's block classification. On a persistent block it leaves that
``(crawl, target)`` uncheckpointed and moves on, so rotating the egress IP (a separate step —
``scripts/discover/rotate_egress.sh``) and re-running resumes with zero re-fetch.

Output: ``data/scratch/zwayam/cc_urls.txt`` — one raw matched URL per line, deduped, appended.

Run: python -u scripts/discover/cc_zwayam.py [--targets openings.co zwayam.com] [--max-crawls N]
"""

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "cc_miner", Path(__file__).resolve().parent / "cc_miner.py"
)
cc_miner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc_miner)

OUT = Path("data/scratch/zwayam/cc_urls.txt")
DONE = Path("data/scratch/zwayam/cc_checkpoint.txt")
_URL = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", nargs="+", default=["openings.co", "zwayam.com"])
    ap.add_argument("--max-crawls", type=int, default=127)
    a = ap.parse_args(argv[1:])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    seen = set(OUT.read_text().split()) if OUT.exists() else set()
    done = set(DONE.read_text().split()) if DONE.exists() else set()

    body, ok = cc_miner.curl(cc_miner.CC_COLLINFO)
    if not ok:
        print("collinfo unreachable", flush=True)
        return 3
    crawls = json.loads(body)[: a.max_crawls]
    print(
        f"cc-zwayam: {len(crawls)} crawls x {len(a.targets)} targets, {len(seen)} urls known",
        flush=True,
    )

    with OUT.open("a") as fh:
        for ci, c in enumerate(crawls, start=1):
            cdx = c["cdx-api"]
            for t in a.targets:
                key = f"{c['id']}|{t}"
                if key in done:
                    continue
                n_pages = cc_miner.num_pages(cdx, t)
                if n_pages is None:  # blocked, not empty
                    print(f"  [{ci}] {c['id']} {t}: BLOCKED (rotate egress, re-run)", flush=True)
                    continue
                new, blocked = 0, False
                for pg in range(n_pages):
                    url = f"{cdx}?url={t}&matchType=domain&output=json&fl=url&limit=20000&page={pg}"
                    b, k = cc_miner.curl(url)
                    if not k:
                        blocked = True
                        break
                    for ln in b.splitlines():
                        m = _URL.search(ln)
                        if not m:
                            continue
                        u = m.group(0).rstrip('",}')
                        if u not in seen:
                            seen.add(u)
                            fh.write(u + "\n")
                            new += 1
                    fh.flush()  # stream: a killed run keeps everything already found
                if not blocked:
                    done.add(key)
                    DONE.write_text("\n".join(sorted(done)))
                if n_pages:
                    print(
                        f"  [{ci}/{len(crawls)}] {c['id']} {t}: {n_pages}p +{new} (total {len(seen)})",
                        flush=True,
                    )
    print(f"DONE {len(seen)} urls -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
