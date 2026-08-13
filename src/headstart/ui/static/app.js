// HeadStart page behaviour. Server-varying config arrives as window.CFG (set inline by
// base.html); everything else is static. Tabs are hash-routed panels — no server round trip.
const CFG = window.CFG || {};
const el = s => document.getElementById(s);
// Job text is scraped third-party content — escape before it reaches innerHTML, and only
// allow http(s) hrefs (no javascript: URLs).
const esc = s => (s==null?'':String(s)).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = u => { const l=(u||'').toLowerCase(); return (l.startsWith('http://')||l.startsWith('https://'))? u : '#'; };
el('q').addEventListener('keydown', e => { if (e.key === 'Enter') go(); });

/* ---- tabs. The hash names the panel (#search, #trends); unknown hashes fall back to
   search, so a stale link never strands anyone on a blank page. Trends data loads the
   first time its tab opens, not on page load. ---- */
function currentTab(){
  const name = location.hash.replace('#','');
  return document.getElementById('panel-' + name) ? name : 'search';
}
function showTab(name){
  document.querySelectorAll('.panel').forEach(p => { p.hidden = p.id !== 'panel-' + name; });
  document.querySelectorAll('.side [data-tab]').forEach(a =>
    a.setAttribute('aria-current', a.dataset.tab === name ? 'page' : 'false'));
  if (name === 'trends' && el('trends') && !trendData) loadTrends(null);
  if (name === 'matches' && el('sets-strip')){
    if (!mySets) loadSets();
    else if (activeSetId) runSet(activeSetId);   // re-run on every visit — never stale
  }
  if (name === 'saved' && el('saved-results')) loadSaved();   // re-check "closed" on every visit
  if (name === 'profile' && el('pquery')) loadProfile();      // server truth on every visit
}
window.addEventListener('hashchange', () => showTab(currentTab()));

function flipTheme(){
  const now = document.documentElement.getAttribute('data-theme')
    || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  document.documentElement.setAttribute('data-theme', now === 'dark' ? 'light' : 'dark');
}
function tryIt(btn){ el('q').value = btn.textContent.trim(); go(); }
// The header identity. /me answers from the caller's own session cookie; when the sign-in
// wall is off (or the caller somehow reached this page signed out) it stays blank.
async function whoAmI(){
  try{
    const d = await (await fetch('/me')).json();
    if (!d.auth || !d.email) return;
    el('who').textContent = d.email;
    el('signout').style.display = '';
  }catch(e){}
}
async function signOut(){
  try{ await fetch('/signout', { method:'POST' }); }catch(e){}
  location.reload();
}
function toggleRail(){
  const rail = el('rail');
  const open = rail.classList.toggle('open');
  // The rail sits before <main> in the DOM, so on a stacked phone layout it opens ABOVE the
  // button that was just tapped. Without this the content jumps and the filters are off-screen.
  if (open) rail.scrollIntoView({ block: 'start', behavior: 'smooth' });
}

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
// The Match ring (ADR-0042): raw cosine lives in a narrow band (a strong on-topic query
// tops out ≈0.78; an absurd one still scores ≈0.66), so the displayed % stretches it
// through two fixed anchors — 0.60 → 0%, 0.85 → 100% — tuned once against real queries
// and revisited only when the embedding model changes. Display only: ranking stays on the
// raw score, and the same job shows the same % wherever it appears.
const matchPct = s => Math.round(Math.max(0, Math.min(1, (s - .60) / .25)) * 100);
// Match strength is a QUANTITY, so it gets a sequential ramp — one hue, increasing intensity —
// not four different hues. Hue is reserved for categories (amber = new, lime = pays, violet =
// remote); reusing those hues here would have made lime mean both "strong match" and "salary".
// Weak matches fade toward the muted ink so a scan shows where the good results stop.
const tone = s => `color-mix(in srgb, var(--aqua) ${25 + matchPct(s) * .75}%, var(--ink-3))`;
const skeleton = () =>
  '<div class="skel"><div class="shim" style="width:52%"></div>' +
  '<div class="shim" style="width:30%; margin-top:10px"></div>' +
  '<div class="shim" style="width:68%; margin-top:14px"></div></div>';

// The filters as a plain object, read once so the search and the alert subscription can
// never disagree about what the user asked for. The server whitelists these again.
function currentFilters(){
  const f = {};
  if (el('remote').checked) f.remote = 'true';
  if (el('hassalary').checked) f.has_salary = 'true';
  if (el('maxyears').value) f.max_years = el('maxyears').value;
  if (el('ats').value) f.ats = el('ats').value;
  if (el('etype').value) f.etype = el('etype').value;
  if (el('india').value) f.india = el('india').value;
  if (el('location').value.trim()) f.location = el('location').value.trim();
  if (el('company').value.trim()) f.company = el('company').value.trim();
  if (el('posted').value) f.posted_within = el('posted').value;
  if (el('seen') && el('seen').value) f.seen_within = el('seen').value;
  return f;
}
const LABELS = { remote:'Remote', has_salary:'Shows salary', max_years:'Your experience',
  ats:'ATS provider', etype:'Type', india:'India', location:'Location', company:'Company',
  posted_within:'Posted ≤', seen_within:'First seen ≤' };
