#!/usr/bin/env python3
"""Ask a live SuccessFactors Board whether its detail-pass gap is closed postings or unread ones.

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
and compares every failing job page against it. A job page that is the tenant's "no such
requisition" shell is not unreadable — it is **closed**, and keeping it out of scope is the leak.

**The comparison is a length window, not byte equality, and that is not a shortcut.** Measured on
`careers.hcltech.com` 2026-08-26, the shell is *not* byte-stable, so a hash or `==` would score
every closed page `unreadable` and invert this tool's conclusion:

- two fabricated requisitions differ in exactly one line — the page echoes its own id back
  (`jobID : '999999\\x2Den_US'`), so no two ids ever produce the same bytes;
- the first response on a fresh connection carries an extra ~514-char `ctid` tracking `<script>`
  that later responses on the same session omit — 12 fetches of one URL over 12 fresh sessions
  gave **12 distinct bodies** (111,523/111,524 chars), while 12 over one shared session gave 11
  identical after the first.

So the shell varies by ~514 chars against a ~111,000-char page — three orders of magnitude below
the ~13,500-char gap to a real posting (a titled HCL page runs ~122,000-127,000). `_SHELL_SLACK`
is set above the measured drift and far below that gap. A tenant whose shell varies by more than
the slack simply scores `unreadable`, which is today's behaviour — the failure is safe.

Two shapes of failure are therefore reported separately, and conflating them would invert the
conclusion:

- **gone** — the page equals the tenant's own not-found shell. The posting is closed. Evicting it
  is correct; ADR-0053 is currently preventing that.
- **unreadable** — a non-200, a timeout, or a 200 whose body is some *other* unparsed shape. Only
  these justify withholding eviction.

Sampling is bounded by ``--sample`` because the big Boards here list 4k-10k postings and a full
sweep is thousands of requests at one origin; pass ``--sample 0`` for the whole Board when the
exact set matters more than the origin's patience. Results stream per Board.

Run: python scripts/eval/successfactors_detail_gap_audit.py successfactors:careers.hcltech.com --sample 200
     python scripts/eval/successfactors_detail_gap_audit.py successfactors:careers.wipro.com --sample 0
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
    _titled_fields,
)

# A requisition id no tenant will ever have issued. The tenant's response to it *is* the
# definition of "this posting does not exist here", which is what every failing page is scored
# against — rather than a guess about what a not-found page ought to look like.
_ABSENT_REQ = "/job/Headstart-Control-Does-Not-Exist/999999-en_US/"

# How far a page's length may sit from the control's and still count as the same shell. See the
# module docstring: measured drift on the one tenant this has been characterised on is ~514
# chars, and the nearest real posting is ~13,500 above the shell, so anything in this band
# discriminates. Not tuned per tenant — a tenant that needs more than this scores `unreadable`.
_SHELL_SLACK = 1024

# Per-Board ids listed in the `--json` capture. Whatever is dropped is counted, never silent.
_MAX_LISTED = 500


def _fetch(url: str) -> tuple[int, str]:
    try:
        r = http.fetch("GET", url, headers={"User-Agent": USER_AGENT}, timeout=30)
        return r.status_code, r.text
    except Exception as exc:  # noqa: BLE001 - one page's failure must not sink the sweep
        return -1, type(exc).__name__


def _titled(page: str, url: str) -> bool:
    """Exactly the predicate production uses to decide a detail page was a loss.

    Deliberately delegates to `_titled_fields` rather than re-deriving it: an audit whose
    classifier has drifted from the scraper's measures its own copy, not the pipeline. An earlier
    pass here re-implemented it with an extra `.strip()`, which would have scored a
    whitespace-only title as a loss where the scrape keeps it.
    """
    return _titled_fields(page, url) is not None


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
    ctl_titled = _titled(ctl_body, "") if ctl_code == 200 else False
    print(
        f"{board}: control (nonexistent requisition) -> HTTP {ctl_code}, {ctl_len} chars, "
        f"titled={ctl_titled if ctl_code == 200 else 'n/a'}",
        flush=True,
    )
    # Every verdict below is scored against this one response, so a bad control does not degrade
    # the result — it inverts it, silently. A non-200 leaves `ctl_len` holding the length of an
    # *exception name*, which no real page is within `_SHELL_SLACK` of, so every closed posting
    # would come back `unreadable` and the report would read as "ADR-0053 is withholding
    # correctly" — the opposite of the truth. A *titled* control means this tenant serves a real
    # posting for an id that cannot exist, so the control does not define "not found" here at
    # all. Refuse both rather than emit a confident wrong number.
    if ctl_code != 200:
        sys.exit(
            f"{board}: control fetch returned {ctl_code} ({ctl_body}) — cannot score"
        )
    if ctl_titled:
        sys.exit(
            f"{board}: control page parsed as a real posting — this tenant does not serve a "
            "not-found shell, so a title-less page cannot be scored against it"
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
            elif abs(len(body) - ctl_len) <= _SHELL_SLACK:
                # Same shell the tenant serves for a requisition that does not exist. The slack
                # absorbs the echoed job id and the first-response tracking block (docstring),
                # not a different page shape.
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
        # Capped so one 10k-Board sweep does not write a multi-megabyte capture, but the cap is
        # recorded: a truncated sample that looks complete is worse than no sample, because the
        # whole point of this file is that someone can re-check the verdicts by hand.
        "gone_ids": gone_ids[:_MAX_LISTED],
        "gone_ids_truncated": max(0, len(gone_ids) - _MAX_LISTED),
        "unreadable": unreadable[:_MAX_LISTED],
        "unreadable_truncated": max(0, len(unreadable) - _MAX_LISTED),
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
