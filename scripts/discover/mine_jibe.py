#!/usr/bin/env python3
"""Jibe client miner: a DNS sweep of `{label}.jibeapply.com`, and a vanity-host -> client resolver.

A Jibe Board is a client id, served at `https://{client}.jibeapply.com` (`headstart.scrapers.jibe`,
`docs/jibe/2026-09-24_api-jobs-measurement.md`). The zone answers A records only for provisioned
clients: an invented label gets no A record (NOERROR with an empty answer, which dnspython raises
as `NoAnswer`, on 1.1.1.1, 8.8.8.8 and 9.9.9.9 alike, 2026-09-24), so a label that resolves is a
client and one DNS query per guess is the whole probe.

**DNS mode (default).** Guesses come from the committed liveness ledgers
(`data/validate/liveness/*.csv`): for iCIMS, whose tenants are Jibe's usual backing ATS, each
tenant label's hyphen components, the part after its first hyphen and the label with hyphens
removed (`careers-celanese` -> `careers`, `celanese`, `careerscelanese`); for every other ledger
the tenant's bare first label. Each is resolved against the public resolvers above, never the OS
resolver: the macOS system resolver returned a false NXDOMAIN for live `uhs.jibeapply.com` under a
64-thread sweep on 2026-09-24. Verdicts:

- an A record -> `client`, written as a pool row `jibe,{label},https://{label}.jibeapply.com,dns`;
- NXDOMAIN or no A record -> `none`, settled;
- a timeout, SERVFAIL or no reachable nameserver on every resolver tried -> `unresolved`. Never
  written as settled and never counted as a miss: the next run asks again.

**Vanity mode (`--vanity FILE`).** Maps each careers host in FILE (`careers.costco.com`) to its
client id. robots.txt is read first and honoured, and every request to one host is at least
`CRAWL_DELAY` (5 s) after the previous one, as `jibe.py` does. Then `/api/jobs?page=1&limit=1&
internal=false` for the rows' `client_code`, else the `/jobs` page's `_jibe = {"cid": ...}`.
`client_code` wins where both exist: the page cid is a template leftover on 4 of 211 checked
(`demant` on three unrelated employers). A robots.txt that is unreachable or answers 5xx, or a
page that answers 5xx/403/429, leaves the host `unresolved` for the next run.

What this does not cover:

- A client whose id matches no ledger label: the sweep only confirms guesses. 51 of the 412
  labels the Wayback sweep archived are in no guess on 2026-09-24 (`amex`, `compassgroup`,
  `farmersinsurance`); Wayback, Common Crawl and the fingerprinter are the other sources.
- `*.career.page` hosts, and the dotted `careers.rm.com.jibeapply.com` names a vanity host
  CNAMEs to: those are vanity hosts, for `--vanity`.
- Liveness. A resolving label may not be a readable Board (27 of 1,143 were not: `www`, `mail`,
  404 APIs); the pool is candidate-grade and the prober settles those.
- Finding vanity hosts: `--vanity` maps hosts it is given, it discovers none.

Output, under `data/discover/jibe/`, appended and flushed per item, so a run can be stopped and
resumed at no cost:
  `candidates.csv`     `ats,tenant,url,source` pool rows, source `dns` or `vanity`
  `{mode}_settled.csv` `input,verdict,client,detail` for every settled label or host

Run:   PYTHONPATH=src python -u scripts/discover/mine_jibe.py [--workers 32]
       PYTHONPATH=src python -u scripts/discover/mine_jibe.py --vanity hosts.txt [--workers 8]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import zlib
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

import dns.exception
import dns.resolver

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from headstart.network import http
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.jibe import ALLOW, CRAWL_DELAY, UNREACHABLE, robots_verdict

ZONE = "jibeapply.com"
RESOLVERS = ("1.1.1.1", "8.8.8.8", "9.9.9.9")
LEDGERS = ROOT / "data" / "validate" / "liveness"
OUT_DIR = ROOT / "data" / "discover" / "jibe"

CLIENT, NONE, UNRESOLVED, DISALLOWED = "client", "none", "unresolved", "disallowed"
API_PATH = "/api/jobs"
API_QUERY = "?page=1&limit=1&internal=false"
PAGE_PATH = "/jobs"

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_CID = re.compile(r"""_jibe\s*=\s*\{[^{}]*?["']cid["']\s*:\s*["']([^"']+)["']""")


def _label(text: str) -> str | None:
    """``text`` as a single DNS label of two or more characters, else None."""
    text = text.strip().lower()
    return text if len(text) >= 2 and _LABEL.fullmatch(text) else None


def labels_from_tenant(ats: str, tenant: str) -> set[str]:
    """The client-id guesses one ledger tenant yields."""
    host = tenant.strip().lower().split("://")[-1].split("/")[0]
    if ats != "icims":
        head = _label(host.split(".")[0])
        return {head} if head else set()
    label = host.removesuffix(".icims.com").strip("-")
    if "." in label or not label:
        return set()
    forms = {part for part in label.split("-")} | {label.replace("-", "")}
    if "-" in label:
        forms.add(label.split("-", 1)[1])
    return {f for f in map(_label, forms) if f}


def candidate_labels(ledgers: Path = LEDGERS) -> list[str]:
    """Every guess across the committed liveness ledgers, sorted."""
    labels: set[str] = set()
    for path in sorted(ledgers.glob("*.csv")):
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                labels |= labels_from_tenant(path.stem, row.get("tenant") or "")
    return sorted(labels)


def _resolver(nameserver: str) -> dns.resolver.Resolver:
    resolver = dns.resolver.Resolver(configure=False)  # never the OS resolver
    resolver.nameservers = [nameserver]
    resolver.timeout, resolver.lifetime = 3.0, 6.0
    return resolver


def resolve(
    label: str, resolvers: list, attempts: int = 6, pause: float = 0.5
) -> tuple[str, str]:
    """(verdict, detail) for ``{label}.jibeapply.com``, rotating resolvers on a failed query.

    A definitive answer from any one resolver settles it. Only after every attempt failed to get
    one is the label ``unresolved`` — which says nothing about whether it is a client.
    """
    start = zlib.crc32(label.encode()) % len(resolvers)
    last = ""
    for attempt in range(attempts):
        resolver = resolvers[(start + attempt) % len(resolvers)]
        try:
            answer = resolver.resolve(f"{label}.{ZONE}", "A", search=False)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer) as exc:
            return NONE, type(exc).__name__
        except (dns.exception.DNSException, OSError) as exc:
            last = type(exc).__name__
            time.sleep(pause * (attempt + 1))
            continue
        return CLIENT, " ".join(sorted(record.address for record in answer))
    return UNRESOLVED, last


def client_code_in(body: str) -> str | None:
    """The `client_code` of the first `/api/jobs` row that states one."""
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    for job in jobs or []:
        row = (job.get("data") or job) if isinstance(job, dict) else {}
        code = _label(str(row.get("client_code") or ""))
        if code:
            return code
    return None


def cid_in(html: str) -> str | None:
    """The board page's `_jibe = {"cid": ...}`."""
    match = _CID.search(html)
    return _label(match.group(1)) if match else None


def _transient(status: int | None) -> bool:
    return status is None or status in http.TRANSIENT


def resolve_vanity(
    host: str,
    get: Callable[[str], tuple[int | None, str]],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, str, str]:
    """(verdict, client, detail) for one vanity host.

    ``get(url)`` returns ``(status, text)``, status None when no answer came. robots.txt is the
    first request, and each later one waits out ``CRAWL_DELAY`` from the one before.
    """
    last: float | None = None

    def paced(path: str) -> tuple[int | None, str]:
        nonlocal last
        if last is not None and (wait := last + CRAWL_DELAY - clock()) > 0:
            sleep(wait)
        last = clock()
        return get(f"https://{host}{path}")

    robots = paced("/robots.txt")
    verdicts = {
        p: robots_verdict(*robots, p, USER_AGENT) for p in (API_PATH, PAGE_PATH)
    }
    if UNREACHABLE in verdicts.values():
        return UNRESOLVED, "", f"robots:{robots[0]}"
    if ALLOW not in verdicts.values():
        return DISALLOWED, "", "robots"
    seen: list[str] = []
    transient = False
    for path, query, read, via in (
        (API_PATH, API_QUERY, client_code_in, "client_code"),
        (PAGE_PATH, "", cid_in, "cid"),
    ):
        if verdicts[path] != ALLOW:
            continue
        status, body = paced(path + query)
        seen.append(f"{path}:{status}")
        transient |= _transient(status)
        client = read(body) if status == 200 else None
        if client:
            return CLIENT, client, via
    return (UNRESOLVED if transient else NONE), "", " ".join(seen)


def _http_get(url: str) -> tuple[int | None, str]:
    """One attempt, no redirect: a retry or an off-host hop would dodge the crawl delay."""
    try:
        response = http.fetch(
            "GET",
            url,
            attempts=1,
            allow_redirects=False,
            timeout=30,
            headers={"User-Agent": USER_AGENT},
        )
    except http.RequestsError:
        return None, ""
    return response.status_code, response.text


class _Sink:
    """Appends settled verdicts and pool rows, flushing each, and remembers what is settled."""

    def __init__(self, out_dir: Path, mode: str) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        self.settled_path = out_dir / f"{mode}_settled.csv"
        self.pool_path = out_dir / "candidates.csv"
        self.settled = _read_column(self.settled_path, "input")
        self.pool = {(r["tenant"], r["source"]) for r in _read_rows(self.pool_path)}
        self._settled_fh = _append(
            self.settled_path, ["input", "verdict", "client", "detail"]
        )
        self._pool_fh = _append(self.pool_path, ["ats", "tenant", "url", "source"])

    def settle(
        self, item: str, verdict: str, client: str, detail: str, source: str
    ) -> None:
        if verdict == UNRESOLVED:
            return  # asked again next run; never a miss
        csv.writer(self._settled_fh).writerow([item, verdict, client, detail])
        self._settled_fh.flush()
        self.settled.add(item)
        if verdict == CLIENT and (client, source) not in self.pool:
            url = f"https://{client}.{ZONE}"
            csv.writer(self._pool_fh).writerow(["jibe", client, url, source])
            self._pool_fh.flush()
            self.pool.add((client, source))

    def close(self) -> None:
        self._settled_fh.close()
        self._pool_fh.close()


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _read_column(path: Path, column: str) -> set[str]:
    return {row[column] for row in _read_rows(path)}


def _append(path: Path, header: list[str]):
    fresh = not path.exists()
    fh = path.open("a", newline="", encoding="utf-8")
    if fresh:
        csv.writer(fh).writerow(header)
        fh.flush()
    return fh


def _vanity_hosts(path: Path) -> list[str]:
    hosts: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            host = (
                urlsplit(line if "://" in line else f"//{line}").hostname or ""
            ).lower()
            if host and host not in hosts:
                hosts.append(host)
    return hosts


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--vanity", type=Path, help="map the careers hosts in FILE to client ids"
    )
    ap.add_argument("--workers", type=int, help="threads (default 32 DNS, 8 vanity)")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args()

    mode = "vanity" if args.vanity else "dns"
    sink = _Sink(args.out_dir, mode)
    items = _vanity_hosts(args.vanity) if args.vanity else candidate_labels()
    todo = [i for i in items if i not in sink.settled]
    print(
        f"{mode}: {len(items)} inputs, {len(items) - len(todo)} settled, {len(todo)} to do",
        flush=True,
    )
    resolvers = [_resolver(ns) for ns in RESOLVERS]

    def one(item: str) -> tuple[str, str, str]:
        if args.vanity:
            return resolve_vanity(item, _http_get)
        verdict, detail = resolve(item, resolvers)
        return verdict, (item if verdict == CLIENT else ""), detail

    counts: Counter[str] = Counter()
    workers = args.workers or (8 if args.vanity else 32)
    try:
        with ThreadPoolExecutor(workers) as pool:
            futures = {pool.submit(one, item): item for item in todo}
            for done, future in enumerate(as_completed(futures), 1):
                item = futures[future]
                verdict, client, detail = future.result()
                counts[verdict] += 1
                sink.settle(item, verdict, client, detail, mode)
                if verdict != NONE or args.vanity:
                    print(
                        f"{verdict:<10} {item} {client} {detail}".rstrip(), flush=True
                    )
                if done % 1000 == 0:
                    print(f"  {done}/{len(todo)} {dict(counts)}", flush=True)
    finally:
        sink.close()
    print(f"DONE {mode}: {dict(counts)}", flush=True)
    if counts[UNRESOLVED]:
        print(f"  {counts[UNRESOLVED]} unresolved: re-run to ask again", flush=True)


if __name__ == "__main__":
    main()
