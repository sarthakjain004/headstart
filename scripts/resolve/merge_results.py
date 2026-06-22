#!/usr/bin/env python3
"""Merge the fingerprinter's main run with the verify-pass recoveries into one coverage list.

Inputs (both produced under data/):
  - fingerprint_results.csv  name,domain,hits          (main run: slug-probe + careers scan)
  - verify_results.csv       name,domain,found,method  (verify: clean re-probe + subdomain title)

Unions the two per company, drops provider self-references (darwinbox.com -> darwinbox:*), and
flags low-confidence hits for an eyeball: an enterprise name on an SMB-only India ATS
(jobsoid/zoho/turbohire/ripplehire/qandle) is a likely namesake/squat (e.g. infosys.zohorecruit
.in), since those systems aren't used by 100k-employee firms. Writes data/resolve/coverage.csv:
  name,domain,ats,slug,source,flag
Run:  python scripts/resolve/merge_results.py
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MAIN = ROOT / "data" / "resolve" / "fingerprint_results.csv"
VERIFY = ROOT / "data" / "resolve" / "verify_results.csv"
OUT = ROOT / "data" / "resolve" / "coverage.csv"

PROVIDER_DOMAINS = {
    "greenhouse": {"greenhouse.io"}, "lever": {"lever.co"}, "ashby": {"ashbyhq.com"},
    "zoho": {"zohorecruit.com", "zohorecruit.eu", "zohorecruit.in", "zohorecruit.ca"},
    "recruitee": {"recruitee.com"}, "workable": {"workable.com"},
    "darwinbox": {"darwinbox.in", "darwinbox.com"}, "keka": {"keka.com"},
    "qandle": {"qandle.com"}, "ripplehire": {"ripplehire.com"}, "turbohire": {"turbohire.co"},
}
# India SMB-only ATSes: a giant employer here is almost certainly a namesake, not the real firm.
SMB_ONLY = {"zoho", "turbohire", "ripplehire", "qandle"}

# Confirmed false positives (verified by hand) — dropped on every merge so they don't creep back
# when the main run re-surfaces them. Keyed by (domain, ats, slug):
#   infosys/gromo/upgrad zoho -> tenant "Page does not exist" or giant-on-SMB namesake
#   ltimindtree ripplehire    -> empty login page, no public feed; 90k-employee firm
#   fi.money lever:fi         -> "fi" board is a US firm (NYC roles), not the Indian Fi Money
FALSE_POSITIVES = {
    ("infosys.com", "zoho", "infosys"), ("gromo.in", "zoho", "gromo"),
    ("upgrad.com", "zoho", "upgrad"), ("ltimindtree.com", "ripplehire", "ltimindtree"),
    ("fi.money", "lever", "fi"),
}


def reg_domain(domain):
    parts = domain.lower().split("//")[-1].split("/")[0].split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain.lower()


def parse_hits(field):
    out = set()
    for h in field.strip().split(";"):
        if ":" in h:
            ats, slug = h.split(":", 1)
            out.add((ats.strip(), slug.strip()))
    return out


def main():
    companies = {}  # domain -> {name, hits:set((ats,slug,source))}
    for r in csv.DictReader(MAIN.open(encoding="utf-8")):
        e = companies.setdefault(r["domain"], {"name": r["name"], "hits": set()})
        for ats, slug in parse_hits(r["hits"]):
            e["hits"].add((ats, slug, "main"))
    for r in csv.DictReader(VERIFY.open(encoding="utf-8")):
        e = companies.setdefault(r["domain"], {"name": r["name"], "hits": set()})
        for ats, slug in parse_hits(r.get("found", "")):
            if (ats, slug, "main") not in e["hits"]:
                e["hits"].add((ats, slug, "verify"))

    rows, dropped_selfref, dropped_fp, flagged = [], 0, 0, 0
    for domain, e in sorted(companies.items(), key=lambda kv: kv[1]["name"].lower()):
        rd = reg_domain(domain)
        for ats, slug, source in sorted(e["hits"]):
            if rd in PROVIDER_DOMAINS.get(ats, set()):
                dropped_selfref += 1
                continue
            if (domain, ats, slug) in FALSE_POSITIVES:
                dropped_fp += 1
                continue
            flag = "review:enterprise-on-smb-ats" if ats in SMB_ONLY else ""
            if flag:
                flagged += 1
            rows.append([e["name"], domain, ats, slug, source, flag])

    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "domain", "ats", "slug", "source", "flag"])
        w.writerows(rows)

    companies_with_ats = len({r[1] for r in rows})
    print(f"{companies_with_ats} companies with >=1 ATS board; {len(rows)} (ats,slug) rows")
    print(f"  dropped {dropped_selfref} self-refs, {dropped_fp} known FPs; flagged {flagged}")
    print(f"-> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
