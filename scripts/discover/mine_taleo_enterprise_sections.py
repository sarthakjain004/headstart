#!/usr/bin/env python3
"""Find the career sections of Taleo Enterprise hosts that the liveness ledger does not hold, and stage them.

A Taleo Enterprise Board is a host plus a section (`{host}.taleo.net/careersection/{section}`), and a host
that resolves is only half of one. This asks each host for `/careersection/{section}/jobsearch.ftl?lang=en`
(the scraper's own `url()`) for a short core list, every section the ledger already names for the host, and every
section Wayback's CDX has an archived job page for. Measured 2026-09-29 on 24 live-row hosts, seeded without their
ledger names: the core list found 22 held live sections, the CDX names 135, the host's own label as a template
none, so 22 of 24 hosts and 157 of 169 held live sections came back.

What an answer means (`classify_probe`), read from the body and never from the status alone:

* a 200 shell carrying the scraper's `portalNo` is a section that exists;
* a 200 titled "Career Section Unavailable" is a section that does not exist on an ACTIVE tenant;
* 404 on every section is a decommissioned tenant (23 of 25 dead-only ledger hosts that still resolve);
* a 30x is a vanity front (`stantec.jobs`) or a `mysubmissions.ftl` redirect: not a section to scrape;
* a timeout, reset, empty status or 5xx is UNSETTLED. It is asked again, and if it never settles the host is
  reported unreachable: an unsettled answer is never read as "absent".

Sections that exist and whose Board key the ledger does not hold (`slug_from` + lower-cased key, any status) are
appended to the pool CSV (`ats,tenant,url`, tenant == url, the ledger's own spelling); land them with
`python scripts/validate/check_liveness.py taleo_enterprise --dir data/wayback-ats`, then re-run
`taleo_enterprise_subset_sections.py`. Each hit row is flushed as it is found.

    PYTHONPATH=src python scripts/discover/mine_taleo_enterprise_sections.py hosts.txt --cdx [--workers 8]
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from headstart.network import http
from headstart.scrapers.taleo_enterprise import TaleoEnterpriseScraper

UA = "HeadStart-liveness/0.1 (careers-board liveness check)"
POOL = ROOT / "data" / "wayback-ats" / "taleo_enterprise.csv"
LEDGER = ROOT / "data" / "validate" / "liveness" / "taleo_enterprise.csv"
#: 1..40, the 100xx codes the ledger shows, and the names live rows use most.
CORE = (
    [str(n) for n in range(1, 41)]
    + ["10000", "10001", "10020", "10040", "10060", "10080", "10100", "10120"]
    + [
        "ex",
        "ext",
        "external",
        "exm",
        "in",
        "internal",
        "careers",
        "jobs",
        "en",
        "us",
        "uk",
        "corporate",
    ]
    + [
        "professional",
        "campus",
        "students",
        "experienced",
        "hourly",
        "retail",
        "mgr",
        "field",
        "office",
    ]
)
_PORTAL = re.compile(r"portalNo:\s*'?(\d+)")
_UNAVAILABLE = "Career Section Unavailable"
_CDX_SKIP = re.compile(
    r"^(\d{4}PRD.*|rest|iam|sitemap.*|careersection|css|js|images?|theme.*|static.*)$",
    re.IGNORECASE,
)
_CDX_SECTION = re.compile(r"/careersection/([^/?#]+)/")

SECTION, ABSENT, REDIRECT, GONE, UNSETTLED = (
    "section",
    "absent",
    "redirect",
    "gone",
    "unsettled",
)


def classify_probe(status: int | None, body: str, location: str | None = None) -> str:
    """One probe's answer, from the body first. A missing or 5xx/429 status is UNSETTLED, never ABSENT."""
    if status is None or status == 0 or status == 429 or status >= 500:
        return UNSETTLED
    if 300 <= status < 400:
        return REDIRECT
    if status == 200 and _PORTAL.search(body):
        return SECTION
    if status == 200 and _UNAVAILABLE in body:
        return ABSENT
    if status in (404, 410):
        return GONE
    return UNSETTLED


def cdx_section_names(cdx_text: str) -> set[str]:
    """The section names in Wayback CDX `original` lines of archived job pages, minus Taleo's asset folders
    (`2024PRD.2.0.50.3.0`), REST and login routes."""
    names = set()
    for line in cdx_text.splitlines():
        match = _CDX_SECTION.search(line)
        if match and not _CDX_SKIP.match(match.group(1)):
            names.add(match.group(1))
    return names


def candidate_sections(
    ledger_names: Iterable[str], cdx_names: Iterable[str]
) -> list[str]:
    """Core list first, then the host's ledger names, then its archived names, each once."""
    return list(dict.fromkeys([*CORE, *sorted(ledger_names), *sorted(cdx_names)]))


