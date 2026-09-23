"""Seed 11, 120 random hiring Boards, <=2 postings each: compares html_to_text of listing description vs the detail's
JSON-LD description and the page's <div class="description">, and inspects pages with no JSON-LD."""
import json,collections,random,re
from headstart.models import html_to_text
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests
import os
CENSUS = os.environ.get('BREEZY_CENSUS', 'artifacts/2026-09-23_pool_census_ramp.jsonl')  # 227 MB ramp.py capture, kept out of git
R=[json.loads(l) for l in open(CENSUS)]
boards=[r for r in R if r['status']==200 and r['rows']]
random.seed(11); bs=random.sample(boards,120)
smp=[]
for b in bs:
    for x in random.sample(b['rows'],min(2,len(b['rows']))): smp.append((b['slug'],x))
def one(t):
    s,x=t
    r=requests.get(x['url'],headers={'User-Agent':'headstart/0.1'},timeout=30,allow_redirects=False)
    rec={'slug':s,'id':x['id'],'status':r.status_code,'loc':r.headers.get('location')}
    if r.status_code!=200: return rec
    h=r.text
    a=html_to_text(x.get('description') or '')
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>',h,re.S):
        try: d=json.loads(m.group(1))
        except Exception: continue
        if d.get('@type')=='JobPosting':
            b=html_to_text(d.get('description') or '')
            rec.update(ld=True, text_eq=a==b, la=len(a), lb=len(b), datePosted=d.get('datePosted'), pub=x.get('published_date'))
            if a!=b:
                import difflib
                sm=difflib.SequenceMatcher(None,a,b); rec['ratio']=round(sm.ratio(),4)
                rec['ops']=[(o,a[i1:i2][:60],b[j1:j2][:60]) for o,i1,i2,j1,j2 in sm.get_opcodes() if o!='equal'][:3]
            return rec
    rec['ld']=False; rec['title']=re.findall(r'<title>(.*?)</title>',h)[:1]; rec['has_desc_div']='class="description"' in h
    return rec
res=[]
with ThreadPoolExecutor(8) as ex:
    for fu in as_completed([ex.submit(one,t) for t in smp]):
        res.append(fu.result()); print(res[-1], flush=True)
json.dump(res,open('artifacts/2026-09-23_desc_listing_vs_detail.json','w'))
print(len(res), collections.Counter((r['status'],r.get('ld'),r.get('text_eq')) for r in res))
for r in res:
    if r.get('ld') and not r['text_eq']: print(r['slug'],r['la'],r['lb'],r.get('ratio'),r.get('ops'))
for r in res:
    if r.get('ld') is False or r['status']!=200: print('NOLD',r)
