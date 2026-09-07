"""Tell a SuccessFactors detail 403 apart from a 200 the parser cannot read — the distinction
production throws away.

`docs/pipeline/2026-09-07_five-run-log-review.md` §1 measured the symptom across five consecutive
runs: **102 SuccessFactors Boards returned 0 jobs in every one of them**, the worst
(`careers.te.com`) fetching 2,127 pages over 27 minutes — 97% of its shard's wall clock — and
building nothing. In one run those Boards listed 56,120 postings and ingested none.

The log could not say why, and that is the gap this fills. ``_job_fields`` maps every outcome onto
the same ``None``::

    if response.status_code != 200:
        return None
    return _titled_fields(response.text, url)

so "2127/2127 detail fields missing" cannot separate a refusal from a body that arrived and would
not parse — and those two have nothing in common as fixes. This keeps them apart, and for the
second case prints the body, its size and which markers it carries.

**What it found, 2026-09-07.** Refusal: HTTP 403, an 111-byte
``{"error":{"status-code":"403","message":"Policy ID: ..."}}``, triggered by the **exact** literal
``headstart/0.1 (job-board reader)`` — ``headstart/0.1 (job-board)``, ``headstart/0.1 (reader)``,
``curl/8.7.1`` and ``python-requests/2.32.3`` all returned 200 on the same URL. A denylist entry
for one string, not a heuristic about crawlers. `docs/successfactors/2026-09-07_user-agent-denylist.md`
has the full bisection and what it cost.

Worth recording that the first draft of this script was built to run **from Actions**, on the
theory that the runner egress was walled — the pages parsed fine from a laptop, so the vantage was
the only variable left. That was wrong, and it was wrong in the expensive direction: the bug
reproduced on the first local run, in about thirty seconds. Reach for the harness before the
elaborate explanation.

**Boards.** A suspect alone proves nothing about the vantage, so `careers.bureauveritas.com` — 1
detail lost of 1,990 in the same runs where `careers.te.com` lost 2,127 of 2,127 — is the control.
If it fails too, the cause is wherever the probe is running, not the tenant.

**Arms.** ``direct`` is the route production takes: SuccessFactors sets no ``egress_fallback_on``
(only eightfold, workday and workable do), so a walled request here has no second address and no
rescue. ``warp`` repeats it through the spare egress, which is what to reach for if this ever comes
back as a genuine per-IP block rather than a per-string one.

Run: python -u scripts/bench/probe_successfactors_detail.py --n 3 --arms direct
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from typing import Any

from headstart import http, spare_egress
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.successfactors import (
    SuccessFactorsScraper,
    _job_urls_from,
    _titled_fields,
)

#: Boards that returned 0 jobs in all five runs of the review, worst-first by seconds burned.
SUSPECTS = ("careers.te.com", "jobs.l3harris.com")
#: A Board that lost 1 detail of 1,990 in those same runs. Without it a failing suspect proves
#: nothing about the vantage.
CONTROLS = ("careers.bureauveritas.com",)

#: Markers worth counting in a 200 body that will not parse. Each one separates a real job page
#: from a specific impostor: an interstitial has none of them, a login wall has a form, and a
#: genuinely-changed template has some but not the ones the parser reads.
_MARKERS = (
    "application/ld+json",
    'itemprop="title"',
    "joblayouttoken",
    "JobPosting",
    "captcha",
    "Incapsula",
    "Request unsuccessful",
    "Access Denied",
)
_BODY_SAMPLE = 400


def _outcome(exc: BaseException) -> str:
    """The groupable label for a raised request: its curl code where it has one, else its type."""
    code = getattr(exc, "code", None)
    return f"{type(exc).__name__}(code={code})" if code else type(exc).__name__


def egress_ip(proxy: str | None) -> str:
    """This arm's public address, so a reader can tell two arms apart by vantage, not by label."""
    kwargs: dict[str, Any] = {"timeout": 15, "attempts": 1}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    try:
        response = http.fetch("GET", "https://api.ipify.org?format=json", **kwargs)
        return str(json.loads(response.text).get("ip", "?"))
    except Exception as exc:  # noqa: BLE001 - a probe never dies on its own diagnostics
        return f"unreadable ({_outcome(exc)})"


def job_urls(slug: str, want: int) -> list[str]:
    """The Board's own listing surface, through the real scraper — not a hand-built URL shape.

    `fetch_raw` is not reused: it would run the whole detail pass, which for `careers.te.com` is
    the 27 minutes this probe exists to explain. Only the listing half is borrowed.
    """
    scraper = SuccessFactorsScraper(slug, slug)
    kind, text, _ = scraper._fetch_sitemap()
    listed = _job_urls_from(text, slug) if kind == "urlset" else []
    if not listed:
        listed, _ = scraper._search_job_urls()
    return [url for url, _ in listed[:want]]