const CONTROL = { remote:'remote', has_salary:'hassalary', max_years:'maxyears', ats:'ats',
  etype:'etype', india:'india', location:'location', company:'company',
  posted_within:'posted', seen_within:'seen' };
function drawActive(){
  const f = currentFilters(), box = el('active');
  box.innerHTML = Object.entries(f).map(([k,v]) =>
    `<span class="pill"><b>${esc(LABELS[k]||k)}</b> ${esc(v === 'true' ? 'yes' : v)}` +
    `<button onclick="dropFilter('${esc(k)}')" aria-label="Remove ${esc(LABELS[k]||k)} filter">×</button></span>`
  ).join('');
}
function dropFilter(key){
  const c = el(CONTROL[key]); if (!c) return;
  if (c.type === 'checkbox') c.checked = false; else c.value = '';
  go();
}
function clearAll(){
  Object.values(CONTROL).forEach(id => { const c = el(id); if (!c) return;
    if (c.type === 'checkbox') c.checked = false; else c.value = ''; });
  go();
}

function showIntro(){
  el('results').innerHTML = '<div class="empty"><div class="big">Describe the role, not the keywords</div>' +
    'This searches meaning, so "someone to own our payments backend" works as well as a job title.<br>' +
    'Set the structured constraints — experience, location, salary — on the left.</div>';
}

let lastRows = null;   // kept so re-sorting never costs another embedding

// Sorting is a view over rows we already have. Re-running go() would refetch /search and
// re-encode the query server-side to reorder six results client-side.
function resort(){ if (lastRows) draw(lastRows); else go(); }

async function go(){
  const q = el('q').value.trim();
  drawActive();
  if(!q){ showIntro(); el('n').textContent = ''; return; }
  const p = new URLSearchParams({ q, k: el('k').value });
  for (const [key, value] of Object.entries(currentFilters())) p.set(key, value);
  el('results').innerHTML = skeleton() + skeleton() + skeleton();
  el('n').textContent = 'searching…';
  let rows;
  try { rows = await (await fetch('/search?'+p)).json(); }
  catch(e){ el('results').innerHTML = '<div class="empty">That search didn\'t go through. Try again.</div>';
            el('n').textContent = ''; return; }
  if(!Array.isArray(rows)){
    el('results').innerHTML = '<div class="empty">One of the filters isn\'t valid — clear it and try again.</div>';
    el('n').textContent = ''; return; }
  if(!rows.length){
    el('results').innerHTML = '<div class="empty"><div class="big">Nothing matched</div>' +
      'Try loosening a filter, or describe the role more broadly.</div>';
    el('n').textContent = '0 results'; return; }
  lastRows = rows;
  el('n').textContent = rows.length + ' result' + (rows.length===1?'':'s');
  draw(rows);
}

function draw(rows, target){
  if (!target && el('sort').value === 'new'){   // the sort control belongs to the Search tab
    const ts = r => { const t = Date.parse(r.posted_at || ''); return isNaN(t) ? -1 : t; };
    rows = rows.slice().sort((a,b) => ts(b) - ts(a));   // unparseable dates sink to the bottom
  }
  rows.forEach(r => { if (r.id) drawnRows.set(r.id, r); });   // starring needs the row later
  el(target || 'results').innerHTML = rows.map((r,i) => {
    const s = Number(r.score) || 0, c = tone(s), pct = matchPct(s);
    return `
    <div class="card" style="--tone:${c}; animation-delay:${Math.min(i,12)*35}ms">
      <div class="hd">
        <div style="flex:1; min-width:0">
          <a class="title" href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener">${esc(r.title)}</a>
          <div class="org">${esc(r.company)}${r.location? ' <span>·</span> '+esc(r.location) : ''}</div>
        </div>
        ${starBtn(r.id)}
        <div class="match" title="Match strength — semantic similarity ${s.toFixed(2)}, scaled to this index's real range">
          <svg class="ring" viewBox="0 0 40 40" aria-hidden="true">
            <circle class="ring-track" cx="20" cy="20" r="16" pathLength="100"/>
            <circle class="ring-fill" cx="20" cy="20" r="16" pathLength="100" style="--p:${pct}"/>
          </svg>
          <div class="v">${pct}%</div>
        </div>
      </div>
      <div class="tags">
        ${isNew(r.first_seen)? '<span class="tag new">new</span>':''}
        ${r.remote? '<span class="tag rem">remote</span>':''}
        ${r.salary? '<span class="tag pay">'+esc(r.salary)+'</span>':''}
        ${r.employment_type? '<span class="tag">'+esc(r.employment_type)+'</span>':''}
        ${r.min_years!=null? '<span class="tag mono">'+(Number(r.min_years)||0)+'+ yrs</span>':''}
        ${age(r.posted_at)? '<span class="tag mono">'+age(r.posted_at)+'</span>':''}
        ${r.ats? '<span class="tag">'+esc(r.ats)+'</span>':''}
      </div>
    </div>`; }).join('');
}

/* ---- Saved sets (ADR-0043): the Matches tab runs one live; "Save this search" creates
   one from the controls the user is looking at. All strip actions ride ONE delegated
   listener + data attributes — never inline handlers with interpolated names. ---- */
let mySets = null, activeSetId = null;

