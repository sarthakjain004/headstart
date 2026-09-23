"""For every hiring Board: `/` and its first posting page, both asked the way a browser asks
(`Accept: text/html,...`), redirects off. Shows whether a Board that lists postings serves them to
a browser. Writes artifacts/hiring_html.csv."""
import csv, json, collections
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
H = {"User-Agent": "headstart/0.1"}
HTML = {**H, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
rows = [r for r in csv.DictReader(open("../../data/validate/liveness/pinpoint.csv")) if r["status"] == "live" and r["jobs"] not in ("", "0")]
def one(r):
    s = r["tenant"]
    try:
        d = json.loads(requests.get(f"https://{s}.pinpointhq.com/postings.json", headers=H, timeout=30).content)["data"]
        root = requests.get(f"https://{s}.pinpointhq.com/", headers=HTML, allow_redirects=False, timeout=30)
        uuid = d[0]["url"].rsplit("/", 1)[1] if d else ""
        pg = requests.get(f"https://{s}.pinpointhq.com/en/postings/{uuid}", headers=HTML, allow_redirects=False, timeout=30) if uuid else None
        return [s, len(d), root.status_code, root.headers.get("location", ""), pg.status_code if pg else "", "JobPosting" in (pg.text if pg else "")]
    except Exception as e:
        return [s, "", type(e).__name__, "", "", ""]
w = csv.writer(open("artifacts/hiring_html.csv", "w", newline="")); w.writerow(["slug", "postings", "root_status", "root_location", "page_status", "page_has_ld"])
c = collections.Counter()
with ThreadPoolExecutor(8) as ex:
    for f in as_completed([ex.submit(one, r) for r in rows]):
        row = f.result(); w.writerow(row); c[(row[2], row[4], row[5])] += 1
print(len(rows), c)
