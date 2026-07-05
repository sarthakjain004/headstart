"""HeadStart semantic search — HF Space app (ADR-0020).

Self-contained twin of ``scripts/ui/serve.py``: pulls the LanceDB ``jobs`` table from the
private HF dataset at startup (HF_TOKEN Space secret), loads the nomic encoder (baked into the
image), and serves the same filter-then-rank search page. The search conventions (model id,
task prefixes, filter clause) mirror ``headstart.search`` — keep them in lockstep.
"""

from __future__ import annotations

import os
from pathlib import Path

import lancedb
from flask import Flask, jsonify, request
from huggingface_hub import snapshot_download

MODEL = "nomic-ai/nomic-embed-text-v1.5"
QUERY_PREFIX = "search_query: "  # load-bearing (ADR-0005)
TABLE = "jobs"
DATASET = os.environ.get("HF_DATASET", "imPoseidon/headstart-index")
_STATE = Path("/app/state")
_MAX_K = 100  # cap the page size so a crafted k can't dump the whole table

print(f"pulling index from {DATASET} ...", flush=True)
snapshot_download(
    DATASET,
    repo_type="dataset",
    local_dir=_STATE,
    allow_patterns=["data/lancedb/*"],
    token=os.environ.get("HF_TOKEN"),
)

print("loading encoder ...", flush=True)
from sentence_transformers import SentenceTransformer  # noqa: E402 - after the cheap fail-fast download

_model = SentenceTransformer(MODEL, trust_remote_code=True, device="cpu")
_table = lancedb.connect(_STATE / "data" / "lancedb").open_table(TABLE)
print(f"ready: {_table.count_rows()} jobs", flush=True)

app = Flask(__name__)


def _build_filter(*, remote: bool, max_years: int | None) -> str | None:
    """The prod-table where-clause (mirrors headstart.search.build_filter's live filters)."""
    filters: list[str] = []
    if remote:
        filters.append("remote = true")
    if max_years is not None:
        filters.append(f"(min_years <= {int(max_years)} OR min_years IS NULL)")
    return " AND ".join(filters) if filters else None


