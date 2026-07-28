"""HeadStart semantic search — HF Space app (ADR-0020).

Self-contained twin of ``scripts/ui/serve.py``: pulls the LanceDB ``jobs`` table from the
private HF dataset at startup (HF_TOKEN Space secret), loads the nomic encoder (baked into the
image), and serves the same filter-then-rank search page. The search conventions (model id,
task prefixes, filter clause) mirror ``headstart.search`` — keep them in lockstep.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import geo  # India gazetteer — synced from src/headstart/geo.py by deploy-space.yml
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
# the ATSes actually present in the index — feeds the filter dropdown, grows as ingestion does
_ATSES = sorted(
    {r["ats"] for r in _table.search().select(["ats"]).limit(1_000_000).to_list()}
)
# The Space redeploys on merge, but `first_seen` only appears on the next pipeline run (ADR-0031).
# Filtering on a column the table lacks errors every query, so the feature stays dark until the
# column lands and then switches itself on — no coordinated deploy.
_HAS_FIRST_SEEN = "first_seen" in _table.schema.names
print(
    f"ready: {_table.count_rows()} jobs across {len(_ATSES)} ATSes"
    + ("" if _HAS_FIRST_SEEN else " (no first_seen column yet — 'new since' hidden)"),
    flush=True,
)

app = Flask(__name__)

# Canonical employment-type filters mapped onto the messy per-ATS raw values
# ("fulltime", "Full-time", "fulltime_permanent", "Permanent / Full-Time", …).
_ETYPE_CLAUSES = {
    "full-time": "(lower(employment_type) LIKE '%full%'"
    " OR lower(employment_type) LIKE '%permanent%')",
    "part-time": "lower(employment_type) LIKE '%part%'",
    "contract": "(lower(employment_type) LIKE '%contract%'"
    " OR lower(employment_type) LIKE '%freelance%')",
    "internship": "lower(employment_type) LIKE '%intern%'",
}


def _int_arg(name: str) -> int | None:
    """An optional integer query param, absent when unset. Raises ``ValueError`` on garbage, which
    ``/search`` turns into a 400 — the same handling every other filter already relies on."""
    raw = request.args.get(name)
    return int(raw) if raw else None


def _like(term: str) -> str:
    """A user term made safe for a quoted LIKE pattern: quotes doubled, length-capped."""
    return term[:60].replace("'", "''").lower()


# TEMPORARY (2026-07-07) — INTENDED FOR REMOVAL. Darwinbox rows scraped before the
# candidatev2 URL fix carry the old `/ms/candidate/careers/jobs/{id}` link, which on v2
# tenants redirects to the careers home instead of the job. The stored data self-heals only
# as those postings turn over (sync leaves re-seen ids untouched — headstart.ingest.index_plan), so this
# rewrites the derivable URL at serve time as a stopgap. Remove once the darwinbox rows have
# healed (or once sync refreshes changed metadata for re-seen ids — the proper fix).
# Caveat: a legacy `new_careers=false` tenant's old-format URL would be wrongly rewritten,
# but none exist today (60/60 surveyed are v2).
_DARWINBOX_OLD = "/ms/candidate/careers/jobs/"
_DARWINBOX_NEW = "/ms/candidatev2/main/careers/jobDetails/"


def _canonical_url(ats: str | None, url: str | None) -> str | None:
    """Serve-time URL normalization; only darwinbox's stale links are rewritten (see above)."""
    if ats == "darwinbox" and url and _DARWINBOX_OLD in url:
        return url.replace(_DARWINBOX_OLD, _DARWINBOX_NEW, 1)
    return url