def probe_board(slug: str, urls: list[str], proxy: str | None) -> dict[str, Any]:
    """Fetch each URL the way `_job_fields` does, but keep apart what it collapses into None."""
    statuses: Counter[str] = Counter()
    raised: Counter[str] = Counter()
    parsed = unparsed = 0
    markers: Counter[str] = Counter()
    sample: dict[str, Any] | None = None
    kwargs: dict[str, Any] = {"headers": {"User-Agent": USER_AGENT}, "timeout": 30}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    for i, url in enumerate(urls, 1):
        started = time.monotonic()
        try:
            response = http.fetch("GET", url, **kwargs)
        except Exception as exc:  # noqa: BLE001 - classifying the failure IS the measurement
            raised[_outcome(exc)] += 1
            print(f"    [{i}/{len(urls)}] raised {_outcome(exc)}", flush=True)
            continue
        elapsed = time.monotonic() - started
        statuses[str(response.status_code)] += 1
        if response.status_code != 200:
            print(
                f"    [{i}/{len(urls)}] HTTP {response.status_code} "
                f"{len(response.text):,}B {elapsed:.1f}s",
                flush=True,
            )
            continue
        fields = _titled_fields(response.text, url)
        if fields:
            parsed += 1
            print(
                f"    [{i}/{len(urls)}] 200 parsed  {len(response.text):,}B {elapsed:.1f}s "
                f"title={str(fields.get('title'))[:48]!r}",
                flush=True,
            )
            continue
        # A 200 the parser cannot use is the interesting case and the one the production log
        # cannot report, so it is the one that gets the body.
        unparsed += 1
        for marker in _MARKERS:
            if marker in response.text:
                markers[marker] += 1
        if sample is None:
            sample = {
                "url": url,
                "bytes": len(response.text),
                "head": re.sub(r"\s+", " ", response.text[:_BODY_SAMPLE]),
            }
        print(
            f"    [{i}/{len(urls)}] 200 UNPARSED {len(response.text):,}B {elapsed:.1f}s",
            flush=True,
        )
    return {
        "board": slug,
        "attempted": len(urls),
        "statuses": dict(statuses),
        "raised": dict(raised),
        "parsed": parsed,
        "unparsed_200": unparsed,
        "markers_in_unparsed": dict(markers),
        "first_unparsed": sample,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=40, help="job pages per board per arm")
    ap.add_argument(
        "--arms", default="direct,warp", help="comma-separated: direct,warp"
    )
    ap.add_argument("--suspects", default=",".join(SUSPECTS))
    ap.add_argument("--controls", default=",".join(CONTROLS))
    ap.add_argument("--out", help="write the full result as JSON here")
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    boards = [("suspect", s) for s in args.suspects.split(",") if s] + [
        ("control", c) for c in args.controls.split(",") if c
    ]

    # Listing once per Board, not once per arm: the question is about the detail pass, and
    # re-listing would double the load while telling us nothing new.
    listings: dict[str, list[str]] = {}
    for role, slug in boards:
        started = time.monotonic()
        try:
            urls = job_urls(slug, args.n)
        except Exception as exc:  # noqa: BLE001
            print(f"{role} {slug}: LISTING FAILED — {_outcome(exc)}", flush=True)
            listings[slug] = []
            continue
        print(
            f"{role} {slug}: listed {len(urls)} job page(s) in "
            f"{time.monotonic() - started:.1f}s",
            flush=True,
        )
        listings[slug] = urls

    results: list[dict[str, Any]] = []
    for arm in arms:
        proxy = None
        if arm == "warp":
            proxy = spare_egress.proxy_url()
            if proxy is None:
                print(
                    "\n== arm warp: SKIPPED — no spare egress on this runner",
                    flush=True,
                )
                results.append({"arm": arm, "skipped": "no spare egress"})
                continue
        ip = egress_ip(proxy)
        print(f"\n== arm {arm} (egress {ip}, proxy={proxy or 'none'})", flush=True)
        for role, slug in boards:
            urls = listings.get(slug) or []
            if not urls:
                print(f"  {role} {slug}: nothing listed, skipping", flush=True)
                continue
            print(f"  {role} {slug}:", flush=True)
            row = probe_board(slug, urls, proxy)
            row.update(arm=arm, role=role, egress_ip=ip)
            results.append(row)
            print(
                f"  -> {slug}: parsed {row['parsed']}/{row['attempted']}, "
                f"200-unparsed {row['unparsed_200']}, "
                f"statuses {row['statuses']}, raised {row['raised']}",
                flush=True,
            )

    print("\n===== summary =====", flush=True)
    print(
        f"{'arm':<8} {'role':<8} {'board':<32} {'parsed':>8} {'unparsed':>9}  statuses",
        flush=True,
    )
    for row in results:
        if "board" not in row:
            print(f"{row['arm']:<8} {'-':<8} {'-':<32} {'skipped':>8}", flush=True)
            continue
        print(
            f"{row['arm']:<8} {row['role']:<8} {row['board']:<32} "
            f"{row['parsed']:>8} {row['unparsed_200']:>9}  {row['statuses']}",
            flush=True,
        )
    for row in results:
        if row.get("first_unparsed"):
            print(
                f"\nfirst 200-but-unparsed body, {row['arm']}/{row['board']} "
                f"({row['first_unparsed']['bytes']:,}B), markers "
                f"{row['markers_in_unparsed']}:\n  {row['first_unparsed']['head']}",
                flush=True,
            )
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
        print(f"\nwrote {args.out}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
