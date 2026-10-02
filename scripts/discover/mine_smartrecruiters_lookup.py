#!/usr/bin/env python3
"""SmartRecruiters miner — Board Slugs from the vendor's own company lookup, and a public roster of it.

SmartRecruiters' job-seeker app ``jobs.smartrecruiters.com`` looks companies up through

    GET https://jobs.smartrecruiters.com/sr-jobs/company-lookup?q=<prefix>
    -> {"results": [{"identifier": "AbesGarden", "name": "Abe's Garden"}, ...]}

a word-prefix search over company names, capped at 100 rows, whose ``identifier`` is the Board's
case-sensitive Slug (the one the posting API reports as ``company.identifier``). It is not in the
developer docs, which is why the Wayback miner (``mine_smartrecruiters.py``) never used it. The
MIT-licensed roster ``amikai/openings-mcp`` ``internal/provider/smartrecruiters/companies.yaml`` was
built by walking this same lookup, so one GET of it replaces most of the walk: it held 11,258
identifiers, 59.8% unheld, and covered 96% of what fresh lookups returned
(``docs/discovery/2026-09-29_new-board-discovery-methods-research.md`` §2.1).

Two modes, both writing only candidates no ledger row already holds (case-insensitive
``board_key``) to ``data/wayback-ats/smartrecruiters.csv``, for ``check_liveness.py`` to verify:

    roster   one GET of the openings-mcp roster
    walk     the lookup itself, prefix by prefix: every 2-character prefix of [a-z0-9], and the
             36 children of any prefix that fills the 100-row cap, down to ``--depth``. Paced at
             one request a second, backing off on 429/5xx (ADR-0026); ``jobs.smartrecruiters.com``
             has no robots.txt (404). Each answer is appended to a JSONL log, so a re-run resumes.

Measured 2026-09-30: the roster gave 6,715 new Boards; the depth-2 walk (1,296 requests, 594 of them
capped) 165 more; 300 depth-3 requests 1 more, so a deeper walk is not worth its cost. The lookup is
fuzzy (``a7`` fills the cap), which is why depth 3 mostly re-finds depth 2. About 90% of the roster's
Boards are Dormant (ADR-0250): nothing posted since 2024.

Both drop SmartRecruiters' own test tenants before landing (``SRTest*``, a ``sandbox``/``demo``/
``test`` word in the identifier or name, ``kombo``) and every Board in ``excluded_and_parked``,
printing each drop. That is a filter on shape, so its output is a list to read, not proof.

Run:  python -u scripts/discover/mine_smartrecruiters_lookup.py roster
      python -u scripts/discover/mine_smartrecruiters_lookup.py walk [--depth 4] [--max-requests N]
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import re
import string
import time
from pathlib import Path

from curl_cffi import requests
from mine_smartrecruiters_roster import identifiers

from headstart.boards.board_identity import board_key
from headstart.boards.excluded_and_parked import EXCLUDED_BOARDS, PARKED_BOARDS
from headstart.scrapers.registry import company_from_row

ROOT = Path(__file__).resolve().parent.parent.parent
LEDGER = ROOT / "data" / "validate" / "liveness" / "smartrecruiters.csv"
POOL = ROOT / "data" / "wayback-ats" / "smartrecruiters.csv"
EXPERIMENT = ROOT / "experiment" / "smartrecruiters-company-lookup"
WALK_LOG = EXPERIMENT / "artifacts" / "lookup-responses.jsonl"
ROSTER_URL = (
    "https://raw.githubusercontent.com/amikai/openings-mcp/HEAD/"
    "internal/provider/smartrecruiters/companies.yaml"
)
LOOKUP_URL = "https://jobs.smartrecruiters.com/sr-jobs/company-lookup"
CAP = 100
ALPHABET = string.ascii_lowercase + string.digits
_TEST_WORDS = {"sandbox", "demo", "test"}


def _key(slug: str) -> str:
    return board_key(company_from_row("smartrecruiters", slug, "")).lower()


def _words(text: str) -> set[str]:
    """Lowercased words of a name or a CamelCase identifier (``BiogenSandbox`` -> biogen, sandbox)."""
    spaced = re.sub(
        r"([a-z])([A-Z])|([A-Za-z])(\d)|(\d)([A-Za-z])", r"\1\3\5 \2\4\6", text
    )
    return {w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w}


def test_tenant(slug: str, name: str) -> str | None:
    """Why this looks like a SmartRecruiters test tenant, or None."""
    if slug.lower().startswith("srtest"):
        return "SRTest*"
    if slug.lower() == "kombo":
        return "kombo"
    if "sandbox" in slug.lower():
        return "sandbox"
    hit = _TEST_WORDS & (_words(slug) | _words(name))
    return f"word:{min(hit)}" if hit else None


class Pool:
    """The candidate file, deduped against the ledger and itself by case-insensitive board_key."""

    def __init__(self) -> None:
        with LEDGER.open(encoding="utf-8") as f:
            self.held = {_key(r["tenant"]) for r in csv.DictReader(f)}
        self.seen: set[str] = set()
        if POOL.exists():
            with POOL.open(encoding="utf-8") as f:
                self.seen = {_key(r["tenant"]) for r in csv.DictReader(f)}
        else:
            POOL.parent.mkdir(parents=True, exist_ok=True)
            POOL.write_text("ats,tenant,url\n", encoding="utf-8")
        self.fh = POOL.open("a", newline="", encoding="utf-8")
        self.out = csv.writer(self.fh)
        self.counts = {"held": 0, "new": 0, "dup": 0, "dropped": 0}

    def offer(self, slug: str, name: str, source: str) -> None:
        key = _key(slug)
        if key in self.held:
            self.counts["held"] += 1
        elif key in self.seen:
            self.counts["dup"] += 1
        elif key in EXCLUDED_BOARDS or key in PARKED_BOARDS:
            self.counts["dropped"] += 1
            print(f"  drop {slug!r} ({name!r}): excluded_and_parked", flush=True)
        elif why := test_tenant(slug, name):
            self.counts["dropped"] += 1
            print(f"  drop {slug!r} ({name!r}): {why}", flush=True)
        else:
            self.seen.add(key)
            self.out.writerow(
                ["smartrecruiters", slug, f"careers.smartrecruiters.com/{slug}"]
            )
            self.fh.flush()
            self.counts["new"] += 1
            print(f"  new {slug} ({name}) via {source}", flush=True)


def roster() -> None:
    r = requests.get(ROSTER_URL, timeout=60)
    r.raise_for_status()
    entries = identifiers(r.text)
    print(f"roster: {len(entries)} entries, {len(r.content)} bytes", flush=True)
    pool = Pool()
    for slug in entries:
        pool.offer(slug, "", "roster")
    print(f"roster done: {pool.counts}", flush=True)


def _lookup(q: str) -> list[dict] | None:
    backoff = 30.0
    for _ in range(6):
        try:
            r = requests.get(LOOKUP_URL, params={"q": q}, timeout=45)
        except requests.RequestsError as e:
            print(f"  [{q}] {type(e).__name__}, retry in {backoff:.0f}s", flush=True)
        else:
            if r.status_code == 200:
                return r.json().get("results") or []
            print(f"  [{q}] HTTP {r.status_code}, retry in {backoff:.0f}s", flush=True)
        time.sleep(backoff)
        backoff = min(backoff * 2, 600)
    return None


def walk(depth: int, max_requests: int) -> None:
    done: dict[str, int] = {}
    pool = Pool()
    if WALK_LOG.exists():
        for line in WALK_LOG.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            for x in rec["results"]:
                pool.offer(
                    str(x["identifier"]), str(x.get("name") or ""), f"q={rec['q']}"
                )
            done[rec["q"]] = len(rec["results"])
    todo = ["".join(p) for p in itertools.product(ALPHABET, repeat=2)]
    WALK_LOG.parent.mkdir(parents=True, exist_ok=True)
    notes = EXPERIMENT / "LOG.md"
    if not notes.exists():
        notes.write_text(
            "# SmartRecruiters company lookup\n\n"
            "Raw prefix responses: artifacts/lookup-responses.jsonl.\n"
            "Restart replays saved responses into the deduplicating candidate pool.\n",
            encoding="utf-8",
        )
    log = WALK_LOG.open("a", encoding="utf-8")
    sent = 0
    while todo:
        q = todo.pop(0)
        if q in done:
            n = done[q]
        else:
            if sent >= max_requests:
                print(f"stopping at --max-requests {max_requests}", flush=True)
                break
            time.sleep(1.0)
            results = _lookup(q)
            sent += 1
            if results is None:
                print(f"  [{q}] gave up after retries", flush=True)
                continue
            log.write(json.dumps({"q": q, "results": results}) + "\n")
            log.flush()
            for x in results:
                pool.offer(str(x["identifier"]), str(x.get("name") or ""), f"q={q}")
            n = done[q] = len(results)
            print(f"[{sent}] q={q} -> {n} rows; {pool.counts}", flush=True)
        if n >= CAP and len(q) < depth:
            todo.extend(q + c for c in ALPHABET)
    print(
        f"walk done: {sent} requests this run, {len(done)} prefixes answered, {pool.counts}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("roster")
    w = sub.add_parser("walk")
    w.add_argument("--depth", type=int, default=4)
    w.add_argument("--max-requests", type=int, default=10**9)
    args = ap.parse_args()
    roster() if args.mode == "roster" else walk(args.depth, args.max_requests)


if __name__ == "__main__":
    main()