async function loadSets(){
  try{
    const r = await fetch('/sets');
    if (!r.ok){ el('matches-msg').textContent = 'Couldn\'t load your sets.'; return; }
    mySets = await r.json();
  }catch(e){ el('matches-msg').textContent = 'Couldn\'t load your sets.'; return; }
  if (activeSetId && !mySets.some(s => s.id === activeSetId)) activeSetId = null;
  renderSets();
  if (!activeSetId && mySets.length) runSet(mySets[0].id);   // land on your first set
}

function renderSets(){
  const strip = el('sets-strip');
  if (!mySets.length){
    strip.innerHTML = '';
    el('matches-msg').textContent = '';
    el('matches-results').innerHTML =
      '<div class="empty"><div class="big">No saved sets yet</div>' +
      'Search for a role, tune the filters, then hit "Save this search" — it lands here ' +
      'and runs live every time you open this tab.</div>';
    return;
  }
  strip.innerHTML = mySets.map(s => `
    <div class="set-chip${s.id === activeSetId ? ' active' : ''}" data-id="${esc(s.id)}">
      <button class="set-name" data-act="run" data-id="${esc(s.id)}"
        title="${esc(s.query)}">${esc(s.name)}${s.emails ? ' <span class="mail-on" title="Emails you new matches">✉</span>' : ''}</button>
      <span class="set-tools">
        <button data-act="email" data-id="${esc(s.id)}" title="${s.emails ? 'Stop emailing this set' : 'Email me this set\'s new matches'}">${s.emails ? '✉ on' : '✉'}</button>
        <button data-act="refine" data-id="${esc(s.id)}" title="Open in Search to adjust">Refine</button>
        <button data-act="rename" data-id="${esc(s.id)}" title="Rename">✎</button>
        <button data-act="del" data-id="${esc(s.id)}" title="Delete" aria-label="Delete ${esc(s.name)}">×</button>
      </span>
    </div>`).join('');
}

// The view controls: refine what the active set SHOWS — never written into the set.
// Ranges go to the server (a range must filter before ranking); sort is a client-side
// reorder of the rows already fetched, like the Search tab's resort().
function matchesRange(){
  const v = id => (el(id) && el(id).value) || '';
  const r = {};
  if (v('mposted-from')) r.posted_after = v('mposted-from');
  if (v('mposted-to')) r.posted_before = v('mposted-to');
  if (v('mseen-from')) r.seen_after = v('mseen-from');
  if (v('mseen-to')) r.seen_before = v('mseen-to');
  return r;
}
function sortMatches(rows){
  const key = (el('msort') && el('msort').value) || 'score';
  const dir = el('mdir') && el('mdir').dataset.dir === 'asc' ? 1 : -1;
  const val = r => key === 'score' ? (Number(r.score) || 0)
    : Date.parse((key === 'posted' ? r.posted_at : r.first_seen) || '');
  return rows.slice().sort((a, b) => {
    const va = val(a), vb = val(b);
    const na = isNaN(va), nb = isNaN(vb);
    if (na && nb) return 0;
    if (na || nb) return na ? 1 : -1;   // rows without the date sink either direction
    return (va - vb) * dir;
  });
}

async function runSet(id){
  const s = (mySets || []).find(x => x.id === id); if (!s) return;
  activeSetId = id; renderSets();
  el('matches-msg').textContent = 'searching…';
  const p = new URLSearchParams({ q: s.query, k: 20 });
  for (const [key, value] of Object.entries(s.search_filters || {})) p.set(key, value);
  for (const [key, value] of Object.entries(matchesRange())) p.set(key, value);
  let rows;
  try { rows = await (await fetch('/search?'+p)).json(); }
  catch(e){ el('matches-msg').textContent = 'That search didn\'t go through.'; return; }
  if (!Array.isArray(rows)){ el('matches-msg').textContent = 'A saved filter isn\'t valid — refine the set.'; return; }
  el('matches-msg').textContent = rows.length
    ? `${rows.length} match${rows.length === 1 ? '' : 'es'} for “${s.name}”`
    : `Nothing matches “${s.name}” right now`;
  draw(sortMatches(rows), 'matches-results');
}

function applySetToControls(s){
  el('q').value = s.query;
  Object.values(CONTROL).forEach(cid => { const c = el(cid); if (!c) return;
    if (c.type === 'checkbox') c.checked = false; else c.value = ''; });
  for (const [key, value] of Object.entries(s.search_filters || {})){
    const c = el(CONTROL[key]); if (!c) continue;
    if (c.type === 'checkbox') c.checked = value === 'true'; else c.value = value;
  }
}

async function handleSetAction(act, id){
  const s = (mySets || []).find(x => x.id === id); if (!s) return;
  if (act === 'run') return runSet(id);
  if (act === 'refine'){ applySetToControls(s); location.hash = '#search'; go(); return; }
  if (act === 'rename'){
    const name = (window.prompt('Rename this set', s.name) || '').trim();
    if (!name || name === s.name) return;
    await postAndReloadSets('/sets', { id, name, query: s.query, filters: s.search_filters });
    return;
  }
  if (act === 'del'){
    if (!window.confirm(`Delete “${s.name}”?${s.emails ? ' Its email digest stops too.' : ''}`)) return;
    try{ await fetch('/sets/' + encodeURIComponent(id), { method: 'DELETE' }); }catch(e){}
    mySets = null; loadSets();
    return;
  }
  if (act === 'email'){
    const r = await postAndReloadSets('/sets/' + encodeURIComponent(id) + '/email', { on: !s.emails }, true);
    if (r && !r.ok){
      const d = await r.json().catch(() => ({}));
      el('matches-msg').textContent = d.error || ('Failed (' + r.status + ')');
    }
  }
}

