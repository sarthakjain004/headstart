"""Per-key fill rate over every saved /postings.json listing (artifacts/listings/)."""
import json, collections, glob
cnt = collections.Counter(); tot = 0; vals = collections.defaultdict(collections.Counter)
nested = collections.Counter()
for f in glob.glob("artifacts/listings/*.json"):
    for p in json.load(open(f))["data"]:
        tot += 1
        for k, v in p.items():
            if v not in (None, "", [], {}):
                cnt[k] += 1
            if k in ("employment_type", "employment_type_text", "workplace_type", "workplace_type_text",
                     "compensation_frequency", "compensation_currency", "compensation_visible"):
                vals[k][v] += 1
            if isinstance(v, dict):
                for kk, vv in v.items():
                    if vv not in (None, "", [], {}):
                        nested[f"{k}.{kk}"] += 1
print("postings", tot)
for k, c in sorted(cnt.items(), key=lambda x: -x[1]):
    print(f"{k:40} {c:6} {100*c/tot:5.1f}%")
for k, c in sorted(nested.items(), key=lambda x: -x[1]):
    print(f"  {k:38} {c:6} {100*c/tot:5.1f}%")
for k, c in vals.items():
    print(k, c.most_common(20))
