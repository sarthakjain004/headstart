#!/usr/bin/env python3
"""PeopleStrong Board miner — a CNAME sieve in front of the candidate-portal listing.

Every PeopleStrong candidate portal is one label under a shared zone (``{label}.peoplestrong.com``)
and that zone has **a wildcard record**: an invented label resolves to a shared A record
(34.100.237.70) and answers HTTP, so the A-only sweep that enumerates Eightfold
(``eightfold_dns_sweep.py``) would call every label a tenant. Certificates do not help either
(one ``*.peoplestrong.com`` wildcard, no customer names).

**A CNAME query still separates them.** A tenant is onboarded onto Imperva's edge, and its zone
carries an explicit record, ``{label}.peoplestrong.com CNAME {token}.ng.impervadns.net``. A label
the wildcard synthesises has no CNAME: the authoritative answer is NOERROR with an empty answer
section. So a CNAME to ``*.impervadns.net`` proves a provisioned tenant at one UDP query and no
HTTP — existence, not liveness. A provisioned label can be a departed tenant (HAProxy's 403 deny
page), an HRMS login, a support host or an empty portal, so ``verify`` still asks the scraper's
own request.

Measured 2026-09-29: of the 434 ledger labels, 108 of 112 live portals carry the CNAME (96.4%)
and 110 of 110 made-up labels do not. The four misses (``careers-myconnect``, ``careers-oppo``,
``digitcareers``, ``namdevcareers``) are registered portals served straight off the wildcard, so
no DNS query sees them; probing 11,820 labels the sieve read as ``wildcard`` over HTTP found two
more (``trualtcareers``, ``premierenergiescareers``). The same public resolvers and the zone's
Route 53 server gave identical verdicts on all 434 labels. Re-derive ``TENANT_CNAME_SUFFIX``
(resolve a garbage label and a known tenant) if PeopleStrong ever moves edge vendor.

**What to feed it.** A tenant label takes several shapes, and only the ``*careers*`` ones are
candidate portals: a bare brand (``oyo``), ``{b}recruit``, ``{b}hrms`` and ``{b}hiring`` are the
customer's HRMS and recruiter hosts, which the portal API answers with code 201. But they name
a *customer*, and a customer's root asked in the portal shapes (``{b}careers``, ``careers-{b}``,
``{b}-careers``, ``{b}career``, ``{b}recruit-careers``) hit 1-3% of the time, against 0.02-0.05%
for the same shapes over a plain company wordlist. So: sweep brand words in the bare shape to
find customers, then expand each tenant's root into the portal shapes and sweep again, until a
round finds nothing.

The sweep treats a lost or throttled query as ``error`` (unrecorded, retried after a pause),
never as "no CNAME", and slips known answers in every ``CONTROL_EVERY`` labels to catch a sieve
that has started failing.

Stage 2 makes the one request ``PeopleStrongScraper`` and ``check_liveness.p_peoplestrong``
make: ``POST /api/cp/rest/altone/cp/jobs/v1?offset=0&limit=1``, built through
``PeopleStrongScraper.url(limit=1)`` and read by ``check_liveness``'s own body classifier. Parse
the body, never the status: a host that is not a registered portal answers 200 with
``response: null`` (code 201), and a departed tenant answers a bare 403 HAProxy page. Only a
``totalRecords`` integer is a Board. One deny page is not a departure: the prober settles DEAD
only when two addresses both get it, and this stage asks once, so it writes ``denied-once``.

Run:  python scripts/discover/mine_peoplestrong_cname.py dns    CAND_FILE OUT_CSV [--concurrency N]
      python scripts/discover/mine_peoplestrong_cname.py verify CAND_FILE OUT_CSV

Both stages stream per item and resume (rows already in OUT_CSV are skipped), and both skip a
label the committed ledger already holds (``data/validate/liveness/peoplestrong.csv``, keyed
through ``PeopleStrongScraper.slug_from``). ``dns`` writes ``label,verdict,target``; ``verify``
writes ``ats,tenant,url,status,jobs`` for every label it settles, ``status`` being ``live`` /
``dead`` / ``denied-once``; an ``unknown`` answer is not written, so a re-run asks it again. Hand
the ``live`` rows to ``scripts/validate/check_liveness.py peoplestrong`` through
``data/wayback-ats/peoplestrong.csv``; it decides ``dead`` for itself.

Requires dnspython for wire parsing (not a base dependency — CI installs base deps only).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import random
import socket
import struct
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "validate"))
# The prober's own body classifier, single source: what a listing response says about its Board.
from check_liveness import (  # needs scripts/validate on sys.path first
    _peoplestrong_denied,
    _peoplestrong_verdict,
)

from headstart.boards.liveness_ledger import LIVE, UNKNOWN
from headstart.network import http
from headstart.scrapers.peoplestrong import PeopleStrongScraper

LEDGER = ROOT / "data" / "validate" / "liveness" / "peoplestrong.csv"

APEX = "peoplestrong.com"
#: One of the zone's four Route 53 nameservers (`dig NS peoplestrong.com`: ns-450.awsdns-56.com
#: 205.251.193.194, ns-895.awsdns-47.net 205.251.195.127, ns-1402.awsdns-47.org 205.251.197.122,
#: ns-1684.awsdns-18.co.uk 205.251.198.148), authoritative, plus public resolvers. On 2026-09-29
#: the four authoritative servers answered from this machine at 11, 15, 66 and 206 a second with
#: 137, 109, 20 and 0 of 300 lost, so `.198.148` is the one worth naming; measure before adding
#: another. A lost query is `error`, never "no CNAME".
NAMESERVERS = ["205.251.198.148", "1.1.1.1", "8.8.8.8", "1.0.0.1", "8.8.4.4"]
#: What a provisioned tenant's CNAME target ends with (`lzikfok.ng.impervadns.net`).
TENANT_CNAME_SUFFIX = ".impervadns.net"
UA = "HeadStart-discovery/0.1 (careers-board discovery; polite)"
DOH_URL = "https://1.1.1.1/dns-query"
#: Known answers, slipped into a sweep every `CONTROL_EVERY` labels: a tenant that reads
#: `wildcard`, or a made-up label that reads `tenant`, means the sieve is failing, not the labels.
CONTROLS = {
    "cholacareers": "tenant",
    "hdfcergocareers": "tenant",
    "zqxjvknotatenantzz": "wildcard",
}
CONTROL_EVERY = 1000

DNS_CONCURRENCY = 100
# One Kong budget per client IP spans every tenant: 5,000 a minute (ADR-0234). The prober's own
# gate is 50 a second; this holds to 15 a second across 8 workers.
HTTP_WORKERS = 8
HTTP_RATE = 15.0
#: One ask got HAProxy's deny page. `p_peoplestrong` settles DEAD only when two named addresses
#: both get it (a wall on our own address reads the same), so a single ask is not `dead`.
DENIED_ONCE = "denied-once"


def _load_done(out: Path, col: int) -> set[str]:
    if not out.exists():
        return set()
    with out.open(encoding="utf-8") as fh:
        return {row[col] for row in list(csv.reader(fh))[1:] if row}


def _held_labels(ledger: Path = LEDGER) -> set[str]:
    """Every label the committed ledger holds, live or dead, in the scraper's own spelling."""
    if not ledger.exists():
        return set()
    with ledger.open(encoding="utf-8", newline="") as fh:
        return {
            PeopleStrongScraper.slug_from(row["tenant"], row.get("url") or "")
            for row in csv.DictReader(fh)
        }