// POST helper for set actions; reloads the strip afterwards so state is always server-truth.
async function postAndReloadSets(url, body, returnResponse){
  let r = null;
  try{
    r = await fetch(url, { method: 'POST', headers: {'Content-Type': 'application/json'},
                           body: JSON.stringify(body) });
  }catch(e){}
  mySets = null; await loadSets();
  return returnResponse ? r : null;
}

function saveSearchToggle(){
  const row = el('saverow');
  row.style.display = row.style.display === 'none' ? '' : 'none';
  if (row.style.display === '') el('savename').focus();
}

async function saveSearch(){
  const name = el('savename').value.trim();
  const q = el('q').value.trim();
  const msg = el('savemsg');
  if (!q){ msg.textContent = 'Type the role you want first.'; return; }
  if (!name){ msg.textContent = 'Give it a name.'; return; }
  try{
    const r = await fetch('/sets', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name, query: q, filters: currentFilters() }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok){ msg.textContent = d.error || ('Failed (' + r.status + ')'); return; }
    mySets = null;                       // the strip reloads next time Matches opens
    el('savename').value = '';
    el('saverow').style.display = 'none';
    msg.textContent = '';
    el('n').textContent = `Saved — see Matches`;
  }catch(e){ msg.textContent = 'That request didn\'t go through. Try again.'; }
}

/* ---- Saved jobs (ADR-0042, ADR-0044): starring keeps a copy of the card's display
   fields, so the Saved tab survives the index churn and marks evicted postings "closed".
   Stars flip optimistically — an HF write is ~1s, too slow for a click — and revert with
   a message if the server refuses. ---- */
const CAN_STAR = !!el('saved-results');   // the Saved tab only renders when configured
let mySaved = null;                        // server-truth list, newest star first
const savedByJob = new Map();              // job id → saved record
const drawnRows = new Map();               // job id → last drawn result row

function starBtn(jobId, on){
  if (!CAN_STAR || !jobId) return '';
  const has = on != null ? on : savedByJob.has(jobId);
  return `<button class="star${has?' on':''}" data-star="${esc(jobId)}" aria-pressed="${has}"
    title="${has?'Remove from saved':'Save this job'}" aria-label="${has?'Remove this job from saved':'Save this job'}">${has?'★':'☆'}</button>`;
}

// The visible tab's status line — where star errors land, wherever the click happened.
function starMsg(text){
  const id = { search:'n', matches:'matches-msg', saved:'saved-msg' }[currentTab()];
  if (id && el(id)) el(id).textContent = text;
}

async function loadSaved(){
  try{
    const r = await fetch('/saved');
    if (!r.ok){ starMsg('Couldn\'t load your saved jobs.'); return; }
    mySaved = await r.json();
  }catch(e){ starMsg('Couldn\'t load your saved jobs.'); return; }
  savedByJob.clear();
  mySaved.forEach(j => savedByJob.set(j.job_id, j));
  renderSaved();
  paintStars();
}

// Repaint every star button in place — cheaper than redrawing three results lists.
function paintStars(){
  document.querySelectorAll('button[data-star]').forEach(b => {
    const on = savedByJob.has(b.dataset.star);
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', on);
    b.textContent = on ? '★' : '☆';
  });
}

function renderSaved(){
  const box = el('saved-results');
  if (!box || mySaved == null) return;
  if (!mySaved.length){
    el('saved-msg').textContent = '';
    box.innerHTML = '<div class="empty"><div class="big">Nothing saved yet</div>' +
      'Hit the ☆ on any result to keep it here — the copy stays even after the posting closes.</div>';
    return;
  }
  const jobs = mySaved.slice().sort((a,b) => (b.starred_at||'').localeCompare(a.starred_at||''));
  el('saved-msg').textContent = jobs.length + ' saved job' + (jobs.length===1?'':'s');
  box.innerHTML = jobs.map((j,i) => `
    <div class="card${j.open === false ? ' gone' : ''}" style="animation-delay:${Math.min(i,12)*35}ms">
      <div class="hd">
        <div style="flex:1; min-width:0">
          <a class="title" href="${esc(safeUrl(j.url))}" target="_blank" rel="noopener">${esc(j.title)}</a>
          <div class="org">${esc(j.company)}${j.location? ' <span>·</span> '+esc(j.location) : ''}</div>
        </div>
        ${starBtn(j.job_id, true)}
      </div>
      <div class="tags">
        ${j.open === false ? '<span class="tag closed">closed</span>' : ''}
        ${j.remote ? '<span class="tag rem">remote</span>' : ''}
        ${j.salary ? '<span class="tag pay">'+esc(j.salary)+'</span>' : ''}
        ${age(j.starred_at) ? '<span class="tag mono">saved '+age(j.starred_at)+'</span>' : ''}
      </div>
    </div>`).join('');
}

