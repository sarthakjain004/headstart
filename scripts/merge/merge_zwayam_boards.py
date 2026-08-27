#!/usr/bin/env python3
"""Union the per-angle Zwayam discovery CSVs into one liveness ledger.

Five discovery agents each wrote `2026-08-27_zwayam-boards-*.csv` from a different angle (DNS/CDN,
CT logs, web corpora, SERP dorking, vendor lists). They overlap by construction, so this unions
them on the canonical board key and re-checks the survivors against the live API.

Zwayam's board key is the **career-site hostname**: the API's `companyId` field is ignored, and
`domain` alone selects the board (Origin/Referer are ignored). So the ledger's `tenant` is the host.

The duplicate risk CLAUDE.md warns about is real here in a Zwayam-specific form: one company can
register more than one hostname (careers.acme.com *and* jobs.acme.com), and both answer `live`.
Row-level dedupe cannot see that — the hostnames genuinely differ. `--verify` catches it by asking
the API: two hosts serving the same totalCount *and* the same newest job id are one board, and the
shorter/`careers.`-prefixed host wins.

Usage:
    python scripts/merge/merge_zwayam_boards.py                 # union only, report duplicates
    python scripts/merge/merge_zwayam_boards.py --verify        # re-probe every board, dedupe
    python scripts/merge/merge_zwayam_boards.py --verify --write  # ... and write the ledger

Add `--warp` to any of those to probe through the local WARP SOCKS proxy instead of directly.
"""

from __future__ import annotations

import argparse
import base64
import csv
import glob
import sys
from collections import defaultdict
from pathlib import Path

import requests

from headstart.scrapers.zwayam import (  # the dead-vs-failed line, single source of truth
    body_error_code,
)

SRC_GLOB = "experiment/india-ats-priority/artifacts/2026-08-27_zwayam-boards-*.csv"
LEDGER = Path("data/validate/liveness/zwayam.csv")
FIELDS = ["ats", "tenant", "url", "status", "jobs", "checked_at"]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_WARP_PROXY = {
    "http": "socks5h://127.0.0.1:40000",
    "https": "socks5h://127.0.0.1:40000",
}
#: None = the direct path (the default). An earlier version pinned every probe to the WARP SOCKS
#: proxy because "Akamai 403s direct bursts", which a 2026-08-27 load test could not reproduce
#: (~2,160 direct requests, 34 req/s sustained, zero 403s — `experiment/zwayam-rate-limit/`).
#: `--warp` restores it for the odd host that does wall.
PROXY: dict[str, str] | None = None
SEARCH = "https://public.zwayam.com/jobs/search"
FILTER = (
    '{"paginationStartNo":0,"selectedCall":"sort",'
    '"sortCriteria":{"name":"modifiedDate","isAscending":false},"anyOfTheseWords":""}'
)
IGNORED_COMPANY_ID = base64.b64encode(b"1").decode()  # the server ignores this field


def probe(host: str) -> tuple[int, str] | None:
    """(totalCount, newest job id) for a live board; None when the host is not a board."""
    s = requests.Session()
    s.proxies = PROXY
    s.headers.update(
        {
            "User-Agent": UA,
            "accept": "application/json, text/plain, */*",
            "Origin": f"https://{host}",
            "Referer": f"https://{host}/",
        }
    )
    r = s.post(
        SEARCH,
        files={
            "filterCri": (None, FILTER),
            "domain": (None, host),
            "companyId": (None, IGNORED_COMPANY_ID),
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"{host}: HTTP {r.status_code} (rotate the egress and retry)"
        )
    payload = r.json() or {}
    failed = body_error_code(payload)
    if failed is not None:
        # This endpoint answers its own failures with HTTP 200, `data: null` and a body code of
        # 500 — byte-identical to a dead Board but for the code. This script *writes the
        # ledger*, so reading that as "not a board" would persist a wrong `dead` verdict and
        # drop a live Board from the scrape until someone re-probed it. Raise instead: the
        # caller already knows how to retry.
        raise RuntimeError(f"{host}: body code {failed} (transient; retry)")
    data = payload.get("data")
    if not data:
        return None
    rows = data.get("data") or []
    return data.get("totalCount", 0), (rows[0].get("_id") if rows else "")


def load_sources() -> dict[str, dict]:
    """Union every angle's CSV on the lowercased hostname. live beats dead; higher jobs wins."""
    best: dict[str, dict] = {}
    for path in sorted(glob.glob(SRC_GLOB)):
        with open(path) as fh:
            rows = list(csv.DictReader(fh))
        for row in rows:
            host = (row.get("tenant") or "").strip().lower()
            if not host:
                continue
            row["tenant"] = host
            row["_src"] = Path(path).stem.rsplit("-", 1)[-1]
            prior = best.get(host)
            if prior is None:
                best[host] = row
                continue
            better_status = row["status"] == "live" and prior["status"] != "live"
            same_status = row["status"] == prior["status"]
            higher = _int(row.get("jobs")) > _int(prior.get("jobs"))
            if better_status or (same_status and higher):
                best[host] = row
    return best


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--verify", action="store_true", help="re-probe every board against the API"
    )
    ap.add_argument(
        "--write", action="store_true", help="write the ledger (implies --verify)"
    )
    ap.add_argument(
        "--warp",
        action="store_true",
        help="route probes through the local WARP SOCKS proxy (default: direct)",
    )
    args = ap.parse_args()
    if args.warp:
        global PROXY
        PROXY = _WARP_PROXY
        print("[warp] probing through the WARP SOCKS proxy")

    rows = load_sources()
    by_src: defaultdict[str, int] = defaultdict(int)
    for r in rows.values():
        by_src[r["_src"]] += 1
    print(
        f"unioned {len(rows)} distinct hosts from {len(glob.glob(SRC_GLOB))} angle files"
    )
    for src, n in sorted(by_src.items(), key=lambda kv: -kv[1]):
        print(f"  best-row source {src:12s} {n}")

    if not (args.verify or args.write):
        return 0

    live: dict[str, tuple[int, str]] = {}
    for host, row in sorted(rows.items()):
        if row["status"] != "live":
            continue
        try:
            got = probe(host)
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f"  {host:44s} ERROR {exc}", flush=True)
            continue
        if got is None:
            print(f"  {host:44s} not a board (data: null) -> dead", flush=True)
            row["status"], row["jobs"] = "dead", ""
            continue
        live[host] = got
        row["jobs"] = got[0]
        print(f"  {host:44s} live {got[0]}", flush=True)

    # Same board under two hostnames: identical count AND identical newest job id.
    dupes: defaultdict[tuple[int, str], list[str]] = defaultdict(list)
    for host, sig in live.items():
        dupes[sig].append(host)
    dropped = set()
    for sig, hosts in dupes.items():
        if len(hosts) < 2 or not sig[1]:
            continue
        keep = min(hosts, key=lambda h: (not h.startswith("careers."), len(h)))
        for h in hosts:
            if h != keep:
                dropped.add(h)
        print(f"DUPLICATE BOARD {sig}: {hosts} -> keeping {keep}")
    if not dropped:
        print("no cross-hostname duplicate boards found")

    final = [r for h, r in sorted(rows.items()) if h not in dropped]
    n_live = sum(1 for r in final if r["status"] == "live")
    print(
        f"\nfinal: {len(final)} rows, {n_live} live, {sum(_int(r['jobs']) for r in final):,} jobs"
    )

    if args.write:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            for r in final:
                r.setdefault("ats", "zwayam")
                r.setdefault("url", f"https://{r['tenant']}")
                w.writerow(r)
        print(f"wrote {LEDGER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
