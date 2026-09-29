#!/usr/bin/env python3
"""Enumerate Avature tenants by asking DNS which names carry an A record under avature.net.

Why this works: avature.net answers a made-up label with NOERROR and no A record (only a wildcard
MX and TXT: `v=spf1 redirect=spf.avature.net`), while a provisioned tenant answers with
`CNAME iatsapp-prod-enNN.avature.net` and that server's A record (`sodexo.avature.net`,
2026-09-29). So an answered A query proves a provisioned host, and NOERROR with no answer proves
the absence. `eightfold_dns_sweep.py` reads that second case as inconclusive and retries it four
times, which is right for eightfold.ai (a made-up label NXDOMAINs there) and 4x too slow here.

A provisioned host is not a Board: it may be a test instance (`sandboxtql`), a second name for
another tenant, or a tenant with no public career site. `check_liveness.py avature` settles all
three; this script only finds the labels.

Two modes:

* **Labels** (default): each word is a label asked as `{word}.avature.net`, straight to one of the
  zone's four authoritative Route53 servers (they answered from this machine on 2026-09-29, ~30
  ms), round-robined.
* **Names** (`--names`): each word is a full host (`careers.acme.com`) asked on public resolvers,
  and only a host whose CNAME target ends `--target-suffix` (`avature.net`) is written. This is the
  vanity route: a customer's career site is often `CNAME {tenant}.avature.net`
  (`jobs.lenovo.com`), which names a tenant whose label is not the company's name.

A SERVFAIL, REFUSED, truncated reply or dropped packet is retried on the next server and, if it
never settles, written to `--unresolved` and never counted absent: re-run that file. Known-tenant
controls are asked every `--control-every` words; a control that does not answer means the link or
the server is degrading, and the sweep says so at once.

Writes each hit to `--out` as `word<TAB>cname target` the moment it is found.

Run:  python -u scripts/discover/mine_avature_dns.py words.txt --out hits.tsv --unresolved lost.txt
"""

from __future__ import annotations

import argparse
import asyncio
import random
import re
import socket
import struct
import sys
import time

APEX = "avature.net"
#: The four Route53 name servers `dig NS avature.net` names, as their A records answered
#: 2026-09-29 (ns-753.awsdns-30.net, ns-440.awsdns-55.com, ns-1066.awsdns-05.org,
#: ns-1704.awsdns-21.co.uk).
AUTHORITATIVE = [
    "205.251.194.241",
    "205.251.193.184",
    "205.251.196.42",
    "205.251.198.168",
]
PUBLIC_RESOLVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9", "1.0.0.1", "8.8.4.4"]
#: Held live tenants (`sodexo.avature.net`, `jobs.lenovo.com` -> `lenovo.avature.net`).
LABEL_CONTROLS = ["bloomberg", "sodexo"]
NAME_CONTROLS = ["jobs.lenovo.com"]
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_HOST = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")
_TYPE_A, _TYPE_CNAME = 1, 5


def _query_packet(qid: int, name: str) -> bytes:
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    qname = (
        b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.split(".")) + b"\x00"
    )
    return header + qname + struct.pack(">HH", _TYPE_A, 1)


def _skip_name(payload: bytes, at: int) -> int:
    """The offset just past a (possibly compressed) name starting at `at`."""
    while True:
        length = payload[at]
        if length == 0:
            return at + 1
        if length & 0xC0 == 0xC0:
            return at + 2
        at += 1 + length


def _read_name(payload: bytes, at: int) -> str:
    labels: list[str] = []
    for _ in range(64):  # a compression loop must not hang the sweep
        length = payload[at]
        if length == 0:
            break
        if length & 0xC0 == 0xC0:
            at = ((length & 0x3F) << 8) | payload[at + 1]
            continue
        labels.append(payload[at + 1 : at + 1 + length].decode("ascii", "replace"))
        at += 1 + length
    return ".".join(labels)


def _verdict(payload: bytes) -> tuple[bool | None, str]:
    """(True, cname target) for an answered A query, (False, "") for NOERROR or NXDOMAIN with no
    answer, (None, "") for anything unsettled (SERVFAIL, REFUSED, a truncated or short reply)."""
    if len(payload) < 12:
        return None, ""
    _, flags, qdcount, ancount, _, _ = struct.unpack(">HHHHHH", payload[:12])
    rcode = flags & 0x0F
    if rcode == 3 or (rcode == 0 and ancount == 0 and not flags & 0x0200):
        return False, ""
    if rcode != 0 or flags & 0x0200:
        return None, ""
    at = 12
    for _ in range(qdcount):
        at = _skip_name(payload, at) + 4
    target = ""
    for _ in range(ancount):
        at = _skip_name(payload, at)
        rtype, _, _, rdlength = struct.unpack(">HHIH", payload[at : at + 10])
        at += 10
        if rtype == _TYPE_CNAME and not target:
            target = _read_name(payload, at)
        at += rdlength
    return True, target


