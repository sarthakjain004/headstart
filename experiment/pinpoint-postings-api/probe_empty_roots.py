"""For every Board whose /postings.json is 200 with no postings: GET / with a browser Accept and
no redirects, and record the status and Location. Writes artifacts/empty_roots.csv."""
import csv, json, collections
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
H = {"User-Agent": "headstart/0.1"}
HTML = {**H, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
slugs = [r["tenant"] for r in csv.DictReader(open("../../data/validate/liveness/pinpoint.csv"))]
def one(s):
    try:
        j = requests.get(f"https://{s}.pinpointhq.com/postings.json", headers=H, allow_redirects=False, timeout=30)
        if j.status_code != 200 or json.loads(j.content)["data"]:
            return None
        r = requests.get(f"https://{s}.pinpointhq.com/", headers=HTML, allow_redirects=False, timeout=30)
        b = requests.get(f"https://{s}.pinpointhq.com/", headers=H, allow_redirects=False, timeout=30)
        return [s, r.status_code, r.headers.get("location", ""), b.status_code]
    except Exception as e:
        return [s, type(e).__name__, "", ""]
w = csv.writer(open("artifacts/empty_roots.csv", "w", newline="")); w.writerow(["slug", "html_status", "html_location", "bare_status"])
c = collections.Counter()
with ThreadPoolExecutor(8) as ex:
    for f in as_completed([ex.submit(one, s) for s in slugs]):
        row = f.result()
        if row: w.writerow(row); c[(row[1], row[3])] += 1
print(c)
