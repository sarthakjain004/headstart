#!/usr/bin/env python3
"""Recruitee labels that OTX passive DNS and urlscan have seen, minus the ones the ledger holds.

Every Recruitee Board is `{label}.recruitee.com` on one address (35.186.220.63), and `*.recruitee.com` is a
wildcard, so DNS and certificates cannot say which labels are real tenants. Reverse-IP can, but the
free HackerTarget answer stops at 500 names in alphabetical order and ignores every paging parameter
(measured 2026-09-29). Two sources answer past that, keyed by recency instead of by name:

  * OTX AlienVault passive DNS, no key: `indicator/IPv4/{ip}/passive_dns` and
    `indicator/domain/recruitee.com/passive_dns`, each the 500 names seen most recently.
    34 unheld labels on 2026-09-29, 16 of them live tenants.
  * urlscan's public search, `page.apexDomain:recruitee.com`, paged with `search_after` (its `has_more`
    is always false). Two exclusions keep it readable: the apex page, scanned thousands of times, and any
    one host that fills 30 of a page's 100 hits (`peripass` did). 40 unheld labels on 2026-09-29.

Held is decided through `RecruiteeScraper.slug_from`, lowercased, the identity `scrapable_boards` uses.
Each unheld label is appended to OUT_FILE and flushed as its source (an OTX answer, a urlscan page)
delivers it, so a crash or a dropped link keeps what was found. A network failure, or a 200 that lacks
the key the answer is read from, is reported as unreachable, never as "no labels", and the run then
exits 1 so a caller re-runs it. A source that answers only ever proves a label was *seen*.

Landing what this finds (it lands nothing itself):

  1. `scripts/discover/mine_recruitee_offers_sieve.py OUT_FILE` verifies each label against the offers
     API on its own label and stages the verified ones to `data/wayback-ats/recruitee.csv`. Both sources
     keep surfacing throwaway phishing tenants (`eliteexecutivesid96384`, ...) whose offers API answers a
     302 to another label with 0 offers, plus vendor hosts (`app`, `blog`, `docs`, `www`) that answer
     non-JSON; the sieve reads both as not-live.
  2. Read the staged tenants' offers before landing. A fresh account carries a "Senior Marketer (Sample)"
     offer, and an account that only ever held such an offer, or only "TEST ..." ones, is a demo
     (`excluded_and_parked.EXCLUDED_BOARDS`).
  3. `python scripts/validate/check_liveness.py --dir data/wayback-ats recruitee` writes the ledger rows.
  4. `python scripts/validate/dedupe_boards.py --ats recruitee --workers 4 --apply`, applied only when
     its summary shows no `unreachable` (ADR-0301): a new label is often the target of a held label's
     redirect, and both would otherwise serve every posting twice.

Run:  python -u scripts/discover/mine_recruitee_otx_urlscan.py OUT_FILE
"""

import csv
import json
import re
import subprocess
import sys
import time
import urllib.parse
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from headstart.scrapers.recruitee import RecruiteeScraper  # needs src on sys.path first

LEDGER = ROOT / "data" / "validate" / "liveness" / "recruitee.csv"
IP = "35.186.220.63"
LABEL = re.compile(r"^([a-z0-9][a-z0-9-]*)\.recruitee\.com$")
URLSCAN_QUERY = (
    'page.apexDomain:recruitee.com AND NOT page.url:"https://recruitee.com/"'
)
PAGE_SIZE = 100
DOMINATED = 30  # hits of one page.domain, out of a page, that earn it an exclusion


def label_of(host: str) -> str | None:
    """`foo` for `foo.recruitee.com`; None for the apex, a deeper name or another domain."""
    m = LABEL.match((host or "").strip().lower())
    return m.group(1) if m else None


def labels_from_otx(payload: dict) -> set[str]:
    """The labels in one OTX passive_dns answer."""
    hosts = (r.get("hostname", "") for r in payload.get("passive_dns", []))
    return {label for h in hosts if (label := label_of(h))}


def labels_from_hits(hits: list[dict]) -> set[str]:
    """The labels in one page of urlscan hits, read off each scan's page domain."""
    return {
        label for h in hits if (label := label_of(h.get("page", {}).get("domain", "")))
    }


