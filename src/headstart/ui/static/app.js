// HeadStart page behaviour. Server-varying config arrives as window.CFG (set inline by
// base.html); everything else is static. Tabs are hash-routed panels — no server round trip.
const CFG = window.CFG || {};
const el = s => document.getElementById(s);
// Job text is scraped third-party content — escape before it reaches innerHTML, and only
// allow http(s) hrefs (no javascript: URLs).
const esc = s => (s==null?'':String(s)).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = u => { const l=(u||'').toLowerCase(); return (l.startsWith('http://')||l.startsWith('https://'))? u : '#'; };
for (const id of ['q', 'kw']) el(id).addEventListener('keydown', e => { if (e.key === 'Enter') go(); });

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
// `salary` (the raw display string, per-ATS formatted) is only ever populated from a
// scraper's own structured field — most of this initiative's own measured salary coverage
// comes from Tier-2 description-mining instead (ADR-0082), which only ever reaches
// min_salary_annual/max_salary_annual/salary_currency. Fall back to building a label from
// those so a Tier-2-only Job still shows a pay tag instead of silently showing none.
const payLabel = r => {
  if (r.salary) return r.salary;
  if (r.min_salary_annual == null) return '';
  const fmt = n => Number(n).toLocaleString();
  const cur = r.salary_currency ? r.salary_currency + ' ' : '';
  const range = (r.max_salary_annual != null && r.max_salary_annual !== r.min_salary_annual)
    ? fmt(r.min_salary_annual) + '–' + fmt(r.max_salary_annual)
    : fmt(r.min_salary_annual) + '+';
  return cur + range + '/yr';
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
  // The Keyword filter (ADR-0104). Its scope is a modifier, sent only beside a keyword — on its
  // own it filters nothing, and the server nulls it anyway (matches filter_kwargs) — and only
  // when it is not the default, which the server already assumes: that keeps a "Look in: Title"
  // pill off every plain keyword search.
  if (el('kw').value.trim()){
    f.kw = el('kw').value.trim();
    if (el('kwin').value !== CFG.keyword_default_scope) f.kw_in = el('kwin').value;
  }
  if (el('posted').value) f.posted_within = el('posted').value;
  if (el('seen') && el('seen').value) f.seen_within = el('seen').value;
  if (el('salmin') && el('salmin').value) f.salary_min = el('salmin').value;
  if (el('salmax') && el('salmax').value) f.salary_max = el('salmax').value;
  // Only meaningful alongside a bound: salaries are never FX-converted, so the currency
  // scopes a bracket rather than filtering on its own (matches build_filter's own guard).
  if (el('salcur') && (f.salary_min || f.salary_max)) f.salary_currency = el('salcur').value;
  return f;
}
const LABELS = { remote:'Remote', has_salary:'Shows salary', max_years:'Your experience',
  kw:'Keyword', kw_in:'Look in',
  ats:'ATS provider', etype:'Type', india:'India', location:'Location', company:'Company',
  posted_within:'Posted ≤', seen_within:'First seen ≤',
  salary_min:'Salary from', salary_max:'Salary to', salary_currency:'Currency' };
// `salary_currency` is deliberately absent: it has a default (USD) rather than an empty
// state, so clearAll() blanking it would leave the picker showing nothing. Clearing the two
// bounds already switches the bracket off, which is what "clear" has to mean here.
// `kw_in` is likewise absent: it has a default (Title), not an empty state — dropping the
// keyword is what switches the scope off, so dropFilter maps it onto `kw` below.
const CONTROL = { remote:'remote', has_salary:'hassalary', max_years:'maxyears', ats:'ats', kw:'kw',
  etype:'etype', india:'india', location:'location', company:'company',
  posted_within:'posted', seen_within:'seen', salary_min:'salmin', salary_max:'salmax' };
function drawActive(){
  const f = currentFilters(), box = el('active');
  box.innerHTML = Object.entries(f).map(([k,v]) =>
    `<span class="pill"><b>${esc(LABELS[k]||k)}</b> ${esc(v === 'true' ? 'yes' : v)}` +
    `<button onclick="dropFilter('${esc(k)}')" aria-label="Remove ${esc(LABELS[k]||k)} filter">×</button></span>`
  ).join('');
}
const BRACKET = ['salary_min', 'salary_max', 'salary_currency'];
function dropFilter(key){
  // The currency picker has a default, not an empty state, so there is nothing to blank on it.
  // Dropping any part of the bracket therefore means clearing the two bounds it scopes — which
  // is also what switches the bracket off server-side.
  const keys = BRACKET.includes(key) ? ['salary_min', 'salary_max'] : (key === 'kw_in' ? ['kw'] : [key]);
  let cleared = false;
  for (const k of keys){
    const c = el(CONTROL[k]); if (!c) continue;
    if (c.type === 'checkbox') c.checked = false; else c.value = '';
    cleared = true;
  }
  if (cleared) go();
}
function clearAll(){
  Object.values(CONTROL).forEach(id => { const c = el(id); if (!c) return;
    if (c.type === 'checkbox') c.checked = false; else c.value = ''; });
  go();
}

// Pagination (ADR-0074): fixed page size, capped page count — matches the server's own
// `max_k`/`max_page` clamp in headstart.search.JobSearch, so a click here never asks for
// something the server would silently clamp anyway.
const PAGE_SIZE = 20;
const MAX_PAGE = 20;
let page = 1;

// go(): a fresh search or browse from page 1 — Search button, Enter, a chip, or a filter
// change. An empty query browses the newest jobs instead of ranking by similarity
// (ADR-0074), so this always fetches; it never shows a static empty state.
async function go(){ page = 1; await fetchPage(); }

// goToPage(n): re-fetch the SAME query and filters at a different page. Never resets page 1
// itself, so Prev/Next can't fight a fresh go() call.
async function goToPage(n){ page = Math.max(1, Math.min(n, MAX_PAGE)); await fetchPage(); }

