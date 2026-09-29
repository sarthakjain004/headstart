#!/usr/bin/env python3
"""Darwinbox Board miner — a DNS sieve over ``{label}.darwinbox.in`` / ``.com``.

Darwinbox puts every tenant on a bare label under two shared domains, and each carries a
wildcard A record, so *an A answer alone* exists for every label and ``eightfold_dns_sweep.py``
(which reads any answer as "exists") would call the whole wordlist a hit. **The wildcard's shape
leaks the namespace anyway**, two ways, both visible in the one A answer:

* A tenant on Darwinbox's Cloudflare edge is a custom hostname, so its label owns an explicit
  ``CNAME {host}.cdn.cloudflare.net``; a made-up label has no CNAME at all (measured 2026-09-29
  over the whole ledger: 480 of 493 live labels, 0 of 80 made-up host lookups, 5 of 123 dead).
* A tenant still on a dedicated address has an explicit A record whose addresses are not the
  wildcard's. The wildcard answers a fixed set (``.in``: two addresses, ``.com``: three; identical
  across 588 made-up labels), so an address outside that set is an explicit record too. The
  wildcard set is re-derived from made-up labels at the start of every run, never trusted from
  this docstring. Together the two signals found 488 of the 493 live ledger labels (334 of the
  335 with jobs); the 5 misses answer the wildcard and cannot be told apart by DNS.

Same idea as Keka's non-``cin02`` pod sieve (``mine_keka.py``): a provisioned tenant is proven
at one cheap UDP query, no HTTP. A provisioned tenant is not a Board, though: the portal may be
HR-only, empty or walled, so every hit still has to be verified with the scraper's own request,
which is what ``scripts/validate/check_liveness.py darwinbox`` sends. Run that probe on a working
resolver: ``p_darwinbox`` reads curl's COULDNT_RESOLVE_HOST (code 6) as "no such tenant", so a
failing OS resolver writes every real tenant ``dead`` (21 of 21 on 2026-09-29).

``.in`` and ``.com`` are separate namespaces, so the sieve asks both hosts of every label. A
Board's identity is the *label* (``DarwinboxScraper.board_key`` is ``darwinbox:{tenant}`` and the
scraper tries ``.in`` then ``.com``). This script reads no ledger and reports no collision: a hit
is matched to the committed ledger by that key when it is staged for landing, outside the miner,
so a label held on one TLD lands once, never as a second row on the other.

Speed comes from a hand-rolled UDP client (one socket per nameserver, every query in flight,
keyed by DNS transaction id), as in ``eightfold_dns_sweep.py``, extended to read the answer's
CNAME target and addresses.

Run:  python -u scripts/discover/mine_darwinbox_dns.py WORDS [WORDS ...] OUT.tsv [--tlds in,com]

Appends ``label<TAB>tld<TAB>verdict<TAB>detail`` per (label, tld) and resumes: pairs already in
OUT.tsv are skipped. Verdicts: ``cf`` (CNAME to ``*.cdn.cloudflare.net``: a tenant), ``a`` (an
address outside the wildcard set: a tenant, detail = the addresses), ``other`` (a CNAME
elsewhere: some other service on a Darwinbox host, detail = the target), ``wild`` (the wildcard
answer: no tenant), ``nx`` (NXDOMAIN), ``fail`` (no conclusive answer after retries).
"""

from __future__ import annotations

import argparse
import asyncio
import random
import socket
import struct
import sys
import time
from pathlib import Path

APEX = "darwinbox"
TLDS = ("in", "com")
#: Public recursive resolvers, round-robined so no single one absorbs the whole sweep.
RESOLVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9", "1.0.0.1", "8.8.4.4"]
_TENANT_TARGET_SUFFIX = ".cdn.cloudflare.net"
#: Made-up labels asked per TLD to learn the wildcard's address set, and the most addresses a
#: stable wildcard may answer with before the run refuses to start.
_WILDCARD_PROBES = 24
_WILDCARD_MAX_ADDRS = 8
_QTYPE_A, _QTYPE_CNAME = 1, 5
_RCODE_NXDOMAIN = 3


def _query_packet(qid: int, name: str) -> bytes:
    """A minimal DNS/A query: header + QNAME + QTYPE=A + QCLASS=IN, recursion desired."""
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    qname = (
        b"".join(bytes([len(p)]) + p.encode("idna") for p in name.split(".") if p)
        + b"\x00"
    )
    return header + qname + struct.pack(">HH", _QTYPE_A, 1)


def _read_name(payload: bytes, pos: int) -> tuple[str, int]:
    """The domain name at ``pos`` (following compression pointers) and the offset just past it
    in the *original* record — a pointer ends the record's own bytes after its two octets."""
    labels: list[str] = []
    end = -1
    hops = 0
    while True:
        length = payload[pos]
        if length == 0:
            pos += 1
            break
        if length & 0xC0 == 0xC0:
            if end < 0:
                end = pos + 2
            pos = ((length & 0x3F) << 8) | payload[pos + 1]
            hops += 1
            if hops > 20:  # a pointer loop in a malformed answer
                raise ValueError("compression loop")
            continue
        labels.append(payload[pos + 1 : pos + 1 + length].decode("ascii", "replace"))
        pos += 1 + length
    return ".".join(labels), (end if end >= 0 else pos)


