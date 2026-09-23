"""Listing bytes, postings and `is_tech(title, department)` over every Hiring Board in the committed
ledger (after config.EXCLUDED_BOARDS), for the ADR-0158 storage bar. Streams one line per Board;
writes artifacts/ledger_tech_yield.csv."""
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from headstart.config import load_active_companies
from headstart.tech_filter import is_tech
refs = [r for r in load_active_companies("../../data/validate/liveness", min_jobs=1) if r.ats == "pinpoint"]
def one(slug):
    r = requests.get(f"https://{slug}.pinpointhq.com/postings.json", headers={"User-Agent": "headstart/0.1"}, timeout=60)
    d = r.json()["data"]
    tech = sum(is_tech(p["title"], ((p.get("job") or {}).get("department") or {}).get("name")) for p in d)
    return slug, len(r.content), len(d), tech
w = csv.writer(open("artifacts/ledger_tech_yield.csv", "w", newline="")); w.writerow(["slug", "bytes", "postings", "tech"])
tot = [0, 0, 0]
with ThreadPoolExecutor(8) as ex:
    for f in as_completed([ex.submit(one, r.slug) for r in refs]):
        try: row = f.result()
        except Exception as e: print("ERR", e, flush=True); continue
        w.writerow(row); tot = [a + b for a, b in zip(tot, row[1:])]
print(len(refs), "boards; bytes, postings, tech =", tot, flush=True)
