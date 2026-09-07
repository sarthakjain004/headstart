#!/usr/bin/env python3
"""Does a Zoho tenant expose an RSS feed, and does it hold jobs the 750-cap widget does not?

The widget's ceiling has been "evidence, not proof" since 2026-08-22: a board with exactly 750 real
openings and one with 5,000 look identical, because nothing in the page reveals a true total. An RSS
feed carrying even one job the widget omits breaks that tie — the union is a *lower bound* on the
true total, so union > 750 proves the widget truncated.

Three things here are deliberate, each because the obvious version was wrong when measured:

* **The widget parse is the scraper's own** (``ZohoScraper._records``), not a lookalike regex. A
  re-derived pattern diverged on the first draft — it required ``type="hidden"`` before ``value``
  where the scraper does not — so the sweep would have measured a parse the pipeline never runs.
* **A disabled feed is detected by shape, not by an English sentence.** Zoho answers a disabled
  feed with ``200 application/rss+xml`` and a one-line body, *localized*: measured live, four
  tenants return it in French ("Oups ! Il semble que la liste des emplois a été supprimée.") or
  Spanish. Matching the English wording counted those as working-but-empty feeds. The
  language-independent signal is the absence of an ``<rss``/``<channel>`` root, which also keeps a
  genuinely empty feed (root present, zero items) as its own distinct outcome.
* **A job id is the path segment after the portal**, not the last long digit-run in the URL. Item
  links are ``/jobs/{Portal}/{id}/{title-slug}``, and title slugs carry requisition numbers on some
  tenants (15 of 640 links on ``talproindia``), so taking the last run silently read the wrong
  number and inflated the "rss-only" count.

Streams one line per board as it lands, per the repo's streaming rule.
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from headstart.http import fetch
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.zoho import ZohoScraper

_ITEM = re.compile(r"<item>(.*?)</item>", re.DOTALL)
_LINK = re.compile(r"<link>([^<]*)</link>")
#: `/jobs/{Portal}/{id}/{title-slug}` — anchored, so a requisition number in the slug can't win.
_JOB_ID = re.compile(r"/jobs/[^/]+/(\d+)")
#: A real feed has an RSS root. A disabled one is a bare localized sentence with 200 + rss+xml
#: and no markup at all. Deliberately NOT matching Atom's `<feed>`: `_ITEM` only reads `<item>`, so
#: admitting an Atom root would report it as "present but empty" — a wrong answer where "disabled"
#: is merely an unsurprising one. No Atom root was observed across 150 boards.
_FEED_ROOT = re.compile(r"<(?:rss|channel)\b", re.IGNORECASE)


def _get(url: str) -> str:
    return fetch("GET", url, headers={"User-Agent": USER_AGENT}, timeout=60).text


def probe(host: str) -> dict:
    """One board: widget ids, feed ids, and how many the feed adds. Never raises."""
    row = {
        "host": host,
        "widget": None,
        "rss": None,
        "rss_only": None,
        "union": None,
        "note": "",
    }
    try:
        # `Careers` is the only portal name any ledger row uses — every live zoho `url` is a
        # bare host, and `zoho.py`'s own `_detail_url` hardcodes the same portal.
        page = _get(f"https://{host}/jobs/Careers")
        widget = {str(r["id"]) for r in ZohoScraper._records(page) if r.get("id")}
        row["widget"] = len(widget)
    except Exception as exc:  # noqa: BLE001 — a probe reports every failure as a row
        row["note"] = f"widget {type(exc).__name__}"
        return row
    try:
        feed = _get(f"https://{host}/jobs/Careers/rss")
    except Exception as exc:  # noqa: BLE001 — one bad host must not stop the sweep
        row["note"] = f"rss {type(exc).__name__}"
        return row
    if not _FEED_ROOT.search(feed):
        row["note"] = "feed disabled"
        return row
    rss = set()
    for item in _ITEM.findall(feed):
        if (link := _LINK.search(item)) and (jid := _JOB_ID.search(link.group(1))):
            rss.add(jid.group(1))
    # An id the widget's *raw HTML* never mentions, not merely one our parse missed.
    extra = {i for i in rss - widget if i not in page}
    row.update(rss=len(rss), rss_only=len(extra), union=len(widget | rss))
    if not rss:
        row["note"] = "feed present but empty"
    return row


def _sample(rows: list[dict], limit: int) -> list[dict]:
    """An even stride across the ledger sorted by job count — every size band, no duplicates.

    The first draft took "top half + from the midpoint", which sampled the largest boards and the
    median ones and nothing between, then reported the result as if it were a population rate.
    """
    rows = sorted(rows, key=lambda r: -int(r["jobs"] or 0))
    if limit < 1:
        return []
    if limit >= len(rows):
        return rows
    step = len(rows) / limit
    return [rows[int(i * step)] for i in range(limit)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", default="data/validate/liveness/zoho.csv")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--min-jobs", type=int, default=1)
    args = ap.parse_args()

    with open(args.ledger, encoding="utf-8") as fh:
        live = [
            r
            for r in csv.DictReader(fh)
            if r["status"] == "live" and int(r["jobs"] or 0) >= args.min_jobs
        ]
    picked = _sample(live, args.limit)
    hosts = [
        r["url"].replace("https://", "").replace("http://", "").rstrip("/")
        for r in picked
    ]
    print(
        f"# {len(hosts)} of {len(live)} Hiring Boards, even stride by job count",
        flush=True,
    )
    print(
        f"{'host':<44} {'widget':>6} {'rss':>5} {'rss-only':>8} {'union':>6}  note",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for fut in as_completed({pool.submit(probe, h): h for h in hosts}):
            r = fut.result()
            print(
                f"{r['host']:<44} {r['widget']!s:>6} {r['rss']!s:>5} "
                f"{r['rss_only']!s:>8} {r['union']!s:>6}  {r['note']}",
                flush=True,
            )


if __name__ == "__main__":
    main()
