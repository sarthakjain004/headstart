#!/usr/bin/env python3
"""Radancy TalentBrew career fronts from urlscan.io: the pages that load ``tbcdn.talentbrew.com``.

A TalentBrew front sits on its customer's own host (``jobs.intuit.com``), so there is no vendor
namespace for Wayback or Common Crawl to sweep. What every front shares is its asset CDN, and
urlscan.io indexes the domains a scanned page requests: ``domain:tbcdn.talentbrew.com`` returns
the scans whose page loaded it, and each scan names its page's host. On 2026-09-26 that query
named 1,073 hosts across 2019-2026, and 187 of the 220 hosts in the first pool came from it and
nothing else (ADR-0246).

Each host is then resolved to the front it belongs to — the host of the job URLs its own
``/sitemap.xml`` lists — because vanity and country hosts redirect to a canonical front
(``www.takedajobs.com`` to ``jobs.takeda.com``, ``jobs.citi.tw`` to ``jobs.citi.com``), and the
ledger must hold that front once. A host whose sitemap lists no job URL is kept as it is: an empty
front looks the same, and ``check_liveness.p_radancy`` is what tells the two apart. Radancy's own
QA hosts (``*.runmytests.com``, ``*.runmytests.eu``) mirror real fronts' postings and are dropped.

What it does not cover: a front no one ever submitted to urlscan. The free search API returns at
most 100 results a query, so each month is asked on its own and a month that reports more is
asked again week by week.

Run:  python scripts/discover/mine_radancy.py [OUT_CSV]   (default: the radancy pool, appended)
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from headstart.scrapers.radancy import JOB_PATH  # the job-URL shape, single source

POOL = ROOT / "data" / "ats-tenants-merged" / "radancy.csv"
UA = "headstart/0.1"
QA_HOST = re.compile(r"\.runmytests\.(?:com|eu)$")
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


#: Windows urlscan never answered — reported, never read as "no scans" (a silent 0 is how a
#: throttled source passes for an exhausted one).
UNANSWERED: list[date] = []


def _search(start: date, end: date) -> dict:
    query = f"domain:tbcdn.talentbrew.com AND date:[{start} TO {end}}}"
    url = "https://urlscan.io/api/v1/search/?" + urllib.parse.urlencode(
        {"q": query, "size": 100}
    )
    for attempt in range(6):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except Exception as exc:  # noqa: BLE001 — urlscan throttles; wait and ask again
            print(f"  {start}: {exc}, retrying", flush=True)
            time.sleep(30 * (attempt + 1))  # 429s on this API cleared within minutes
    UNANSWERED.append(start)
    return {}


def _windows() -> list[tuple[date, date]]:
    months, day = [], date(2019, 1, 1)
    while day < datetime.now(UTC).date():
        nxt = date(day.year + day.month // 12, day.month % 12 + 1, 1)
        months.append((day, nxt))
        day = nxt
    return months


def urlscan_hosts() -> set[str]:
    hosts: set[str] = set()
    for start, end in _windows():
        found = _search(start, end)
        windows = [(start, end)]
        if found.get("total", 0) > 100:  # the page is capped: ask week by week
            windows = [
                (d, min(d + timedelta(days=7), end))
                for d in (start + timedelta(days=7 * i) for i in range(5))
                if d < end
            ]
        for a, b in windows:
            page = found if windows == [(start, end)] else _search(a, b)
            for result in page.get("results", []):
                for key in ("page", "task"):
                    host = ((result.get(key) or {}).get("domain") or "").lower()
                    if host:
                        hosts.add(host)
            time.sleep(2)
        print(f"{start}: {found.get('total', 0)} scans, {len(hosts)} hosts", flush=True)
    return hosts


def front_of(host: str) -> str | None:
    """The host of the job URLs ``host``'s sitemap lists, else ``host``; None if unreachable."""
    try:
        response = requests.get(
            f"https://{host}/sitemap.xml", headers={"User-Agent": UA}, timeout=45
        )
    except requests.RequestException:
        return None
    for loc in LOC.findall(response.content.decode("utf-8-sig", "replace")):
        parts = urllib.parse.urlsplit(loc)
        if JOB_PATH.match(parts.path):
            return (parts.hostname or host).lower()
    return host


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else POOL
    held: set[str] = set()
    if out.exists():
        held = {row["tenant"].lower() for row in csv.DictReader(out.open())}
    new = 0
    with out.open("a", newline="") as fh:
        writer = csv.writer(fh)
        if not held:
            writer.writerow(["ats", "tenant", "url", "source"])
        for host in sorted(urlscan_hosts()):
            front = front_of(host)
            if not front or QA_HOST.search(front) or front in held:
                continue
            held.add(front)
            writer.writerow(["radancy", front, f"https://{front}", "urlscan-tbcdn"])
            fh.flush()
            new += 1
            print(f"  + {front}  (from {host})", flush=True)
    print(f"DONE {new} new fronts -> {out}", flush=True)
    if UNANSWERED:
        print(f"NOT MEASURED (urlscan never answered): {UNANSWERED}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
