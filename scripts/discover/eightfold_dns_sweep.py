#!/usr/bin/env python3
"""Enumerate Eightfold Boards by resolving ``{label}.eightfold.ai`` against a name wordlist.

Why this works where CT logs don't (docs/discovery/overview.md rules CT out for subdomain
ATSes — one wildcard cert hides every customer): eightfold.ai publishes **no wildcard DNS
record**. A bogus label NXDOMAINs while ``qualcomm.eightfold.ai`` answers with the shared
CloudFront distribution — so a resolvable label *is* a provisioned Board, and DNS alone
enumerates the tenant space. Resolving is only the first half: a resolving host still has to
be liveness-checked, since plenty of Boards are internal-mobility-only or empty.

Speed comes from a hand-rolled UDP client rather than a resolver library — one socket per
nameserver, all queries in flight at once, keyed by DNS transaction id. dnspython's async
resolver opens a socket per query and tops out near 140 q/s; this sustains >1500 q/s.

Writes every resolving host to ``--out`` incrementally (one per line, flushed as found) so a
crash keeps the hits already proven.

Run:  python -u scripts/discover/eightfold_dns_sweep.py words.txt --out hits.txt
"""

from __future__ import annotations

import argparse
import asyncio
import random
import socket
import struct
import sys
import time

APEX = "eightfold.ai"
# Public recursive resolvers, round-robined so no single one absorbs the whole sweep.
RESOLVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9", "1.0.0.1", "8.8.4.4", "208.67.222.222"]
_RCODE_NXDOMAIN = 3


def _query_packet(qid: int, name: str) -> bytes:
    """A minimal DNS/A query: header + QNAME + QTYPE=A + QCLASS=IN, recursion desired."""
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    qname = (
        b"".join(bytes([len(p)]) + p.encode("idna") for p in name.split(".") if p)
        + b"\x00"
    )
    return header + qname + struct.pack(">HH", 1, 1)


def _answered(payload: bytes) -> bool | None:
    """True if the name exists (NOERROR with answers), False if NXDOMAIN, None if neither —
    a None (NOERROR/0 answers, SERVFAIL, REFUSED) is inconclusive and gets retried."""
    if len(payload) < 12:
        return None
    _, flags, _, ancount, _, _ = struct.unpack(">HHHHHH", payload[:12])
    rcode = flags & 0x0F
    if rcode == _RCODE_NXDOMAIN:
        return False
    if rcode == 0:
        return True if ancount > 0 else None
    return None


class _Client(asyncio.DatagramProtocol):
    """One UDP socket to one nameserver; resolves the future registered under each query id."""

    def __init__(self) -> None:
        self.pending: dict[int, asyncio.Future] = {}
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:  # type: ignore[no-untyped-def]
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[no-untyped-def]
        if len(data) < 2:
            return
        fut = self.pending.pop(struct.unpack(">H", data[:2])[0], None)
        if fut is not None and not fut.done():
            fut.set_result(data)

    def error_received(self, exc: Exception) -> None:
        pass


async def _open(nameserver: str) -> _Client:
    loop = asyncio.get_running_loop()
    _, proto = await loop.create_datagram_endpoint(
        _Client, remote_addr=(nameserver, 53), family=socket.AF_INET
    )
    return proto  # type: ignore[return-value]


async def _ask(client: _Client, name: str, timeout: float) -> bool | None:
    loop = asyncio.get_running_loop()
    for _ in range(6):  # a free id; collisions are vanishingly rare but must not clash
        qid = random.getrandbits(16)
        if qid not in client.pending:
            break
    else:
        return None
    fut: asyncio.Future = loop.create_future()
    client.pending[qid] = fut
    try:
        assert client.transport is not None
        client.transport.sendto(_query_packet(qid, name))
        return _answered(await asyncio.wait_for(fut, timeout))
    except (TimeoutError, OSError):
        return None
    finally:
        client.pending.pop(qid, None)


async def sweep(
    labels: list[str],
    apex: str,
    out_path: str,
    concurrency: int,
    nameservers: list[str],
) -> int:
    clients = [await _open(ns) for ns in nameservers]
    sem = asyncio.Semaphore(concurrency)
    hits = 0
    done = 0
    started = time.monotonic()
    out = open(out_path, "a", encoding="utf-8")  # noqa: ASYNC230, SIM115

    async def one(idx: int, label: str) -> None:
        """Up to 4 tries on rotating nameservers — a dropped UDP packet or a rate-limit
        SERVFAIL must never be mistaken for 'this Board does not exist'."""
        nonlocal hits, done
        host = f"{label}.{apex}"
        verdict = None
        async with sem:
            for attempt in range(4):
                verdict = await _ask(
                    clients[(idx + attempt) % len(clients)], host, 2.0 + attempt
                )
                if verdict is not None:
                    break
                await asyncio.sleep(0.05 * (attempt + 1))
        done += 1
        if verdict:
            hits += 1
            out.write(host + "\n")
            out.flush()
            print(f"HIT {host}", flush=True)
        if done % 10000 == 0:
            rate = done / max(time.monotonic() - started, 1e-9)
            print(
                f"  ... {done}/{len(labels)} probed, {hits} hits, {rate:.0f}/s",
                flush=True,
            )

    tasks = [asyncio.ensure_future(one(i, w)) for i, w in enumerate(labels)]
    for fut in asyncio.as_completed(tasks):
        await fut
    out.close()
    return hits


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", help="wordlist files (default: stdin)")
    ap.add_argument("--out", required=True, help="append resolving hosts here")
    ap.add_argument("--apex", default=APEX)
    ap.add_argument("--concurrency", type=int, default=600)
    ap.add_argument("--nameservers", default=",".join(RESOLVERS))
    args = ap.parse_args()

    raw: list[str] = []
    if args.files:
        for f in args.files:
            raw.extend(open(f, encoding="utf-8", errors="replace").read().split())  # noqa: SIM115
    else:
        raw.extend(sys.stdin.read().split())
    labels = list(dict.fromkeys(w.strip().lower().strip(".") for w in raw if w.strip()))
    print(f"sweeping {len(labels)} labels under {args.apex}", flush=True)
    hits = asyncio.run(
        sweep(
            labels,
            args.apex,
            args.out,
            args.concurrency,
            [n.strip() for n in args.nameservers.split(",") if n.strip()],
        )
    )
    print(f"done: {hits} resolving hosts -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