async function fetchPage(){
  const q = el('q').value.trim();
  drawActive();
  const p = new URLSearchParams({ q, k: PAGE_SIZE, page });
  for (const [key, value] of Object.entries(currentFilters())) p.set(key, value);
  if (el('sort').value !== 'rel') p.set('sort', el('sort').value);
  el('results').innerHTML = skeleton() + skeleton() + skeleton();
  el('pager').innerHTML = '';
  el('n').textContent = q ? 'searching…' : 'loading…';
  // Fired together, not one after the other: the counts depend only on the filters, never on
  // the query, so they neither wait for the ranking nor make the user wait for them.
  const facetsPromise = fetch('/facets?'+p).then(r => r.json()).catch(() => null);
  facetsPromise.then(applyFacets);
  drawSortNote();
  let rows;
  try { rows = await (await fetch('/search?'+p)).json(); }
  catch(e){ el('results').innerHTML = '<div class="empty">That search didn\'t go through. Try again.</div>';
            el('n').textContent = ''; return; }
  if(!Array.isArray(rows)){
    el('results').innerHTML = '<div class="empty">One of the filters isn\'t valid — clear it and try again.</div>';
    el('n').textContent = ''; return; }
  const facets = await facetsPromise;
  drawKeywordNote(facets);
  if(!rows.length){
    el('results').innerHTML = page === 1
      ? '<div class="empty"><div class="big">Nothing matched</div>' + whyNothing(facets) + '</div>'
      : '<div class="empty"><div class="big">No more jobs</div>' +
        'You\'ve reached the end of these results.</div>';
    el('n').textContent = page === 1 ? '0 results' : '';
    drawPager(0, facets);
    return; }
  drawCount(rows.length, facets);
  draw(rows);
  drawPager(rows.length, facets);
}

// "Showing 1–20 of 40,807 matching your filters". The qualifier is not padding: a vector
// search RANKS the filtered set rather than shrinking it, so the total counts rows matching
// the filters, and the query decides only their order. Calling it "results for your query"
// would promise a relevance the number never measured.
function drawCount(shown, facets){
  if (!facets || typeof facets.total !== 'number'){
    el('n').textContent = shown + ' result' + (shown===1?'':'s'); return; }
  const from = (page-1)*PAGE_SIZE + 1, to = (page-1)*PAGE_SIZE + shown;
  el('n').textContent = `Showing ${from.toLocaleString()}–${to.toLocaleString()} of ` +
    `${facets.total.toLocaleString()} matching your filters`;
}

// The sort caveat, written on EVERY fetch rather than only alongside a count — the zero-row
// path and a failed /facets both skip drawCount, and a note left over from the previous search
// is worse than none.
//
// Deliberately no row count in it. The window the server ranks before re-sorting is its own
// max_k * max_page, which this page cannot see — PAGE_SIZE * MAX_PAGE is a different number
// (400 against 2,000) and printing it would state the caveat with the wrong figure. The fact is
// what the user needs: this is newest among their best matches, not a global date sort.
function drawSortNote(){
  const sort = el('sort').value, q = el('q').value.trim();
  el('sortnote').textContent = sort === 'rel' ? ''
    : (q ? 'newest among your best matches — not a global date sort' : 'newest first');
}

// When a search returns nothing, name the one filter that costs the most rather than telling
// the user to go and guess. `blocking` is the server's own answer: the active filter whose
// removal recovers the most results (headstart.facets), so the advice is measured, not guessed.
function whyNothing(facets){
  const key = facets && facets.blocking;
  if (!key) return 'Try loosening a filter, or describe the role more broadly.';
  const label = LABELS[key] || key;
  return `Your <b>${esc(label)}</b> filter is the one ruling everything out — ` +
    `<button class="linkish" onclick="dropFilter('${esc(key)}')">remove it</button> ` +
    'to see what comes back.';
}

// ── Facet counts (issue #275) ────────────────────────────────────────────────────────────
// Every filter option carries the number of jobs it would actually return, so the user can
// see what a filter costs BEFORE spending a click on it. Counts come from /facets with each
// dimension's own constraint lifted, which is why the ATS strip answers "how many if I
// switched to Greenhouse" rather than repeating the current total once per option.
//
// A zero option is disabled rather than hidden: hiding it would silently rewrite the control
// under the user's cursor, and "Contract (0)" is itself the answer to "why no contract jobs".
const FACET_CONTROL = { seen_within:'seen', posted_within:'posted', etype:'etype', ats:'ats' };

// The base label, stashed the first time so repeated renders never append count onto count.
function baseLabel(node){
  if (node.dataset.baseLabel === undefined) node.dataset.baseLabel = node.textContent;
  return node.dataset.baseLabel;
}
const withCount = (label, n) => `${label} (${n.toLocaleString()})`;

function applyFacets(facets){
  if (!facets || !facets.facets) return;
  const f = facets.facets;
  for (const [dimension, id] of Object.entries(FACET_CONTROL)){
    const sel = el(id); if (!sel || !f[dimension]) continue;
    const byValue = new Map(f[dimension].map(o => [String(o.value), o.count]));
    for (const opt of sel.options){
      const label = baseLabel(opt);
      // The "Any" row is counted server-side with this dimension lifted (ADR-0084) and comes
      // back keyed `null`. Using `facets.total` here instead would print the CONSTRAINED total,
      // so an active 2-hour window would make "Any time" read smaller than the 24-hour option
      // nested inside it.
      const n = opt.value === '' ? byValue.get('null') : byValue.get(opt.value);
      if (n === undefined) continue;
      opt.textContent = withCount(label, n);
      if (opt.value === ''){ opt.disabled = false; continue; }
      // never disable what is currently selected — that would strand the user on an option
      // they cannot see the name of, and its own count is legitimately the current total
      opt.disabled = n === 0 && opt.value !== sel.value;
    }
  }
  countSwitch('remote', f.remote);
  countSwitch('hassalary', f.has_salary);
  countYears(f.max_years);
}

