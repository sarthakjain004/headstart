import json,collections,random,re
from concurrent.futures import ThreadPoolExecutor
from curl_cffi import requests
R=[json.loads(l) for l in open('2026-09-23_pool_census_ramp.jsonl')]
rows=[(r['slug'],x) for r in R if r['status']==200 for x in r['rows']]
cls=collections.defaultdict(list)
for s,x in rows:
    L=x.get('location') or {}
    cls[(str(L.get('is_remote')), (L.get('remote_details') or {}).get('value'))].append(x)
random.seed(5)
def ld(x):
    h=requests.get(x['url'],headers={'User-Agent':'headstart/0.1'},timeout=30).text
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>',h,re.S):
        try: d=json.loads(m.group(1))
        except Exception: continue
        if d.get('@type')=='JobPosting': return d.get('jobLocationType'), bool(re.search(r'fa-wifi|Remote|Hybrid', h[:20000] and h[h.find('class="location"'):h.find('class="location"')+400] if 'class="location"' in h else ''))
    return 'nold',None
out={}
with ThreadPoolExecutor(8) as ex:
    for k,v in cls.items():
        smp=random.sample(v,min(12,len(v)))
        out[str(k)]=collections.Counter(str(r) for r in ex.map(ld,smp))
for k,v in out.items(): print(k,dict(v))
