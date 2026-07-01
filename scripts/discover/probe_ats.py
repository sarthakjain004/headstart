"""Probe candidate India ATS/HRMS provider domains against one CC index to see which
expose crawlable per-tenant subdomains (i.e. are worth adding to cc_miner's DOMAINS).
Reuses cc_miner.curl (curl via WARP, with throttle handling). Run: python -u probe_ats.py
"""

import json
import re

import cc_miner

LATEST = json.loads(cc_miner.curl("https://index.commoncrawl.org/collinfo.json"))[0][
    "cdx-api"
]
print("probing against", LATEST.split("/")[-1], flush=True)

# Candidate India-origin / India-heavy ATS + HRMS platforms (subdomain-per-tenant style),
# plus a few global subdomain-style boards commonly used by Indian employers.
CANDIDATES = [
    "peoplestrong.com",
    "hrone.cloud",
    "zinghr.com",
    "myadrenalin.com",
    "qandle.com",
    "sumhr.com",
    "factohr.com",
    "kredily.com",
    "pockethrms.com",
    "beehivehcm.com",
    "empxtrack.com",
    "ceipal.com",
    "recooty.com",
    "jobsoid.com",
    "ismartrecruit.com",
    "turbohire.co",
    "pitchnhire.com",
    "talent500.co",
    "skillate.com",
    "springrecruit.com",
    "expertia.ai",
    "jobma.com",
    "talview.com",
    "zwayam.com",
    "ripplehire.com",
    "workable.com",
    "recruitee.com",
    "smartrecruiters.com",
    "hrmantra.com",
    "keka.hire",
]

results = []
for dom in CANDIDATES:
    body = cc_miner.curl(
        f"{LATEST}?url={dom}&matchType=domain&output=json&fl=url&limit=4000"
    )
    hosts = set()
    for line in body.splitlines():
        try:
            u = json.loads(line)["url"]
        except Exception:
            continue
        m = re.match(r"https?://([^/]+)", u)
        if m and m.group(1).lower().endswith("." + dom):
            lab = m.group(1).lower()[: -(len(dom) + 1)].split(".")[0]
            if lab and lab not in cc_miner.INFRA:
                hosts.add(m.group(1).lower())
    results.append((len(hosts), dom, sorted(hosts)[:3]))
    print(f"  {dom:22} {len(hosts):>5}  e.g. {sorted(hosts)[:3]}", flush=True)

print("\n=== productive (>=3 tenants), ranked ===", flush=True)
for n, dom, sample in sorted(results, reverse=True):
    if n >= 3:
        print(f"  {dom:22} {n:>5}", flush=True)
