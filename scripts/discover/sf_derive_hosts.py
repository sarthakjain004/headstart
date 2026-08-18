#!/usr/bin/env python3
"""Derive candidate SuccessFactors vanity hosts from the company names discovery already knows.

A board's slug is the customer's vanity host, so derivation is only worth doing where the *shape*
is this predictable. Measured over the 445 boards confirmed by 2026-07-27, the first label is
``careers`` / ``jobs`` / ``career`` for 92.4% of them, and the TLD is ``.com`` for 77%.

Three name sources feed it, in decreasing precision:

1. **Apex domains already seen next to an RMK board** (urlscan's ``apexDomain``, or the apex of a
   confirmed board) — the TLD is *known*, so only the prefix is guessed. Highest hit-rate.
2. **jobs2web customer keys** mined from Wayback (``accenture.jobs2web.com`` -> ``accenture``) —
   the CNAME target's own label, which is usually the brand.
3. **SuccessFactors CSB company ids** (``data/discover/sf_csb_companies.csv``) with their
   environment suffixes stripped (``atlascopcoP`` -> ``atlascopco``).

Output is one candidate host per line, for ``sf_cname_probe.py`` to filter by the jobs2web CNAME
oracle before anything pays for an HTTP request. Derivation alone is a measured dud on other
ATSes (0.10% on Greenhouse); it is only viable here because the oracle makes a wrong guess cost
a single UDP packet.

Run:  python -u scripts/discover/sf_derive_hosts.py OUT_FILE
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRATCH = ROOT / "data" / "scratch" / "sf"

# Measured prefix distribution over confirmed boards (see docstring), plus the localised forms
# that showed up in the long tail (carreiras.klabin.com.br, carriere., empleo.).
PREFIXES = (
    "careers",
    "jobs",
    "career",
    "jobsearch",
    "apply",
    "carreiras",
    "carriere",
    "carrieres",
    "karriere",
    "empleo",
    "empleos",
    "jobdetails",
    "recruitment",
    "talent",
)
# .com dominates; the rest are the country TLDs actually seen on confirmed boards.
TLDS = (
    ".com",
    ".net",
    ".org",
    ".eu",
    ".co.uk",
    ".ca",
    ".com.au",
    ".de",
    ".fr",
    ".es",
    ".it",
    ".nl",
    ".se",
    ".ch",
    ".at",
    ".in",
    ".com.br",
    ".sg",
    ".jp",
    ".ae",
    ".co.za",
    ".mx",
    ".pl",
    ".dk",
)

# Environment/instance suffixes on a name-like SF company id: BurberryProd, atlascopcoP.
_SUFFIX = re.compile(
    r"(prod|prd|production|test|tst|stage|stg|dev|corp|global|ext|dp|p|t)+$",
    re.IGNORECASE,
)
_OPAQUE = re.compile(r"^C?\d")  # numeric customer ids can't be derived from
_LABEL_OK = re.compile(r"^[a-z0-9][a-z0-9-]{2,40}$")
# Public suffixes we must not treat as a registrable apex when taking apexDomain shortcuts.
_MULTI_TLD = (".co.uk", ".com.au", ".com.br", ".co.za", ".co.in", ".co.jp", ".com.mx")


def apex_of(host: str) -> str | None:
    """The registrable domain of `host`, or None if it is already an apex/an IP."""
    if re.match(r"^[\d.]+$", host):
        return None
    parts = host.split(".")
    for suffix in _MULTI_TLD:
        if host.endswith(suffix):
            return ".".join(parts[-3:]) if len(parts) >= 3 else None
    return ".".join(parts[-2:]) if len(parts) >= 2 else None


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        print(f"  (missing: {path})", flush=True)
        return []
    return [ln.strip().lower() for ln in path.read_text().splitlines() if ln.strip()]


def main(argv: list[str]) -> int:
    out_path = Path(argv[1]) if len(argv) > 1 else SCRATCH / "derived_hosts.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    known = set(read_lines(SCRATCH / "known.txt")) | set(
        read_lines(SCRATCH / "allboards_sofar.txt")
    )

    # 1. apexes seen beside an RMK board -> prefix-only guesses (TLD known)
    apexes: set[str] = set()
    for host in read_lines(SCRATCH / "urlscan_uniq.txt") + sorted(known):
        apex = apex_of(host)
        if apex and "." in apex:
            apexes.add(apex)
    # 2. jobs2web customer keys
    keys = {
        h.split("//")[-1].split(".")[0]
        for h in read_lines(SCRATCH / "jobs2web_keys.txt")
    }
    # 3. SF CSB company ids
    ids: set[str] = set()
    csb = ROOT / "data" / "discover" / "sf_csb_companies.csv"
    if csb.exists():
        for row in csv.DictReader(csb.open(encoding="utf-8")):
            cid = (row.get("company") or "").strip()
            if cid and not _OPAQUE.match(cid):
                ids.add(_SUFFIX.sub("", cid.lower()) or cid.lower())

    labels = {label for label in (keys | ids) if _LABEL_OK.match(label)}
    print(
        f"sources: {len(apexes)} apexes, {len(keys)} jobs2web keys, {len(ids)} csb ids "
        f"-> {len(labels)} usable labels",
        flush=True,
    )

    candidates: set[str] = set()
    for apex in apexes:  # TLD known: only the prefix is guessed
        for prefix in PREFIXES:
            candidates.add(f"{prefix}.{apex}")
    for label in labels:  # both guessed
        for tld in TLDS:
            for prefix in PREFIXES:
                candidates.add(f"{prefix}.{label}{tld}")

    candidates -= known
    out_path.write_text("\n".join(sorted(candidates)) + "\n")
    print(f"DONE {len(candidates)} candidate hosts -> {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
