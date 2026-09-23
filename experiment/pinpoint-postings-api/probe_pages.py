"""Fetch 2 posting pages from each of 40 random hiring boards, twice ~5 s apart; record raw and
gzip bytes, JSON-LD keys, datePosted (both fetches), jobLocation shape, and addressCountry.
Writes artifacts/pages.jsonl (one row per posting), streams as it goes."""
import json, glob, random, re, time, gzip
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
random.seed(7)
H = {"User-Agent": "headstart/0.1", "Accept-Encoding": "gzip"}
boards = [f for f in glob.glob("artifacts/listings/*.json") if json.load(open(f))["data"]]
picks = []
for f in random.sample(boards, 40):
    d = json.load(open(f))["data"]
    for p in random.sample(d, min(2, len(d))):
        picks.append((f.split("/")[-1][:-5], p))
LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
def ld(html):
    for m in LD.finditer(html):
        d = json.loads(m.group(1))
        if isinstance(d, dict) and d.get("@type") == "JobPosting":
            return d
def one(slug, p):
    r1 = requests.get(p["url"], headers=H, timeout=30)
    wire = int(r1.headers.get("content-length") or 0)
    time.sleep(5)
    r2 = requests.get(p["url"], headers=H, timeout=30)
    a, b = ld(r1.text), ld(r2.text)
    if slug in ("workwithus",) or random.random() < 0.1:
        open(f"artifacts/pages/{slug}_{p['id']}.html", "w").write(r1.text)
    return {"slug": slug, "id": p["id"], "status": r1.status_code, "final_url": r1.url, "raw": len(r1.content),
            "gzip": len(gzip.compress(r1.content)), "wire_cl": wire, "ld_keys": sorted(a) if a else None,
            "date1": a and a.get("datePosted"), "date2": b and b.get("datePosted"),
            "loc": a and a.get("jobLocation"), "valid": a and a.get("validThrough"),
            "salary": a and a.get("baseSalary"), "emp": a and a.get("employmentType"),
            "listing_desc_len": len(p.get("description") or ""), "ld_desc_len": len((a or {}).get("description") or ""),
            "deadline_at": p.get("deadline_at")}
out = open("artifacts/pages.jsonl", "w")
with ThreadPoolExecutor(8) as ex:
    for f in as_completed([ex.submit(one, s, p) for s, p in picks]):
        try: row = f.result()
        except Exception as e: row = {"err": repr(e)}
        out.write(json.dumps(row) + "\n"); out.flush()
        print(row.get("slug"), row.get("status"), row.get("raw"), row.get("date1"), row.get("date2"), flush=True)