class _Client(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.pending: dict[int, asyncio.Future] = {}
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:  # type: ignore[no-untyped-def]
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[no-untyped-def]
        fut = (
            self.pending.pop(struct.unpack(">H", data[:2])[0], None)
            if len(data) > 1
            else None
        )
        if fut is not None and not fut.done():
            fut.set_result(data)

    def error_received(self, exc: Exception) -> None:
        pass


async def _open(nameserver: str) -> _Client:
    _, proto = await asyncio.get_running_loop().create_datagram_endpoint(
        _Client, remote_addr=(nameserver, 53), family=socket.AF_INET
    )
    return proto  # type: ignore[return-value]


async def _ask(client: _Client, name: str, timeout: float) -> tuple[bool | None, str]:
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    for _ in range(6):
        qid = random.getrandbits(16)
        if qid not in client.pending:
            break
    else:
        return None, ""
    client.pending[qid] = fut
    try:
        assert client.transport is not None
        client.transport.sendto(_query_packet(qid, name))
        return _verdict(await asyncio.wait_for(fut, timeout))
    except (TimeoutError, OSError, IndexError, struct.error):
        return None, ""
    finally:
        client.pending.pop(qid, None)


async def sweep(
    words: list[str],
    apex: str,
    target_suffix: str,
    controls: list[str],
    control_every: int,
    out_path: str,
    unresolved_path: str,
    concurrency: int,
    nameservers: list[str],
) -> tuple[int, int]:
    clients = [await _open(ns) for ns in nameservers]
    sem = asyncio.Semaphore(concurrency)
    hits = unresolved = done = 0
    controls_asked = controls_answered = 0
    started = time.monotonic()
    out = open(out_path, "a", encoding="utf-8")  # noqa: ASYNC230, SIM115
    lost = open(unresolved_path, "a", encoding="utf-8")  # noqa: ASYNC230, SIM115

    queue: list[tuple[str, bool]] = []
    for i, word in enumerate(words):
        if controls and i % control_every == 0:
            queue.extend((control, True) for control in controls)
        queue.append((word, False))

    async def one(idx: int, word: str, is_control: bool) -> None:
        nonlocal hits, unresolved, done, controls_asked, controls_answered
        host = f"{word}.{apex}" if apex else word
        verdict, target = None, ""
        async with sem:
            for attempt in range(4):
                verdict, target = await _ask(
                    clients[(idx + attempt) % len(clients)], host, 2.0 + attempt
                )
                if verdict is not None:
                    break
                await asyncio.sleep(0.1 * (attempt + 1))
        if is_control:
            controls_asked += 1
            controls_answered += bool(verdict)
            if not verdict:
                print(
                    f"CONTROL FAILED {host}: the link or server is degrading",
                    flush=True,
                )
            return
        done += 1
        if verdict and target.endswith(target_suffix):
            hits += 1
            out.write(f"{word}\t{target}\n")
            out.flush()
            print(f"HIT {host} -> {target or '(A)'}", flush=True)
        elif verdict is None:
            unresolved += 1
            lost.write(word + "\n")
            lost.flush()
        if done % 10000 == 0:
            rate = done / max(time.monotonic() - started, 1e-9)
            print(
                f"  ... {done}/{len(words)} asked, {hits} hits, {unresolved} unresolved, "
                f"controls {controls_answered}/{controls_asked}, {rate:.0f}/s",
                flush=True,
            )

    for fut in asyncio.as_completed(
        [asyncio.ensure_future(one(i, w, c)) for i, (w, c) in enumerate(queue)]
    ):
        await fut
    out.close()
    lost.close()
    print(f"controls answered {controls_answered}/{controls_asked}", flush=True)
    return hits, unresolved


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", help="wordlist files (default: stdin)")
    ap.add_argument("--out", required=True, help="append `word<TAB>cname` hits here")
    ap.add_argument(
        "--unresolved", required=True, help="append words that never settled here"
    )
    ap.add_argument(
        "--names",
        action="store_true",
        help="words are full hosts, asked on public resolvers",
    )
    ap.add_argument(
        "--target-suffix",
        default=None,
        help="keep a hit only if its CNAME ends with this",
    )
    ap.add_argument("--concurrency", type=int, default=100)
    ap.add_argument("--nameservers", default=None)
    ap.add_argument(
        "--controls",
        default=None,
        help="comma-separated known tenants, asked periodically",
    )
    ap.add_argument("--control-every", type=int, default=2000)
    args = ap.parse_args()

    apex = "" if args.names else APEX
    suffix = args.target_suffix or (APEX if args.names else "")
    nameservers = (
        args.nameservers or ",".join(PUBLIC_RESOLVERS if args.names else AUTHORITATIVE)
    ).split(",")
    default_controls = NAME_CONTROLS if args.names else LABEL_CONTROLS
    controls = (
        default_controls
        if args.controls is None
        else [c for c in args.controls.split(",") if c]
    )
    pattern = _HOST if args.names else _LABEL

    raw: list[str] = []
    for path in args.files:
        raw.extend(open(path, encoding="utf-8", errors="replace").read().split())  # noqa: SIM115
    if not args.files:
        raw.extend(sys.stdin.read().split())
    words = [
        w
        for w in dict.fromkeys(x.strip().lower().strip(".") for x in raw)
        if pattern.match(w)
    ]
    print(
        f"asking {len(words)} {'hosts' if args.names else 'labels under ' + APEX} "
        f"({len(raw)} words read), concurrency {args.concurrency}",
        flush=True,
    )
    hits, unresolved = asyncio.run(
        sweep(
            words,
            apex,
            suffix,
            controls,
            args.control_every,
            args.out,
            args.unresolved,
            args.concurrency,
            [n.strip() for n in nameservers if n.strip()],
        )
    )
    print(
        f"done: {hits} hits -> {args.out}, {unresolved} unresolved -> {args.unresolved}",
        flush=True,
    )


if __name__ == "__main__":
    main()