def host_state(kinds: Iterable[str]) -> str:
    """`decommissioned` (every probe 404/410), `unreachable` (nothing settled), else `active`."""
    kinds = list(kinds)
    if kinds and all(k == GONE for k in kinds):
        return "decommissioned"
    if not any(k in (SECTION, ABSENT, REDIRECT, GONE) for k in kinds):
        return "unreachable"
    return "active"


def _ask(host: str, section: str) -> str:
    """One section, asked up to three times; the last unsettled answer stands."""
    url = f"https://{host}/careersection/{quote(section)}/jobsearch.ftl?lang=en"
    kind = UNSETTLED
    for attempt in range(3):
        status, body, location = None, "", None
        try:
            response = http.fetch(
                "GET",
                url,
                headers={"User-Agent": UA},
                timeout=20,
                allow_redirects=False,
                attempts=2,
            )
        except Exception as exc:  # noqa: BLE001 - a 302 whose body Taleo mislabels raises but carries the response
            response = getattr(exc, "response", None)
        if response is not None:
            status, location = response.status_code, response.headers.get("location")
            body = response.text if status == 200 else ""
        kind = classify_probe(status, body, location)
        if kind != UNSETTLED:
            return kind
        time.sleep(1.5 * (attempt + 1))
    return kind


_cdx_lock = threading.Lock()


def _cdx_names(host: str) -> set[str]:
    """Archived section names for the host, one request at a time (archive.org rate-limits); a failed answer
    is retried, never read as "no archived sections"."""
    url = (
        f"http://web.archive.org/cdx/search/cdx?url={quote(host)}/careersection/*&output=txt&fl=original"
        "&collapse=urlkey&limit=3000&filter=original:.*(jobsearch|jobdetail|jobapply|moresearch).*"
    )
    with _cdx_lock:
        for attempt in range(3):
            try:
                response = http.fetch(
                    "GET", url, headers={"User-Agent": UA}, timeout=60, attempts=1
                )
                if response.status_code == 200:
                    time.sleep(1.0)
                    return cdx_section_names(response.text)
            except Exception:  # noqa: BLE001, S110 - retried below
                pass
            time.sleep(5 * (attempt + 1))
    return set()


def _ledger() -> tuple[dict[str, set[str]], set[str]]:
    """``({host: section names it has rows for}, {lower-cased Board keys held under any status})``."""
    names: dict[str, set[str]] = {}
    keys: set[str] = set()
    with LEDGER.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            slug = TaleoEnterpriseScraper.slug_from(row["tenant"], row["url"])
            parts = urlsplit(slug)
            names.setdefault(parts.hostname, set()).add(parts.path.rsplit("/", 1)[-1])
            keys.add(slug.lower())
    return names, keys


def mine_host(
    host: str, ledger_names: set[str], use_cdx: bool
) -> tuple[str, list[str]]:
    """``(host state, sections that exist)``."""
    cdx = _cdx_names(host) if use_cdx else set()
    kinds = {s: _ask(host, s) for s in candidate_sections(ledger_names, cdx)}
    return host_state(kinds.values()), [s for s, k in kinds.items() if k == SECTION]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "hosts", help="file of hosts, one per line, with or without .taleo.net"
    )
    ap.add_argument(
        "--cdx",
        action="store_true",
        help="also try the section names Wayback has archived",
    )
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--pool", type=Path, default=POOL)
    args = ap.parse_args()

    hosts = [
        h.strip().lower() for h in Path(args.hosts).read_text().split() if h.strip()
    ]
    hosts = [h if h.endswith(".taleo.net") else f"{h}.taleo.net" for h in hosts]
    names, held = _ledger()
    args.pool.parent.mkdir(parents=True, exist_ok=True)
    fresh = not args.pool.exists()
    out = args.pool.open("a", encoding="utf-8", newline="")
    try:
        writer = csv.writer(out)
        if fresh:
            writer.writerow(["ats", "tenant", "url"])
        with ThreadPoolExecutor(args.workers) as pool:
            futures = {
                pool.submit(mine_host, h, names.get(h, set()), args.cdx): h
                for h in hosts
            }
            for n, future in enumerate(as_completed(futures), 1):
                host = futures[future]
                state, sections = future.result()
                new = []
                for section in sections:
                    url = f"https://{host}/careersection/{section}"
                    if TaleoEnterpriseScraper.slug_from(url, url).lower() not in held:
                        new.append(url)
                        writer.writerow(["taleo_enterprise", url, url])
                out.flush()
                print(
                    f"[{n}/{len(hosts)}] {host}: {state}, {len(sections)} sections, {len(new)} new",
                    flush=True,
                )
    finally:
        out.close()


if __name__ == "__main__":
    main()