// The Keyword filter's disclaimer (ADR-0104). Not every Job carries a description — none indexed
// before the column existed do, nor any whose detail pass found nothing — so a keyword looked for
// in descriptions can only ever match the share that has one. Quantified rather than static:
// `description_coverage` is `{covered, total}` — the rows the OTHER filters match that carry a
// description, and all the rows they match — counted with the keyword lifted, so the share stays
// meaningful while a keyword is applied (with it intact, a description-scoped keyword matches only
// rows that have one, and the note would read "N of N"). Its `total` is therefore not the header's.
// Which scopes carry it comes from CFG.keyword_scopes (the server's scope map), never from a scope
// name hard-coded here.
//
// Written on EVERY fetch, like drawSortNote: a note left over from the previous search is worse
// than none. Three states, kept distinct — /facets failed (no numbers to show, say the fact
// plainly), the column does not exist yet (`null`), and a real count.
function drawKeywordNote(facets){
  const note = el('kwnote'), scope = el('kwin');
  const needs = (CFG.keyword_scopes || {})[scope.value];
  if (!needs){ note.textContent = ''; return; }
  if (!facets){
    note.textContent = 'Only jobs with a stored description can match a keyword here — not every job has one.';
    return; }
  if (facets.description_coverage === null || facets.description_coverage === undefined){
    note.textContent = 'Matching inside descriptions isn\'t available yet — descriptions are still being added to the index.';
    return; }
  const { covered, total } = facets.description_coverage;
  note.textContent = typeof covered === 'number' && typeof total === 'number'
    ? `Descriptions are stored for ${covered.toLocaleString()} of the ${total.toLocaleString()} jobs your other filters match — ` +
      'a keyword looked for here can only match inside those; the rest have no stored description.'
    : 'Only jobs with a stored description can match a keyword here — not every job has one.';
}

// The two checkboxes are labels, not option lists — their single count goes on the text.
function countSwitch(id, options){
  const box = el(id); if (!box || !options || !options.length) return;
  // The count goes in its own node rather than onto whichever text node happens to sit last
  // inside the label — reaching for `lastChild` breaks silently the first time anything else
  // is appended there, and a wrong count is worse than none.
  const span = box.parentElement;
  let tag = span.querySelector('.fcount');
  if (!tag){ tag = document.createElement('span'); tag.className = 'fcount'; span.appendChild(tag); }
  tag.textContent = ` (${options[0].count.toLocaleString()})`;
}

// `maxyears` is a free number input, so its counts cannot ride on options. They go under it
// as a hint instead — the same information, in the only shape the control allows.
function countYears(options){
  const input = el('maxyears'); if (!input || !options) return;
  let hint = el('yearscount');
  if (!hint){
    hint = document.createElement('span');
    hint.id = 'yearscount'; hint.className = 'tip counts';
    input.insertAdjacentElement('afterend', hint);
  }
  hint.textContent = options.map(o => `${o.label}: ${o.count.toLocaleString()}`).join(' · ');
}

// A short page (fewer than PAGE_SIZE rows) is how "no next page" is known — there is no
// total-count query on the server (ADR-0074), so this is the only signal available.
function drawPager(rowCount, facets){
  const total = facets && typeof facets.total === 'number' ? facets.total : null;
  // A short page still means "no next page"; the total, new in issue #275, additionally rules
  // out a next page whose rows exist but sit past what ADR-0074 lets pagination address.
  const hasPrev = page > 1;
  const hasNext = rowCount === PAGE_SIZE && page < MAX_PAGE &&
    (total === null || page * PAGE_SIZE < total);
  el('pager').innerHTML =
    `<button class="ghost" ${hasPrev?'':'disabled'} onclick="goToPage(${page-1})">‹ Prev</button>` +
    `<span class="note">Page ${page}</span>` +
    `<button class="ghost" ${hasNext?'':'disabled'} onclick="goToPage(${page+1})">Next ›</button>`;
}

