#!/usr/bin/env python3
"""Zwayam Board miner — a DNS sieve and a certificate harvest in front of the board-search API.

Zwayam (Info Edge / Naukri Talent Cloud) puts each customer's Board on the *customer's own*
domain, so unlike every other ATS here there is no vendor namespace to walk. Two provider-side
channels leak the tenant list anyway, and they find **disjoint** populations — measured
2026-08-27, 0 of 109 certificate tenants answer on ``openings.co`` and no ``openings.co`` tenant
appears in either certificate — so a complete sweep needs both.

**`cert` — the shared TLS certificate (best precision, and already complete).** Every Akamai-fronted
Board is covered by one of two shared OV certificates whose SAN list *is* the tenant index. Two
`openssl` calls return 122 names; 102 of them verified as live Boards (85%). CT search on subject
organisation ``INFO EDGE (INDIA) LIMITED`` shows exactly two tenant-batch common names
(``www.zwayam.com``, ``infoedge.com``) among 24, so this is the whole Akamai-fronted population,
not a sample of it.

**`dns` — the sieve.** Boards CNAME into four Zwayam namespaces, three of which have **no wildcard**
(authoritative NXDOMAIN for an unknown label), so one UDP query proves an explicit record exists:

    {label}.ocean.edgekey.net     no wildcard -> NXDOMAIN unless real
    {label}ocean.edgekey.net      no wildcard -> NXDOMAIN unless real
    {label}.cluster.edgekey.net   no wildcard -> NXDOMAIN unless real
    {label}.openings.co           IS a wildcard (-> A 13.71.116.55); an explicit record CNAMEs to
                                  *.edgekey.net instead, which is what proves a real tenant

The sieve is precise (0 false positives over 34 known non-Zwayam companies) but a poor *generator*:
58,354 wordlist labels yielded 18 tenants and one new Board, because a hit names an Akamai **edge
label**, not a Board hostname, and 30% of edge labels are not derivable from any host label. Its
real use is as a **filter on the ``openings.co`` namespace**, where it cuts API traffic 72x for 75%
recall (measured: 17 explicit records over 1,232 labels, 12 of them live Boards).

**`verify` is the only thing that decides.** An explicit DNS record outlives tenancy —
``careers.azad.in`` CNAMEs into the namespace but is not a registered Board. Parse the body, never
the status: an unregistered domain answers HTTP 200 with ``"data": null``.

`verify` paces itself at 4 concurrent, 0.25s apart. An earlier version of this note called WARP
*required* — "Akamai 403s direct bursts persistently" — but a 2026-08-27 load test could not
reproduce that (~2,160 direct requests, 34 req/s sustained, zero 403s; `experiment/zwayam-rate-
limit/`), so the one 403 ever seen is rare and transient, not a threshold. The optional
``--warp`` flag still routes through the local WARP SOCKS proxy for the odd host that does wall,
but the direct path is the default.

Run:  python -m ... cert   OUT_TXT              # SAN list -> candidate hostnames
      python -m ... dns    WORDS OUT_CSV        # labels -> explicit-record namespaces
      python -m ... verify CAND OUT_CSV [--warp] # hostnames -> host,verdict,jobs

All stages stream per item and resume from their output file.

Requires dnspython (`dns`) and PySocks (`verify`); neither is a base dependency.
"""

import csv
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

API = "https://public.zwayam.com/jobs/search"
_WARP_PROXY = {
    "http": "socks5h://127.0.0.1:40000",
    "https": "socks5h://127.0.0.1:40000",
}
#: None = the direct path (the default; the endpoint does not wall bursts — see the module
#: docstring). `--warp` sets this to route through the local WARP SOCKS proxy.
PROXY: dict[str, str] | None = None
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
FILTER = json.dumps(
    {
        "paginationStartNo": 0,
        "selectedCall": "sort",
        "sortCriteria": {"name": "modifiedDate", "isAscending": False},
        "anyOfTheseWords": "",
    }
)

#: One host per certificate batch. Both are Boards in their own right; they are named here because
#: the SAN list they serve is the index, not because they are special tenants.
CERT_HOSTS = ["careers.persistent.com", "careers.manipalhospitals.com"]

