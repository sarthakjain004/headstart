#!/usr/bin/env python3
"""Probe candidate Eightfold hosts on the scraper's *primary* surface and report the job count.

``check_liveness.py`` reads only ``/careers/sitemap.xml``, but ``EightfoldScraper`` prefers the
PCSX JSON API and falls back to the sitemap. A host whose sitemap 403s can still be a fully
scrapable Board, so a discovery sweep has to ask the same two questions the scraper asks:

  1. ``GET /careers`` -> ``_EF_GROUP_ID`` (the API's ``domain`` param);
  2. ``GET /api/pcsx/search?domain={gid}&start=0`` -> ``data.count`` is the Board total;
  3. else ``GET /careers/sitemap.xml`` -> count ``/careers/job/`` locs (one level of index).

Writes ``host,group_id,surface,jobs,note`` to ``--out`` incrementally, one row per host as it
settles.

**Politeness is load-bearing here, not decoration** (ADR-0026). Every Board host is the same
shared CloudFront distribution, so the whole sweep lands on one origin. Running 12 workers
alongside a portal sweep tripped Eightfold's bot wall on 2026-07-27 — it answers *every* host,
including known-good ones, with ``405`` and a "Human Verification" body, which reads exactly
like "no public board" and would silently poison the results. Hence the low worker default, the
per-request ``--delay``, and the bot-wall detection that aborts the run instead of recording
hundreds of false negatives.

Run:  python -u scripts/discover/eightfold_board_probe.py hosts.txt --out probe.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import threading
import time
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
from headstart import http

UA = "headstart/0.1 (job-board reader)"
_EF_GROUP_ID = re.compile(r'_EF_GROUP_ID\s*=\s*"([^"]+)"')
_CHILD_SITEMAP = re.compile(
    r"<loc>\s*([^<\s]*sitemap[^<\s]*\.xml[^<\s]*)\s*</loc>", re.IGNORECASE
)
# Eightfold's own board, served as the fallthrough for a host with no tenant behind it. On the
# careers page it shows up as one of these group ids; in the sitemap, as job URLs whose
# `?domain=` is eightfold.ai rather than the tenant's own. Either way the host has no Board.
_VENDOR_GROUPS = {"volkscience.com", "eightfold.ai"}
_SITEMAP_DOMAIN = re.compile(
    r"/careers/job/[^<\s]*?[?&]domain=([^&\"'<\s]+)", re.IGNORECASE
)


def _is_vendor_sitemap(text: str) -> bool:
    domains = {d.lower() for d in _SITEMAP_DOMAIN.findall(text)}
    return bool(domains) and domains <= _VENDOR_GROUPS


_BACKOFF_LOCK = threading.Lock()
_BACKOFF_UNTIL = [0.0]
_DELAY = [0.0]  # seconds to pause after each request, set from --delay
_BOT_WALL = (
    threading.Event()
)  # set when the origin starts answering everything with 405


def _sleep_if_backing_off() -> None:
    with _BACKOFF_LOCK:
        delay = _BACKOFF_UNTIL[0] - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def _trip_backoff(seconds: float) -> None:
    with _BACKOFF_LOCK:
        _BACKOFF_UNTIL[0] = max(_BACKOFF_UNTIL[0], time.monotonic() + seconds)


def _get(host: str, url: str, accept: str, timeout: int = 25):
    _sleep_if_backing_off()
    r = http.fetch(
        "GET",
        url,
        attempts=1,
        headers={
            "User-Agent": UA,
            "Accept": accept,
            "Referer": f"https://{host}/careers",
        },
        timeout=timeout,
    )
    if r.status_code in (429, 503):
        _trip_backoff(20.0)
    if r.status_code == 405:
        # The bot wall, not a real verdict: it 405s every host, known-good ones included.
        _BOT_WALL.set()
    if _DELAY[0]:
        time.sleep(_DELAY[0])
    return r


def probe(host: str) -> dict[str, object]:
    """One Board's verdict: which surface answered and how many jobs it reported."""
    row = {"host": host, "group_id": "", "surface": "", "jobs": "", "note": ""}
    if _BOT_WALL.is_set():
        row["note"] = "skipped: bot wall"
        return row
    group_id = None
    try:
        r = _get(host, f"https://{host}/careers", "text/html")
        if r.status_code == 200:
            m = _EF_GROUP_ID.search(r.text)
            group_id = m.group(1) if m else None
            if group_id is None:
                row["note"] = "careers 200 but no _EF_GROUP_ID"
        else:
            row["note"] = f"careers {r.status_code}"
    except http.RequestsError as exc:
        row["note"] = f"careers error {type(exc).__name__}"

    if group_id and group_id.lower() in _VENDOR_GROUPS and host not in _VENDOR_GROUPS:
        # Fallthrough: this host serves Eightfold's own board, so querying it would file
        # Eightfold's postings under the wrong company. Not a Board — record it as such.
        row["group_id"] = group_id
        row["note"] = "vendor fallthrough — no tenant board on this host"
        return row

    if group_id:
        row["group_id"] = group_id
        q = urllib.parse.urlencode(
            {"domain": group_id, "query": "", "location": "", "start": 0}
        )
        try:
            r = _get(host, f"https://{host}/api/pcsx/search?{q}", "application/json")
            if r.status_code == 200:
                data = (r.json() or {}).get("data") or {}
                row["surface"] = "pcsx"
                row["jobs"] = int(data.get("count") or 0)
                return row
            row["note"] = f"{row['note']}; pcsx {r.status_code}".strip("; ")
        except (http.RequestsError, ValueError) as exc:
            row["note"] = f"{row['note']}; pcsx {type(exc).__name__}".strip("; ")

    # sitemap fallback — the same shape check_liveness uses
    try:
        r = _get(host, f"https://{host}/careers/sitemap.xml", "application/xml")
        if r.status_code != 200:
            row["note"] = f"{row['note']}; sitemap {r.status_code}".strip("; ")
            return row
        text = r.text
        if _is_vendor_sitemap(text):
            row["note"] = "vendor fallthrough — no tenant board on this host"
            return row
        n = text.count("/careers/job/")
        if n:
            row["surface"] = "sitemap"
            row["jobs"] = n
            return row
        child = _CHILD_SITEMAP.search(text)
        if child and "index" not in child.group(1).lower():
            cr = _get(host, child.group(1), "application/xml")
            if cr.status_code == 200:
                if _is_vendor_sitemap(cr.text):
                    row["note"] = "vendor fallthrough — no tenant board on this host"
                    return row
                row["surface"] = "sitemap-index"
                row["jobs"] = cr.text.count("/careers/job/")
                return row
        if "<urlset" in text or "<sitemapindex" in text:
            row["surface"] = "sitemap"
            row["jobs"] = 0
            row["note"] = f"{row['note']}; empty board".strip("; ")
    except http.RequestsError as exc:
        row["note"] = f"{row['note']}; sitemap {type(exc).__name__}".strip("; ")
    return row


