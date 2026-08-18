#!/usr/bin/env python3
"""Keka Board miner — a DNS sieve in front of the careers-embed API.

Keka puts every Board on a bare label under one shared domain (``{slug}.keka.com``), which
normally makes subdomain enumeration the best source. It isn't, here: ``keka.com`` is fronted by
a single ``*.keka.com`` wildcard cert (crt.sh returns 35 names, all Keka's own infra) and the zone
is unsigned, so neither CT logs nor NSEC walking name a single tenant.

**DNS still leaks the namespace, via the wildcard's shape.** Every ``*.keka.com`` label resolves,
but a *wildcard-synthesized* answer always CNAMEs to ``cin02.hr.keka.com``, while a tenant with an
explicit record CNAMEs to whichever pod actually hosts it. So a CNAME to any pod **other than
cin02** proves an explicit record exists — i.e. a real Keka tenant — at one cheap UDP query, no
HTTP. Measured over the 1,196 already-known Slugs: 661 (55%) sit on a non-cin02 pod and are
provable this way; the other 519 sit on cin02 and are indistinguishable from the wildcard, so they
still need an HTTP probe. Of the DNS-provable ones ~66% turn out to have a public Board, versus a
name-guess hit rate two orders of magnitude below that — which is what makes sweeping a
six-figure wordlist affordable.

Stage 2 verifies against the real embed API, in the two steps ``KekaScraper`` uses:
``/careers/api/organization/default/careerportalinfo`` for the org UUID (it only rides along inside
``careersBackgroundPath``, so background-less portals fall back to the ``/careers`` page HTML),
then ``/careers/api/embedjobs/default/active/{uuid}`` for the job array.

**Parse the body, never the status.** Keka soft-errors at HTTP 200 — an unknown Slug renders
"Invalid Tenant", a disabled portal "Forbidden Access". Both mean no public Board; a probe that
trusts the status code marks every label in the namespace live.

Run:  python scripts/discover/mine_keka.py dns    CAND_FILE OUT_CSV   # labels -> pods
      python scripts/discover/mine_keka.py verify CAND_FILE OUT_CSV   # labels -> ats,tenant,url

Both stages stream per item and resume: rows already in OUT_CSV are skipped, so a run killed
mid-sweep costs only the item in flight. `verify` writes the candidate shape
data/wayback-ats/keka.csv expects; hand it to scripts/validate/check_liveness.py to land.

Requires dnspython for the `dns` stage (not a base dependency — CI installs base deps only).
"""

import csv
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
from headstart import http

UA = "HeadStart-discovery/0.1 (careers-board discovery; polite)"
# Keka renders these at HTTP 200: unknown slug -> "Invalid Tenant", disabled portal ->
# "Forbidden Access". Either means there is no public Board behind the label.
DEAD_MARKERS = ("Invalid Tenant", "Forbidden Access")
# The pod every wildcard-synthesized answer lands on. A CNAME anywhere else proves an explicit
# record — i.e. a real tenant. Re-derive it (resolve a garbage label) rather than trusting this
# constant if Keka ever repoints the wildcard.
WILDCARD_POD = "cin02.hr.keka.com"

DNS_WORKERS = 128
# One shared origin serves every Board, so this is a per-host budget, not a machine one
# (ADR-0026). 24 in-flight measured ~30 boards/s with no 429s.
HTTP_WORKERS = 24


def _load_done(out: Path, col: int = 0) -> set[str]:
    """Labels already recorded in `out`, so a re-run resumes instead of re-probing."""
    if not out.exists():
        return set()
    with out.open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    return {r[col] for r in rows[1:] if r}


def _candidates(path: Path, out: Path, col: int) -> list[str]:
    labels = [
        line.strip().lower()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    done = _load_done(out, col)
    seen, fresh = set(), []
    for label in labels:
        if label in done or label in seen:
            continue
        seen.add(label)
        fresh.append(label)
    print(
        f"{len(labels)} candidates, {len(done)} already done -> {len(fresh)} to probe",
        flush=True,
    )
    return fresh


# --- stage 1: the DNS sieve ----------------------------------------------------------------


def resolve_pod(label: str, resolver) -> tuple[str, str]:
    """(label, pod) — the CNAME target's first label chain, or an ERR:* marker."""
    try:
        ans = resolver.resolve(f"{label}.keka.com", "CNAME")
        return label, str(ans[0].target).rstrip(".")
    # NXDOMAIN/NoAnswer/timeout are all "no usable signal"
    except Exception as exc:  # noqa: BLE001
        return label, f"ERR:{type(exc).__name__}"


def stage_dns(cand_file: Path, out: Path) -> int:
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.nameservers = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]
    resolver.timeout, resolver.lifetime = 4, 10

    fresh = _candidates(cand_file, out, col=0)
    if not fresh:
        return 0
    new = out.exists()
    fh = out.open("a", encoding="utf-8", newline="")
    writer = csv.writer(fh)
    if not new:
        writer.writerow(["label", "pod"])
    hits = done = 0
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=DNS_WORKERS) as ex:
        futures = [ex.submit(resolve_pod, c, resolver) for c in fresh]
        for fut in as_completed(futures):
            label, pod = fut.result()
            done += 1
            writer.writerow([label, pod])
            if pod != WILDCARD_POD and not pod.startswith("ERR:"):
                hits += 1
                print(f"  [dns] {label} -> {pod}", flush=True)
            if done % 5000 == 0:
                fh.flush()
                rate = done / (time.monotonic() - start)
                print(
                    f"  [dns] {done}/{len(fresh)} resolved, {hits} explicit "
                    f"({rate:.0f}/s)",
                    flush=True,
                )
    fh.close()
    print(f"dns done: {hits} explicit-record labels in {done} probed", flush=True)
    return hits


