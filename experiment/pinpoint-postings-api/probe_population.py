"""Fetch /postings.json (no redirects) for every pool + seed slug; record status, bytes,
posting count, Location; save each 200 body under artifacts/listings/{slug}.json.

Run: python3 probe_population.py  (writes artifacts/population.csv, streams per slug)"""
import csv, json, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import requests

HERE = Path(__file__).parent
ART = HERE / "artifacts"
SEED = Path(sys.argv[1]) if len(sys.argv) > 1 else None
slugs = {r["tenant"].lower() for r in csv.DictReader(open(HERE.parents[1] / "data/ats-tenants-merged/pinpoint.csv"))}
if SEED:
    slugs |= {r["slug"].lower() for r in csv.DictReader(open(SEED))}

def one(slug):
    t = time.time()
    try:
        r = requests.get(f"https://{slug}.pinpointhq.com/postings.json", headers={"User-Agent": "headstart/0.1"},
                         allow_redirects=False, timeout=30)
    except Exception as e:
        return slug, "ERR", 0, None, type(e).__name__, time.time() - t
    n = None
    if r.status_code == 200:
        try:
            n = len(r.json()["data"])
            (ART / "listings" / f"{slug}.json").write_bytes(r.content)
        except Exception:
            n = "unparseable"
    return slug, r.status_code, len(r.content), n, r.headers.get("location", ""), time.time() - t

out = csv.writer(open(ART / "population.csv", "w", newline=""))
out.writerow(["slug", "status", "bytes", "postings", "location", "secs"])
with ThreadPoolExecutor(8) as ex:
    for f in as_completed([ex.submit(one, s) for s in sorted(slugs)]):
        row = f.result()
        out.writerow(row)
        print(*row, sep="\t", flush=True)