function draw(rows, target){
  // No client-side reorder any more. It only ever sorted the twenty rows already fetched,
  // which reads as "sort my results" and is not: the server now orders the whole result set
  // (issue #275), so by the time rows arrive they are already in the asked-for order.
  rows.forEach(r => { if (r.id) drawnRows.set(r.id, r); });   // starring needs the row later
  el(target || 'results').innerHTML = rows.map((r,i) => {
    // A browsed row (no query) was never ranked, so it carries no score (ADR-0074) — the
    // match ring would otherwise show a misleading "0%" rather than "not applicable".
    const ranked = r.score != null;
    const s = Number(r.score) || 0, pct = matchPct(s);
    return `
    <div class="card" style="${ranked?`--tone:${tone(s)}; `:''}animation-delay:${Math.min(i,12)*35}ms">
      <div class="hd">
        <div style="flex:1; min-width:0">
          <a class="title" href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener">${esc(r.title)}</a>
          <div class="org">${esc(r.company)}${r.location? ' <span>·</span> '+esc(r.location) : ''}</div>
        </div>
        ${starBtn(r.id)}
        ${ranked? `<div class="match" title="Match strength — semantic similarity ${s.toFixed(2)}, scaled to this index's real range">
          <svg class="ring" viewBox="0 0 40 40" aria-hidden="true">
            <circle class="ring-track" cx="20" cy="20" r="16" pathLength="100"/>
            <circle class="ring-fill" cx="20" cy="20" r="16" pathLength="100" style="--p:${pct}"/>
          </svg>
          <div class="v">${pct}%</div>
        </div>` : ''}
      </div>
      <div class="tags">
        ${isNew(r.first_seen)? '<span class="tag new">new</span>':''}
        ${r.remote? '<span class="tag rem">remote</span>':''}
        ${payLabel(r)? '<span class="tag pay">'+esc(payLabel(r))+'</span>':''}
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
// Ranges go to the server (a range must filter before ranking); sort here is a client-side
// reorder of the rows already fetched — unlike the Search tab, whose sort is server-side.
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
  // TODO: a Tier-2-only pay tag (payLabel(), above) is real in search results but is lost
  // once starred — SavedJob (alerts/store.py) only ever persists a raw `salary` string, so
  // there's nowhere to carry min_salary_annual/max_salary_annual/salary_currency through.
  // Fixing this needs a SavedJob schema change, not a display fix; out of scope here.
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
   other. "New" is charted as counts: its meaning ("312 fresh AI/ML roles") IS the number.

   Redesigned 2026-08-20 against the dataviz skill's method and
   docs/product/2026-08-20_trends-ui-design-research.md (WAI-ARIA APG, FT/Datawrapper/NN·g).
   Colour is 8 CSS custom properties (--series-1..8, style.css) from a CVD-validated categorical
   palette, read live via getComputedStyle so a mid-session theme flip repaints correctly —
   never a hardcoded hex array. Assignment is by ENTITY: `assignSeriesColors` caches name -> slot
   the first time a name is drawn and keeps it for as long as that name keeps appearing, so a
   Metric/Unit/ATS change that merely reshuffles the ranking never repaints a still-visible
   category (the "recolor-on-filter" failure mode: a reader who learned "AI/ML is violet" must
   not have it turn blue because a filter changed who's biggest). ---- */
let trendData = null, trendDrill = null;
let trendMetric = 'stock', trendUnit = 'share', trendSplit = 'bands';
let hoveredSeries = null;   // legend/chart hover-focus name — dims every other line (§ emphasis)
let tableView = false;      // the WCAG-clean twin of the chart, independent of the SVG
let lastGeom = null;        // scales + resolved values from the last drawTrends() — hover reads this
const CHART_MAX = 8;        // matches the 8-slot validated categorical palette

const seriesColorAssignment = new Map();   // family/role name -> slot index, sticky per entity
function seriesColor(slot){
  return getComputedStyle(document.documentElement).getPropertyValue(`--series-${slot + 1}`).trim();
}
// Keep whatever slot a name already had; hand any new name the lowest slot nothing currently
// visible is using. Names no longer drawn are dropped so their slot can be reused — the map only
// ever holds the (at most CHART_MAX) names on screen right now, never a 24-family reservation.
function assignSeriesColors(names){
  for (const name of [...seriesColorAssignment.keys()]) if (!names.includes(name)) seriesColorAssignment.delete(name);
  const used = new Set(seriesColorAssignment.values());
  for (const name of names){
    if (seriesColorAssignment.has(name)) continue;
    let slot = 0; while (used.has(slot)) slot++;
    seriesColorAssignment.set(name, slot);
    used.add(slot);
  }
}
// Everything past CHART_MAX, folded into one honest aggregate row instead of the N real-looking
// legend rows the previous design left permanently inert (a documented dead-click: dataviz
// skill anti-patterns, "cycling past 8" — fold the tail into "Other," don't seat a 9th).
// A stamp where every hidden series is unmeasured stays null, so the aggregate's line breaks at
// the same points a real series' would; where some measured and others didn't, an unmeasured
// series contributes 0 rather than being excluded — a display approximation, not a data claim.
function buildOtherSeries(rest){
  if (!rest.length) return null;
  const points = trendData.stamps.map((_, j) => {
    const anyMeasured = rest.some(s => s.points[j] != null);
    if (!anyMeasured) return null;
    return rest.reduce((sum, s) => sum + (s.points[j] || 0), 0);
  });
  const latest = rest.reduce((sum, s) => sum + (s.latest || 0), 0);
  return { name: '__other__', label: `Other (${rest.length} smaller categor${rest.length===1?'y':'ies'})`, points, latest };
}

// The chart and the table view both need "the top CHART_MAX, plus Other" — one place computes
// it so the split can't quietly drift between the two renderers.
function chartedAndOther(d){
  const charted = d.series.slice(0, CHART_MAX);
  const other = buildOtherSeries(d.series.slice(CHART_MAX));
  return { charted, other, shown: other ? [...charted, other] : charted };
}

// The window inputs go to the server — a range must filter before the share denominator is
// computed, not after. `datetime-local` reads in the browser's local zone; converted to UTC so
// the filter lines up with the chart's own UTC axis (stampLabel already renders in UTC).
function trendRange(){
  const r = {};
  const since = el('trends-since') && el('trends-since').value;
  const until = el('trends-until') && el('trends-until').value;
  if (since) r.since = new Date(since).toISOString();
  if (until) r.until = new Date(until).toISOString();
  return r;
}

// Checked ATS names, or null when every box is checked — the only spelling of "no filter"
// (ADR-0075): sending all of them explicitly would exclude pre-ship, undecomposed rows.
function trendAtsSelected(){
  const menu = el('trends-ats-menu'); if (!menu) return null;
  const boxes = [...menu.querySelectorAll('input[type=checkbox]')];
  const checked = boxes.filter(b => b.checked).map(b => b.value);
  return checked.length === boxes.length ? null : checked;
}

function trendAtsLabel(){
  const menu = el('trends-ats-menu'); if (!menu) return;
  const boxes = menu.querySelectorAll('input[type=checkbox]');
  const n = [...boxes].filter(b => b.checked).length;
  el('trends-ats-trigger').textContent = (n === boxes.length ? 'All ATS' : `${n} ATS`) + ' ▾';
}

function toggleAtsPopover(force){
  const open = force ?? el('trends-ats-menu').hidden;
  el('trends-ats-menu').hidden = !open;
  el('trends-ats-trigger').setAttribute('aria-expanded', open);
}

async function loadTrends(family){
  const q = new URLSearchParams();
  // The roles split only exists for families that HAVE watched roles. Carrying a sticky
  // 'roles' into one that doesn't returns an empty series with its toggle hidden — nothing
  // to click, nothing to go back from, and only a reload escapes.
  if (family && !(trendData?.watch_parents || []).includes(family)) trendSplit = 'bands';
  if (family) { q.set('family', family); q.set('split', trendSplit); }
  if (trendMetric !== 'stock') q.set('metric', trendMetric);
  const range = trendRange();
  if (range.since) q.set('since', range.since);
  if (range.until) q.set('until', range.until);
  const ats = trendAtsSelected();
  if (ats) ats.forEach(a => q.append('ats', a));
  // Refetch keeps the frame (dataviz skill, interaction.md): the previous render stays up,
  // dimmed, rather than the panel blanking — a filter change must never read as "it broke"
  // while the round trip is in flight, and a genuine failure must never look like a dead toggle.
  if (el('trends-viz')) el('trends-viz').classList.add('loading');
  let r;
  try { r = await fetch('/trends' + (q.size ? '?' + q : '')); }
  catch(e){ showTrendsError('That request didn’t go through.'); return; }
  if (!r.ok){
    showTrendsError(r.status === 401
      ? 'Your session expired — sign in again to see trends.'
      : 'Trends didn’t load. Try again.');
    return;
  }
  hideTrendsError();
  trendData = await r.json(); trendDrill = family || null;
  drawTrends();
}

// A failed fetch used to hide the whole #trends section — every toggle then looked locked with
// no explanation. Now only the chart/legend area is replaced; the header, toggles and Retry stay
// reachable, and Retry replays the exact same request `loadTrends` just made.
function showTrendsError(msg){
  if (el('trends-viz')){ el('trends-viz').classList.remove('loading'); el('trends-viz').hidden = true; }
  if (el('trends-error')){ el('trends-error').hidden = false; el('trends-error-msg').textContent = msg; }
}
function hideTrendsError(){
  if (el('trends-error')) el('trends-error').hidden = true;
  if (el('trends-viz')){ el('trends-viz').hidden = false; el('trends-viz').classList.remove('loading'); }
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
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function stampLabel(ts){
  const d = new Date(ts);
  if (isNaN(d)) return '';
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()} ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;
}

function fmtValue(v){
  if (trendUnit === 'share') return v >= 10 ? v.toFixed(0) + '%' : v.toFixed(1) + '%';
  return Math.round(v).toLocaleString();
}

// Shared by every place that flips a radiogroup button's checked state — keeps the three call
// sites (drawTrends' split-toggle sync, trendSeg's select, setUnit) from drifting apart on the
// aria-checked/roving-tabindex pair.
function setRadioChecked(btn, on){
  btn.setAttribute('aria-checked', on);
  btn.tabIndex = on ? 0 : -1;
}

function drawTrends(){
  const d = trendData; if (!d) return;
  const W = 720, H = 260, PAD_L = 44, PAD_B = 18, PAD_T = 8;
  const { charted, other, shown } = chartedAndOther(d);

  assignSeriesColors(charted.map(s => s.name));   // Other is a bucket, not an entity — no slot

  const vals = shown.flatMap(s => s.points.map((v, j) => trendValue(v, j))).filter(v => v != null);
  const lo = 0;
  let hi = Math.max(trendUnit === 'share' ? 0.1 : 1, ...vals);
  // Datawrapper's line-chart guidance: extend the axis to 100% once a share reading comes
  // close to it, so the reader sees the true ceiling rather than an axis that implies more
  // headroom exists. Left alone otherwise — most shares here are single digits, and forcing
  // every chart to a 0-100 scale would flatten the small-value comparisons this page is for.
  if (trendUnit === 'share' && hi > 80) hi = 100;
  const x = i => PAD_L + (d.stamps.length < 2 ? 0 : i * (W - PAD_L - 6) / (d.stamps.length - 1));
  const y = v => PAD_T + (H - PAD_T - PAD_B) * (1 - (v - lo) / (hi - lo));

  let svg = '';
  for (let g = 0; g <= 4; g++){                       // horizontal gridlines + y labels
    const v = hi * g / 4, yy = y(v);
    svg += `<line class="gridline" x1="${PAD_L}" y1="${yy}" x2="${W}" y2="${yy}"/>`;
    svg += `<text class="axis-label" x="0" y="${yy+3}">${fmtValue(v)}</text>`;
  }
  // Time axis: first / middle / last measurement, so the window is visible on the chart
  // itself rather than inferred. Sparse on purpose — 19 stamps don't need 19 ticks.
  if (d.stamps.length > 1){
    const ticks = [0, Math.floor((d.stamps.length - 1) / 2), d.stamps.length - 1];
    for (const [ti, anchor] of [[ticks[0],'start'],[ticks[1],'middle'],[ticks[2],'end']]){
      svg += `<text class="axis-label" x="${x(ti).toFixed(1)}" y="${H-4}"
               text-anchor="${anchor}">${stampLabel(d.stamps[ti])}</text>`;
    }
  }

  const geomSeries = [];   // color + per-index resolved value, read by the hover layer below
  svg += '<g id="lines-layer">';
  shown.forEach(s => {
    const isOther = s.name === '__other__';
    const c = isOther ? 'var(--ink-3)' : seriesColor(seriesColorAssignment.get(s.name));
    const values = s.points.map((v, j) => trendValue(v, j));
    // break the path at gaps rather than bridging them — an unmeasured run is not a value
    let path = '', pen = 'M', first = null, lastPt = null;
    values.forEach((u, j) => {
      if (u == null) { pen = 'M'; return; }
      const px = x(j), py = y(u);
      path += `${pen}${px.toFixed(1)},${py.toFixed(1)} `;
      if (pen === 'M') first = px;
      lastPt = [px, py];
      pen = 'L';
    });
    path = path.trim();
    // Soft area fill under the line — a wash, never a saturated block (dataviz skill,
    // marks-and-anatomy.md). Skipped for Other: a gray wash under a gray line reads as murk,
    // not signal, and Other already carries no identity worth reinforcing with a fill.
    if (path && !isOther){
      const areaPath = `${path} L${x(s.points.length-1).toFixed(1)},${y(lo).toFixed(1)} ` +
                        `L${(first ?? PAD_L).toFixed(1)},${y(lo).toFixed(1)} Z`;
      svg += `<path d="${areaPath}" fill="${c}" opacity=".10" stroke="none"/>`;
    }
    svg += `<path class="series-line" data-name="${esc(s.name)}" d="${path}" fill="none" stroke="${c}"
             stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    if (lastPt) svg += `<circle class="series-end" data-name="${esc(s.name)}" cx="${lastPt[0].toFixed(1)}"
             cy="${lastPt[1].toFixed(1)}" r="4" fill="${c}" stroke="var(--raise)" stroke-width="2"/>`;
    geomSeries.push({ name: s.name, label: s.label, color: c, values });
  });
  svg += '</g>';
  // The hover layer: a crosshair line + one dot per series, hidden until pointermove positions
  // them (dataviz skill, interaction.md — "an HTML chart is interactive by default"). Built once
  // per redraw, in the same order as geomSeries, so the pointermove handler below can index
  // straight into `dotEls` with no per-event DOM query.
  svg += `<g id="trends-crosshair" style="display:none">
    <line class="crosshair-line" x1="0" y1="${PAD_T}" x2="0" y2="${H - PAD_B}"/>
    ${geomSeries.map(s => `<circle class="ch-dot" r="4" fill="${s.color}" stroke="var(--raise)" stroke-width="2"/>`).join('')}
  </g>`;
  svg += `<rect id="trends-hitrect" x="0" y="0" width="${W}" height="${H}" fill="transparent" pointer-events="all"/>`;
  el('trends-chart').innerHTML = svg;
  const dotEls = [...el('trends-chart').querySelectorAll('.ch-dot')];
  lastGeom = { x, y, W, stamps: d.stamps, series: geomSeries, dotEls };
  positionHoverLayer(null);
  hoveredSeries = null;

  const legendRows = charted.map(s => {
    const c = seriesColor(seriesColorAssignment.get(s.name));
    const dl = trendDelta(s.points);
    const cls = dl == null ? 'flat' : dl > 1 ? 'up' : dl < -1 ? 'down' : 'flat';
    const txt = dl == null ? '—' : (dl > 0 ? '+' : '') + dl.toFixed(1) + '%';
    const j = d.stamps.length - 1;
    const latest = s.latest == null ? null : trendValue(s.latest, j);
    // Mark the categories that hold named roles, so the by-role drill is DISCOVERABLE from the
    // top level. Without it the split toggle only appears after drilling in, which means the
    // one place that advertises the feature is the one place you reach by already knowing it
    // exists.
    const hasRoles = !trendDrill && (d.watch_parents || []).includes(s.name);
    // data-name + the delegated listener below, NOT an inline onclick: esc() is HTML-entity
    // escaping, and inside onclick="...'${name}'..." the parser decodes entities back
    // before the JS parses — a name with a quote would break out of the string.
    return `<li class="charted"><span class="row" data-name="${esc(s.name)}" role="button" tabindex="0"
      ><span class="swatch" style="background:${c}"></span>
      <span class="nm" title="${esc(s.label)}">${esc(s.label)}</span>
      ${hasRoles ? '<span class="drill" role="img" aria-label="has tracked roles" title="Named roles are tracked inside this category">▸ roles</span>' : ''}
      <span class="ct">${latest == null ? '—' : fmtValue(latest)}</span>
      <span class="dl ${cls}">${txt}</span></span></li>`;
  });
  if (other){
    // Everything past CHART_MAX, as one honestly-labeled, honestly-inert row — not N rows that
    // look like real categories but silently do nothing on click (the dead-click this replaces).
    // No role/tabindex: `trendClick` already no-ops on a name absent from trendData.series
    // (findIndex returns -1), so `__other__` is inert by the SAME guard real unknown names use,
    // not a new special case.
    const dl = trendDelta(other.points);
    const cls = dl == null ? 'flat' : dl > 1 ? 'up' : dl < -1 ? 'down' : 'flat';
    const txt = dl == null ? '—' : (dl > 0 ? '+' : '') + dl.toFixed(1) + '%';
    const j = d.stamps.length - 1;
    const latest = other.points[j] == null ? null : trendValue(other.points[j], j);
    legendRows.push(`<li class="other"><span class="row" data-name="__other__"
      ><span class="swatch" style="background:var(--ink-3)"></span>
      <span class="nm" title="${esc(other.label)}">${esc(other.label)}</span>
      <span class="ct">${latest == null ? '—' : fmtValue(latest)}</span>
      <span class="dl ${cls}">${txt}</span></span></li>`);
  }
  el('trends-legend').innerHTML = legendRows.join('');

  const runs = d.stamps.length;
  // The measurement window, stated on the page: "19 measurements" alone could be two hours
  // or two years, and every judgement about a trend depends on which.
  const spanMs = runs > 1 ? new Date(d.stamps[runs-1]) - new Date(d.stamps[0]) : 0;
  const spanDays = spanMs / 864e5;
  const span = runs < 2 ? '' :
    spanDays >= 1.5 ? ` over ${Math.round(spanDays)} days` : ` over ${Math.round(spanMs/36e5)} hours`;
  const measured = `${runs} measurement${runs===1?'':'s'}${span}`;
  // Every category is now represented — individually if it's one of the top CHART_MAX, folded
  // into Other otherwise — so this no longer needs a "top N of M" caveat.
  el('trends-scope').textContent = trendDrill
    ? (trendSplit === 'roles'
        ? `tracked roles · ${measured} — click any line to go back`
        : `by experience level · ${measured} — click to go back`)
    : `${d.series.length} categories · ${measured} — click any of the top ${CHART_MAX} to break it down`;
  // A narrow ATS selection has its own reason for a short history (ADR-0075): per-ATS rows
  // only exist from the run this shipped in forward, not because the pipeline itself is new —
  // conflating the two would read as "the pipeline is broken" rather than "you narrowed it."
  el('trends-empty').textContent = runs < 2
    ? (trendAtsSelected()
        ? `Only ${runs} measurement${runs === 1 ? '' : 's'} for this ATS selection — per-ATS history starts from when this filter shipped, not before. Broaden the selection to see more.`
        : 'Only one measurement so far — trend lines appear once the pipeline has run a few more times.')
    : (trendDrill && trendSplit === 'roles' && !d.series.length
      // "not tracked" would be wrong and self-contradictory next to a marker that just said
      // roles ARE tracked here: the watchlist is config, the rows are measurements, and between
      // a deploy and the next pipeline run the first exists without the second.
      ? 'These roles have not been measured yet — they appear after the next pipeline run.'
      : '');
  const nt = d.non_tech.filter(v => v != null).pop();
  const parts = [];
  // "7 days" mirrors role_trends.NEW_WINDOW_DAYS — change one and this sentence starts lying.
  parts.push(trendMetric === 'new'
    ? 'Openings first seen in the last 7 days, re-measured every pipeline run.'
    : (trendUnit === 'share'
      ? 'Each line is a category’s share of all live openings in the index — immune to the index itself growing or shrinking.'
      : 'Counts are live openings in the index, re-measured every pipeline run. The index itself grows as coverage does, which lifts every count.'));
  if (nt && trendMetric === 'stock') parts.push(`${nt.toLocaleString()} further rows sit in non-tech categories and are excluded here.`);
  el('trends-foot').textContent = parts.join(' ');

  // The by-level / by-role toggle only exists inside a family that has watched roles.
  const seg = el('trends-split');
  if (seg) {
    seg.hidden = !(trendDrill && (d.watch_parents || []).includes(trendDrill));
    seg.querySelectorAll('button').forEach(b => setRadioChecked(b, b.dataset.split === trendSplit));
  }
  if (tableView) el('trends-table').innerHTML = buildTrendsTable();
}

