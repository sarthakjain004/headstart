"""Re-read the recovered Boards' listings and count the TECH postings among them.

The raw posting count is not the number that reaches users: ADR-0017's post-hoc gate keeps only
the tech subset, so a recovered Board contributes `is_tech` rows, not rows. Arguments match
`tech_filter.filter_jobs` exactly — title AND department — because title-only is a different
number (airapps: 206 vs 216).

Writes a JSON summary beside the raw re-probe; prints per Board as it goes.
"""

import json
import re
import sys

import requests

sys.path.insert(0, "src")
from headstart.tech_filter import is_tech

UA = "Mozilla/5.0 (compatible; HeadStart/1.0; +https://github.com/imPoseidon/HeadStart)"
with open(
    "experiment/quarantine-redemption/artifacts/2026-09-16_quarantined-reprobe.jsonl"
) as fh:
    recs = [json.loads(line) for line in fh]
ok = sorted([r for r in recs if r.get("status") == 200], key=lambda r: r["board"])

out = []
for r in ok:
    board, ats, url = r["board"], r["ats"], r["url"]
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=90)
        if ats == "trakstar":
            titles = re.findall(
                r'<a[^>]+href="[^"]*/jobs/[^"]*"[^>]*>([^<]{3,120})</a>', resp.text
            )
            depts = [None] * len(titles)
        else:
            d = resp.json()
            jobs = d.get("jobs", d if isinstance(d, list) else [])
            if ats == "greenhouse":
                titles = [j.get("title") for j in jobs]
                depts = [
                    ", ".join(x.get("name", "") for x in (j.get("departments") or []))
                    or None
                    for j in jobs
                ]
            else:  # ashby posting API
                titles = [j.get("title") for j in jobs]
                depts = [j.get("department") or j.get("team") for j in jobs]
        n = len(titles)
        t = sum(1 for ti, de in zip(titles, depts) if is_tech(ti, de))
        out.append(
            (board, n, t, [ti for ti, de in zip(titles, depts) if is_tech(ti, de)][:3])
        )
        print(
            f"{board:32s} {n:5d} postings  {t:5d} tech  {100 * t / n if n else 0:5.1f}%",
            flush=True,
        )
    except Exception as e:  # noqa: BLE001 - one bad board must not stop the sweep
        print(f"{board:32s} ERR {type(e).__name__}: {str(e)[:80]}", flush=True)

tot_n = sum(o[1] for o in out)
tot_t = sum(o[2] for o in out)
boards_any = sum(1 for o in out if o[1])
boards_tech = sum(1 for o in out if o[2])
print(f"\nTOTAL  {tot_n} postings, {tot_t} tech ({100 * tot_t / tot_n:.1f}%)")
print(f"Boards with >=1 posting: {boards_any}; with >=1 TECH posting: {boards_tech}")
print("\nper-board tech, desc:")
for b, n, t, ex in sorted(out, key=lambda o: -o[2]):
    if t:
        print(f"  {b:32s} {t:5d} tech / {n:5d}   e.g. {ex[:2]}")
print("\nzero-tech boards that answered 200 with postings:")
for b, n, t, ex in sorted(out, key=lambda o: -o[1]):
    if n and not t:
        print(f"  {b:32s} 0 tech / {n:5d}")

with open(
    "experiment/quarantine-redemption/artifacts/2026-09-16_recovered-tech-share.json",
    "w",
) as fh:
    json.dump(
        {
            "boards_200": len(ok),
            "boards_with_postings": boards_any,
            "boards_with_tech": boards_tech,
            "postings_raw": tot_n,
            "postings_tech": tot_t,
            "per_board": [{"board": b, "postings": n, "tech": t} for b, n, t, _ in out],
        },
        fh,
        indent=2,
    )