#: Info Edge's own estate. These answer the search API (it keys on a domain string, not on
#: tenancy) but are not customer Boards.
INFRA = ("zwayam.com", "naukri", "infoedge", "ambitionbox", "naukimg", "openings.co/")

SHAPES = [
    ("ocean_dot", "{}.ocean.edgekey.net"),
    ("ocean_suffix", "{}ocean.edgekey.net"),
    ("cluster", "{}.cluster.edgekey.net"),
    ("openings", "{}.openings.co"),
]
RESOLVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9", "8.8.4.4", "1.0.0.1"]
DNS_WORKERS = 60
# One shared origin serves every Board, so this is a per-host budget, not a machine one (ADR-0026).
HTTP_WORKERS, HTTP_GAP = 4, 0.25

_lock = threading.Lock()
_next = [0.0]
_backoff = threading.Event()


def _resume(out: Path) -> set[str]:
    """Keys already recorded in `out`, so a re-run resumes instead of re-probing."""
    try:
        with out.open(encoding="utf-8") as fh:
            return {r[0] for r in list(csv.reader(fh))[1:] if r}
    except FileNotFoundError:
        return set()


# --- stage: certificate harvest --------------------------------------------------------------


def stage_cert(out: Path) -> int:
    names: set[str] = set()
    for host in CERT_HOSTS:
        proc = subprocess.run(
            f"echo | openssl s_client -connect {host}:443 -servername {host} 2>/dev/null "
            f"| openssl x509 -noout -ext subjectAltName",
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        for tok in proc.stdout.replace(",", "\n").split():
            if tok.startswith("DNS:"):
                n = tok[4:].strip().lower().lstrip("*.")
                if n and "." in n and not any(k in n for k in INFRA):
                    names.add(n)
        print(f"  [cert] {host}: {len(names)} names so far", flush=True)
    with out.open("w", encoding="utf-8") as fh:
        fh.write("\n".join(sorted(names)) + "\n")
    print(f"cert done: {len(names)} candidate hostnames -> {out}", flush=True)
    return len(names)


# --- stage: the DNS sieve --------------------------------------------------------------------


def _probe_label(label: str) -> list[tuple[str, str, str]]:
    import dns.resolver

    res = dns.resolver.Resolver()
    res.nameservers = RESOLVERS
    res.timeout, res.lifetime = 5, 12
    out = []
    for shape, tmpl in SHAPES:
        name = tmpl.format(label)
        tgt = None
        # A timeout is not an answer. Without the retry a saturated resolver reports real tenants
        # as absent -- measured: careers.cyient.com and careers.agilysys.com both resolve by hand
        # but came back empty from a 200-worker sweep.
        for attempt in range(3):
            try:
                tgt = str(res.resolve(name, "CNAME")[0].target).rstrip(".")
                break
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                break
            except Exception:  # noqa: BLE001
                if attempt == 2:
                    break
                time.sleep(1)
        if not tgt:
            continue
        if shape == "openings" and "edgekey.net" not in tgt:
            continue  # wildcard-synthesized, not a tenant
        out.append((shape, name, tgt))
    return out


def stage_dns(words_file: Path, out: Path) -> int:
    done = _resume(out)
    labels, seen = [], set()
    for line in words_file.read_text(encoding="utf-8", errors="replace").splitlines():
        w = line.strip().lower()
        if w and w not in done and w not in seen:
            seen.add(w)
            labels.append(w)
    print(f"{len(done)} labels done -> {len(labels)} to sieve", flush=True)
    fh = out.open("a", encoding="utf-8", newline="")
    wr = csv.writer(fh)
    if not done:
        wr.writerow(["label", "shape", "name", "target"])
    hits = n = 0
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=DNS_WORKERS) as ex:
        futs = {ex.submit(_probe_label, w): w for w in labels}
        for fut in as_completed(futs):
            label = futs[fut]
            n += 1
            try:
                rows = fut.result()
            except Exception:  # noqa: BLE001
                rows = []
            for shape, name, tgt in rows:
                hits += 1
                wr.writerow([label, shape, name, tgt])
                print(f"  [HIT] {name} -> {tgt}", flush=True)
            if not rows:
                wr.writerow([label, "", "", ""])
            else:
                fh.flush()
            if n % 5000 == 0:
                fh.flush()
                print(
                    f"  ...{n}/{len(labels)} hits={hits} ({n / (time.monotonic() - t0):.0f}/s)",
                    flush=True,
                )
    fh.close()
    print(f"dns done: {hits} explicit records over {n} labels", flush=True)
    return hits