// ---- hover: crosshair + one-tooltip-for-every-series (dataviz skill, interaction.md) --------

function positionHoverLayer(index){
  const svgEl = el('trends-chart'), tip = el('trends-tooltip');
  if (!svgEl || !lastGeom) return;
  const group = svgEl.querySelector('#trends-crosshair');
  if (!group) return;
  if (index == null){
    group.style.display = 'none';
    if (tip) tip.hidden = true;
    return;
  }
  group.style.display = '';
  const { x, y, series, dotEls, stamps } = lastGeom;
  const line = group.querySelector('.crosshair-line');
  line.setAttribute('x1', x(index)); line.setAttribute('x2', x(index));
  const rows = [];
  series.forEach((s, i) => {
    const v = s.values[index], dot = dotEls[i];
    if (v == null){ if (dot) dot.style.display = 'none'; return; }
    if (dot){ dot.style.display = ''; dot.setAttribute('cx', x(index)); dot.setAttribute('cy', y(v)); }
    rows.push({ label: s.label, color: s.color, value: v });
  });
  if (!tip) return;
  tip.hidden = rows.length === 0;
  if (rows.length){
    // Labels come from config/role_families.json content, not user text — still built with
    // textContent/DOM nodes rather than innerHTML string concatenation, matching the rest of
    // this file's rule for anything that reaches the DOM (esc() is for innerHTML call sites).
    tip.replaceChildren();
    const head = document.createElement('div');
    head.className = 'tt-date'; head.textContent = stampLabel(stamps[index]);
    tip.appendChild(head);
    rows.forEach(r => {
      const row = document.createElement('div'); row.className = 'tt-row';
      const key = document.createElement('span'); key.className = 'tt-key'; key.style.background = r.color;
      const val = document.createElement('span'); val.className = 'tt-val'; val.textContent = fmtValue(r.value);
      const nm = document.createElement('span'); nm.className = 'tt-name'; nm.textContent = r.label;
      row.append(key, val, nm); tip.appendChild(row);
    });
    const wrap = svgEl.parentElement, wrapRect = wrap.getBoundingClientRect();
    const svgRect = svgEl.getBoundingClientRect();
    const scaleX = lastGeom.W ? svgRect.width / lastGeom.W : 1;
    const px = x(index) * scaleX;
    const left = px + 200 > wrapRect.width ? Math.max(4, px - 212) : px + 12;
    tip.style.left = left + 'px';
    tip.style.top = '8px';
  }
}

