"""JSON listing ids vs the tenant's sitemap.xml `/p/` ids and the portal root page's `/p/` links.
Largest 10 Boards plus 60 random hiring Boards and 20 random live-empty Boards."""
import json,random,re,collections
from concurrent.futures import ThreadPoolExecutor
from curl_cffi import requests
R=[json.loads(l) for l in open('2026-09-23_pool_census_ramp.jsonl')]
hire=sorted([r for r in R if r['status']==200 and r['rows']],key=lambda r:-r['n'])
empty=[r for r in R if r['status']==200 and not r['rows']]
random.seed(4)
smp=hire[:10]+random.sample(hire[10:],60)+random.sample(empty,20)
H={'User-Agent':'headstart/0.1'}
def ids(text): return {m.split('-')[0] for m in re.findall(r'/p/([0-9a-f]{12,14}(?:-[^"<?/\s]*)?)',text)}
def one(r):
    s=r['slug']
    j=requests.get(f'https://{s}.breezy.hr/json',headers=H,timeout=60).json()
    jid={x['id'] for x in j}
    sm=requests.get(f'https://{s}.breezy.hr/sitemap.xml',headers=H,timeout=60).text
    root=requests.get(f'https://{s}.breezy.hr/',headers=H,timeout=60).text
    return dict(slug=s,json=len(jid),sitemap=len(ids(sm)),root=len(ids(root)),
                json_not_sitemap=len(jid-ids(sm)),sitemap_not_json=len(ids(sm)-jid),
                json_not_root=len(jid-ids(root)),root_not_json=len(ids(root)-jid),
                census=r['n'])
with ThreadPoolExecutor(8) as ex: res=list(ex.map(one,smp))
json.dump(res,open('hidden_check.json','w'),indent=0)
for x in res[:10]: print(x)
agree=collections.Counter((x['json_not_sitemap']==0 and x['sitemap_not_json']==0, x['json_not_root']==0 and x['root_not_json']==0) for x in res); print(agree)
for x in res[10:]:
    if x['json_not_sitemap'] or x['sitemap_not_json'] or x['json_not_root'] or x['root_not_json']: print(x)