def dominant_domain(hits: list[dict], excluded: list[str]) -> str | None:
    """The one page.domain filling `DOMINATED` or more of the page, if it is not yet excluded.

    `page.domain:X` matches X's subdomains too, so the apex `recruitee.com` can never be excluded
    this way and is never returned."""
    counts = Counter(h.get("page", {}).get("domain") for h in hits)
    if not counts:
        return None
    top, n = counts.most_common(1)[0]
    if n >= DOMINATED and top and top not in excluded and top != "recruitee.com":
        return top
    return None


def urlscan_query(excluded: list[str]) -> str:
    return URLSCAN_QUERY + "".join(f' AND NOT page.domain:"{d}"' for d in excluded)


def next_cursor(hits: list[dict]) -> str:
    """urlscan's `search_after` value: the last hit's `sort` pair, comma-joined."""
    return ",".join(str(x) for x in hits[-1]["sort"])


def held(ledger: Path = LEDGER) -> set[str]:
    """The Board identities the ledger holds: the scraper's slug, lowercased."""
    with ledger.open(encoding="utf-8") as f:
        return {
            RecruiteeScraper.slug_from(r["tenant"], r["url"]).lower()
            for r in csv.DictReader(f)
        }


def get_json(url: str) -> dict | None:
    """`curl -m` so the timeout covers DNS. None after three failed tries, which the caller reports."""
    for attempt in range(3):
        out = subprocess.run(
            ["curl", "-sS", "-m", "90", "-w", "\n%{http_code}", url],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        body, _, code = out.rpartition("\n")
        if code == "200":
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                pass
        time.sleep(10 * (attempt + 1))
    return None


def otx(path: str) -> set[str] | None:
    """The labels of one OTX answer; None when it failed or lacks `passive_dns` (an empty list is fine)."""
    data = get_json(f"https://otx.alienvault.com/api/v1/indicator/{path}/passive_dns")
    if data is None or "passive_dns" not in data:
        return None
    return labels_from_otx(data)


def urlscan_pages() -> Iterator[set[str] | None]:
    """The labels of each urlscan page, newest scans first; a final None means a page was unreachable.

    A 200 without a `results` list is a failed page, not an empty one: reading it as empty would end
    the walk early and look like a complete answer."""
    excluded: list[str] = []
    after = None
    while True:
        url = "https://urlscan.io/api/v1/search/?" + urllib.parse.urlencode(
            {"q": urlscan_query(excluded), "size": PAGE_SIZE}
        )
        data = get_json(url + (f"&search_after={after}" if after else ""))
        if data is None or not isinstance(data.get("results"), list):
            yield None
            return
        hits = data["results"]
        if (top := dominant_domain(hits, excluded)) is not None:
            excluded.append(top)
            continue
        yield labels_from_hits(hits)
        if len(hits) < PAGE_SIZE:
            return
        after = next_cursor(hits)
        time.sleep(3)  # the free search allows 30 calls a minute per address


def sources() -> Iterator[tuple[str, set[str] | None]]:
    yield "otx ip", otx(f"IPv4/{IP}")
    yield "otx domain", otx("domain/recruitee.com")
    for labels in urlscan_pages():
        yield "urlscan", labels


def main(argv: list[str]) -> int:
    """0 when every source answered, 1 when any was unreachable, 2 for a missing OUT_FILE."""
    if len(argv) < 2:
        print(__doc__)
        return 2
    have = held()
    written: set[str] = set()
    unreachable = 0
    with open(argv[1], "a", encoding="utf-8") as out:
        for name, labels in sources():
            if labels is None:
                unreachable += 1
                print(f"{name}: UNREACHABLE, re-run later", flush=True)
                continue
            fresh = sorted(labels - have - written)
            for label in fresh:
                out.write(f"{label}\n")
                out.flush()
            written.update(fresh)
            print(f"{name}: {len(labels)} labels, {len(fresh)} new unheld", flush=True)
    print(f"{len(written)} distinct unheld -> {argv[1]}", flush=True)
    if unreachable:
        print(f"{unreachable} source answer(s) unreachable: exit 1", flush=True)
    return 1 if unreachable else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