def _search(query: str, where: str | None, k: int) -> list[dict]:
    qv = _model.encode([QUERY_PREFIX + query], normalize_embeddings=True)[0].astype(
        "float32"
    )
    search = _table.search(qv).metric("cosine")
    if where:
        search = search.where(where, prefilter=True)
    rows = search.limit(k).to_list()
    return [
        {
            "score": round(1 - r["_distance"], 3),
            "title": r["title"],
            "company": r["company"],
            "location": r.get("location"),
            "remote": r["remote"],
            "employment_type": r.get("employment_type"),
            "min_years": r.get("min_years"),
            "salary": r.get("salary"),
            "url": r.get("url"),
        }
        for r in rows
    ]


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    try:
        max_years = request.args.get("max_years")
        k = int(request.args.get("k") or 20)
        where = _build_filter(
            remote=request.args.get("remote") == "true",
            max_years=int(max_years) if max_years else None,
        )
    except ValueError:
        return jsonify({"error": "invalid filter"}), 400
    return jsonify(_search(q, where, max(1, min(k, _MAX_K))))


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
  /* Travelers-style glyph board shown while a search is in flight */
  .glyphboard{ display:grid; grid-template-columns:repeat(auto-fill,minmax(24px,1fr)); gap:2px 6px;
    background:#0a0903; border:1px solid #241f08; border-radius:12px; padding:18px 16px;
    min-height:280px; align-content:center; overflow:hidden;
    opacity:0; transition:opacity .25s ease; }
  .glyphboard.on{ opacity:1; }
  .glyphboard .g{ font:700 19px/1.35 ui-monospace,Menlo,Consolas,monospace; color:#d9a406;
    text-align:center; opacity:.72; text-shadow:0 0 6px rgba(233,180,10,.35);
    transition:opacity .18s linear; }
  .glyphboard .g.dim{ opacity:.26; }
  .glyphboard .g.bright{ opacity:1; color:#ffd21f; text-shadow:0 0 10px rgba(255,210,30,.8); }
  .glyphboard .g.ghost{ opacity:.85;
    text-shadow:2px 0 0 rgba(255,40,60,.55), -1px 0 6px rgba(233,180,10,.4); }
  .fadein{ animation:fadein .25s ease; }
  @keyframes fadein{ from{ opacity:0; } to{ opacity:1; } }
</style></head><body><div class="wrap">
  <h1>HeadStart — Semantic Job Search</h1>
  <p class="sub">nomic embeddings → LanceDB filter-then-rank · tech corpus (English) · refreshed nightly</p>
  <div class="bar">
    <input id="q" type="text" placeholder="e.g. backend engineer at a climate startup" autofocus>
    <button onclick="go()">Search</button>
  </div>
  <div class="filters">
    <label><input type="checkbox" id="remote"> remote only</label>
    <label>max years <input id="maxyears" type="number" min="0" style="width:60px"></label>
  </div>
  <div id="results"></div>
</div>
<script>
const el = s => document.getElementById(s);
// Job text is scraped third-party content — escape before it reaches innerHTML, and only
// allow http(s) hrefs (no javascript: URLs).
const esc = s => (s==null?'':String(s)).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = u => { const l=(u||'').toLowerCase(); return (l.startsWith('http://')||l.startsWith('https://'))? u : '#'; };
el('q').addEventListener('keydown', e => { if (e.key === 'Enter') go(); });

// Travelers-style loading board: a field of amber glyphs, each cell flickering to a new
// symbol on its own beat, a few flaring bright or ghosting red — until results resolve.
const GLYPHS = 'ᐃᐄᐅᐆᐊᐋᒉᒋᒌᒎᒐᒑᔐᔑᔒᔓᔔᕂᕃᕆᕇᕈᕋᘂᘃᘄᘇᘈᘉᘊᘔᗄᗅᗆᗇᗈᗉᗊᗋᗌᗎᗏᑌᑎᑐᑕᑫᑭᑯᑲᒡᒥᒧᒪᒫƎƆΞΠΣШЖИЧЯ0123456789';
const rg = () => GLYPHS[Math.random()*GLYPHS.length|0];
const rcls = () => { const r = Math.random();
  return r<.10 ? 'g bright' : r<.32 ? 'g dim' : r<.38 ? 'g ghost' : 'g'; };
let glyphTimer = null;
function glyphBoard(){
  clearInterval(glyphTimer);
  let cells = '';
  for (let i=0;i<260;i++) cells += `<span class="${rcls()}">${rg()}</span>`;
  el('results').innerHTML = `<div class="glyphboard" id="gb">${cells}</div>`;
  requestAnimationFrame(()=>{ const gb = el('gb'); if(gb) gb.classList.add('on'); });
  const spans = el('gb').querySelectorAll('span');
  glyphTimer = setInterval(()=>{
    for (let i=0;i<22;i++){
      const s = spans[Math.random()*spans.length|0];
      s.textContent = rg();
      s.className = rcls();
    }
  }, 90);
}
function resolveGlyphs(render){
  clearInterval(glyphTimer); glyphTimer = null;
  const gb = el('gb');
  if(!gb){ render(); return; }
  gb.classList.remove('on');           // fade the board out…
  setTimeout(()=>{                     // …then fade the results in
    render();
    const first = el('results').firstElementChild;
    if(first) first.classList.add('fadein');
  }, 260);
}
async function go(){
  const q = el('q').value.trim(); if(!q) return;
  const p = new URLSearchParams({ q, k: 20 });
  if (el('remote').checked) p.set('remote','true');
  if (el('maxyears').value) p.set('max_years', el('maxyears').value);
  const t0 = Date.now();
  glyphBoard();
  let rows;
  try { rows = await (await fetch('/search?'+p)).json(); }
  catch(e){ resolveGlyphs(()=>{ el('results').innerHTML = '<div class="empty">search failed — try again</div>'; }); return; }
  // hold the board briefly so a fast response doesn't strobe
  const hold = Math.max(0, 600 - (Date.now() - t0));
  setTimeout(()=> resolveGlyphs(()=> renderRows(rows)), hold);
}
function renderRows(rows){
  if(!rows.length){ el('results').innerHTML = '<div class="empty">no matches</div>'; return; }
  el('results').innerHTML = rows.map(r => `
    <div class="card">
      <a href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener">${esc(r.title)}</a>
      <div class="co">${esc(r.company)}${r.location? ' · '+esc(r.location) : ''}</div>
      <div class="tags">
        <span class="tag score">${Number(r.score)||0}</span>
        ${r.remote? '<span class="tag rem">remote</span>':''}
        ${r.employment_type? '<span class="tag">'+esc(r.employment_type)+'</span>':''}
        ${r.min_years!=null? '<span class="tag">'+(Number(r.min_years)||0)+'+ yrs</span>':''}
        ${r.salary? '<span class="tag">'+esc(r.salary)+'</span>':''}
      </div>
    </div>`).join('');
}
</script></body></html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860)