# --- stage: verify against the board-search API -----------------------------------------------


def _pace() -> None:
    with _lock:
        now = time.monotonic()
        wait = max(0.0, _next[0] - now)
        _next[0] = max(now, _next[0]) + HTTP_GAP
    if wait:
        time.sleep(wait)


def _probe_host(host: str) -> tuple[str, str, int]:
    """(host, verdict, jobs). verdict in {board, none, err:*}."""
    import requests

    for attempt in range(3):
        while _backoff.is_set():
            time.sleep(5)
        _pace()
        try:
            r = requests.post(
                API,
                proxies=PROXY,
                timeout=45,
                headers={
                    "User-Agent": UA,
                    "Origin": f"https://{host}",
                    "Referer": f"https://{host}/",
                    "Accept": "application/json",
                },
                files={
                    "filterCri": (None, FILTER),
                    "domain": (None, host),
                    # Ignored by the server -- verified 2026-08-27; the domain field alone selects
                    # the tenant. Sent anyway because the form is what the real frontend posts.
                    "companyId": (None, "MQ=="),
                },
            )
            if r.status_code == 403:
                if not _backoff.is_set():
                    _backoff.set()
                    print("  [!] 403 from Akamai - backing off 60s", flush=True)
                    time.sleep(60)
                    _backoff.clear()
                continue
            if r.status_code != 200:
                return host, f"err:http{r.status_code}", 0
            data = r.json().get("data")
            if not data:
                return host, "none", 0
            return host, "board", int(data.get("totalCount") or 0)
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                return host, f"err:{type(exc).__name__}", 0
            time.sleep(3)
    return host, "err:retries", 0


def stage_verify(cand_file: Path, out: Path) -> int:
    done = _resume(out)
    hosts, seen = [], set()
    for line in cand_file.read_text(encoding="utf-8", errors="replace").splitlines():
        h = line.strip().lower().lstrip("*.")
        if h and "*" not in h and h not in done and h not in seen:
            seen.add(h)
            hosts.append(h)
    print(f"{len(done)} done -> {len(hosts)} to probe", flush=True)
    fh = out.open("a", encoding="utf-8", newline="")
    wr = csv.writer(fh)
    if not done:
        wr.writerow(["host", "verdict", "jobs"])
    boards = n = 0
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=HTTP_WORKERS) as ex:
        for fut in as_completed([ex.submit(_probe_host, h) for h in hosts]):
            host, verdict, jobs = fut.result()
            n += 1
            wr.writerow([host, verdict, jobs])
            fh.flush()
            if verdict == "board":
                boards += 1
                print(f"  [BOARD] {host}  jobs={jobs}", flush=True)
            elif verdict.startswith("err"):
                print(f"  [err]   {host}  {verdict}", flush=True)
            if n % 25 == 0:
                print(
                    f"  ...{n}/{len(hosts)} boards={boards} "
                    f"({n / (time.monotonic() - t0):.1f}/s)",
                    flush=True,
                )
    fh.close()
    print(f"verify done: {boards} boards in {n} probed", flush=True)
    return boards


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    global PROXY
    stage = argv[1]
    rest = [a for a in argv[2:] if a != "--warp"]
    if "--warp" in argv[2:]:
        if stage != "verify":
            print(f"--warp applies to `verify`, not `{stage}`", flush=True)
            return 2
        PROXY = _WARP_PROXY
        print("[warp] routing verify through the WARP SOCKS proxy", flush=True)
    if stage == "cert" and len(rest) == 1:
        stage_cert(Path(rest[0]))
    elif stage == "dns" and len(rest) == 2:
        stage_dns(Path(rest[0]), Path(rest[1]))
    elif stage == "verify" and len(rest) == 2:
        stage_verify(Path(rest[0]), Path(rest[1]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
