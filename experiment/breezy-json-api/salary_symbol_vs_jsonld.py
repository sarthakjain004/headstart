"""For postings whose listing salary uses a bare `$`, the detail JSON-LD's baseSalary.currency vs
the posting's location country. All non-US-country `$` postings (capped 250) + 150 random US ones;
also a sample of every other symbol."""
import json,collections,random,re
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests
import os
CENSUS = os.environ.get('BREEZY_CENSUS', 'artifacts/2026-09-23_pool_census_ramp.jsonl')  # 227 MB ramp.py capture, kept out of git
R=[json.loads(l) for l in open(CENSUS)]
rows=[(r['slug'],x) for r in R if r['status']==200 for x in r['rows'] if x.get('salary')]
def sym(s):
    m=re.match(r'(?:Up to )?([^\d\s]*)\d',s); return m.group(1) if m else None
def cc(x): return ((x.get('location') or {}).get('country') or {}).get('id')
random.seed(2)
dol=[t for t in rows if sym(t[1]['salary'])=='$']
nonus=[t for t in dol if cc(t[1])!='US']; us=[t for t in dol if cc(t[1])=='US']
other=collections.defaultdict(list)
for t in rows:
    s=sym(t[1]['salary'])
    if s!='$': other[s].append(t)
smp=random.sample(nonus,min(250,len(nonus)))+random.sample(us,150)
for s,v in other.items(): smp+=random.sample(v,min(8,len(v)))
print('dollar rows',len(dol),'non-US country',len(nonus), collections.Counter(cc(x) for _,x in nonus).most_common(12))
def one(t):
    s,x=t
    try: h=requests.get(x['url'],headers={'User-Agent':'headstart/0.1'},timeout=30).text
    except Exception as e: return (s,x['salary'],cc(x),'err',None)
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>',h,re.S):
        try: d=json.loads(m.group(1))
        except Exception: continue
        if d.get('@type')=='JobPosting':
            b=d.get('baseSalary') or {}
            return (s,x['salary'],cc(x),b.get('currency'),(b.get('value') or {}).get('unitText'))
    return (s,x['salary'],cc(x),'nold',None)
res=[]
with ThreadPoolExecutor(8) as ex:
    for fu in as_completed([ex.submit(one,t) for t in smp]):
        res.append(fu.result()); print(res[-1], flush=True)
json.dump(res,open('artifacts/2026-09-23_salary_symbol_vs_jsonld_currency.json','w'))
c=collections.Counter((sym(r[1]),r[2] if r[2] in('US','CA','AU') else ('other' if sym(r[1])=='$' else r[2]),r[3]) for r in res)
for k,v in sorted(c.items(),key=lambda kv:-kv[1]): print(v,k)