def _candidates(path: Path, out: Path, col: int, held: set[str]) -> list[str]:
    """The labels in ``path`` neither read into ``out`` (column ``col``) nor held by the ledger."""
    labels = [
        line.strip().lower()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    done = _load_done(out, col)
    seen: set[str] = set()
    fresh = []
    for label in labels:
        if len(label) > 63 or label in done or label in held or label in seen:
            continue  # a DNS label is at most 63 bytes; a longer line cannot be a tenant
        seen.add(label)
        fresh.append(label)
    print(
        f"{len(labels)} candidates, {len(done)} already done, {len(held)} held in the ledger"
        f" -> {len(fresh)} to probe",
        flush=True,
    )
    return fresh


# --- stage 1: the CNAME sieve ----------------------------------------------------------------


def _query_packet(qid: int, name: str) -> bytes:
    """A DNS query, QTYPE=CNAME (5). Recursion is requested so a public resolver answers too;
    the zone's own authoritative servers ignore the flag."""
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    labels = [
        p.encode("idna") for p in name.split(".") if p
    ]  # length of the encoded form
    qname = b"".join(bytes([len(label)]) + label for label in labels) + b"\x00"
    return header + qname + struct.pack(">HH", 5, 1)


def _verdict(payload: bytes) -> tuple[str, str] | None:
    """(verdict, target), or None when the answer is no verdict (SERVFAIL, REFUSED, garbage).

    ``tenant``: a CNAME into ``TENANT_CNAME_SUFFIX``; ``other-cname``: a CNAME elsewhere;
    ``wildcard``: NOERROR and no CNAME (the wildcard's own synthesis); ``nxdomain``."""
    import dns.message

    try:
        message = dns.message.from_wire(payload)
    except Exception:  # noqa: BLE001 - a truncated or foreign packet is no verdict
        return None
    rcode = message.rcode()
    if rcode == 3:
        return "nxdomain", ""
    if rcode != 0:
        return None
    for rrset in message.answer:
        if rrset.rdtype == 5:  # CNAME
            target = str(rrset[0].target).rstrip(".").lower()
            kind = "tenant" if target.endswith(TENANT_CNAME_SUFFIX) else "other-cname"
            return kind, target
    return "wildcard", ""


class _Client(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.pending: dict[int, asyncio.Future] = {}
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:  # type: ignore[no-untyped-def]
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[no-untyped-def]
        if len(data) >= 2:
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


async def _ask(client: _Client, name: str, timeout: float) -> tuple[str, str] | None:
    loop = asyncio.get_running_loop()
    for _ in range(6):
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
        return _verdict(await asyncio.wait_for(fut, timeout))
    except (TimeoutError, OSError):
        return None
    finally:
        client.pending.pop(qid, None)


async def _sweep(
    labels: list[str], out: Path, concurrency: int, nameservers: list[str]
) -> dict[str, int]:
    clients = [await _open(ns) for ns in nameservers]
    counts: dict[str, int] = {}
    started = time.monotonic()
    fresh_file = not out.exists()
    fh = out.open("a", encoding="utf-8", newline="")
    writer = csv.writer(fh)
    if fresh_file:
        writer.writerow(["label", "verdict", "target"])
    done = 0

    async def ask(idx: int, label: str) -> tuple[str, str] | None:
        # Four tries on rotating servers: a dropped UDP packet or a throttled SERVFAIL is never
        # "not a tenant".
        for attempt in range(4):
            verdict = await _ask(
                clients[(idx + attempt) % len(clients)],
                f"{label}.{APEX}",
                2.0 + attempt,
            )
            if verdict is not None:
                return verdict
            await asyncio.sleep(0.1 * (attempt + 1))
        return None

    async def check_controls(idx: int) -> None:
        wrong = []
        for label, expected in CONTROLS.items():
            verdict = await ask(idx, label)
            if verdict is not None and verdict[0] != expected:
                wrong.append(f"{label} read {verdict[0]}, expected {expected}")
        counts["control_failed"] = counts.get("control_failed", 0) + len(wrong)
        print(
            f"  [control] {'FAILED: ' + '; '.join(wrong) if wrong else 'ok'} at {done} asked",
            flush=True,
        )

    async def pass_over(todo: list[str]) -> list[str]:
        """One pass; the labels still unanswered after their four tries."""
        nonlocal done
        queue = iter(enumerate(todo))
        lost: list[str] = []

        async def worker() -> None:
            nonlocal done
            for idx, label in queue:
                verdict = await ask(idx, label)
                done += 1
                if verdict is None:  # unrecorded, so a re-run asks again
                    lost.append(label)
                    continue
                kind, target = verdict
                counts[kind] = counts.get(kind, 0) + 1
                writer.writerow([label, kind, target])
                if kind == "tenant":
                    fh.flush()
                    print(f"  [dns] {label} -> {target}", flush=True)
                if done % CONTROL_EVERY == 0:
                    fh.flush()
                    await check_controls(idx)
                if done % 10000 == 0:
                    rate = done / max(time.monotonic() - started, 1e-9)
                    print(f"  [dns] {done} asked, {rate:.0f}/s, {counts}", flush=True)

        await asyncio.gather(*[worker() for _ in range(concurrency)])
        return lost

    try:
        lost = await pass_over(labels)
        if lost:
            print(
                f"  [dns] {len(lost)} unanswered, asking again after a pause",
                flush=True,
            )
            await asyncio.sleep(10)
            lost = await pass_over(lost)
    finally:
        fh.close()
    counts["error"] = len(lost)
    return counts


def stage_dns(
    cand_file: Path, out: Path, concurrency: int, nameservers: list[str]
) -> int:
    fresh = _candidates(cand_file, out, col=0, held=_held_labels())
    if not fresh:
        return 0
    counts = asyncio.run(_sweep(fresh, out, concurrency, nameservers))
    print(f"dns done: {counts}", flush=True)
    return counts.get("tenant", 0)


# --- stage 2: verify against the candidate-portal listing --------------------------------------


class _Pace:
    """Space request starts HTTP_RATE a second apart across every worker."""

    def __init__(self) -> None:
        self.next_slot = 0.0
        self.until = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        while True:
            with self._lock:
                rest = self.until - time.monotonic()
                if rest <= 0:
                    start = max(self.next_slot, time.monotonic())
                    self.next_slot = start + 1 / HTTP_RATE
                    break
            time.sleep(min(rest, 5))
        time.sleep(max(0.0, start - time.monotonic()))

    def rest(self, seconds: float) -> None:
        with self._lock:
            self.until = max(self.until, time.monotonic() + seconds)


def classify(r) -> tuple[str, int | None]:
    """(status, jobs) for one listing response: ``live`` with the Board's stated total, ``dead``
    for a host that is no registered portal (code 201), ``denied-once`` for HAProxy's deny page,
    else ``unknown``. The prober's own classifier reads everything but the deny page."""
    if _peoplestrong_denied(r):
        return DENIED_ONCE, None
    return _peoplestrong_verdict(r)


def verify(label: str, pace: _Pace) -> tuple[str, str, int | None]:
    """(label, status, jobs): the scraper's own `limit=1` listing read, body first.

    The name is resolved over DNS-over-HTTPS to a public resolver (`DOH_URL`): on a slow link the
    OS resolver times out on names a public one answers in ~100 ms, and a timeout is no verdict
    about the host."""
    url = PeopleStrongScraper(label).url(limit=1)
    for _ in range(3):
        pace.wait()
        try:
            r = http.fetch(
                "POST",
                url,
                json={},
                headers={"User-Agent": UA},
                timeout=30,
                # As the prober's pinned ask does: the verdict is read off the body, so a
                # certificate fault on a provisioned edge must not turn into `unknown`. Live
                # portals answer the same with verification on (2 of 2 tried, 2026-09-29).
                verify=False,
                attempts=1,
                doh_url=DOH_URL,
            )
        except Exception:  # noqa: BLE001
            return label, UNKNOWN, None
        if r.status_code == 429:  # the shared per-minute budget: rest out the window
            pace.rest(60)
            continue
        break
    else:
        return label, UNKNOWN, None
    status, jobs = classify(r)
    return label, status, jobs


def stage_verify(cand_file: Path, out: Path) -> int:
    fresh = _candidates(cand_file, out, col=1, held=_held_labels())
    if not fresh:
        return 0
    pace = _Pace()
    fresh_file = not out.exists()
    fh = out.open("a", encoding="utf-8", newline="")
    writer = csv.writer(fh)
    if fresh_file:
        writer.writerow(["ats", "tenant", "url", "status", "jobs"])
    live = done = 0
    try:
        with ThreadPoolExecutor(max_workers=HTTP_WORKERS) as ex:
            futures = [ex.submit(verify, c, pace) for c in fresh]
            for fut in as_completed(futures):
                label, status, jobs = fut.result()
                done += 1
                if status == UNKNOWN:  # unrecorded, so a re-run asks again
                    continue
                writer.writerow(
                    [
                        "peoplestrong",
                        label,
                        f"https://{label}.{APEX}",
                        status,
                        "" if jobs is None else jobs,
                    ]
                )
                fh.flush()
                if status == LIVE:
                    live += 1
                    print(f"  [live] {label}: {jobs} postings", flush=True)
                if done % 200 == 0:
                    print(f"  [verify] {done}/{len(fresh)}, {live} live", flush=True)
    finally:
        fh.close()
    print(f"verify done: {live} live Boards in {done} probed", flush=True)
    return live


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("dns", "verify"))
    parser.add_argument("cand_file", type=Path)
    parser.add_argument("out_csv", type=Path)
    parser.add_argument("--concurrency", type=int, default=DNS_CONCURRENCY)
    parser.add_argument("--nameservers", default=",".join(NAMESERVERS))
    args = parser.parse_args(argv[1:])
    if args.stage == "dns":
        stage_dns(
            args.cand_file,
            args.out_csv,
            args.concurrency,
            [n.strip() for n in args.nameservers.split(",") if n.strip()],
        )
    else:
        stage_verify(args.cand_file, args.out_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
