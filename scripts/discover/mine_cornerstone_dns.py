#!/usr/bin/env python3
"""Cornerstone Board miner — a DNS existence sweep of ``{label}.csod.com``, made safe on a slow link.

``csod.com`` publishes **no wildcard record**: an invented label NXDOMAINs and a provisioned tenant
resolves (measured 2026-09-29: every invented label NXDOMAIN, 76 of 76 known live 1-3 character
tenants resolve), so a wordlist swept against it names every tenant it contains without one HTTP
request.

**What a hit is, and is not.** A resolving host is a provisioned Cornerstone *account*, and csod
hosts the learning product on the same label. Of the unheld hits verified on 2026-09-29, about 78%
had no career site at all (every page id 1-3 redirects to ``/ui/error``: DEAD), about 12% had one
with nothing open, and about 1.5% were hiring, several of them demo or test tenants (the
`EXCLUDED_BOARDS` Cornerstone block). The hits are a superset of the Boards: verify each with
``check_liveness.py`` and read a hiring one's postings before it lands.

**Non-production siblings.** ``{tenant}-pilot`` and ``{tenant}-stg`` resolve for most tenants
(471 and 463 of 735 live labels, a lower bound: 3,064 of 8,928 lookups timed out). Their CNAME
names the environment (``...ppil01...``, ``...sstg01...`` against production's ``...prd01...``).
They are clones of a tenant, never Boards, so ``stage`` drops any label carrying such a token.

**Never read a timeout as "does not exist".** ``eightfold_dns_sweep.py`` counts a label whose four
tries all timed out as absent: on a congested link it read 13 of 101 real hosts as absent
(2026-09-29, the same 34,000 labels swept twice). This miner retries every inconclusive label
(``classify_reply`` returns None for a timeout, SERVFAIL and NOERROR-without-answer), lists any
still unsettled in ``OUT.unresolved`` (never counted absent; the 20 checked by hand were
infrastructure names such as ``api``, ``vpn`` and ``glb`` that answer NODATA), and puts known-live
and invented controls in every chunk: a chunk whose controls disagree is discarded, the sweep
rests a minute, and the chunk is redone.

Run:  python scripts/discover/mine_cornerstone_dns.py dns WORDLIST OUT_HITS [--concurrency 100]
      python scripts/discover/mine_cornerstone_dns.py stage HITS_FILE [HITS_FILE ...]

``dns`` appends every resolving host to OUT_HITS as found (each row flushed) and records its offset
in ``OUT_HITS.offset`` after every clean chunk, so a killed sweep resumes there; an offset it cannot
read restarts the list, never skips it. ``stage`` writes ``data/wayback-ats/cornerstone.csv`` (the
pool ``check_liveness.py cornerstone --dir data/wayback-ats`` reads) with the hits the ledger does
not hold, matched through ``CornerstoneScraper.slug_from`` and not through a URL parse. Resolve DNS
on public resolvers, never the OS one, under load (CLAUDE.md, Jibe): this uses 1.1.1.1, 8.8.8.8,
9.9.9.9 and others. Landing through ``check_liveness.py`` on a slow link needs the same care: its
code-6 "could not resolve host" reads DEAD, and a router resolver that stalled did that to real
tenants (2026-09-29), so resolve its requests over DoH there.
"""

import argparse
import asyncio
import csv
import os
import random
import re
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import IO

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
from eightfold_dns_sweep import (  # the UDP client and reply parser, single source
    RESOLVERS,
    _ask,
    _open,
)

from headstart.scrapers.cornerstone import CornerstoneScraper

APEX = "csod.com"
LEDGER = ROOT / "data" / "validate" / "liveness" / "cornerstone.csv"
POOL = ROOT / "data" / "wayback-ats" / "cornerstone.csv"
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# csod's own environment names, as `{tenant}-pilot` / `{tenant}-stg` (module docstring).
_NONPROD = re.compile(
    r"(?:^|[-_])(?:pilot|stg|stage|staging|preview|sandbox|sbx|test|qa|uat|dev|demo|train)\d*(?:$|[-_])"
)
_CONTROLS_LIVE = 4
_CONTROLS_DEAD = 3


def _held() -> set[str]:
    with LEDGER.open(encoding="utf-8") as f:
        return {
            CornerstoneScraper.slug_from(r["tenant"], r["url"])
            for r in csv.DictReader(f)
        }


def read_offset(path: Path, total: int) -> int:
    """Where a resumed sweep starts: the saved offset, 0 when there is none or it cannot be read.

    Starting over re-probes labels already settled, which is only slow; a bad number taken as an
    offset would skip labels no sweep ever read, which is a silent miss. A saved offset past the end
    of a shorter wordlist means the list is done.
    """
    try:
        return min(max(int(path.read_text().strip()), 0), total)
    except (OSError, ValueError):
        return 0


