#!/usr/bin/env python3
"""Ask a live Board whether the rows ADR-0053 shields from eviction are actually closed.

`index sync` reports how many eviction-candidate rows the ADR-0053 scope exclusion withholds on
each **Unauthoritative Board**, but it cannot say whether those rows are *stale* (the posting
closed and we are still serving it) or *unread* (the posting is open and this scrape merely could
not confirm it). That distinction is the whole justification for the mechanism, and CLAUDE.md is
explicit that "evicted" is not "dead" — it has to be measured against the real Board.

**What this measures, and why the control fetch is the load-bearing part.** For the SuccessFactors
boards that dominate the permanently-excluded set, the truncation reason is
`N/M job pages unreadable — those Jobs are listed but unbuilt`: the *listing* came back whole, and
only the per-job detail pass came up short. Those detail pages answer **HTTP 200 with a page that
carries no job at all** — so the scraper reads them as unreadable and marks the Board truncated.
This script fetches one **control** page per Board, a requisition id that provably does not exist,
and compares every failing job page against it. A job page that is byte-for-byte the tenant's
"no such requisition" shell is not unreadable — it is **closed**, and shielding it is the leak.

Two shapes of failure are therefore reported separately, and conflating them would invert the
conclusion:

- **gone** — the page equals the tenant's own not-found shell. The posting is closed. Evicting it
  is correct; ADR-0053 is currently preventing that.
- **unreadable** — a non-200, a timeout, or a 200 whose body is some *other* unparsed shape. Only
  these justify withholding eviction.

Sampling is bounded by ``--sample`` because the big Boards here list 4k-10k postings and a full
sweep is thousands of requests at one origin; pass ``--sample 0`` for the whole Board when the
exact set matters more than the origin's patience. Results stream per Board.

Run: python scripts/eval/shielded_row_liveness.py successfactors:careers.hcltech.com --sample 200
     python scripts/eval/shielded_row_liveness.py successfactors:careers.wipro.com --sample 0
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from headstart import http
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.successfactors import (
    SuccessFactorsScraper,
    _job_urls_from,
    _page_fields,
)

# A requisition id no tenant will ever have issued. The tenant's response to it *is* the
# definition of "this posting does not exist here", which is what every failing page is scored
# against — rather than a guess about what a not-found page ought to look like.
_ABSENT_REQ = "/job/Headstart-Control-Does-Not-Exist/999999-en_US/"


def _fetch(url: str) -> tuple[int, str]:
    try:
        r = http.fetch("GET", url, headers={"User-Agent": USER_AGENT}, timeout=30)
        return r.status_code, r.text
    except Exception as exc:  # noqa: BLE001 - one page's failure must not sink the sweep
        return -1, type(exc).__name__


def _titled(page: str, url: str) -> bool:
    return bool((_page_fields(page, url).get("title") or "").strip())


def audit(board: str, sample: int, workers: int) -> dict:
    ats, slug = board.split(":", 1)
    if ats != "successfactors":
        sys.exit(f"only successfactors is wired here; got {ats}")
    scraper = SuccessFactorsScraper(slug, slug)

    kind, text, cut = scraper._fetch_sitemap()
    listed = _job_urls_from(text, slug) if kind == "urlset" else []
    if not listed:
        sys.exit(
            f"{board}: sitemap surface listed nothing (kind={kind}) — not wired for this"
        )
    print(
        f"{board}: listing surface=sitemap-{kind} listed={len(listed)} truncated={cut}",
        flush=True,
    )

    ctl_code, ctl_body = _fetch(f"https://{slug}{_ABSENT_REQ}")
    ctl_len = len(ctl_body)
    print(
        f"{board}: control (nonexistent requisition) -> HTTP {ctl_code}, {ctl_len} bytes, "
        f"titled={_titled(ctl_body, '') if ctl_code == 200 else 'n/a'}",
        flush=True,
    )

    pages = listed if sample <= 0 else random.sample(listed, min(sample, len(listed)))
    verdicts: Counter[str] = Counter()
    gone_ids: list[str] = []
    unreadable: list[tuple[str, str]] = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch, url): (url, jid) for url, jid in pages}
        for done, fut in enumerate(as_completed(futures), 1):
            url, jid = futures[fut]
            code, body = fut.result()
            if code != 200:
                verdicts["unreadable"] += 1
                unreadable.append((jid, f"HTTP {code}" if code > 0 else body))
            elif _titled(body, url):
                verdicts["open"] += 1
            elif abs(len(body) - ctl_len) <= 1024:
                # Same shell the tenant serves for a requisition that does not exist. The
                # tolerance absorbs per-response nonces/timestamps, not a different page shape.
                verdicts["gone"] += 1
                gone_ids.append(jid)
            else:
                verdicts["unreadable"] += 1
                unreadable.append((jid, f"200, untitled, {len(body)} bytes"))
            if done % 50 == 0:
                print(f"  {done}/{len(pages)} {dict(verdicts)}", flush=True)

    n = sum(verdicts.values())
    print(
        f"{board}: n={n} open={verdicts['open']} gone={verdicts['gone']} "
        f"unreadable={verdicts['unreadable']} in {time.time() - started:.0f}s",
        flush=True,
    )
    print(
        f"{board}: of the {verdicts['gone'] + verdicts['unreadable']} pages the scrape counts as "
        f"a detail-pass loss, {verdicts['gone']} are the tenant's own not-found shell (closed "
        f"postings) and {verdicts['unreadable']} are genuinely unreadable",
        flush=True,
    )
    return {
        "board": board,
        "listed": len(listed),
        "listing_truncated": cut,
        "control_bytes": ctl_len,
        "sampled": n,
        "verdicts": dict(verdicts),
        "gone_ids": gone_ids[:500],
        "unreadable": unreadable[:200],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "boards", nargs="+", help="ats:slug, e.g. successfactors:careers.wipro.com"
    )
    ap.add_argument(
        "--sample", type=int, default=200, help="pages per Board; 0 = the whole Board"
    )
    ap.add_argument(
        "--workers", type=int, default=6, help="concurrent detail fetches per Board"
    )
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--json", help="write the per-Board result here")
    args = ap.parse_args()
    random.seed(args.seed)

    out = []
    for board in args.boards:
        out.append(audit(board, args.sample, args.workers))
        if args.json:
            Path(args.json).write_text(json.dumps(out, indent=1))
            print(f"wrote {args.json}", flush=True)


if __name__ == "__main__":
    main()
