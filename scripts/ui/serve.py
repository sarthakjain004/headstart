"""Minimal localhost UI for the Wellfound semantic search (dev demo).

Loads nomic + the LanceDB ``wellfound`` table once at startup, serves a search page at ``/`` and a
JSON endpoint at ``/search`` that runs filter-then-rank (ADR-0008): encode the query with the
``search_query:`` prefix (ADR-0005), pre-filter on the typed metadata (remote / employment_type /
min_years), rank the survivors by cosine. Wellfound-only — that's the only table in the index.

Run:  python scripts/ui/serve.py    then open  http://localhost:8000
"""

from __future__ import annotations

from pathlib import Path

import lancedb
import torch
from flask import Flask, jsonify, request
from sentence_transformers import SentenceTransformer

_DB = Path(__file__).resolve().parents[2] / "data" / "lancedb"
_TABLE = "wellfound"
_MODEL = "nomic-ai/nomic-embed-text-v1.5"
_QUERY_PREFIX = "search_query: "

print("loading model + index ...", flush=True)
_device = "mps" if torch.backends.mps.is_available() else "cpu"
_model = SentenceTransformer(_MODEL, trust_remote_code=True, device=_device)
if _device == "mps":
    _model = _model.half()
_table = lancedb.connect(_DB).open_table(_TABLE)
print(f"ready: {_table.count_rows()} jobs on {_device}", flush=True)

app = Flask(__name__)


def _search(query: str, remote: bool, emp_type: str | None, max_years: int | None, k: int) -> list[dict]:
    qv = _model.encode([_QUERY_PREFIX + query], normalize_embeddings=True)[0].astype("float32")
    search = _table.search(qv).metric("cosine")
    filters = []
    if remote:
        filters.append("remote = true")
    if emp_type:
        filters.append(f"employment_type = '{emp_type}'")
    if max_years is not None:
        filters.append(f"(min_years <= {max_years} OR min_years IS NULL)")
    if filters:
        search = search.where(" AND ".join(filters), prefilter=True)
    rows = search.limit(k).to_list()
    return [{
        "score": round(1 - r["_distance"], 3),
        "title": r["title"], "company": r["company"], "location": r.get("location"),
        "remote": r["remote"], "employment_type": r.get("employment_type"),
        "min_years": r.get("min_years"), "salary": r.get("salary"), "url": r.get("url"),
    } for r in rows]


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    max_years = request.args.get("max_years")
    return jsonify(_search(
        q,
        remote=request.args.get("remote") == "true",
        emp_type=request.args.get("type") or None,
        max_years=int(max_years) if max_years else None,
        k=int(request.args.get("k") or 20),
    ))


@app.route("/")
def index():
    return _PAGE


_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>HeadStart — Semantic Job Search</title>
<style>
  :root { --bg:#0f1117; --card:#1a1d27; --line:#2a2e3c; --fg:#e6e8ee; --mut:#9aa0b4; --acc:#6ea8fe; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; }
  .wrap { max-width:820px; margin:0 auto; padding:32px 20px 60px; }
  h1 { font-size:22px; margin:0 0 4px; } .sub { color:var(--mut); margin:0 0 24px; font-size:13px; }
  .bar { display:flex; gap:8px; margin-bottom:12px; }
  input[type=text]{ flex:1; padding:12px 14px; border-radius:10px; border:1px solid var(--line);
    background:var(--card); color:var(--fg); font-size:15px; }
  button{ padding:12px 20px; border:0; border-radius:10px; background:var(--acc); color:#0b1020;
    font-weight:600; cursor:pointer; }
  .filters{ display:flex; gap:14px; flex-wrap:wrap; align-items:center; margin-bottom:22px;
    color:var(--mut); font-size:13px; }
  .filters select,.filters input{ background:var(--card); color:var(--fg); border:1px solid var(--line);
    border-radius:8px; padding:6px 8px; font-size:13px; }
  .card{ border:1px solid var(--line); background:var(--card); border-radius:12px; padding:14px 16px;
    margin-bottom:10px; }
  .card a{ color:var(--fg); text-decoration:none; font-weight:600; font-size:16px; }
  .card a:hover{ color:var(--acc); }
  .co{ color:var(--mut); font-size:13px; margin:2px 0 8px; }
  .tags{ display:flex; gap:6px; flex-wrap:wrap; }
  .tag{ font-size:11px; padding:3px 8px; border-radius:999px; background:#222634; color:var(--mut); }
  .tag.score{ background:#1d2b1d; color:#7bd88f; } .tag.rem{ background:#1c2636; color:var(--acc); }
  .empty{ color:var(--mut); text-align:center; padding:40px; }
</style></head><body><div class="wrap">
  <h1>HeadStart — Semantic Job Search</h1>
  <p class="sub">nomic embeddings → LanceDB filter-then-rank · Wellfound corpus (English)</p>
  <div class="bar">
    <input id="q" type="text" placeholder="e.g. backend engineer at a climate startup" autofocus>
    <button onclick="go()">Search</button>
  </div>
  <div class="filters">
    <label><input type="checkbox" id="remote"> remote only</label>
    <label>type
      <select id="type"><option value="">any</option><option>full-time</option>
        <option>contract</option><option>internship</option><option>cofounder</option></select>
    </label>
    <label>max years <input id="maxyears" type="number" min="0" style="width:60px"></label>
  </div>
  <div id="results"></div>
</div>
<script>
const el = s => document.getElementById(s);
el('q').addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
async function go(){
  const q = el('q').value.trim(); if(!q) return;
  const p = new URLSearchParams({ q, k: 20 });
  if (el('remote').checked) p.set('remote','true');
  if (el('type').value) p.set('type', el('type').value);
  if (el('maxyears').value) p.set('max_years', el('maxyears').value);
  el('results').innerHTML = '<div class="empty">searching…</div>';
  const rows = await (await fetch('/search?'+p)).json();
  if(!rows.length){ el('results').innerHTML = '<div class="empty">no matches</div>'; return; }
  el('results').innerHTML = rows.map(r => `
    <div class="card">
      <a href="${r.url||'#'}" target="_blank">${r.title||''}</a>
      <div class="co">${r.company||''}${r.location? ' · '+r.location : ''}</div>
      <div class="tags">
        <span class="tag score">${r.score}</span>
        ${r.remote? '<span class="tag rem">remote</span>':''}
        ${r.employment_type? '<span class="tag">'+r.employment_type+'</span>':''}
        ${r.min_years!=null? '<span class="tag">'+r.min_years+'+ yrs</span>':''}
        ${r.salary? '<span class="tag">'+r.salary+'</span>':''}
      </div>
    </div>`).join('');
}
</script></body></html>"""


if __name__ == "__main__":
    app.run(port=8000, debug=False)
