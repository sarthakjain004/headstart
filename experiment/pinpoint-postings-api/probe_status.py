"""Status census of /postings.json (no redirects) and / for a list of slugs.
Usage: python3 probe_status.py slugs.txt out.csv   (streams one row per slug)"""
import csv, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
H = {"User-Agent": "headstart/0.1"}
def one(s):
    out = [s]
    for path in ("/postings.json", "/"):
        try:
            r = requests.get(f"https://{s}.pinpointhq.com{path}", headers=H, allow_redirects=False, timeout=30)
            n = ""
            if path == "/postings.json" and r.status_code == 200:
                try: n = len(r.json()["data"])
                except Exception: n = "unparseable"
            out += [r.status_code, len(r.content), n, r.headers.get("location", "")]
        except Exception as e:
            out += ["ERR", 0, "", type(e).__name__]
    return out
w = csv.writer(open(sys.argv[2], "w", newline=""))
w.writerow(["slug", "json_status", "json_bytes", "postings", "json_location", "root_status", "root_bytes", "_", "root_location"])
with ThreadPoolExecutor(8) as ex:
    for f in as_completed([ex.submit(one, s) for s in open(sys.argv[1]).read().split()]):
        row = f.result(); w.writerow(row); print(*row, sep="\t", flush=True)