async function toggleStar(jobId){
  const existing = savedByJob.get(jobId);
  if (existing && !existing.id) return;   // a star still in flight — let it land first

  if (existing){
    // optimistic unstar; 404 means another tab already removed it, which is the same outcome
    savedByJob.delete(jobId);
    if (mySaved) mySaved = mySaved.filter(j => j.job_id !== jobId);
    paintStars(); renderSaved();
    let r = null;
    try{ r = await fetch('/saved/' + encodeURIComponent(existing.id), { method: 'DELETE' }); }
    catch(e){}
    if (!r || (!r.ok && r.status !== 404)){
      savedByJob.set(jobId, existing);
      // a loadSaved may have refreshed mySaved while the DELETE was in flight — don't duplicate
      if (mySaved && !mySaved.some(j => j.job_id === jobId)) mySaved.push(existing);
      paintStars(); renderSaved();
      starMsg('Couldn\'t remove that star — try again.');
    }
    return;
  }

  const row = drawnRows.get(jobId);
  if (!row) return;
  const copy = { title: row.title, company: row.company, url: row.url,
                 location: row.location || '', remote: !!row.remote, salary: row.salary || '' };
  // optimistic star: a placeholder record until the server answers with the real one
  const local = { id: '', job_id: jobId, starred_at: new Date().toISOString(), open: true, ...copy };
  savedByJob.set(jobId, local);
  if (mySaved) mySaved.unshift(local);
  paintStars(); renderSaved();
  let r = null, d = null;
  try{
    r = await fetch('/saved', { method: 'POST', headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({ job_id: jobId, ...copy }) });
    d = await r.json().catch(() => null);
  }catch(e){}
  if (r && r.ok && d){
    savedByJob.set(jobId, d);
    // A loadSaved that raced this POST (opening the Saved tab re-fetches) read server truth
    // from BEFORE the write and unpainted the star — repaint from the confirmed record, and
    // put it back in the list if the refresh dropped it.
    if (mySaved){
      mySaved = mySaved.some(j => j.job_id === jobId)
        ? mySaved.map(j => j.job_id === jobId ? d : j)
        : [d, ...mySaved];
    }
    paintStars(); renderSaved();
  } else {
    savedByJob.delete(jobId);
    if (mySaved) mySaved = mySaved.filter(j => j.job_id !== jobId);
    paintStars(); renderSaved();
    starMsg((d && d.error) || 'Couldn\'t save that job — try again.');
  }
}

/* ---- Profile (ADR-0041): the stored career extraction — one sentence that drives
   ranking, facts that pre-fill filters. One LLM read per paste, capped per Account;
   every field stays hand-editable, so the AI is a convenience, not a gate. ---- */
const PROFILE_FIELDS = { query:'pquery', title:'ptitle', years:'pyears', skills:'pskills',
  roles:'proles', education:'pedu', location:'plocation' };

function fillProfileForm(p){
  for (const [key, cid] of Object.entries(PROFILE_FIELDS))
    el(cid).value = p[key] == null ? '' : p[key];
  el('pparses').textContent = p.parses_left > 0
    ? `${p.parses_left} of ${p.parses_left + p.parses_used} résumé reads left`
    : 'No résumé reads left — edit by hand below.';
  el('pparse').disabled = !(p.parses_left > 0);
}

function readProfileForm(){
  const out = {};
  for (const [key, cid] of Object.entries(PROFILE_FIELDS)) out[key] = el(cid).value.trim();
  return out;
}

async function loadProfile(){
  const msg = el('profile-msg');
  try{
    const r = await fetch('/profile');
    if (!r.ok){ msg.textContent = 'Couldn\'t load your profile.'; return; }
    fillProfileForm(await r.json());
  }catch(e){ msg.textContent = 'Couldn\'t load your profile.'; }
}

