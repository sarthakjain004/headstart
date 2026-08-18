#!/usr/bin/env python3
"""RippleHire Board discovery by DNS enumeration of `*.ripplehire.com`.

**Why DNS, when every other miner here reads archives.** RippleHire's Slug is a bare label
under one shared domain (`{slug}.ripplehire.com`) and — verified 2026-07-25 — the domain has
**no wildcard DNS record**:

    zzzznotarealtenant9x.ripplehire.com   NXDOMAIN
    altimetrik.ripplehire.com             NOERROR  34.149.230.79

so a name resolves *if and only if* the Board exists. That makes the namespace itself directly
enumerable and resolution *is* the candidate list — no archive needed. It also makes a miss
nearly free: one UDP round trip, against a TCP+TLS handshake for an HTTP probe. **Resolve
first, probe second** is therefore structural here, not an optimisation — it is the only reason
a six-figure wordlist is affordable. This script does the resolve half and writes candidates to
the pool; `scripts/validate/check_liveness.py ripplehire` does the probe half and settles them.

The archive feeders that carry other ATSes are near-useless for this one, all measured:

    Certificate Transparency   dud — one `*.ripplehire.com` wildcard cert hides every customer;
                               Cert Spotter returned 14 names, all RippleHire's own infra
    Common Crawl               dud — 34 labels over 11 crawls, only `app`/`www` new. The
                               careers flow is a token redirect (`/candidate/careers` ->
                               `/candidate/?token=...`) so crawlers never index a Board
    Wayback CDX                near-exhausted — 125 pages / 430k urls yielded 137 labels,
                               almost all already known
    urlscan.io                 small but real — 36 hosts, 13 labels not otherwise known

**Wordlist quality dominates.** Hit rates measured on this domain:

    India-weighted company names (NSE + curated IT/GCC/BFSI)   0.73%   <- the winner
    generic global ATS slug pool                              0.15%
    BSE-listed tail (after the NSE overlap was removed)        0.00%   dud, 5k labels
    UUID/hex-shaped labels scraped from other ATS pools        0.00%   dud, filtered out below

So the default list is built from company names, not subdomain dictionaries, and the
highest-signal shape found is the **conglomerate cluster**: RippleHire lands a *group*, not a
company. Axis holds six Boards (axisbank, axisfinance, axissecurities, axiscapital,
axistrustee, axisfl) and Tata five (tataaia, tatasteel, tatatechnologies, tataelxsi,
tatacapital), so every large Indian conglomerate, bank and insurer is worth probing as a
cluster — parent, business lines, and captive arm — which is what CLUSTER_SUFFIX encodes.
`7-eleven-gsc` shows the captive-centre suffix and hyphenated form are both live conventions.

Run:  python scripts/discover/mine_ripplehire.py --names config/seed_india.csv
      python scripts/discover/mine_ripplehire.py --wordlist my_labels.txt --concurrency 40
      python scripts/discover/mine_ripplehire.py --clusters          # conglomerate sweep only
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DOMAIN = "ripplehire.com"
POOL = ROOT / "data" / "ats-tenants-merged" / "ripplehire.csv"
LEDGER = ROOT / "data" / "validate" / "liveness" / "ripplehire.csv"
STATE = ROOT / "data" / "scratch" / "ripplehire_dns_seen.csv"

# Public recursive resolvers, round-robined. Concurrency is the knob that matters: at 250
# in-flight queries they rate-limited us into a 46% error rate (silent false negatives); at
# 40-60 the error rate is ~0. Errors are recorded as `err` and re-probed on the next run —
# never folded into "does not exist", which would bury Boards.
RESOLVERS = [
    "1.1.1.1",
    "1.0.0.1",
    "8.8.8.8",
    "8.8.4.4",
    "9.9.9.9",
    "149.112.112.112",
    "208.67.222.222",
    "208.67.220.220",
    "94.140.14.14",
    "76.76.2.0",
]

VALID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
# Labels shaped like an id, not a company — they come free with the other ATS pools and cost a
# query each for a measured 0% return.
JUNK = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,}|[0-9-]+)$"
)

# Corporate-form noise to strip before deriving a Slug from a company name.
STOP = {
    "limited",
    "ltd",
    "private",
    "pvt",
    "public",
    "company",
    "co",
    "corporation",
    "corp",
    "inc",
    "incorporated",
    "plc",
    "llp",
    "the",
    "and",
    "of",
    "group",
    "holdings",
    "holding",
    "enterprises",
    "enterprise",
    "industries",
    "industry",
    "international",
}

# Per-group shapes — the conglomerate-cluster pattern (see module docstring).
CLUSTER_SUFFIX = [
    "",
    "bank",
    "finance",
    "financial",
    "life",
    "amc",
    "securities",
    "capital",
    "trustee",
    "insurance",
    "general",
    "health",
    "tech",
    "technologies",
    "infotech",
    "systems",
    "gsc",
    "gcc",
    "global",
    "india",
    "labs",
    "solutions",
    "services",
    "digital",
    "consulting",
    "group",
    "motors",
    "power",
    "steel",
    "chemicals",
    "consumer",
    "retail",
    "housing",
    "mutualfund",
    "asset",
    "cards",
    "payments",
    "ventures",
]


def slug_variants(name: str) -> set[str]:
    """A company name -> the Slug shapes an ATS realistically uses."""
    words = [w for w in re.split(r"[^a-z0-9]+", (name or "").lower()) if w]
    if not words:
        return set()
    core = [w for w in words if w not in STOP] or words
    out = {
        "".join(core),
        "-".join(core),
        core[0],
        "".join(words),
        "-".join(words),
    }
    if len(core) >= 2:
        out |= {"".join(core[:2]), "-".join(core[:2])}
    return {s for s in out if VALID.match(s) and not JUNK.match(s)}


def cluster_variants(parent: str) -> set[str]:
    """A group stem -> parent + business-line + captive-arm Slugs, plain and hyphenated."""
    p = re.sub(r"[^a-z0-9]+", "", (parent or "").lower())
    if not p:
        return set()
    out = set()
    for s in CLUSTER_SUFFIX:
        out.add(p + s)
        if s:
            out.add(f"{p}-{s}")
    return {s for s in out if VALID.match(s) and not JUNK.match(s)}


def known_slugs() -> set[str]:
    """Every Slug already in the pool or the ledger — case-folded, since a re-cased Slug would
    duplicate the row *and* orphan its board_priority/board_cost history, which key on the
    exact string."""
    seen = set()
    for f in (POOL, LEDGER):
        if f.exists():
            for r in csv.DictReader(f.open(encoding="utf-8")):
                seen.add(r["tenant"].strip().lower())
    return seen


def load_settled() -> dict[str, str]:
    """label -> verdict, for verdicts that are final. `err` rows are deliberately absent so
    they get re-probed: a resolver timeout is a rate limit, not a missing Board."""
    out = {}
    if STATE.exists():
        for ln in STATE.read_text().splitlines():
            p = ln.split(",")
            if len(p) >= 2 and p[1] in ("hit", "nx", "noanswer"):
                out[p[0]] = p[1]
    return out


async def sweep(labels: list[str], concurrency: int) -> set[str]:
    """Resolve each label under DOMAIN. Returns the set that exists."""
    import dns.asyncresolver
    import dns.resolver

    def make(ip):
        r = dns.asyncresolver.Resolver(configure=False)
        r.nameservers = [ip]
        r.timeout, r.lifetime = 4.0, 6.0
        return r

    resolvers = [make(ip) for ip in RESOLVERS]
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    hits: set[str] = set()
    done = {"n": 0}
    t0 = time.monotonic()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    f = STATE.open("a")

    async def one(i: int, label: str):
        async with sem:
            res = resolvers[i % len(resolvers)]
            verdict = "err"
            for attempt in range(3):
                try:
                    await res.resolve(f"{label}.{DOMAIN}", "A")
                    verdict = "hit"
                    break
                except dns.resolver.NXDOMAIN:
                    verdict = "nx"
                    break
                except dns.resolver.NoAnswer:
                    verdict = "noanswer"
                    break
                except Exception:  # noqa: BLE001
                    res = resolvers[(i + attempt + 1) % len(resolvers)]
        async with lock:
            done["n"] += 1
            f.write(f"{label},{verdict}\n")
            f.flush()  # stream: a killed sweep keeps every settled verdict
            if verdict in ("hit", "noanswer"):
                hits.add(label)
                print(f"  HIT {label}.{DOMAIN}", flush=True)
            if done["n"] % 2000 == 0:
                rate = done["n"] / max(time.monotonic() - t0, 1e-9)
                print(
                    f"  [{done['n']}/{len(labels)}] {rate:.0f}/s, {len(hits)} hits",
                    flush=True,
                )

    await asyncio.gather(*(one(i, x) for i, x in enumerate(labels)))
    f.close()
    return hits


def write_pool(new: set[str]) -> int:
    """Add `new` Slugs to the pool. Never re-cases an existing Slug and never writes a
    duplicate: rows, unique Slugs and case-folded unique Slugs must all be equal."""
    rows: dict[str, dict] = {}
    if POOL.exists():
        for r in csv.DictReader(POOL.open(encoding="utf-8")):
            rows[r["tenant"].strip().lower()] = r
    added = 0
    for label in sorted(new):
        if label.lower() in rows:
            continue
        rows[label.lower()] = {
            "ats": "ripplehire",
            "tenant": label,
            "url": f"https://{label}.{DOMAIN}",
            "source": "dns",
        }
        added += 1
    ordered = sorted(rows.values(), key=lambda r: r["tenant"])
    slugs = [r["tenant"] for r in ordered]
    assert len(ordered) == len(set(slugs)) == len({s.lower() for s in slugs}), (
        "duplicate or case-colliding Slug in the RippleHire pool"
    )
    POOL.parent.mkdir(parents=True, exist_ok=True)
    with POOL.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ats", "tenant", "url", "source"])
        w.writeheader()
        w.writerows(ordered)
    return added


def build_labels(args) -> list[str]:
    cands: set[str] = set()
    if args.wordlist:
        for ln in Path(args.wordlist).read_text().splitlines():
            s = ln.strip().lower()
            if s and VALID.match(s) and not JUNK.match(s):
                cands.add(s)
    if args.names:
        p = Path(args.names)
        if p.suffix == ".csv":
            for row in csv.DictReader(p.open(encoding="utf-8", errors="replace")):
                for v in row.values():
                    if v and not v.startswith("http"):
                        cands |= slug_variants(v)
        else:
            for ln in p.read_text(errors="replace").splitlines():
                cands |= slug_variants(ln)
    if args.clusters:
        for ln in Path(args.clusters).read_text().splitlines():
            cands |= cluster_variants(ln)
    return sorted(cands)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wordlist", help="file of bare candidate labels, one per line")
    ap.add_argument(
        "--names", help="company names (txt) or a CSV whose cells hold names"
    )
    ap.add_argument(
        "--clusters", help="file of group stems -> conglomerate-cluster Slugs"
    )
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if not (args.wordlist or args.names or args.clusters):
        ap.error("give at least one of --wordlist / --names / --clusters")

    labels = build_labels(args)
    settled = load_settled()
    known = known_slugs()
    todo = [x for x in labels if x not in settled and x not in known]
    if args.limit:
        todo = todo[: args.limit]
    print(
        f"ripplehire: {len(labels)} candidates, {len(labels) - len(todo)} already "
        f"settled/known, {len(todo)} to resolve (concurrency={args.concurrency})",
        flush=True,
    )
    if not todo:
        return 0
    hits = asyncio.run(sweep(todo, args.concurrency))
    added = write_pool(hits)
    print(
        f"DONE {len(hits)} resolve of {len(todo)} "
        f"({100 * len(hits) / len(todo):.2f}%), {added} new -> {POOL}",
        flush=True,
    )
    print(
        "next: python scripts/validate/check_liveness.py ripplehire "
        "(cap LIVENESS_WORKERS ~10 — every Board shares one host)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