def parse_answer(payload: bytes) -> tuple[str, list[str], list[str]] | None:
    """``(rcode-class, cname targets, A addresses)`` of one answer, or None when it is
    inconclusive (SERVFAIL, REFUSED, a truncated or malformed packet) and must be retried.
    The class is ``"nx"`` for NXDOMAIN and ``"ok"`` for NOERROR."""
    if len(payload) < 12:
        return None
    _, flags, qdcount, ancount, _, _ = struct.unpack(">HHHHHH", payload[:12])
    rcode = flags & 0x0F
    if rcode == _RCODE_NXDOMAIN:
        return "nx", [], []
    if rcode != 0:
        return None
    cnames: list[str] = []
    addrs: list[str] = []
    try:
        pos = 12
        for _ in range(qdcount):
            _, pos = _read_name(payload, pos)
            pos += 4
        for _ in range(ancount):
            _, pos = _read_name(payload, pos)
            rtype, _, _, rdlen = struct.unpack(">HHIH", payload[pos : pos + 10])
            pos += 10
            if rtype == _QTYPE_CNAME:
                cnames.append(_read_name(payload, pos)[0].lower())
            elif rtype == _QTYPE_A and rdlen == 4:
                addrs.append(socket.inet_ntoa(payload[pos : pos + 4]))
            pos += rdlen
    except (ValueError, IndexError, struct.error, OSError):
        # OSError is inet_ntoa refusing an A record cut short of four octets
        return None
    if (
        not cnames and not addrs
    ):  # NOERROR with nothing: a resolver that lost the lookup
        return None
    return "ok", cnames, addrs


def verdict_of(
    answer: tuple[str, list[str], list[str]], wildcard: frozenset[str]
) -> tuple[str, str]:
    """The sieve's verdict and detail for one parsed answer under one TLD's wildcard set."""
    kind, cnames, addrs = answer
    if kind == "nx":
        return "nx", ""
    for target in cnames:
        if target.endswith(_TENANT_TARGET_SUFFIX):
            return "cf", target
    if cnames:
        return "other", cnames[0]
    outside = sorted(set(addrs) - wildcard)
    if outside:
        return "a", ",".join(outside)
    return "wild", ""


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


async def _ask(
    client: _Client, name: str, timeout: float
) -> tuple[str, list[str], list[str]] | None:
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
        return parse_answer(await asyncio.wait_for(fut, timeout))
    except (TimeoutError, OSError):
        return None
    finally:
        client.pending.pop(qid, None)


async def answer_for(
    clients: list[_Client], idx: int, host: str, sem: asyncio.Semaphore
) -> tuple[str, list[str], list[str]] | None:
    """Up to 4 tries on rotating nameservers — a dropped packet or a rate-limit SERVFAIL must
    never be read as 'no tenant here'. None after the last try."""
    async with sem:
        for attempt in range(4):
            answer = await _ask(
                clients[(idx + attempt) % len(clients)], host, 2.0 + attempt
            )
            if answer is not None:
                return answer
            await asyncio.sleep(0.05 * (attempt + 1))
    return None


def _made_up_label() -> str:
    return "".join(random.choices("abcdefghijklmnopqrstuvwxyz", k=12)) + "zq"


async def derive_wildcards(
    clients: list[_Client], tlds: list[str], sem: asyncio.Semaphore
) -> dict[str, frozenset[str]]:
    """Each TLD's wildcard address set, from made-up labels. Refuses a TLD whose made-up labels
    answer a CNAME (the CNAME oracle would be void) or too many addresses (not a fixed set)."""
    wildcards: dict[str, frozenset[str]] = {}
    for tld in tlds:
        addrs: set[str] = set()
        for attempt in range(
            4
        ):  # a slow link answers nothing at first: wait, do not guess
            answers = await asyncio.gather(
                *(
                    answer_for(clients, i, f"{_made_up_label()}.{APEX}.{tld}", sem)
                    for i in range(_WILDCARD_PROBES)
                )
            )
            for answer in answers:
                if answer is None or answer[0] != "ok":
                    continue
                if answer[1]:
                    raise SystemExit(
                        f".{tld}: a made-up label answered CNAME {answer[1]}"
                    )
                addrs.update(answer[2])
            if addrs:
                break
            print(f".{tld}: no made-up label answered, retrying in 20s", flush=True)
            await asyncio.sleep(20)
        if not addrs or len(addrs) > _WILDCARD_MAX_ADDRS:
            raise SystemExit(f".{tld}: wildcard set is not fixed: {sorted(addrs)}")
        wildcards[tld] = frozenset(addrs)
        print(f".{tld} wildcard set: {sorted(addrs)}", flush=True)
    return wildcards