async function saveProfile(){
  const msg = el('profile-msg');
  msg.textContent = 'Saving…';
  try{
    const r = await fetch('/profile', { method: 'POST', headers: {'Content-Type': 'application/json'},
                                        body: JSON.stringify(readProfileForm()) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok){ msg.textContent = d.error || ('Failed (' + r.status + ')'); return; }
    fillProfileForm(d);
    msg.textContent = 'Saved.';
  }catch(e){ msg.textContent = 'That request didn\'t go through. Try again.'; }
}

async function parseResume(){
  const text = el('presume').value.trim();
  const msg = el('profile-msg'), btn = el('pparse');
  if (!text){ msg.textContent = 'Paste your résumé first.'; return; }
  btn.disabled = true; msg.textContent = 'Reading…';
  let ok = false, spent = false;
  try{
    const r = await fetch('/profile/parse', { method: 'POST', headers: {'Content-Type': 'application/json'},
                                              body: JSON.stringify({ text }) });
    const d = await r.json().catch(() => ({}));
    if (r.ok){
      ok = true;
      fillProfileForm(d);   // also sets the button from the fresh parses_left
      el('presume').value = '';   // the document was never stored; don't keep it on screen either
      msg.textContent = 'Read — check the fields below, edit anything, then Save.';
    } else {
      spent = r.status === 502;   // the router answered nothing usable — a read was still spent
      msg.textContent = d.error || ('Failed (' + r.status + ')');
    }
  }catch(e){ msg.textContent = 'That request didn\'t go through. Try again.'; }
  if (!ok){
    if (spent) await loadProfile();   // refresh the reads-left counter (and button state)
    else btn.disabled = false;
  }
}

// The explicit hand-off to Search: the sentence becomes the query, the facts become
// filters (years → experience, location → location) — never the other way around.
function applyProfile(){
  const p = readProfileForm();
  if (!p.query){ el('profile-msg').textContent = 'Fill in what to search for first.'; return; }
  el('q').value = p.query;
  Object.values(CONTROL).forEach(id => { const c = el(id); if (!c) return;
    if (c.type === 'checkbox') c.checked = false; else c.value = ''; });
  if (p.years) el('maxyears').value = p.years;
  if (p.location) el('location').value = p.location;
  location.hash = '#search';
  go();
}

async function deleteProfile(){
  if (!window.confirm('Clear your stored profile? Your saved sets and starred jobs stay.')) return;
  const msg = el('profile-msg');
  try{
    const r = await fetch('/profile', { method: 'DELETE' });
    if (!r.ok){ msg.textContent = 'Couldn\'t delete — try again.'; return; }
    await loadProfile();
    msg.textContent = 'Profile cleared.';
  }catch(e){ msg.textContent = 'Couldn\'t delete — try again.'; }
}

// Google sign-in returns a signed credential; the address is read from it server-side, so
// the browser never gets to say who it is subscribing.
async function onGoogleCredential(resp){
  const msg = el('amsg');
  const q = el('q').value.trim();
  if (!q){ msg.textContent = 'Type the role you want first.'; return; }
  msg.textContent = 'Subscribing…';
  try {
    const r = await fetch('/subscribe', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ credential: resp.credential, query: q, filters: currentFilters() })
    });
    const data = await r.json();
    msg.textContent = r.ok ? ('Subscribed — digests go to ' + data.email)
                           : (data.error || ('Failed (' + r.status + ')'));
  } catch(e){ msg.textContent = 'That request didn\'t go through. Try again.'; }
}
/* ---- role trends (ADR-0040/0051). The ledger is one count per (metric, family, band) per
   pipeline run; this draws a line per series and ranks them by size. Two measures ("All
   openings" = live stock, "New this week" = rows first seen inside the flow window) and two
   units (share of the index, absolute count). Share is the default on stock because raw stock
   counts move with our coverage — a night the index grows 1.5% lifts every category ~1.5%
   and says nothing about the market; a share only moves when categories move RELATIVE to each
   other. "New" is charted as counts: its meaning ("312 fresh AI/ML roles") IS the number. ---- */
const TREND_COLOURS = ['#12D6B4','#8B5CF6','#F59E0B','#A3E635','#FB7185','#60A5FA','#F472B6','#34D399'];
let trendData = null, trendDrill = null, trendHidden = new Set();
let trendMetric = 'stock', trendUnit = 'share', trendSplit = 'bands';
const LEGEND_MAX = 12;   // rows listed; CHART_MAX of them are plotted
const CHART_MAX = 8;     // distinct colours in TREND_COLOURS

async function loadTrends(family){
  const q = new URLSearchParams();
  if (family) { q.set('family', family); q.set('split', trendSplit); }
  if (trendMetric !== 'stock') q.set('metric', trendMetric);
  const r = await fetch('/trends' + (q.size ? '?' + q : ''));
  if (!r.ok) { el('trends').style.display = 'none'; return; }
  trendData = await r.json(); trendDrill = family || null; trendHidden = new Set();
  drawTrends();
}

// A series' value in the displayed unit: share of the index for stock, else the raw count.
// Share divides by that stamp's WHOLE table (families + non-tech), so index growth cancels.
function trendValue(v, j){
  if (v == null) return null;
  if (trendUnit !== 'share') return v;
  const t = trendData.totals[j];
  return t ? v / t * 100 : null;
}

// A series' movement across the window, in the displayed unit. Averages the first and last
// up-to-3 measured points rather than comparing two single runs — one noisy measurement at
// either end must not swing the headline number.
function trendDelta(points){
  const seen = points.map((v, j) => trendValue(v, j)).filter(v => v != null);
  if (seen.length < 2) return null;
  const k = Math.min(3, Math.floor(seen.length / 2)) || 1;
  const head = seen.slice(0, k).reduce((a, b) => a + b) / k;
  const tail = seen.slice(-k).reduce((a, b) => a + b) / k;
  if (!head) return null;
  return (tail - head) / head * 100;
}

