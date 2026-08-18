#!/usr/bin/env python3
"""The SuccessFactors DNS oracle: does a host CNAME into jobs2web.com?

A SuccessFactors RMK board lives on the customer's own vanity host, so there is no namespace to
sweep — but every one of them is a CNAME into SAP's RMK estate:

    careers.wipro.com  ->  107155.jobs2web.com  ->  rmk55.jobs2web.com  ->  34.89.238.75
    jobs.sap.com       ->  sap.jobs2web.com     ->  rmk12.jobs2web.com  ->  130.214.193.81

That makes DNS a ~1 ms oracle for "is this host a SuccessFactors career site", where the HTTP
probe costs a full TLS handshake plus a sitemap read. It is a *pre-filter*, not a verdict: the
CNAME says SAP hosts the site, it does not say the site exposes the crawlable RMK surface (a
CSB-only tenant CNAMEs the same way and has no RMK sitemap). Every hit still goes through
scripts/validate/check_liveness.py, which is what actually settles live/dead.

Emits ``host,verdict,chain`` where verdict is ``rmk`` (chain reaches jobs2web.com), ``other``
(resolves elsewhere — the chain tail is kept, since it names whichever ATS/CDN owns the host),
or ``nxdomain``.

Run:  python -u scripts/discover/sf_cname_probe.py HOSTS_FILE OUT_CSV [--workers N]
"""

from __future__ import annotations

import asyncio
import csv
import sys
from pathlib import Path

import dns.asyncresolver
import dns.rdatatype
import dns.resolver

RESOLVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9", "1.0.0.1", "8.8.4.4"]
MARKER = "jobs2web.com"


def _resolver(i: int) -> dns.asyncresolver.Resolver:
    r = dns.asyncresolver.Resolver(configure=False)
    r.nameservers = [RESOLVERS[i % len(RESOLVERS)]]
    r.timeout, r.lifetime = 5.0, 8.0
    return r


async def probe(host: str, res: dns.asyncresolver.Resolver) -> tuple[str, str, str]:
    """(host, verdict, chain) — chain is the CNAME targets joined by '>'."""
    try:
        answer = await res.resolve(host, "A", raise_on_no_answer=False)
    except dns.resolver.NXDOMAIN:
        return host, "nxdomain", ""
    except Exception as exc:  # timeout / SERVFAIL / no nameservers  # noqa: BLE001
        return host, "error", type(exc).__name__
    chain = [
        str(item.target).rstrip(".").lower()
        for rrset in answer.response.answer
        if rrset.rdtype == dns.rdatatype.CNAME
        for item in rrset
    ]
    joined = ">".join(chain)
    if any(c == MARKER or c.endswith("." + MARKER) for c in chain):
        return host, "rmk", joined
    return host, "other", joined


async def run(hosts: list[str], out_path: Path, workers: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
    for pair in enumerate(hosts):
        queue.put_nowait(pair)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["host", "verdict", "chain"])
        f.flush()
        lock = asyncio.Lock()
        done = 0

        async def worker(slot: int) -> None:
            nonlocal done
            res = _resolver(slot)
            while True:
                try:
                    _, host = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                row = await probe(host, res)
                async with (
                    lock
                ):  # stream: a killed run keeps every host already settled
                    w.writerow(row)
                    counts[row[1]] = counts.get(row[1], 0) + 1
                    done += 1
                    if row[1] == "rmk":
                        print(f"  RMK  {row[0]:<45} {row[2]}", flush=True)
                    if done % 2000 == 0:
                        f.flush()
                        print(
                            f"  ... {done}/{len(hosts)}  {dict(sorted(counts.items()))}",
                            flush=True,
                        )

        await asyncio.gather(*(worker(i) for i in range(workers)))
    return counts


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    workers = 120
    if "--workers" in argv:
        workers = int(argv[argv.index("--workers") + 1])
    hosts_path, out_path = Path(argv[1]), Path(argv[2])
    hosts = list(
        dict.fromkeys(
            ln.strip().lower()
            for ln in hosts_path.read_text().splitlines()
            if ln.strip()
        )
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"resolving {len(hosts)} hosts, {workers} workers", flush=True)
    counts = asyncio.run(run(hosts, out_path, workers))
    print(f"DONE {dict(sorted(counts.items()))} -> {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