// Hover-highlight (dataviz skill; converges ECharts' emphasis/blur, Chart.js's alpha-dim,
// amCharts' stroke-width — see docs/product/2026-08-20_trends-ui-design-research.md §2): the
// hovered series goes full-opacity + thicker stroke and repaints on top of its siblings
// (Observable Plot's z-order technique, plain `appendChild` re-insertion here); everything else
// dims to .25, never display:none, so shape/position context survives. A style toggle, not a
// redraw — cheap, and it cannot itself trigger a data refetch.
function applyEmphasis(){
  const chartEl = el('trends-chart');
  const layer = chartEl && chartEl.querySelector('#lines-layer');
  if (layer){
    layer.querySelectorAll('.series-line').forEach(node => {
      const active = !hoveredSeries || node.dataset.name === hoveredSeries;
      node.style.opacity = active ? '' : '.25';
      node.style.strokeWidth = (hoveredSeries && active) ? '3' : '2';
      if (hoveredSeries && active) layer.appendChild(node);   // paint the focused line on top
    });
    layer.querySelectorAll('.series-end').forEach(node => {
      node.style.opacity = (!hoveredSeries || node.dataset.name === hoveredSeries) ? '' : '.25';
    });
  }
  const legend = el('trends-legend');
  if (legend) legend.querySelectorAll('.row[data-name]').forEach(row =>
    row.classList.toggle('dim', !!hoveredSeries && row.dataset.name !== hoveredSeries));
}

