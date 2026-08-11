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
// Match strength is a QUANTITY, so it gets a sequential ramp — one hue, increasing intensity —
// not four different hues. Hue is reserved for categories (amber = new, lime = pays, violet =
// remote); reusing those hues here would have made lime mean both "strong match" and "salary".
// Weak matches fade toward the muted ink so a scan shows where the good results stop.
const tone = s => {
  const pct = Math.round(Math.max(0, Math.min(1, (s - .35) / .45)) * 100);
  return `color-mix(in srgb, var(--aqua) ${25 + pct * .75}%, var(--ink-3))`;
};
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
  ats:'Board', etype:'Type', india:'India', location:'Location', company:'Company',
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

function draw(rows){
  if (el('sort').value === 'new'){
    const ts = r => { const t = Date.parse(r.posted_at || ''); return isNaN(t) ? -1 : t; };
    rows = rows.slice().sort((a,b) => ts(b) - ts(a));   // unparseable dates sink to the bottom
  }
  el('results').innerHTML = rows.map((r,i) => {
    const s = Number(r.score) || 0, c = tone(s);
    return `
    <div class="card" style="--tone:${c}; animation-delay:${Math.min(i,12)*35}ms">
      <div class="hd">
        <div style="flex:1; min-width:0">
          <a class="title" href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener">${esc(r.title)}</a>
          <div class="org">${esc(r.company)}${r.location? ' <span>·</span> '+esc(r.location) : ''}</div>
        </div>
        <div class="match" title="Semantic similarity to your search">
          <div class="v">${s.toFixed(2)}</div>
          <div class="track"><div class="fill" style="--pct:${Math.round(Math.max(0,Math.min(1,s))*100)}%"></div></div>
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

// Résumé → Query: fills the search box for the user to edit, never searches on its own.
// The password rides along when filled; the server remembers this IP after one success.
async function readResume(){
  const text = el('resume').value.trim();
  const msg = el('rmsg'), btn = el('rbtn');
  if (!text){ msg.textContent = 'Paste your résumé first.'; return; }
  btn.disabled = true; msg.textContent = 'Reading…';
  try {
    const r = await fetch('/resume-to-query', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text, password: el('rpass').value })
    });
    const data = await r.json();
    if (!r.ok){ msg.textContent = data.error || ('Failed (' + r.status + ')'); return; }
    el('q').value = data.query;
    el('q').focus();
    msg.textContent = 'Edit the search above, then hit Search.';
  } catch(e){ msg.textContent = 'That request didn\'t go through. Try again.'; }
  finally { btn.disabled = false; }
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
/* ---- role trends (ADR-0040). The ledger is one count per (family, band) per pipeline run;
   this draws a line per series and ranks them by how much they moved across the window. ---- */
const TREND_COLOURS = ['#12D6B4','#8B5CF6','#F59E0B','#A3E635','#FB7185','#60A5FA','#F472B6','#34D399'];
let trendData = null, trendDrill = null, trendHidden = new Set();

async function loadTrends(family){
  const r = await fetch('/trends' + (family ? '?family=' + encodeURIComponent(family) : ''));
  if (!r.ok) { el('trends').style.display = 'none'; return; }
  trendData = await r.json(); trendDrill = family || null; trendHidden = new Set();
  drawTrends();
}

// A series' movement across the window. Uses its first and last MEASURED points, so a run that
// skipped a family doesn't read as a crash to zero.
function trendDelta(points){
  const seen = points.filter(p => p != null);
  if (seen.length < 2) return null;
  const first = seen[0], last = seen[seen.length-1];
  if (!first) return null;
  return (last - first) / first * 100;
}

function drawTrends(){
  const d = trendData; if (!d) return;
  const W = 720, H = 260, PAD_L = 44, PAD_B = 18, PAD_T = 8;
  const shown = d.series.filter(s => !trendHidden.has(s.name)).slice(0, 8);
  const vals = shown.flatMap(s => s.points).filter(v => v != null);
  const lo = 0, hi = Math.max(1, ...vals);
  const x = i => PAD_L + (d.stamps.length < 2 ? 0 : i * (W - PAD_L - 6) / (d.stamps.length - 1));
  const y = v => PAD_T + (H - PAD_T - PAD_B) * (1 - (v - lo) / (hi - lo));

  let svg = '';
  for (let g = 0; g <= 4; g++){                       // horizontal gridlines + y labels
    const v = hi * g / 4, yy = y(v);
    svg += `<line class="gridline" x1="${PAD_L}" y1="${yy}" x2="${W}" y2="${yy}"/>`;
    svg += `<text class="axis-label" x="0" y="${yy+3}">${Math.round(v).toLocaleString()}</text>`;
  }
  shown.forEach((s, i) => {
    const c = TREND_COLOURS[i % TREND_COLOURS.length];
    // break the path at gaps rather than bridging them — an unmeasured run is not a value
    let path = '', pen = 'M';
    s.points.forEach((v, j) => { if (v == null) { pen = 'M'; return; } path += `${pen}${x(j).toFixed(1)},${y(v).toFixed(1)} `; pen = 'L'; });
    svg += `<path d="${path.trim()}" fill="none" stroke="${c}" stroke-width="2"
             stroke-linejoin="round" stroke-linecap="round"/>`;
    const last = s.points.map((v,j)=>[v,j]).filter(([v])=>v!=null).pop();
    if (last) svg += `<circle cx="${x(last[1]).toFixed(1)}" cy="${y(last[0]).toFixed(1)}" r="3" fill="${c}"/>`;
  });
  el('trends-chart').innerHTML = svg;

  el('trends-legend').innerHTML = d.series.slice(0, 12).map((s, i) => {
    const c = TREND_COLOURS[i % TREND_COLOURS.length];
    const dl = trendDelta(s.points);
    const cls = dl == null ? 'flat' : dl > 1 ? 'up' : dl < -1 ? 'down' : 'flat';
    const txt = dl == null ? '—' : (dl > 0 ? '+' : '') + dl.toFixed(1) + '%';
    const off = trendHidden.has(s.name) || i >= 8;
    return `<li onclick="trendClick('${esc(s.name)}')" aria-pressed="${!off}"
      style="${off?'opacity:.45':''}"><span class="swatch" style="background:${i<8?c:'var(--ink-3)'}"></span>
      <span class="nm" title="${esc(s.label)}">${esc(s.label)}</span>
      <span class="ct">${(s.latest||0).toLocaleString()}</span>
      <span class="dl ${cls}">${txt}</span></li>`;
  }).join('');

  const runs = d.stamps.length;
  el('trends-scope').textContent = trendDrill
    ? 'by experience level — click to go back'
    : `${d.series.length} categories · ${runs} measurement${runs===1?'':'s'}`;
  el('trends-empty').textContent = runs < 2
    ? 'Only one measurement so far — trend lines appear once the pipeline has run a few more times.'
    : '';
  const nt = d.non_tech.filter(v => v != null).pop();
  el('trends-foot').textContent = nt
    ? `Counts are live openings in the index, re-measured every pipeline run. ${nt.toLocaleString()} further rows sit in non-tech categories and are excluded here.`
    : 'Counts are live openings in the index, re-measured every pipeline run.';
}

function trendClick(name){
  if (trendDrill) { loadTrends(null); return; }        // already drilled in — go back up
  const s = trendData.series.find(x => x.name === name);
  if (s && trendData.series.indexOf(s) < 8) { loadTrends(name); return; }
  trendHidden.delete(name); drawTrends();              // off-chart series: bring it into view
}

function initAlerts(){
  if (!window.google || !CFG.google_client_id) return;
  google.accounts.id.initialize({ client_id: CFG.google_client_id, callback: onGoogleCredential });
  google.accounts.id.renderButton(el('gsignin'), { theme: 'outline', size: 'medium' });
}
showIntro();
whoAmI();
showTab(currentTab());
