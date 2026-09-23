"""One /postings.json request per distinct live tenant at a given concurrency; tallies statuses
and exception classes. Measures whether refusals span tenants. Usage: probe_spanning.py CONC N"""
import sys, time, csv, collections
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
conc, n = int(sys.argv[1]), int(sys.argv[2])
ledger = "../../data/validate/liveness/pinpoint.csv"
slugs = [r["tenant"] for r in csv.DictReader(open(ledger)) if r["status"] == "live"][:n]
def one(s):
    try:
        return requests.get(f"https://{s}.pinpointhq.com/postings.json", headers={"User-Agent": "headstart/0.1"}, timeout=30, allow_redirects=False).status_code
    except Exception as e:
        return type(e).__name__
t = time.time(); c = collections.Counter()
with ThreadPoolExecutor(conc) as ex:
    for f in as_completed([ex.submit(one, s) for s in slugs]):
        c[f.result()] += 1
print(f"conc={conc} n={len(slugs)} {len(slugs)/(time.time()-t):.1f} req/s", dict(c), flush=True)