// ---- the table view: the WCAG-clean twin of the chart (dataviz skill, components.md) --------

function buildTrendsTable(){
  const d = trendData; if (!d) return '';
  const { shown: rows } = chartedAndOther(d);
  const head = '<tr><th scope="col">Category</th>' +
    d.stamps.map(ts => `<th scope="col">${esc(stampLabel(ts))}</th>`).join('') + '</tr>';
  const body = rows.map(s => '<tr><th scope="row">' + esc(s.label) + '</th>' +
    s.points.map((v, j) => { const u = trendValue(v, j); return `<td>${u == null ? '—' : esc(fmtValue(u))}</td>`; }).join('') +
    '</tr>').join('');
  return `<thead>${head}</thead><tbody>${body}</tbody>`;
}

function toggleTrendsTable(force){
  tableView = force ?? !tableView;
  if (el('trends-table-toggle')) el('trends-table-toggle').setAttribute('aria-pressed', tableView);
  if (el('trends-viz')) el('trends-viz').hidden = tableView;
  if (el('trends-table-wrap')){
    el('trends-table-wrap').hidden = !tableView;
    if (tableView && el('trends-table')) el('trends-table').innerHTML = buildTrendsTable();
  }
}

function trendClick(name){
  if (trendDrill) { trendSplit = 'bands'; loadTrends(null); return; }   // drilled in — go back up
  // Only charted rows drill. `< 0` as well as `>= CHART_MAX`: findIndex returns -1 for a name
  // that is not in the series at all, and -1 passes a bare upper-bound check.
  const i = trendData.series.findIndex(x => x.name === name);
  if (i < 0 || i >= CHART_MAX) return;
  // Land on whichever split the row advertised. A row carrying the "▸ roles" marker that
  // opened on experience bands would make the one affordance naming roles the one that does
  // not show them; the toggle is then the only way to the thing you just clicked for.
  trendSplit = (trendData.watch_parents || []).includes(name) ? 'roles' : 'bands';
  loadTrends(name);
}