#: Known tenants whose verdict must hold for the sieve's silence to mean anything: a link that
#: drops lookups would otherwise drop real tenants. (label, tld, verdict), checked every
#: ``_CANARY_EVERY`` answers; a made-up label must read ``wild`` as the negative control.
_CANARIES = (("kelly", "in", "cf"), ("nrucfc", "com", "cf"), ("aragen", "in", "a"))
_CANARY_EVERY = 3000


async def canaries_hold(
    clients: list[_Client],
    wildcards: dict[str, frozenset[str]],
    sem: asyncio.Semaphore,
) -> bool:
    checks = [(label, tld, want) for label, tld, want in _CANARIES if tld in wildcards]
    checks.append((_made_up_label(), next(iter(wildcards)), "wild"))
    for label, tld, want in checks:
        answer = await answer_for(clients, 0, f"{label}.{APEX}.{tld}", sem)
        got = verdict_of(answer, wildcards[tld])[0] if answer else "fail"
        if got != want:
            print(f"CANARY {label}.{tld}: wanted {want}, got {got}", flush=True)
            return False
    return True


def _done_pairs(out: Path) -> set[tuple[str, str]]:
    if not out.exists():
        return set()
    done = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if (
            len(parts) >= 3 and parts[2] != "fail"
        ):  # a fail is asked again on the next run
            done.add((parts[0], parts[1]))
    return done


async def sieve(
    labels: list[str],
    tlds: list[str],
    out: Path,
    concurrency: int,
    nameservers: list[str],
) -> dict[str, int]:
    done = _done_pairs(out)
    pairs = [(w, t) for w in labels for t in tlds if (w, t) not in done]
    print(
        f"{len(labels)} labels x {len(tlds)} TLDs, {len(done)} pairs already done "
        f"-> {len(pairs)} to ask",
        flush=True,
    )
    clients = [await _open(ns) for ns in nameservers]
    sem = asyncio.Semaphore(concurrency)
    wildcards = await derive_wildcards(clients, tlds, sem)
    counts: dict[str, int] = {}
    started = time.monotonic()

    async def one(idx: int, label: str, tld: str) -> tuple[str, str, str, str]:
        answer = await answer_for(clients, idx, f"{label}.{APEX}.{tld}", sem)
        if answer is None:
            return label, tld, "fail", ""
        return (label, tld, *verdict_of(answer, wildcards[tld]))

    # A bounded in-flight window: a task per pair up front would hold the whole wordlist.
    window = concurrency * 4
    it = iter(enumerate(pairs))
    inflight: set[asyncio.Task] = set()
    finished = 0
    # The file closes (and so flushes) on every way out — a Ctrl-C or a canary abort included.
    with out.open("a", encoding="utf-8") as fh:
        while True:
            while len(inflight) < window:
                nxt = next(it, None)
                if nxt is None:
                    break
                i, (label, tld) = nxt
                inflight.add(asyncio.ensure_future(one(i, label, tld)))
            if not inflight:
                break
            done_now, inflight = await asyncio.wait(
                inflight, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done_now:
                label, tld, verdict, detail = task.result()
                counts[verdict] = counts.get(verdict, 0) + 1
                finished += 1
                fh.write(f"{label}\t{tld}\t{verdict}\t{detail}\n")
                if verdict in ("cf", "a", "other"):
                    fh.flush()  # a tenant found is on disk before it is printed
                    print(f"HIT {verdict} {label}.{APEX}.{tld} {detail}", flush=True)
                if finished % 5000 == 0:
                    fh.flush()
                    rate = finished / max(time.monotonic() - started, 1e-9)
                    print(
                        f"  ... {finished}/{len(pairs)} asked, {counts}, {rate:.0f}/s",
                        flush=True,
                    )
                if finished % _CANARY_EVERY == 0:
                    for attempt in range(3):
                        if await canaries_hold(clients, wildcards, sem):
                            break
                        print(
                            f"  canary failed, pausing 30s (try {attempt + 1}/3)",
                            flush=True,
                        )
                        await asyncio.sleep(30)
                    else:
                        raise SystemExit(
                            "canaries keep failing: the link is dropping lookups; "
                            "re-run to resume (fails are asked again)"
                        )
    return counts


def _read_labels(paths: list[str]) -> list[str]:
    raw: list[str] = []
    for f in paths:
        raw.extend(Path(f).read_text(encoding="utf-8", errors="replace").split())
    return list(dict.fromkeys(w.strip().lower().strip(".") for w in raw if w.strip()))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("words", nargs="+", help="wordlist file(s), one label per line")
    ap.add_argument(
        "out", help="append label/tld/verdict/detail TSV here (the last argument)"
    )
    ap.add_argument("--tlds", default=",".join(TLDS))
    ap.add_argument("--concurrency", type=int, default=100)
    ap.add_argument("--nameservers", default=",".join(RESOLVERS))
    args = ap.parse_args(argv)
    counts = asyncio.run(
        sieve(
            _read_labels(args.words),
            [t.strip() for t in args.tlds.split(",") if t.strip()],
            Path(args.out),
            args.concurrency,
            [n.strip() for n in args.nameservers.split(",") if n.strip()],
        )
    )
    print(f"done: {counts}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
