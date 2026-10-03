#!/usr/bin/env python3
"""Fold JobScore's public sitemap into its candidate pool.

Robots publishes this gzip sitemap; 2,332 URLs named 498 Boards on 2026-10-03.
It includes empty Boards and vendor test accounts, so it is discovery, never liveness.
The API's hourly polling guidance does not require repeatedly reading any job feed.
"""

from __future__ import annotations

import csv
import gzip
import re
from pathlib import Path

from headstart.network import http
from headstart.scrapers.base import USER_AGENT

ROOT = Path(__file__).resolve().parents[2]
SITEMAP = "https://careers.jobscore.com/sitemaps/careers.xml.gz"
POOL = ROOT / "data/ats-tenants-merged/jobscore.csv"


def tenants_in(xml: str) -> set[str]:
    return {
        s.lower()
        for s in re.findall(
            r"<loc>https://careers\.jobscore\.com/careers/([\w-]+)(?:/[^<]*)?</loc>",
            xml,
        )
    }


def main() -> None:
    response = http.fetch(
        "GET", SITEMAP, headers={"User-Agent": USER_AGENT}, timeout=30
    )
    response.raise_for_status()
    body = response.content
    xml = (gzip.decompress(body) if body.startswith(b"\x1f\x8b") else body).decode()
    tenants = tenants_in(xml)
    if not tenants:
        raise ValueError("JobScore sitemap has no Board URLs")
    rows = {}
    if POOL.exists():
        rows = {r["tenant"]: r for r in csv.DictReader(POOL.open())}
    for slug in tenants:
        old = rows.get(slug, {})
        sources = set(filter(None, old.get("source", "").split("+"))) | {
            "vendor-sitemap"
        }
        rows[slug] = {
            "ats": "jobscore",
            "tenant": slug,
            "url": f"https://careers.jobscore.com/careers/{slug}",
            "source": "+".join(sorted(sources)),
        }
    POOL.parent.mkdir(parents=True, exist_ok=True)
    with POOL.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ats", "tenant", "url", "source"])
        writer.writeheader()
        writer.writerows(rows[k] for k in sorted(rows))
    print(f"{len(tenants)} sitemap Boards; {len(rows)} candidates")


if __name__ == "__main__":
    main()
