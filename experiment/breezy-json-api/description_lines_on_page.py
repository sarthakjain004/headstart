"""The no-JSON-LD pages from description_listing_vs_detail: is every line of the listing's description text on the page?"""
import json
from headstart.models import html_to_text
from curl_cffi import requests
import os
CENSUS = os.environ.get('BREEZY_CENSUS', 'artifacts/2026-09-23_pool_census_ramp.jsonl')  # 227 MB ramp.py capture, kept out of git
R={(r['slug']):r for r in map(json.loads,open(CENSUS)) if r['status']==200}
res=json.load(open('artifacts/2026-09-23_desc_listing_vs_detail.json'))
full=0;n=0
for r in res:
    if r.get('ld') is not False: continue
    x=[x for x in R[r['slug']]['rows'] if x['id']==r['id']][0]
    page=html_to_text(requests.get(x['url'],headers={'User-Agent':'headstart/0.1'},timeout=30).text)
    lines=[l.strip() for l in (html_to_text(x['description']) or '').splitlines() if l.strip()]
    hit=sum(1 for l in lines if l in page); n+=1; full+= hit==len(lines)
    print(r['slug'],hit,len(lines),flush=True)
print('all lines on page:',full,'/',n)