# --- stage 2: verify against the careers embed API -----------------------------------------


class _Politeness:
    """Back off the whole sweep when the shared origin pushes back (429/5xx)."""

    def __init__(self) -> None:
        self.until = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        while True:
            with self._lock:
                delay = self.until - time.monotonic()
            if delay <= 0:
                return
            time.sleep(min(delay, 5))

    def trip(self, seconds: float, why: str) -> None:
        with self._lock:
            if time.monotonic() < self.until:
                return
            self.until = time.monotonic() + seconds
        print(f"  [backoff] {why} — pausing {seconds:.0f}s", flush=True)


def _get(url: str, pol: _Politeness, timeout: int = 20):
    pol.wait()
    r = http.fetch(
        "GET",
        url,
        headers={"User-Agent": UA},
        timeout=timeout,
        verify=False,
        attempts=1,
    )
    if r.status_code == 429 or r.status_code >= 500:
        pol.trip(60, f"HTTP {r.status_code}")
    return r


def verify(label: str, pol: _Politeness) -> tuple[str, str, int | None]:
    """(label, verdict, jobs). verdict is live / dead / unknown; jobs only for live.

    One request: the careers SPA's own job call (`/api/jobs/{portal}/active`, portal "default"),
    read off cdn.keka.com/careers/v/2026/scripts/app/app.min.js. It needs no org UUID, so it also
    reads the Boards whose portal exposes none — Keka only leaks the UUID inside an
    /ats/documents/{uuid}/ asset path (the careers background image, else the org logo), so a
    portal with neither is invisible to the UUID route even while serving jobs.

    Reads the body for Keka's HTTP-200 soft errors ("Invalid Tenant" / "Forbidden Access")
    instead of trusting the status code.
    """
    try:
        r = _get(f"https://{label}.keka.com/careers/api/jobs/default/active", pol)
    except Exception:  # noqa: BLE001
        return label, "unknown", None
    if r.status_code in (404, 410):
        return label, "dead", None
    if r.status_code != 200:
        return label, "unknown", None
    body = r.text
    if any(m in body for m in DEAD_MARKERS):
        return label, "dead", None
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return label, "unknown", None
    if not isinstance(data, list):
        return label, "unknown", None
    return label, "live", len(data)


def stage_verify(cand_file: Path, out: Path) -> int:
    fresh = _candidates(cand_file, out, col=1)
    if not fresh:
        return 0
    pol = _Politeness()
    new = out.exists()
    fh = out.open("a", encoding="utf-8", newline="")
    writer = csv.writer(fh)
    if not new:
        writer.writerow(["ats", "tenant", "url", "status", "jobs"])
    live = done = 0
    with ThreadPoolExecutor(max_workers=HTTP_WORKERS) as ex:
        futures = [ex.submit(verify, c, pol) for c in fresh]
        for fut in as_completed(futures):
            label, verdict, jobs = fut.result()
            done += 1
            if verdict == "live":
                live += 1
                writer.writerow(
                    ["keka", label, f"https://{label}.keka.com", verdict, jobs]
                )
                fh.flush()
                print(f"  [live] {label}: {jobs} jobs", flush=True)
            if done % 500 == 0:
                print(f"  [verify] {done}/{len(fresh)}, {live} live", flush=True)
    fh.close()
    print(f"verify done: {live} live Boards in {done} probed", flush=True)
    return live


def main(argv: list[str]) -> int:
    if len(argv) < 4 or argv[1] not in ("dns", "verify"):
        print(__doc__, file=sys.stderr)
        return 2
    stage, cand_file, out = argv[1], Path(argv[2]), Path(argv[3])
    out.parent.mkdir(parents=True, exist_ok=True)
    (stage_dns if stage == "dns" else stage_verify)(cand_file, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