def write_offset(path: Path, pos: int) -> None:
    """Replace the offset file whole, so a crash never leaves half a number."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(str(pos))
    os.replace(tmp, path)


def controls_agree(
    res: dict[str, bool | None], live: list[str], dead: list[str]
) -> bool:
    """Known-live labels must resolve and invented ones must NXDOMAIN; an unsettled control fails."""
    return all(res[c] is True for c in live) and all(res[c] is False for c in dead)


def split_verdicts(
    res: dict[str, bool | None], part: list[str]
) -> tuple[list[str], list[str]]:
    """(resolving hosts, unsettled labels) of one chunk. An unsettled label is in neither the hits
    nor the absent: it goes to the `.unresolved` list."""
    found = [f"{w}.{APEX}" for w in part if res[w] is True]
    unsettled = [w for w in part if res[w] is None]
    return found, unsettled


def select_candidates(
    lines: Iterable[str], held: set[str]
) -> tuple[list[str], int, int, int]:
    """(labels to stage, hits seen, hits already held, hits dropped as non-production)."""
    seen: dict[str, None] = {}
    for line in lines:
        host = line.strip().lower()
        if host.endswith(f".{APEX}"):
            seen[host[: -len(APEX) - 1]] = None
    fresh = [x for x in seen if x not in held]
    kept = [x for x in fresh if not _NONPROD.search(x)]
    return kept, len(seen), len(seen) - len(fresh), len(fresh) - len(kept)


def write_rows(out: IO[str], rows: list[str]) -> None:
    """Append rows, one flush each: a hit is on disk before the next lookup, so a kill loses none."""
    for row in rows:
        out.write(row + "\n")
        out.flush()


def _controls() -> tuple[list[str], list[str]]:
    """Known-live labels (must resolve) and invented ones (must NXDOMAIN)."""
    with LEDGER.open(encoding="utf-8") as f:
        live = [
            r["tenant"]
            for r in csv.DictReader(f)
            if r["status"] == "live" and _LABEL.match(r["tenant"])
        ]
    invented = [f"zz{random.getrandbits(40):x}qx" for _ in range(_CONTROLS_DEAD)]
    return random.sample(live, _CONTROLS_LIVE), invented


async def _resolve_many(clients, labels: list[str], concurrency: int, rounds: int = 3):
    """{label: True | False | None}: None is inconclusive, retried `rounds` more times."""
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, bool | None] = {}

    async def one(idx: int, label: str) -> None:
        async with sem:
            verdict = None
            for attempt in range(4):
                verdict = await _ask(
                    clients[(idx + attempt) % len(clients)],
                    f"{label}.{APEX}",
                    2.5 + attempt,
                )
                if verdict is not None:
                    break
                await asyncio.sleep(0.1 * (attempt + 1))
            out[label] = verdict

    await asyncio.gather(*(one(i, w) for i, w in enumerate(labels)))
    for _ in range(rounds):
        pending = [w for w, v in out.items() if v is None]
        if not pending:
            break
        await asyncio.sleep(3)
        await asyncio.gather(*(one(i, w) for i, w in enumerate(pending)))
    return out


async def sweep(wordlist: Path, out: Path, concurrency: int, chunk: int = 2000) -> int:
    labels = list(
        dict.fromkeys(
            w.strip().lower()
            for w in wordlist.read_text().split()
            if _LABEL.match(w.strip().lower())
        )
    )
    offset_path = Path(f"{out}.offset")
    pos = read_offset(offset_path, len(labels))
    print(
        f"{len(labels)} labels, resuming at {pos}, concurrency {concurrency}",
        flush=True,
    )
    clients = [await _open(ns) for ns in RESOLVERS]
    hits = unresolved = redone = 0
    started = time.monotonic()
    first = pos
    hits_file = out.open("a", encoding="utf-8")
    unresolved_file = Path(f"{out}.unresolved").open("a", encoding="utf-8")  # noqa: ASYNC230, SIM115
    try:
        while pos < len(labels):
            part = labels[pos : pos + chunk]
            live_ctl, dead_ctl = _controls()
            res = await _resolve_many(clients, part + live_ctl + dead_ctl, concurrency)
            if not controls_agree(res, live_ctl, dead_ctl):
                redone += 1
                print(
                    f"  CONTROL FAIL at {pos}: resting 60s, redoing the chunk (redo {redone})",
                    flush=True,
                )
                if redone > 30:
                    print("too many control failures, stopping", flush=True)
                    return 1
                await asyncio.sleep(60)
                continue
            found, missed = split_verdicts(res, part)
            write_rows(hits_file, found)
            write_rows(unresolved_file, missed)
            hits += len(found)
            unresolved += len(missed)
            for h in found:
                print(f"HIT {h}", flush=True)
            pos += len(part)
            write_offset(offset_path, pos)
            rate = (pos - first) / max(time.monotonic() - started, 1e-9)
            print(
                f"  ... {pos}/{len(labels)} probed, {hits} hits, {unresolved} unresolved, {rate:.0f}/s",
                flush=True,
            )
    finally:
        hits_file.close()
        unresolved_file.close()
    print(f"done: {hits} hits, {unresolved} unresolved -> {out}", flush=True)
    return 0


def stage(hit_files: list[Path]) -> int:
    lines = [ln for path in hit_files for ln in path.read_text().split()]
    kept, seen, held, nonprod = select_candidates(lines, _held())
    POOL.parent.mkdir(parents=True, exist_ok=True)
    with POOL.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ats", "tenant", "url"])
        w.writerows(["cornerstone", label, f"https://{label}.{APEX}"] for label in kept)
    print(
        f"{seen} hits, {held} held, {nonprod} non-production, {len(kept)} staged in {POOL}",
        flush=True,
    )
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dns", help="sweep a wordlist against csod.com")
    d.add_argument("wordlist", type=Path)
    d.add_argument("out", type=Path)
    d.add_argument("--concurrency", type=int, default=100)
    s = sub.add_parser("stage", help="hits -> data/wayback-ats/cornerstone.csv")
    s.add_argument("hits", type=Path, nargs="+")
    args = ap.parse_args(argv)
    if args.cmd == "dns":
        return asyncio.run(sweep(args.wordlist, args.out, args.concurrency))
    return stage(args.hits)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