// "Aug 12 09:00" from an ISO stamp — enough to anchor the axis without a timezone lecture.
const _MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function stampLabel(ts){
  const d = new Date(ts);
  if (isNaN(d)) return '';
  return `${_MONTHS[d.getUTCMonth()]} ${d.getUTCDate()} ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;
}

function fmtValue(v){
  if (trendUnit === 'share') return v >= 10 ? v.toFixed(0) + '%' : v.toFixed(1) + '%';
  return Math.round(v).toLocaleString();
}

function drawTrends(){
  const d = trendData; if (!d) return;
  const W = 720, H = 260, PAD_L = 44, PAD_B = 18, PAD_T = 8;
  const shown = d.series.filter(s => !trendHidden.has(s.name)).slice(0, CHART_MAX);
  const vals = shown.flatMap(s => s.points.map((v, j) => trendValue(v, j))).filter(v => v != null);
  const lo = 0, hi = Math.max(trendUnit === 'share' ? 0.1 : 1, ...vals);
  const x = i => PAD_L + (d.stamps.length < 2 ? 0 : i * (W - PAD_L - 6) / (d.stamps.length - 1));
  const y = v => PAD_T + (H - PAD_T - PAD_B) * (1 - (v - lo) / (hi - lo));

  let svg = '';
  for (let g = 0; g <= 4; g++){                       // horizontal gridlines + y labels
    const v = hi * g / 4, yy = y(v);
    svg += `<line class="gridline" x1="${PAD_L}" y1="${yy}" x2="${W}" y2="${yy}"/>`;
    svg += `<text class="axis-label" x="0" y="${yy+3}">${fmtValue(v)}</text>`;
  }
  // Time axis: first / middle / last measurement, so the window is visible on the chart
  // itself rather than inferred. Sparse on purpose — 19 two-hourly stamps don't need 19 ticks.
  if (d.stamps.length > 1){
    const ticks = [0, Math.floor((d.stamps.length - 1) / 2), d.stamps.length - 1];
    for (const [ti, anchor] of [[ticks[0],'start'],[ticks[1],'middle'],[ticks[2],'end']]){
      svg += `<text class="axis-label" x="${x(ti).toFixed(1)}" y="${H-4}"
               text-anchor="${anchor}">${stampLabel(d.stamps[ti])}</text>`;
    }
  }
  shown.forEach((s, i) => {
    const c = TREND_COLOURS[i % TREND_COLOURS.length];
    // break the path at gaps rather than bridging them — an unmeasured run is not a value
    let path = '', pen = 'M';
    s.points.forEach((v, j) => { const u = trendValue(v, j); if (u == null) { pen = 'M'; return; } path += `${pen}${x(j).toFixed(1)},${y(u).toFixed(1)} `; pen = 'L'; });
    svg += `<path d="${path.trim()}" fill="none" stroke="${c}" stroke-width="2"
             stroke-linejoin="round" stroke-linecap="round"/>`;
    const last = s.points.map((v,j)=>[trendValue(v,j),j]).filter(([v])=>v!=null).pop();
    if (last) svg += `<circle cx="${x(last[1]).toFixed(1)}" cy="${y(last[0]).toFixed(1)}" r="3" fill="${c}"/>`;
  });
  el('trends-chart').innerHTML = svg;

  el('trends-legend').innerHTML = d.series.slice(0, LEGEND_MAX).map((s, i) => {
    const c = TREND_COLOURS[i % TREND_COLOURS.length];
    const dl = trendDelta(s.points);
    const cls = dl == null ? 'flat' : dl > 1 ? 'up' : dl < -1 ? 'down' : 'flat';
    const txt = dl == null ? '—' : (dl > 0 ? '+' : '') + dl.toFixed(1) + '%';
    const off = trendHidden.has(s.name) || i >= CHART_MAX;
    const j = d.stamps.length - 1;
    const latest = s.latest == null ? null : trendValue(s.latest, j);
    // data-name + the delegated listener below, NOT an inline onclick: esc() is HTML-entity
    // escaping, and inside onclick="...'${name}'..." the parser decodes entities back
    // before the JS parses — a name with a quote would break out of the string.
    return `<li data-name="${esc(s.name)}" aria-pressed="${!off}"
      style="${off?'opacity:.45':''}"><span class="swatch" style="background:${i<CHART_MAX?c:'var(--ink-3)'}"></span>
      <span class="nm" title="${esc(s.label)}">${esc(s.label)}</span>
      <span class="ct">${latest == null ? '—' : fmtValue(latest)}</span>
      <span class="dl ${cls}">${txt}</span></li>`;
  }).join('');

  const runs = d.stamps.length;
  const hidden = d.series.slice(LEGEND_MAX);
  const tail = hidden.reduce((n, s) => n + (s.latest || 0), 0);
  // The measurement window, stated on the page: "19 measurements" alone could be two hours
  // or two years, and every judgement about a trend depends on which.
  const spanMs = runs > 1 ? new Date(d.stamps[runs-1]) - new Date(d.stamps[0]) : 0;
  const spanDays = spanMs / 864e5;
  const span = runs < 2 ? '' :
    spanDays >= 1.5 ? ` over ${Math.round(spanDays)} days` : ` over ${Math.round(spanMs/36e5)} hours`;
  el('trends-scope').textContent = trendDrill
    ? (trendSplit === 'roles' ? 'watched roles — click any line to go back' : 'by experience level — click to go back')
    : hidden.length
      // Say what is on screen, not just what exists. The list is capped, so quoting only the
      // total invites adding the visible rows up against the indexed-jobs headline and finding
      // tens of thousands unaccounted for — they are in the tail, reported in the footer.
      ? `top ${LEGEND_MAX} of ${d.series.length} categories · ${runs} measurement${runs===1?'':'s'}${span}`
      : `${d.series.length} categories · ${runs} measurement${runs===1?'':'s'}${span}`;
  el('trends-empty').textContent = runs < 2
    ? 'Only one measurement so far — trend lines appear once the pipeline has run a few more times.'
    : (trendDrill && trendSplit === 'roles' && !d.series.length
      ? 'No specific roles are tracked under this category yet.'
      : '');
  const nt = d.non_tech.filter(v => v != null).pop();
  const parts = [];
  parts.push(trendMetric === 'new'
    ? 'Openings first seen in the last 7 days, re-measured every pipeline run.'
    : (trendUnit === 'share'
      ? 'Each line is a category\u2019s share of all live openings in the index \u2014 immune to the index itself growing or shrinking.'
      : 'Counts are live openings in the index, re-measured every pipeline run. The index itself grows as coverage does, which lifts every count.'));
  if (tail) parts.push(`${hidden.length} smaller categories are not listed, holding ${tail.toLocaleString()} further openings.`);
  if (nt && trendMetric === 'stock') parts.push(`${nt.toLocaleString()} further rows sit in non-tech categories and are excluded here.`);
  el('trends-foot').textContent = parts.join(' ');

  // The by-level / by-role toggle only exists inside a family that has watched roles.
  const seg = el('trends-split');
  if (seg) seg.hidden = !(trendDrill && (d.watch_parents || []).includes(trendDrill));
}

function trendClick(name){
  if (trendDrill) { loadTrends(null); return; }        // already drilled in — go back up
  const s = trendData.series.find(x => x.name === name);
  if (s && trendData.series.indexOf(s) < CHART_MAX) { loadTrends(name); return; }
  trendHidden.delete(name); drawTrends();              // off-chart series: bring it into view
}

// The three segmented toggles share one wiring: press -> update state -> refetch.
function trendSeg(id, attr, apply){
  const seg = el(id); if (!seg) return;
  seg.addEventListener('click', e => {
    const b = e.target.closest(`button[data-${attr}]`);
    if (!b || b.getAttribute('aria-pressed') === 'true') return;
    seg.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', x === b));
    apply(b.dataset[attr]);
  });
}
// Share divides by the STOCK total, so it is meaningless against "new" — a share of new
// openings over all live ones is a number with no reading. The unit segment is therefore
// locked there. `disabled`, not just pointer-events: the latter stops the mouse and not the
// keyboard, and a focused Space press would have plotted new counts over stock totals.
function setUnit(value, locked){
  trendUnit = value;
  const seg = el('trends-unit'); if (!seg) return;
  seg.style.opacity = locked ? .4 : '';
  seg.querySelectorAll('button').forEach(b => {
    b.disabled = locked;
    b.setAttribute('aria-pressed', b.dataset.unit === value);
  });
}
trendSeg('trends-metric', 'metric', v => {
  trendMetric = v;
  setUnit(v === 'new' ? 'count' : 'share', v === 'new');
  loadTrends(trendDrill);
});
trendSeg('trends-unit', 'unit', v => { setUnit(v, false); drawTrends(); });
trendSeg('trends-split', 'split', v => { trendSplit = v; loadTrends(trendDrill); });

function initAlerts(){
  if (!window.google || !CFG.google_client_id) return;
  google.accounts.id.initialize({ client_id: CFG.google_client_id, callback: onGoogleCredential });
  google.accounts.id.renderButton(el('gsignin'), { theme: 'outline', size: 'medium' });
}
// One listener on the list itself — it survives every innerHTML redraw of its children.
if (el('trends-legend')) el('trends-legend').addEventListener('click', e => {
  const li = e.target.closest('li[data-name]');
  if (li) trendClick(li.dataset.name);
});
if (el('sets-strip')) el('sets-strip').addEventListener('click', e => {
  const btn = e.target.closest('[data-act]');
  if (btn) handleSetAction(btn.dataset.act, btn.dataset.id);
});
// Stars appear in three containers (Search, Matches, Saved); one document-level listener
// survives every redraw of all of them.
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-star]');
  if (b) toggleStar(b.dataset.star);
});
if (el('pparse')){
  el('pparse').addEventListener('click', parseResume);
  el('psave').addEventListener('click', saveProfile);
  el('papply').addEventListener('click', applyProfile);
  el('pdelete').addEventListener('click', deleteProfile);
}
if (el('matches-controls')){
  const rerun = () => { if (activeSetId) runSet(activeSetId); };
  // sort is a client-side reorder; a range change re-queries — both go through runSet
  // so the count message and rows always agree.
  ['msort','mposted-from','mposted-to','mseen-from','mseen-to'].forEach(id => {
    if (el(id)) el(id).addEventListener('change', rerun);
  });
  el('mdir').addEventListener('click', () => {
    const b = el('mdir');
    const dir = b.dataset.dir === 'desc' ? 'asc' : 'desc';
    b.dataset.dir = dir;
    b.textContent = dir === 'desc' ? '↓' : '↑';
    b.setAttribute('aria-label', 'Sort direction, currently ' +
      (dir === 'desc' ? 'descending' : 'ascending'));
    rerun();
  });
  el('mclear').addEventListener('click', () => {
    ['mposted-from','mposted-to','mseen-from','mseen-to'].forEach(id => {
      if (el(id)) el(id).value = '';
    });
    rerun();
  });
}
showIntro();
whoAmI();
showTab(currentTab());
// Result cards need star states before the Saved tab is ever opened; landing ON the tab
// already loads via showTab above.
if (CAN_STAR && currentTab() !== 'saved') loadSaved();