def _build_filter(
    *,
    remote: bool,
    max_years: int | None,
    ats: str | None,
    etype: str | None,
    india: str | None,
    location: str | None,
    company: str | None,
    has_salary: bool,
    posted_within: int | None,
    seen_within: int | None,
) -> str | None:
    """The prod-table where-clause. `headstart.search.build_filter` carries the same three core
    filters (remote / employment_type / max_years); the rest — location, salary, recency — live only
    here, so the two have diverged and this one is the reference (ADR-0031)."""
    filters: list[str] = []
    if remote:
        filters.append("remote = true")
    if max_years is not None:
        filters.append(f"(min_years <= {int(max_years)} OR min_years IS NULL)")
    if ats in _ATSES:  # whitelist — never interpolated from free text
        filters.append(f"ats = '{ats}'")
    if etype in _ETYPE_CLAUSES:
        filters.append(_ETYPE_CLAUSES[etype])
    if india:
        clause = geo.where(india)  # canonical-place lookup — unknown values are ignored
        if clause:
            filters.append(clause)
    if location:
        filters.append(f"lower(location) LIKE '%{_like(location)}%'")
    if company:
        filters.append(f"lower(company) LIKE '%{_like(company)}%'")
    if has_salary:
        filters.append("salary IS NOT NULL")
    if posted_within is not None:
        # posted_at is a raw string; ISO-prefixed values (97%) compare correctly. The LIKE
        # shape guard excludes the rest — non-ISO forms like darwinbox's legacy
        # '21-Apr-2026' sort lexicographically ABOVE any ISO cutoff and would otherwise
        # leak into every window.
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=int(posted_within))
        ).strftime("%Y-%m-%d")
        filters.append(f"(posted_at >= '{cutoff}' AND posted_at LIKE '____-__-__%')")
    if seen_within is not None and _HAS_FIRST_SEEN:
        # In HOURS, not days: this window is meant to be shorter than one pipeline cycle. No shape
        # guard is needed here — unlike `posted_at`, we write `first_seen` ourselves, so it is
        # always ISO-8601 UTC. Rows predating the column are null, and `NULL >= '…'` is never true,
        # so they drop out on their own (ADR-0031).
        since = (
            datetime.now(timezone.utc) - timedelta(hours=int(seen_within))
        ).isoformat(timespec="seconds")
        filters.append(f"first_seen >= '{since}'")
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
            "ats": r.get("ats"),
            "posted_at": r.get("posted_at"),
            "first_seen": r.get("first_seen"),
            "url": _canonical_url(
                r.get("ats"), r.get("url")
            ),  # temporary; see _canonical_url
        }
        for r in rows
    ]


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    try:
        k = int(request.args.get("k") or 20)
        where = _build_filter(
            remote=request.args.get("remote") == "true",
            max_years=_int_arg("max_years"),
            ats=(request.args.get("ats") or "").strip() or None,
            etype=(request.args.get("etype") or "").strip() or None,
            india=(request.args.get("india") or "").strip().lower() or None,
            location=(request.args.get("location") or "").strip() or None,
            company=(request.args.get("company") or "").strip() or None,
            has_salary=request.args.get("has_salary") == "true",
            posted_within=_int_arg("posted_within"),
            seen_within=_int_arg("seen_within"),
        )
    except ValueError:
        return jsonify({"error": "invalid filter"}), 400
    return jsonify(_search(q, where, max(1, min(k, _MAX_K))))


@app.route("/")
def index():
    return _PAGE


_TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>HeadStart — Semantic Job Search</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#0a0a0b; --panel:#141416; --panel2:#1a1a1d; --line:#232327; --line2:#2e2e34;
    --fg:#f3f3f5; --mut:#96969f; --red:#e50914; --red-hi:#ff2c38; --red-tint:rgba(229,9,20,.12);
    --r-lg:18px; --r-md:14px;
  }
  *{ box-sizing:border-box; }
  html,body{ margin:0; }
  body{ background:var(--bg); color:var(--fg);
    font:15px/1.55 Inter,-apple-system,Segoe UI,Roboto,sans-serif;
    background-image:radial-gradient(900px 380px at 50% -140px, rgba(229,9,20,.08), transparent 70%); }
  .wrap{ max-width:880px; margin:0 auto; padding:44px 22px 72px; }
  .brand{ display:flex; align-items:center; gap:10px; }
  .mark{ width:14px; height:14px; background:var(--red); border-radius:4px;
    box-shadow:0 0 18px rgba(229,9,20,.55); }
  h1{ font-size:24px; font-weight:800; letter-spacing:-.02em; margin:0; }
  h1 .red{ color:var(--red); }
  .sub{ color:var(--mut); margin:6px 0 30px; font-size:13.5px; }
  .sub b{ color:var(--fg); font-weight:600; }

  .bar{ display:flex; gap:10px; margin-bottom:14px; }
  input,select,button{ font:inherit; }
  .bar input{ flex:1; padding:15px 18px; border-radius:var(--r-lg); border:1px solid var(--line2);
    background:var(--panel); color:var(--fg); font-size:15.5px; outline:none;
    transition:border-color .18s, box-shadow .18s; }
  .bar input::placeholder{ color:#6c6c76; }
  .bar input:focus{ border-color:var(--red); box-shadow:0 0 0 3px rgba(229,9,20,.18); }
  .bar button{ padding:15px 28px; border:0; border-radius:var(--r-lg); background:var(--red);
    color:#fff; font-weight:700; font-size:15px; cursor:pointer; letter-spacing:.01em;
    transition:background .15s, transform .12s, box-shadow .15s; }
  .bar button:hover{ background:var(--red-hi); transform:translateY(-1px);
    box-shadow:0 6px 22px rgba(229,9,20,.32); }
  .bar button:active{ transform:translateY(0); }

  .filters{ display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:12px;
    background:var(--panel); border:1px solid var(--line); border-radius:var(--r-lg);
    padding:16px 18px 18px; margin-bottom:28px; }
  .f{ display:flex; flex-direction:column; gap:6px; min-width:0; }
  .f label{ font-size:10.5px; font-weight:600; letter-spacing:.09em; text-transform:uppercase;
    color:var(--mut); }
  .f select,.f input[type=text],.f input[type=number]{ width:100%; padding:9px 11px;
    border-radius:var(--r-md); border:1px solid var(--line2); background:var(--panel2);
    color:var(--fg); font-size:13.5px; outline:none; transition:border-color .15s; }
  .f select:focus,.f input:focus{ border-color:var(--red); }
  .f.toggles{ flex-direction:row; align-items:flex-end; gap:16px; grid-column:span 2; }
  .tg{ display:flex; align-items:center; gap:8px; color:var(--mut); font-size:13.5px;
    cursor:pointer; user-select:none; padding-bottom:9px; }
  .tg input{ appearance:none; width:34px; height:20px; border-radius:999px; background:var(--line2);
    position:relative; cursor:pointer; transition:background .18s; margin:0; }
  .tg input::after{ content:''; position:absolute; top:3px; left:3px; width:14px; height:14px;
    border-radius:50%; background:#fff; transition:transform .18s; }
  .tg input:checked{ background:var(--red); }
  .tg input:checked::after{ transform:translateX(14px); }

  .card{ border:1px solid var(--line); background:var(--panel); border-radius:var(--r-lg);
    padding:18px 20px; margin-bottom:12px; transition:border-color .18s, transform .15s,
    box-shadow .18s; }
  .card:hover{ border-color:rgba(229,9,20,.45); transform:translateY(-1px);
    box-shadow:0 10px 30px rgba(0,0,0,.35); }
  .card a{ color:var(--fg); text-decoration:none; font-weight:600; font-size:16.5px;
    letter-spacing:-.01em; }
  .card a:hover{ color:var(--red-hi); }
  .co{ color:var(--mut); font-size:13.5px; margin:4px 0 12px; }
  .tags{ display:flex; gap:7px; flex-wrap:wrap; }
  .tag{ font-size:11.5px; font-weight:500; padding:4px 11px; border-radius:999px;
    background:var(--panel2); border:1px solid var(--line); color:var(--mut); }
  .tag.score{ background:var(--red-tint); border-color:rgba(229,9,20,.25); color:#ff8189;
    font-weight:600; }
  .tag.sal{ color:#e8e8ec; border-color:var(--line2); }
  .tag.new{ background:rgba(46,204,113,.12); border-color:rgba(46,204,113,.3); color:#5fd693;
    font-weight:600; }
  .empty{ color:var(--mut); text-align:center; padding:56px 20px; font-size:14.5px; }
  .foot{ color:#5c5c66; text-align:center; font-size:12px; margin-top:44px; }

  .skel{ border:1px solid var(--line); background:var(--panel); border-radius:var(--r-lg);
    padding:18px 20px; margin-bottom:12px; }
  .shim{ height:14px; border-radius:8px;
    background:linear-gradient(90deg, var(--panel2) 25%, #232329 45%, var(--panel2) 65%);
    background-size:200% 100%; animation:shim 1.15s linear infinite; }
  @keyframes shim{ from{ background-position:200% 0; } to{ background-position:-200% 0; } }
  .fadein{ animation:fadein .3s ease; }
  @keyframes fadein{ from{ opacity:0; transform:translateY(4px);} to{ opacity:1; transform:none;} }
</style></head><body><div class="wrap">
  <div class="brand"><div class="mark"></div>
    <h1>Head<span class="red">Start</span></h1></div>
  <p class="sub">semantic search over <b>__NJOBS__</b> tech jobs, read straight from company ATS boards · refreshed 4× daily</p>
  <div class="bar">
    <input id="q" type="text" placeholder="describe the role — e.g. backend engineer at a climate startup" autofocus>
    <button onclick="go()">Search</button>
  </div>
  <div class="filters">
    <div class="f"><label>ATS</label>
      <select id="ats"><option value="">any</option>__ATS_OPTIONS__</select></div>
    <div class="f"><label>Type</label>
      <select id="etype"><option value="">any</option>
        <option value="full-time">full-time</option><option value="part-time">part-time</option>
        <option value="contract">contract</option><option value="internship">internship</option>
      </select></div>
    <div class="f"><label>Max years</label>
      <input id="maxyears" type="number" min="0" placeholder="any"></div>
    <div class="f"><label>India</label>
      <select id="india"><option value="">anywhere</option>
        <option value="india">all India</option>__INDIA_OPTIONS__</select></div>
    <div class="f"><label>Location contains</label>
      <input id="location" type="text" placeholder="e.g. berlin"></div>
    <div class="f"><label>Company contains</label>
      <input id="company" type="text" placeholder="e.g. binance"></div>
    <div class="f"><label>Posted</label>
      <select id="posted"><option value="">any time</option>
        <option value="1">last 24h</option><option value="7">last 7 days</option>
        <option value="30">last 30 days</option><option value="90">last 90 days</option>
      </select></div>
    <div class="f"__SINCE_HIDDEN__><label>New to HeadStart</label>
      <select id="seen"><option value="">any time</option>
        <option value="2">last 2h</option><option value="24">last 24h</option>
        <option value="168">last 7 days</option>
      </select></div>
    <div class="f"><label>Sort</label>
      <select id="sort"><option value="rel">relevance</option>
        <option value="new">newest first</option></select></div>
    <div class="f"><label>Results</label>
      <select id="k"><option>10</option><option selected>20</option><option>50</option></select></div>
    <div class="f toggles">
      <label class="tg"><input type="checkbox" id="remote"> remote only</label>
      <label class="tg"><input type="checkbox" id="hassalary"> has salary</label>
    </div>
  </div>
  <div id="results"></div>
  <div class="foot">nomic embeddings · LanceDB filter-then-rank · English tech corpus</div>
</div>
<script>
const el = s => document.getElementById(s);
// Job text is scraped third-party content — escape before it reaches innerHTML, and only
// allow http(s) hrefs (no javascript: URLs).
const esc = s => (s==null?'':String(s)).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = u => { const l=(u||'').toLowerCase(); return (l.startsWith('http://')||l.startsWith('https://'))? u : '#'; };
el('q').addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
const age = d => {
  const t = Date.parse(d || ''); if (isNaN(t)) return '';
  const days = Math.floor((Date.now() - t) / 86400000);
  if (days < 0) return '';
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return days + 'd ago';
  if (days < 365) return Math.floor(days/30) + 'mo ago';
  return Math.floor(days/365) + 'y ago';
};
// Indexed inside the window the user asked for, so the badge always means "newer than your
// filter" — defaulting to 24h when no window is set. `first_seen` is ours, so it always parses.
const isNew = s => {
  const t = Date.parse(s || ''); if (isNaN(t)) return false;
  const hours = Number((el('seen') && el('seen').value) || 24);
  return Date.now() - t < hours * 3600000;
};
const skeleton = () =>
  '<div class="skel"><div class="shim" style="width:55%"></div>' +
  '<div class="shim" style="width:32%; margin-top:10px"></div>' +
  '<div class="shim" style="width:70%; margin-top:14px"></div></div>';
async function go(){
  const q = el('q').value.trim(); if(!q) return;
  const p = new URLSearchParams({ q, k: el('k').value });
  if (el('remote').checked) p.set('remote','true');
  if (el('hassalary').checked) p.set('has_salary','true');
  if (el('maxyears').value) p.set('max_years', el('maxyears').value);
  if (el('ats').value) p.set('ats', el('ats').value);
  if (el('etype').value) p.set('etype', el('etype').value);
  if (el('india').value) p.set('india', el('india').value);
  if (el('location').value.trim()) p.set('location', el('location').value.trim());
  if (el('company').value.trim()) p.set('company', el('company').value.trim());
  if (el('posted').value) p.set('posted_within', el('posted').value);
  if (el('seen') && el('seen').value) p.set('seen_within', el('seen').value);
  el('results').innerHTML = skeleton() + skeleton() + skeleton();
  let rows;
  try { rows = await (await fetch('/search?'+p)).json(); }
  catch(e){ el('results').innerHTML = '<div class="empty">search failed — try again</div>'; return; }
  if(!Array.isArray(rows)){ el('results').innerHTML = '<div class="empty">invalid filter</div>'; return; }
  if(!rows.length){ el('results').innerHTML = '<div class="empty">no matches — try loosening a filter</div>'; return; }
  if (el('sort').value === 'new'){
    const ts = r => { const t = Date.parse(r.posted_at || ''); return isNaN(t) ? -1 : t; };
    rows.sort((a,b) => ts(b) - ts(a));   // unparseable dates sink to the bottom
  }
  el('results').innerHTML = '<div class="fadein">' + rows.map(r => `
    <div class="card">
      <a href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener">${esc(r.title)}</a>
      <div class="co">${esc(r.company)}${r.location? ' · '+esc(r.location) : ''}</div>
      <div class="tags">
        <span class="tag score">${Number(r.score)||0}</span>
        ${r.remote? '<span class="tag">remote</span>':''}
        ${r.employment_type? '<span class="tag">'+esc(r.employment_type)+'</span>':''}
        ${r.min_years!=null? '<span class="tag">'+(Number(r.min_years)||0)+'+ yrs</span>':''}
        ${r.salary? '<span class="tag sal">'+esc(r.salary)+'</span>':''}
        ${age(r.posted_at)? '<span class="tag">'+age(r.posted_at)+'</span>':''}
        ${isNew(r.first_seen)? '<span class="tag new">new</span>':''}
        ${r.ats? '<span class="tag">'+esc(r.ats)+'</span>':''}
      </div>
    </div>`).join('') + '</div>';
}
</script></body></html>"""

_PAGE = (
    _TEMPLATE.replace("__NJOBS__", f"{_table.count_rows():,}")
    # Hidden until the pipeline adds the column, so the control can ship ahead of the data
    # (the `el('seen')` guard in go() keeps the query-string builder happy either way).
    .replace("__SINCE_HIDDEN__", "" if _HAS_FIRST_SEEN else ' style="display:none"')
    .replace(
        "__ATS_OPTIONS__",
        "".join(f'<option value="{a}">{a}</option>' for a in _ATSES),
    )
    .replace(
        "__INDIA_OPTIONS__",
        "".join(
            f'<option value="{c}">{c.title().replace("Ncr", "NCR")}</option>'
            for c in geo.DROPDOWN
        ),
    )
)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860)