def _warn_if_constant(counts: list[int], threshold: float = 0.4) -> None:
    """Shout if one job count dominates the batch.

    Real Boards vary by orders of magnitude — Qualcomm has 1,918 openings, a small Board has 16.
    When a single value dominates, the probe is almost certainly reading one shared response
    rather than per-tenant data: on 2026-07-27 a fallthrough put an identical 63 on 44 Boards and
    every one of them looked like a plausible answer. Cheap guard, catches the whole class.
    """
    if len(counts) < 5:
        return
    top, n = Counter(counts).most_common(1)[0]
    if n / len(counts) >= threshold:
        print(
            f"WARNING: {n}/{len(counts)} probed Boards all report {top} jobs "
            f"({n / len(counts):.0%}). A constant across unrelated companies means a shared "
            f"or default response, not real per-Board counts — do not trust this batch.",
            flush=True,
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", help="host list files (default: stdin)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.5, help="pause after each request")
    args = ap.parse_args()
    _DELAY[0] = args.delay

    raw: list[str] = []
    for f in args.files:
        raw.extend(open(f, encoding="utf-8", errors="replace").read().split())  # noqa: SIM115
    if not args.files:
        raw.extend(sys.stdin.read().split())
    hosts = list(dict.fromkeys(h.strip().lower() for h in raw if h.strip()))
    print(f"probing {len(hosts)} hosts with {args.workers} workers", flush=True)

    out = open(args.out, "w", newline="", encoding="utf-8")  # noqa: SIM115
    writer = csv.DictWriter(
        out, fieldnames=["host", "group_id", "surface", "jobs", "note"]
    )
    writer.writeheader()
    out.flush()
    live = 0
    counts: list[int] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(probe, h): h for h in hosts}
        for i, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            writer.writerow(row)
            out.flush()
            if row["surface"]:
                live += 1
                counts.append(int(row["jobs"] or 0))
            print(
                f"[{i}/{len(hosts)}] {row['host']} surface={row['surface'] or '-'} "
                f"jobs={row['jobs']} {row['note']}",
                flush=True,
            )
    out.close()
    _warn_if_constant(counts)
    if _BOT_WALL.is_set():
        print(
            "ABORTED: the origin answered 405 (bot wall) — every verdict after that point is "
            "a false negative. Wait it out and re-run the remaining hosts.",
            flush=True,
        )
    print(
        f"done: {live}/{len(hosts)} answered a public surface -> {args.out}", flush=True
    )


if __name__ == "__main__":
    main()