// The three segmented toggles are WAI-ARIA radiogroups (APG's own Toolbar Example resolves
// this exact shape — toggle-styled buttons that are mutually exclusive and apply immediately,
// no save step — as radiogroup, not a plain button group: see the Radio Group pattern and
// docs/product/2026-08-20_trends-ui-design-research.md §4). Roving tabindex: exactly one
// button is tabbable at a time; arrow/Home/End keys move focus AND select, matching the
// pattern's keyboard model.
function trendSeg(id, attr, apply){
  const seg = el(id); if (!seg) return;
  const buttons = () => [...seg.querySelectorAll('button')];
  function select(b){
    if (!b || b.getAttribute('aria-checked') === 'true') return;
    buttons().forEach(x => setRadioChecked(x, x === b));
    apply(b.dataset[attr]);
  }
  seg.addEventListener('click', e => {
    const b = e.target.closest(`button[data-${attr}]`);
    if (b) select(b);
  });
  seg.addEventListener('keydown', e => {
    const list = buttons().filter(b => !b.disabled);
    if (!list.length) return;
    const i = list.indexOf(document.activeElement);
    let next = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = list[(i + 1 + list.length) % list.length];
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = list[(i - 1 + list.length) % list.length];
    else if (e.key === 'Home') next = list[0];
    else if (e.key === 'End') next = list[list.length - 1];
    if (!next) return;
    e.preventDefault();
    select(next); next.focus();
  });
}
// Share divides by the STOCK total, so it is meaningless against "new" — a share of new
// openings over all live ones is a number with no reading. The unit segment is therefore
// locked there. `disabled`, not just pointer-events: the latter stops the mouse and not the
// keyboard, and a focused Space press would have plotted new counts over stock totals. `title`
// says why, rather than leaving a dimmed control to speak for itself.
function setUnit(value, locked){
  trendUnit = value;
  const seg = el('trends-unit'); if (!seg) return;
  seg.style.opacity = locked ? .4 : '';
  seg.title = locked
    ? 'Share isn’t meaningful for “New this week” — a share of new openings over all live ones has no reading, so this shows Count instead.'
    : '';
  seg.querySelectorAll('button').forEach(b => {
    b.disabled = locked;
    setRadioChecked(b, b.dataset.unit === value);
  });
}
trendSeg('trends-metric', 'metric', v => {
  trendMetric = v;
  setUnit(v === 'new' ? 'count' : 'share', v === 'new');
  loadTrends(trendDrill);
});
trendSeg('trends-unit', 'unit', v => { setUnit(v, false); drawTrends(); });
trendSeg('trends-split', 'split', v => { trendSplit = v; loadTrends(trendDrill); });
['trends-since', 'trends-until'].forEach(id => {
  if (el(id)) el(id).addEventListener('change', () => loadTrends(trendDrill));
});
if (el('trends-range-clear')) el('trends-range-clear').addEventListener('click', () => {
  ['trends-since', 'trends-until'].forEach(id => { if (el(id)) el(id).value = ''; });
  loadTrends(trendDrill);
});
if (el('trends-retry')) el('trends-retry').addEventListener('click', () => loadTrends(trendDrill));
if (el('trends-table-toggle')) el('trends-table-toggle').addEventListener('click', () => toggleTrendsTable());
if (el('trends-ats-trigger')) {
  el('trends-ats-trigger').addEventListener('click', () => toggleAtsPopover());
  el('trends-ats-menu').addEventListener('change', () => { trendAtsLabel(); loadTrends(trendDrill); });
  document.addEventListener('click', e => {
    if (!el('trends-ats-menu').hidden && !e.target.closest('#trends-ats')) toggleAtsPopover(false);
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !el('trends-ats-menu').hidden) toggleAtsPopover(false);
  });
}
// Pointer tracking for the crosshair+tooltip lives on the SVG element itself, wired once: an
// innerHTML rewrite of its children (every drawTrends() call) leaves listeners on the element
// itself intact, so this never needs re-attaching.
if (el('trends-chart')) {
  el('trends-chart').addEventListener('pointermove', e => {
    if (!lastGeom) return;
    const rect = el('trends-chart').getBoundingClientRect();
    const svgX = rect.width ? (e.clientX - rect.left) / rect.width * lastGeom.W : 0;
    let best = 0, bestDist = Infinity;
    lastGeom.stamps.forEach((_, j) => {
      const dist = Math.abs(lastGeom.x(j) - svgX);
      if (dist < bestDist){ bestDist = dist; best = j; }
    });
    positionHoverLayer(best);
  });
  el('trends-chart').addEventListener('pointerleave', () => positionHoverLayer(null));
}

function initAlerts(){
  if (!window.google || !CFG.google_client_id) return;
  google.accounts.id.initialize({ client_id: CFG.google_client_id, callback: onGoogleCredential });
  google.accounts.id.renderButton(el('gsignin'), { theme: 'outline', size: 'medium' });
}
// One listener on the list itself — it survives every innerHTML redraw of its children.
if (el('trends-legend')) {
  const legend = el('trends-legend');
  legend.addEventListener('click', e => {
    const row = e.target.closest('.row[data-name]');
    if (row) trendClick(row.dataset.name);
  });
  // Charted rows are `role="button" tabindex="0"`, so they must answer the keyboard too — a
  // button reachable by Tab that does nothing on Enter is worse than one never focusable.
  // Space is prevented before acting (its default is to scroll), and `repeat` is ignored so a
  // held key does not fire a drill per repeat tick.
  legend.addEventListener('keydown', e => {
    if (e.repeat || (e.key !== 'Enter' && e.key !== ' ')) return;
    const row = e.target.closest('.row[data-name][role="button"]');
    if (!row) return;
    e.preventDefault();
    trendClick(row.dataset.name);
  });
  // Hover-highlight (dataviz skill; design-research doc §2): a legend row's mouse hover reaches
  // the chart, not just its own background. `pointerover`/`pointerout` bubble (mouseenter/leave
  // don't), so this is one delegated pair rather than one listener per row; `relatedTarget` is
  // checked so moving between two DOM nodes inside the SAME row doesn't flicker the state.
  legend.addEventListener('pointerover', e => {
    const row = e.target.closest('.row[data-name]');
    if (row && hoveredSeries !== row.dataset.name){ hoveredSeries = row.dataset.name; applyEmphasis(); }
  });
  legend.addEventListener('pointerout', e => {
    const row = e.target.closest('.row[data-name]');
    if (row && !(e.relatedTarget && e.relatedTarget.closest('.row[data-name]'))){ hoveredSeries = null; applyEmphasis(); }
  });
  // Same emphasis on keyboard focus as on hover (dataviz skill, interaction.md: "same details
  // on keyboard focus as on hover") — charted rows are already tabindex="0".
  legend.addEventListener('focusin', e => {
    const row = e.target.closest('.row[role="button"]');
    if (row){ hoveredSeries = row.dataset.name; applyEmphasis(); }
  });
  legend.addEventListener('focusout', e => {
    const row = e.target.closest('.row[role="button"]');
    if (row){ hoveredSeries = null; applyEmphasis(); }
  });
}
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
go();   // an empty query browses the newest jobs (ADR-0074) — the Search tab is never empty
whoAmI();
showTab(currentTab());
// Result cards need star states before the Saved tab is ever opened; landing ON the tab
// already loads via showTab above.
if (CAN_STAR && currentTab() !== 'saved') loadSaved();
