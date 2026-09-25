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
  // A tab can carry its own state after a `?` (`#trends?company=…`, ADR-0185), so the panel
  // is named by what comes before it.
  const name = location.hash.replace('#','').split('?')[0];
  return document.getElementById('panel-' + name) ? name : 'search';
}
function showTab(name){
  document.querySelectorAll('.panel').forEach(p => { p.hidden = p.id !== 'panel-' + name; });
  document.querySelectorAll('.tabs [data-tab]').forEach(a => {
    a.setAttribute('aria-current', a.dataset.tab === name ? 'page' : 'false');
    // On a 390px phone the strip scrolls, and the tab you are on sat off its edge.
    if (a.dataset.tab === name && a.scrollIntoView) a.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  });
  if (name === 'search' && readSearchHash()) go();
  if (name === 'hot' && el('hot-results') && !hotData) loadHot();
  if (name === 'trends' && el('trends')){
    // Back onto a bare `#trends` is a view with no picks; the tab strip's own bare link keeps
    // them. One Back after picking Google landed on `#trends` and the kept pick wrote itself
    // straight back, so the press did nothing.
    const bare = location.hash.indexOf('?') < 0;
    if (bare && !viaTabStrip && trendPicks.length){ replacePicks([]); loadTrends(null); viaTabStrip = false; return; }
    viaTabStrip = false;
    const linked = readTrendHash();
    if (linked || !trendData) loadTrends(linked ? linked.family : null);
    else writeTrendHash();   // the tab strip's bare `#trends` gets the view back, for sharing
  }
  if (name === 'matches' && el('sets-strip')){
    if (!mySets) loadSets();
    else if (activeSetId) runSet(activeSetId);   // re-run on every visit — never stale
  }
  if (name === 'saved' && el('saved-results')) loadSaved();   // re-check "closed" on every visit
  if (name === 'profile' && el('pquery')) loadProfile();      // server truth on every visit
  if (name === 'data' && el('cov') && !coverage) loadCoverage();
  // The résumé builder converts pixels to inches from the page's measured width, and a hidden
  // panel measures zero — so it re-paints on the way in rather than on page load.
  if (name === 'resume' && window.ResumeEditor) ResumeEditor.shown();
}
window.addEventListener('hashchange', () => showTab(currentTab()));
// Whether the hash change about to land came from the tab strip's own link (showTab).
let viaTabStrip = false;
document.addEventListener('click', e => { if (e.target.closest && e.target.closest('.tabs [data-tab]')) viaTabStrip = true; }, true);

/* ---- The Data tab's coverage counts (ADR-0113). Fetched on first open, never on page load:
   the tab is a minority of visits and the counts, though cheap, are not free on the first one.

   Every row is a share of the served index, phrased as what a user would actually ask —
   "how often will I see a salary" — rather than as a column name. A field the table does not
   carry yet comes back null and is skipped entirely; rendering it as 0% would read as
   "measured, and none have it", which is a different and wrong claim. ---- */
let coverage = null;
// `remote` is deliberately absent: it is a facet, not a gap. Nearly every row has a value, so
// a percentage here would answer "how many are remote" — which the Search rail's own counts
// already answer — rather than "how often is this unknown". Its provenance is mixed too (some
// boards publish the field, others are read from the location text), which is why the rail's
// caveat describes both and this row describes neither.
const COV_ROWS = [
  ['salary', 'state a salary', 'Most boards publish none. Filters that need one can only match these.'],
  ['posted_at', 'carry the employer\u2019s posting date', 'Their date, in their format \u2014 not ours, and not always given.'],
  ['min_years', 'have a years figure we could derive', 'Read from the posting where it states one \u2014 otherwise estimated from a seniority word like \u201cSenior\u201d in the title, which is a guess rather than the employer\u2019s stated requirement.'],
  ['first_seen', 'record when HeadStart first saw them', 'Stamped on arrival, so older rows predate the field and cannot show a \u201cnew\u201d tag.'],
  ['description', 'have their full text stored', 'Keyword search inside descriptions reaches only these.'],
];

async function loadCoverage(){
  const box = el('cov');
  // `r.ok` is checked, not just the parse. A 401 or 500 body parses perfectly well into an
  // object with no `fields`, and the render below would then have reported "the index carries
  // none of these fields yet" \u2014 a false claim, on the one page whose subject is not making any.
  const fail = '<p class="aside">Couldn\u2019t reach the index to count just now. ' +
    'Reload to try again \u2014 no figure is better than a guessed one.</p>';
  try{
    const r = await fetch('/coverage');
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    if (!d || typeof d.total !== 'number' || !d.fields) throw new Error('shape');
    coverage = d;
  }catch(e){ box.innerHTML = fail; return; }
  const total = coverage.total;
  // One count per field against the one total \u2014 the server used to repeat `total` on every
  // field, which is one number said five times and five chances for them to disagree.
  const rows = COV_ROWS
    .filter(([key]) => typeof coverage.fields[key] === 'number')
    .map(([key, what, why]) => {
      const share = coverage.fields[key] / total;
      const pct = Math.round(share * 100);
      // A nonzero count must never print "0%": rounded down it reads as "measured, and none
      // have it", which is the unknown-is-not-zero confusion one row over (ADR-0009).
      const label = coverage.fields[key] > 0 && pct === 0 ? 'under 1%' : pct + '%';
      return `<div class="cov-row">
        <div class="cov-bar"><span style="width:${Math.max(pct, share > 0 ? 1 : 0)}%"></span></div>
        <div class="cov-txt"><b>${label}</b> ${esc(what)}
          <span class="aside">${esc(why)}</span></div>
      </div>`; }).join('');
  // An empty table would otherwise render "Of 0 jobs \u2026 0% state a salary" \u2014 exactly the
  // "measured, and none have it" reading the unknown-is-not-zero rule exists to prevent.
  box.innerHTML = !total
    ? '<p class="aside">The index is empty right now, so there is nothing to measure.</p>'
    : rows
      ? `<p class="cov-total">Of <b>${total.toLocaleString()}</b> jobs in the index right now:</p>${rows}`
      : '<p class="aside">The index carries none of these fields yet.</p>';
}

const DENSITY_KEY = 'hs.dense';
function applyDensity(on){
  const box = document.querySelector('.content'), btn = el('density');
  if (box) box.classList.toggle('dense', on);
  if (btn){ btn.setAttribute('aria-pressed', String(on)); btn.textContent = on ? 'Comfortable' : 'Compact'; }
}
function flipDensity(){
  const on = !document.querySelector('.content').classList.contains('dense');
  try { localStorage.setItem(DENSITY_KEY, on ? '1' : ''); } catch(e){}
  applyDensity(on);
}
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
  const btn = el('filtersbtn');
  if (btn) btn.setAttribute('aria-expanded', String(open));
  // Deliberately no scrollIntoView. It was here because the panel used to open ABOVE its own
  // button and take it off screen; now the panel opens directly below and its top is already
  // in view, and `block:'nearest'` on a 761px panel scrolls the page 179px on every click —
  // moving the page under the cursor, which is the thing this whole change was about.
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
// Capped at 48h rather than tracking the filter window. Tied to the window, a "last 7 days"
// filter badged EVERY row — a badge on every row is chrome, not signal, and the row it most
// needs to distinguish is the one that arrived overnight.
const isNew = s => {
  const t = Date.parse(s || ''); if (isNaN(t)) return false;
  const hours = Math.min(Number((el('seen') && el('seen').value) || 24), 48);
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
/* ---- the bracket's rates, client-side (ADR-0117). The SAME table the server compiled the
   query with, handed over on window.CFG rather than fetched: one table and one as_of, so the
   figure printed beside a row can never disagree with the where-clause that returned it. Absent
   — the server could not read the table either — means nothing is converted here, which is the
   same fail-safe direction `fx.table()` takes. ---- */
const FX = CFG.fx || null;
// Rates are units-per-base, so the base cancels — the mirror of headstart.fx.convert.
const fxConvert = (amount, frm, to) => {
  const rates = (FX && FX.rates) || {};
  const a = rates[(frm || '').toUpperCase()], b = rates[(to || '').toUpperCase()];
  return (a && b) ? amount / a * b : null;
};
// The currency the Search bracket is asking in, set by `draw` and read by `jobCard`. Only the
// Search list gets it: a Matches row is drawn from its own saved query, and labelling it with
// a currency picked on another tab would answer a question nobody asked there. (The Saved list
// cannot show one at all — `savedRow` carries no derived salary columns.)
let convTo = '';
// "≈ USD 34,000–51,000" — why a row priced in another currency is in a converted bracket at
// all. Built from the ADR-0082 annual columns, never from `r.salary`: that string is the
// board's own text, in the board's own period, and converting it would restate a number this
// page never normalised. Rounded to the nearest thousand and prefixed ≈, because the rates
// are approximate and dated and a figure to the rupee would claim a precision they have not
// got. The date itself rides on `#fxnote` beside the results, not on every row.
const convLabel = r => {
  if (!convTo || !FX || r.min_salary_annual == null) return '';
  const from = (r.salary_currency || '').toUpperCase();
  if (!from || from === convTo) return '';
  const lo = fxConvert(Number(r.min_salary_annual), from, convTo);
  if (lo == null) return '';
  // Three significant figures, never finer than a thousand: rounded to the nearest 1,000 a
  // rupee figure came out as "14,940,000", six figures of precision the rates have not got.
  const k = n => { const step = Math.max(1000, Math.pow(10, Math.floor(Math.log10(n)) - 2));
                   return Math.round(n / step) * step; };
  const hi = (r.max_salary_annual != null && r.max_salary_annual !== r.min_salary_annual)
    ? fxConvert(Number(r.max_salary_annual), from, convTo) : null;
  return '\u2248 ' + convTo + ' ' + salFmt(k(lo)) + (hi != null ? '\u2013' + salFmt(k(hi)) : '');
};
// The Match ring (ADR-0042): raw cosine lives in a narrow band (a strong on-topic query
// tops out ≈0.78; an absurd one still scores ≈0.66), so the displayed % stretches it
// through two fixed anchors — 0.60 → 0%, 0.85 → 100% — tuned once against real queries
// and revisited only when the embedding model changes. Display only: ranking stays on the
// raw score, and the same job shows the same % wherever it appears.
const matchPct = s => Math.round(Math.max(0, Math.min(1, (s - .60) / .25)) * 100);
// Match strength is a QUANTITY, so it gets a sequential ramp — one hue, increasing LIGHTNESS.
// Mixed toward `--rule`, not `--ink-3`: measured, accent-into-ink-3 gave a luminance ratio of
// 0.96 across the whole 0-100 range — the ring changed hue and not brightness, which the eye
// does not read at 12px. Against `--rule` the ratio is 1.81, and the 45% floor keeps the
// dimmest ring at 2.62:1 on the card. The percentage NUMBER stays `--ink`, so the ring is a
// redundant encoding rather than the only carrier of the value.
// not four different hues. Hue is reserved for categories (--remote, --pay, --alert); reusing
// one here would have made a category colour mean "strong match" as well as what it names.
// Weak matches fade toward the muted ink so a scan shows where the good results stop.
const tone = s => `color-mix(in srgb, var(--accent) ${45 + matchPct(s) * .55}%, var(--rule))`;
const busy = on => el('results').setAttribute('aria-busy', String(!!on));
// The ultrawide card grids flow COLUMN-major so a vertical scan follows rank, which means each
// one has to be told how deep its column is. Counted off what was actually rendered, never
// PAGE_SIZE: a short last page (or a one-line empty state) otherwise fills a full 10-deep
// column and leaves a tall empty one beside it. Halved because the block opens two columns.
const setResultRows = (n, id) => { const box = el(id || 'results');
  if (box) box.style.setProperty('--rows', Math.max(1, Math.ceil(n / 2))); };
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
  // own it filters nothing, and the server nulls it anyway (matches parse_filters) — and only
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
  // Only meaningful alongside a bound: the currency says what the bracket's two numbers are
  // counted in, and the server restates them in every other currency from there (ADR-0117) —
  // so on its own it filters nothing, which is build_filter's own guard too.
  if (el('salcur') && (f.salary_min || f.salary_max)) f.salary_currency = el('salcur').value;
  return f;
}
const LABELS = { remote:'Remote', has_salary:'Shows salary', max_years:'Your experience',
  kw:'Keyword', kw_in:'Look in',
  ats:'ATS provider', etype:'Type', india:'India', location:'Location', company:'Company',
  posted_within:'Posted ≤', seen_within:'First seen ≤',
  salary_min:'Salary from', salary_max:'Salary to' };
// A chip should read as the sentence the user set, in the units the read-out and the results
// use: "Salary from USD 60,000", not the raw "60000" out of the number field. The currency has
// no chip of its own — it is not a filter, it is what both bounds are counted in, and a third
// chip repeating it would also inflate the count on the Filters button by one.
const chipValue = (key, value, f) =>
  (key === 'salary_min' || key === 'salary_max')
    ? `${f.salary_currency || ''} ${salFmt(value)}`.trim()
    : (value === 'true' ? 'yes' : value);
// `salary_currency` is deliberately absent: it has a default (USD) rather than an empty
// state, so clearAll() blanking it would leave the picker showing nothing. Clearing the two
// bounds already switches the bracket off, which is what "clear" has to mean here.
// `kw_in` is likewise absent: it has a default (Title), not an empty state — dropping the
// keyword is what switches the scope off, so dropFilter maps it onto `kw` below.
const CONTROL = { remote:'remote', has_salary:'hassalary', max_years:'maxyears', ats:'ats', kw:'kw',
  etype:'etype', india:'india', location:'location', company:'company',
  posted_within:'posted', seen_within:'seen', salary_min:'salmin', salary_max:'salmax' };
function drawActive(){
  syncSalarySlider();
  const f = currentFilters(), box = el('active');
  // Everything but the currency, which both bracket chips print for themselves.
  const shown = Object.entries(f).filter(([k]) => k !== 'salary_currency');
  // The panel is closed by default now (ADR-0116), so the button has to carry how many
  // filters are hiding behind it — otherwise a narrowed result set has no visible cause.
  const btn = el('filtersbtn'), n = shown.length + (searchScope ? 1 : 0);
  if (btn){
    btn.textContent = n ? `Filters (${n})` : 'Filters';
    btn.classList.toggle('has', n > 0);
  }
  // A converted figure has to carry the date of the rates that made it (ADR-0117), and the
  // rail's own tip saying so is behind a panel that is closed by default. Written on every
  // draw, like #sortnote and #kind, so it can never describe a bracket that is no longer set.
  const fxnote = el('fxnote');
  if (fxnote) fxnote.textContent = (f.salary_currency && FX && FX.as_of)
    ? `Other currencies are converted at rates from ${FX.as_of} \u2014 currency conversion, not cost of living.`
    : '';
  box.innerHTML = (searchScope
    ? `<span class="pill" title="The boards HeadStart counts for this company${searchScope.category ? ', in the category its trend counted' : ''}. Not kept in a saved search."><b>Company</b> ${esc(searchScope.label)}${
        searchScope.category ? ` · ${esc(searchScope.category.label)}` : ''}` +
      `<button onclick="dropFilter('board')" aria-label="Remove Company filter">×</button></span>` : '') +
    shown.map(([k,v]) =>
    `<span class="pill"><b>${esc(LABELS[k]||k)}</b> ${esc(chipValue(k, v, f))}` +
    `<button onclick="dropFilter('${esc(k)}')" aria-label="Remove ${esc(LABELS[k]||k)} filter">×</button></span>`
  ).join('');
}
/* ---- the salary bracket's slider. Two native ranges over one track; `#salmin`/`#salmax`
   stay the values every other part of this file reads (currentFilters, clearAll, dropFilter,
   applySetToControls), so the slider is an input method for them and never a second source of
   truth. It writes into them and fires `input`, which is what the number fields would fire if
   a hand had typed there.

   Traffic is one-way on the read side: a typed figure MOVES the handle but is never rewritten
   by it. Salary asks for exact numbers, and a slider that rounded 137,000 up to its nearest
   stop the moment focus left the field would be editing the user's filter behind them. It also
   keeps the promise the number fields make on their own — a figure past the top of the scale
   is still reachable by typing it.

   The stops are round numbers, closer together where the salaries are: annual pay is heavily
   skewed, and an even 0-500k track would spend four fifths of its length on a range almost
   nothing sits in. The printed range is always the true value out of the number fields, never
   the stop the handle is resting on.

   The top of the scale is "no maximum", not "the most anyone here pays" — nothing in the
   client has measured that, and a number implying it would be an invented bound. ---- */
const BASE_STOPS = (() => {
  const out = [];
  for (let v = 0; v < 100000; v += 5000) out.push(v);
  for (let v = 100000; v < 200000; v += 10000) out.push(v);
  for (let v = 200000; v <= 500000; v += 25000) out.push(v);
  return out;
})();
const SAL_TOP = BASE_STOPS.length - 1;
/* The ladder above is drawn in the rate table's base currency (USD). Left at those numbers it
   was unusable in every other one: in INR the whole track topped out at ₹5,00,000 — below
   entry-level pay in the market this index covers best — so the handles could only ever park
   at the far right and the control said nothing.

   So the scale is restated in whichever currency the bracket is in, at the rate rounded to ONE
   significant figure — 83 → 80, 0.79 → 0.8. The rounding is the point: every stop then stays a
   round number in the currency it is printed in (₹4,00,000 steps, not ₹4,15,000), and the top
   of the scale means "no maximum" rather than a converted figure anyone should read. The exact
   rates are the server's business; these only decide where a handle can rest, and a figure
   between two stops is still reachable by typing it into the number field. */
const oneSig = x => { const p = Math.pow(10, Math.floor(Math.log10(x))); return Math.round(x / p) * p; };
let SALARY_STOPS = BASE_STOPS;
let stopsCurrency = '';
function useStops(cur){
  cur = (cur || '').toUpperCase();
  if (cur === stopsCurrency) return;
  stopsCurrency = cur;
  const rate = FX ? fxConvert(1, FX.base, cur) : null;
  const scale = rate ? oneSig(rate) : 1;   // no table, or no rate for it: the base ladder
  SALARY_STOPS = scale === 1 ? BASE_STOPS : BASE_STOPS.map(v => Math.round(v * scale));
}
// The stop nearest a typed figure — nearest, not floor, so 137,000 rests on 140,000 rather
// than sliding back to 130,000. Anything past the top end parks on the top.
const salStop = v => {
  let best = 0;
  SALARY_STOPS.forEach((stop, i) => {
    if (Math.abs(stop - v) < Math.abs(SALARY_STOPS[best] - v)) best = i;
  });
  return best;
};
const salFmt = n => Number(n).toLocaleString();

// Position the handles, the fill and the read-out from whatever `#salmin`/`#salmax` now hold.
// Called on every fetch (via drawActive) as well as on direct edits, so every path that can
// change those fields — Clear all, a removed chip, a saved set, the Profile hand-off — leaves
// the slider agreeing with them without each one having to know it exists.
function syncSalarySlider(){
  const lo = el('salrmin'), hi = el('salrmax'); if (!lo || !hi) return;
  // Before anything is read off the stops: the scale belongs to the currency now picked.
  useStops(el('salcur') ? el('salcur').value : '');
  lo.max = String(SAL_TOP); hi.max = String(SAL_TOP);
  const minV = el('salmin').value, maxV = el('salmax').value;
  const li = minV === '' ? 0 : salStop(Number(minV));
  const ri = maxV === '' ? SAL_TOP : salStop(Number(maxV));
  lo.value = String(Math.min(li, ri));
  hi.value = String(Math.max(li, ri));
  // With both handles at the same stop the one underneath is unreachable; lift whichever is
  // at the far end so there is always a thumb on top to drag back.
  lo.style.zIndex = Number(lo.value) >= SAL_TOP ? '4' : '';
  const pct = i => (i / SAL_TOP) * 100;
  const fill = el('salfill');
  if (fill){
    fill.style.left = pct(Number(lo.value)) + '%';
    fill.style.width = (pct(Number(hi.value)) - pct(Number(lo.value))) + '%';
    // Coloured only once a bound exists: at rest the span covers the whole track, and in
    // --accent that reads as an applied filter on a search nobody has filtered.
    fill.classList.toggle('on', minV !== '' || maxV !== '');
  }
  // The ends of the scale, from the stops themselves — so a rebuilt scale relabels itself.
  if (el('salcap0')) el('salcap0').textContent = salFmt(SALARY_STOPS[0]);
  if (el('salcap1')) el('salcap1').textContent = salFmt(SALARY_STOPS[SAL_TOP]) + '+';
  // The screen reader hears the salary, not the index the range actually holds.
  lo.setAttribute('aria-valuetext', minV === '' ? 'no minimum' : salFmt(minV));
  hi.setAttribute('aria-valuetext', maxV === '' ? 'no maximum' : salFmt(maxV));
  const cur = el('salcur') ? el('salcur').value : '';
  const read = el('salread');
  if (read){
    read.textContent = (minV === '' && maxV === '') ? 'Any salary'
      : `${cur} ${minV === '' ? 'any' : salFmt(minV)} \u2013 ${maxV === '' ? 'no maximum' : salFmt(maxV)}`;
  }
}

// A dragged handle writes the stop it landed on into the number field it stands for, then
// searches — `input` so anything watching those fields sees the change the way it would see
// a keystroke. Blank, not 0 / the top stop: an end-stop means "unbounded", and 0 is a real
// minimum the server would compile into a clause.
function salSlide(which){
  const lo = el('salrmin'), hi = el('salrmax');
  let li = Number(lo.value), ri = Number(hi.value);
  if (li > ri){ if (which === 'min') li = ri; else ri = li; }   // handles never cross
  lo.value = String(li); hi.value = String(ri);
  el('salmin').value = li === 0 ? '' : String(SALARY_STOPS[li]);
  el('salmax').value = ri === SAL_TOP ? '' : String(SALARY_STOPS[ri]);
  for (const id of ['salmin', 'salmax'])
    el(id).dispatchEvent(new Event('input', { bubbles: true }));
  syncSalarySlider();
}

const BRACKET = ['salary_min', 'salary_max', 'salary_currency'];
function dropFilter(key){
  if (key === 'board'){ searchScope = null; go(); return; }
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
  searchScope = null;
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
let searchRequest = 0;

// go(): a fresh search or browse from page 1 — Search button, Enter, a chip, or a filter
// change. An empty query browses the newest jobs instead of ranking by similarity
// (ADR-0074), so this always fetches; it never shows a static empty state.
async function go(){ page = 1; searched = readSearch(); writeSearchHash(); drawActive(); await fetchPage(); }

// What go() read off the controls. Prev/Next and a hide re-run page THIS, never an edit the
// user has not submitted — re-reading the box live sent a new query at page N and skipped its
// first rows — and the lines describing the rows read it too, so they describe what is paged.
let searched = null;
// One company's Boards, handed over by the Trends and Hot tabs (ADR-0185) as `{boards, label}`.
// Like "mine", kept out of currentFilters(): a Saved Set serializes that, and a hand-off is not
// a control to freeze. By Board key rather than the Company text filter, whose substring match
// misses aliased names ("RTX" from `globalhr` rows) and cannot tell two same-named employers apart.
// The hand-off rides in the hash (`#search?board=…&label=…&family=…&q=…`), so a reload or a
// shared link keeps it; a bare `#search` lost the company and showed every job. A Trends
// category comes along as `family` (with its display name, `area`), which the Space turns into
// the exact Jobs the trend counted — a semantic query alone ranked all 1,856 Google jobs under
// a trend of 243.
let searchScope = null;
// `counted`: the trend's own figure for what is handed over and the run it is as of, so Search
// can say why its live count differs — Google's AI roles read 352 in Trends, 346 here, with the
// trend five hours older and nothing saying so.
function searchCompany(boards, label, q, category, aside, counted){
  searchScope = { boards, label, category: category || null, aside: aside || 0, counted: counted || null };
  el('company').value = '';   // the text filter would narrow the Boards again, by name
  if (q != null) el('q').value = q;
  location.hash = searchHash();
  go();
}
function searchHash(){
  if (!searchScope) return '#search';
  const p = new URLSearchParams();
  searchScope.boards.forEach(b => p.append('board', b));
  if (searchScope.label) p.set('label', searchScope.label);
  if (searchScope.category){
    if (searchScope.category.role) p.set('role', searchScope.category.role);
    else p.set('family', searchScope.category.family);
    p.set('family_label', searchScope.category.label);
  } else if (searchScope.aside) p.set('aside', String(searchScope.aside));
  if (searchScope.counted){ p.set('trend_n', String(searchScope.counted.n)); p.set('trend_at', searchScope.counted.at); }
  if (el('q').value.trim()) p.set('q', el('q').value.trim());
  return '#search?' + p;
}
// A hand-off arriving by link or reload. True when it changed the scope, so the caller runs it.
function readSearchHash(){
  const at = location.hash.indexOf('?');
  if (currentTab() !== 'search' || at < 0) return false;
  const p = new URLSearchParams(location.hash.slice(at + 1));
  const boards = p.getAll('board');
  // The whole hand-off, not only its Boards: Back from Google › Data to Google › AI names the
  // same Boards with another query, and comparing the Boards alone left the page on Data.
  if (!boards.length || location.hash === searchHash()) return false;
  searchScope = { boards, label: p.get('label') || `${boards.length} board${boards.length === 1 ? '' : 's'}`,
                  category: p.get('role') ? { role: p.get('role'), label: p.get('family_label') || p.get('role') }
                    : p.get('family') ? { family: p.get('family'), label: p.get('family_label') || p.get('family') } : null,
                  aside: Number(p.get('aside')) || 0,
                  counted: p.get('trend_at') && p.get('trend_n') ? { n: Number(p.get('trend_n')), at: p.get('trend_at') } : null };
  el('company').value = '';
  el('q').value = p.get('q') || '';
  return true;
}
// Kept in step with what was searched: the query as run, and the scope gone once dropped.
function writeSearchHash(){
  if (currentTab() !== 'search' || typeof history === 'undefined') return;
  if (!searchScope && location.hash.indexOf('?') < 0) return;   // nothing to keep, or to drop
  const hash = searchHash();
  if (location.hash !== hash) history.replaceState(null, '', hash);
}
function readSearch(){
  return { q: el('q').value.trim(), filters: currentFilters(), sort: el('sort').value,
           mine: !!(el('mine') && el('mine').checked), scope: searchScope,
           currency: el('salcur') ? el('salcur').value : '' };
}

// goToPage(n): re-fetch the SAME query and filters at a different page. Never resets page 1
// itself, so Prev/Next can't fight a fresh go() call.
async function goToPage(n){ page = Math.max(1, Math.min(n, MAX_PAGE)); await fetchPage(); }

async function fetchPage(){
  const request = ++searchRequest;
  const { q, filters, sort, mine, currency, scope } = searched;
  const p = new URLSearchParams({ q, k: PAGE_SIZE, page });
  for (const [key, value] of Object.entries(filters)) p.set(key, value);
  if (sort !== 'rel') p.set('sort', sort);
  // A salary sort is stated in one currency — the picker's — even with no bound set, which is
  // why this is not in currentFilters(): there the currency means "the bracket is counted in".
  if (sort === 'salary' && currency && !p.has('salary_currency')) p.set('salary_currency', currency);
  // Deliberately NOT part of currentFilters(): a Saved Set serializes that, and freezing "only
  // my companies" into a stored Set would pin it to the list as it was on the day it was saved.
  if (mine) p.set('mine', '1');
  if (scope){
    scope.boards.forEach(b => p.append('board', b));
    if (scope.category){
      if (scope.category.role) p.set('role', scope.category.role);
      else p.set('family', scope.category.family);
    }
  }
  el('results').innerHTML = skeleton() + skeleton() + skeleton();
  setResultRows(3);
  busy(true);
  el('pager').innerHTML = '';
  el('kind').textContent = '';   // never describe the previous search's rows over the new ones
  el('n').textContent = q ? 'searching…' : 'loading…';
  // Fired together, not one after the other: the counts depend only on the filters, never on
  // the query, so they neither wait for the ranking nor make the user wait for them.
  const facetsPromise = fetch('/facets?'+p).then(r => r.json()).catch(() => null);
  facetsPromise.then(facets => { if (request === searchRequest) applyFacets(facets); });
  drawSortNote();
  let rows, r;
  try { r = await fetch('/search?'+p); rows = await r.json(); }
  catch(e){ if (request !== searchRequest) return;
            busy(false); el('results').innerHTML = '<div class="empty">That search didn\'t go through. Try again.</div>';
            setResultRows(1);
            el('n').textContent = ''; el('kind').textContent = ''; return; }
  if (request !== searchRequest) return;
  busy(false);
  if(!Array.isArray(rows)){
    // The sign-in wall's 401 is not a filter's fault — reading it as one had the user clearing
    // filters that were never the problem.
    el('results').innerHTML = '<div class="empty">' + (r.status === 401
      ? 'Your session expired — sign in again to search.'
      : 'One of the filters isn\'t valid — clear it and try again.') + '</div>';
    setResultRows(1);
    el('n').textContent = ''; el('kind').textContent = ''; return; }
  // Paint the ranked rows as soon as /search returns. Facets are independent counts and can be
  // much slower on a cold filter shape; keeping the skeleton up until they finish made their
  // latency the page's latency even though the actual Jobs were already here. The count/pager
  // start from the rows we know and are reconciled below when facets arrive.
  drawResultKind(q, rows.length);
  if(!rows.length){
    el('results').innerHTML = page === 1
      ? '<div class="empty"><div class="big">Nothing matched</div>' + whyNothing(null) + '</div>'
      : '<div class="empty"><div class="big">No more jobs</div>' +
        'You\'ve reached the end of these results.</div>';
    setResultRows(1);
    el('n').textContent = page === 1 ? '0 results' : '';
    drawPager(0, null);
  } else {
    drawCount(rows.length, undefined);   // the total is still coming
    draw(rows);
    drawPager(rows.length, null);
  }

  const facets = await facetsPromise;
  if (request !== searchRequest) return;
  drawKeywordNote(facets);
  if(!rows.length){
    if (page === 1){
      el('results').innerHTML = '<div class="empty"><div class="big">Nothing matched</div>' +
        whyNothing(facets) + '</div>';
    }
    drawPager(0, facets);
    return;
  }
  drawCount(rows.length, facets);
  drawPager(rows.length, facets);
}

// "Showing 1–20 of 40,807 matching your filters". The qualifier is not padding: a vector
// search RANKS the filtered set rather than shrinking it, so the total counts rows matching
// the filters, and the query decides only their order. Calling it "results for your query"
// would promise a relevance the number never measured.
// What the list below IS, in one line — the orientation a first-time user has nowhere else to
// get. A browse (no Query, unranked per ADR-0074) and a ranked search look identical apart from
// the match rings, and the ordering in force is not visible at all. Written on every fetch so
// it can never describe the previous one.
function drawResultKind(q, shown){
  const node = el('kind');
  if (!node || !shown) { if (node) node.textContent = ''; return; }
  const explain = ' <a href="#data">How the match score works \u2192</a>';
  // Three states, not two. A date sort re-orders the best matches, so claiming similarity
  // order there would contradict #sortnote, which sits two lines above this in the same
  // column and already says exactly that.
  if (q && searched.sort !== 'rel'){
    node.innerHTML = 'Your best matches for what you described, re-ordered by date rather ' +
      'than by closeness.' + explain;
  } else if (q){
    node.innerHTML = 'Ranked by how close each job is to what you described.' + explain;
  } else {
    // A browse is ordered three different ways depending on the sort control and on whether
    // the table even has `first_seen` — and the line has to name the one actually in force.
    // The default browse falls back to ordering by `id` without that column, which is not a
    // date at all, so "newest first" would simply be untrue there.
    const sort = searched.sort;
    const order = sort === 'posted' ? 'newest by the employer\u2019s posting date first'
      : sort === 'seen' ? 'most recently added first'
      : CFG.has_first_seen ? 'most recently added first'
      : 'in no particular order';
    // "across every board" only if nothing is narrowing it: a browse takes the same
    // where-clause a ranked search does, so with ATS=lever the chips one line above would
    // read "ATS: lever" while this claimed the whole index. Same class of unconditional
    // sentence as the `has_first_seen` one directly below.
    // A company hand-off is a narrowing too: under "Company: Google · AI / Machine Learning"
    // it said "across every board".
    // The trend counts tech roles; Search lists every job HeadStart serves for the company,
    // including those its role classifier sets aside as non-tech. The gap is said, not left to
    // read as a mismatch (Google: 1,800 in Trends, 1,854 here).
    const aside = searched.scope && searched.scope.aside
      ? ` — including ${searched.scope.aside.toLocaleString()} its trend leaves out as non-tech` : '';
    const scope = searched.scope
      ? `Jobs from ${searched.scope.label}${searched.scope.category ? `, ${searched.scope.category.label}` : ''}${aside}`
      : Object.keys(searched.filters).length ? 'Jobs matching the filters above' : 'Jobs from across every board';
    const counted = searched.scope && searched.scope.counted;
    node.textContent = `${scope}, ${order} \u2014 no search yet, so nothing is ranked. ` +
      'Describe a role above to rank by meaning.' + (counted
        ? ` The trend counted ${counted.n.toLocaleString()} as of ${stampLabel(counted.at)} UTC; this list is what is open now.` : '');
  }
}

function drawCount(shown, facets){
  // Before the total lands (a cold /facets took 3–16s, measured), a full page says it is a
  // page of more: "20 results" there read as the answer to a count Trends had just given.
  const from = (page-1)*PAGE_SIZE + 1, to = (page-1)*PAGE_SIZE + shown;
  if (!facets || typeof facets.total !== 'number'){
    // Pending (undefined) says so; failed (null) says only what is on the page, for good.
    el('n').textContent = facets === undefined && shown === PAGE_SIZE
      ? `Showing ${from.toLocaleString()}–${to.toLocaleString()}, counting the rest…`
      : shown + ' result' + (shown===1?'':'s'); return; }
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
const SORT_NOTES = {
  seen:    ['most recently added first', 'newest among your best matches — not a global date sort'],
  posted:  ['newest by the employer’s date first', 'newest among your best matches — not a global date sort'],
  // Functions of the picker's currency: salary is stored in each employer's own, so the server
  // lists that currency's jobs first on a browse and converts the rest on a ranked page.
  salary:  [c => `highest ${c} salary first, then other currencies grouped by currency — jobs with none come last`,
            c => `best-paid among your best matches, other currencies converted to ${c} — not a global salary sort`],
};
function drawSortNote(){
  const note = SORT_NOTES[searched.sort];
  const text = !note ? '' : note[searched.q ? 1 : 0];
  el('sortnote').textContent = typeof text === 'function' ? text(searched.currency || 'USD') : text;
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
  if (!searched.filters.kw || !needs){ note.textContent = ''; return; }
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
  // Nothing to page through. The pager used to render "Prev · Page 1 · Next" over an empty
  // result set, offering navigation through zero rows.
  if (!rowCount && page === 1){ el('pager').innerHTML = ''; return; }
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

/* ---- ONE result card, rendered by Search, Matches and Saved alike.
   Saved used to build its own: a `.hd` wrapper no stylesheet has carried since the row layout
   landed, the salary as a wrapped grey pill rather than in the pay column, no tags row, no
   external-link glyph. Measured, its content ended 140px short of the search card's and the
   same job read as two different things depending on which tab you found it on. A Saved
   record is mapped onto this row shape in renderSaved rather than this function growing a
   second branch — the card knows about rows, not about where they came from.

   A Saved row carries the two facts only it has — whether the job has closed, and when it
   was starred — and `canHide` is the one thing about the row that is about WHERE it is being
   drawn: the × belongs to the Search list, which is the one with the hidden-count note and the
   "show" toggle beside it. On Saved the equivalent gesture is unstarring, and two controls for
   one intent would disagree about which list the row is in. ---- */
function jobCard(r, i, canHide, canHideCompany){
  // A browsed row (no query) was never ranked, so it carries no score (ADR-0074) — the
  // match ring would otherwise show a misleading "0%" rather than "not applicable".
  const ranked = r.score != null;
  const s = Number(r.score) || 0, pct = matchPct(s);
  const hidden = r.id && dismissed.has(r.id);
  const cls = ['card', r.closed && 'gone', hidden && 'dismissed'].filter(Boolean).join(' ');
  return `
    <div class="${cls}" style="${ranked?`--tone:${tone(s)}; `:''}animation-delay:${Math.min(i,12)*35}ms">
      <div class="who">
        <a class="title" href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener">${esc(r.title)}<svg class="ext" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M6.5 3.5H3.5v9h9v-3M9.5 3.5h3v3M12.5 3.5 7 9" stroke-linecap="round" stroke-linejoin="round"/></svg><span class="sr">, opens on the employer's own board</span></a>
        <div class="org">${esc(r.company)}${r.location? ' <span>·</span> '+esc(r.location) : ''}${
          // On every card, not only on a capped one: hiding a company is how a user acts on a
          // result they do not want, and the first such result is rarely the third from that
          // company. A plain inline control rather than a hover reveal — a control that only
          // exists on hover does not exist on a phone.
          CAN_COMPANIES && canHideCompany && boardOf(r.id)
            ? ` <button class="linkish hide-co" data-hide-company="${esc(boardOf(r.id))}"
                 title="Stop showing ${esc(r.company)} in your results">hide</button>`
            : ''}${
          // Into Trends already picked (ADR-0185). The Board is boardOf's guess, which names no
          // directory company for ~2% of rows; the picker then says so rather than failing.
          el('trends') && boardOf(r.id)
            ? ` <button class="linkish see-trend" data-trend="${esc(boardOf(r.id))}"
                 data-trend-name="${esc(r.company)}" aria-label="Hiring trend at ${esc(r.company)}"
                 >trend</button>`
            : ''}</div>
        <div class="tags">
          ${r.closed? '<span class="tag closed" title="No longer in HeadStart\u2019s index \u2014 almost always because the employer took it down. The link still goes to them.">closed</span>':''}
          ${isNew(r.first_seen)? '<span class="tag new" title="New to HeadStart\u2019s index within your chosen window \u2014 not necessarily newly posted by the employer">new</span>':''}
          ${r.remote? '<span class="tag rem">remote</span>':''}
          ${r.employment_type? '<span class="tag">'+esc(r.employment_type)+'</span>':''}
          ${r.min_years!=null? '<span class="tag mono">'+(Number(r.min_years)||0)+'+ yrs</span>':''}
          ${age(r.posted_at)? '<span class="tag mono" title="The date the employer put on it, in their own format \u2014 not when HeadStart saw it">'+age(r.posted_at)+'</span>':''}
          ${age(r.starred_at)? '<span class="tag mono">saved '+age(r.starred_at)+'</span>':''}
          ${r.ats? '<span class="src" title="Read directly from this company\'s '+esc(r.ats)+' board \u2014 not a repost">via '+esc(r.ats)+'</span>':''}
        </div>
      </div>
      <div class="pay">${payLabel(r)? esc(payLabel(r)) : '<span class="nopay" title="This board did not publish one">\u2014</span>'}${convLabel(r)? `<span class="conv">${esc(convLabel(r))}</span>` : ''}</div>
      ${ranked? `<div class="match" role="img"
             aria-label="Match ${pct} percent \u2014 how close this job is to your search, on a fixed scale that gives the same job the same number every time"
             title="Match strength \u2014 semantic similarity ${s.toFixed(2)}, scaled to this index's real range">
          <svg class="ring" viewBox="0 0 40 40" aria-hidden="true">
            <circle class="ring-track" cx="20" cy="20" r="16" pathLength="100"/>
            <circle class="ring-fill" cx="20" cy="20" r="16" pathLength="100" style="--p:${pct}"/>
          </svg>
          <div class="v" aria-hidden="true">${pct}%</div>
        </div>`
      // The column stays reserved on an unranked row rather than collapsing. Dropping it moved
      // the star 76px between a browse and a search and left a 107px ragged right edge against
      // the ranked rows' 31 — the aligned columns are the whole point of the row layout, and
      // they cannot align across two different grids.
      : '<div class="match" aria-hidden="true"></div>'}
      ${starBtn(r.id, r.starred_at ? true : undefined)}
      ${canHide ? dismissBtn(r.id) : ''}
    </div>`;
}

/* ---- Per-company capping and the follow/hide lists (ADR-0171) ----------------------------

   Capping applies to the two lists that re-run against the index, Search and Matches; the Saved
   tab renders through `jobCard` directly and is deliberately left alone (see `renderSaved`).

   Capping is a DISPLAY grouping over the rows this page already fetched, not a filter: the
   ranked set, its count and its pagination are untouched, and every capped row is one click
   away rather than gone. Doing it server-side would mean over-fetching and then slicing, which
   breaks offset pagination — the same tied-sort trap `run()` documents, where rows repeat and
   vanish across pages.

   It earns its place because the median company contributes ONE job to a query (measured: a
   `backend` search spans 2,807 companies at a median of 1), so capping costs almost every
   company nothing and only trims the handful that would otherwise fill the screen. ---- */
const COMPANY_CAP = 2;
// The Board a Job id belongs to. Mirrors `board_identity.board_of` and inherits its documented
// caveat (ADR-0049): exact only where the native id carries no colon, so a Board whose jobs have
// colon-bearing native ids resolves to a phantom prefix and hiding it hides fewer rows than the
// user expects — never more, because the prefix is longer, not shorter. `-1` is guarded: without
// it a colonless string would slice to itself-minus-a-character rather than to nothing.
const boardOf = id => {
  const cut = (id || '').lastIndexOf(':');
  return cut > 0 ? id.slice(0, cut) : '';
};

let myCompanies = { followed: [], hidden: [] };
// Keyed by LIST, then Board. One shared map was a real defect: `draw` renders both Search and
// Matches, so drawing one cleared the other's withheld rows and its "N more" buttons became
// dead clicks — while the ADR claims a capped row is one click away.
const capOverflow = new Map();   // listId -> Map(board -> [card html])

async function loadCompanies(){
  try{
    const r = await fetch('/companies');
    if (r.ok) myCompanies = await r.json();
  }catch(e){ /* dark deployment or signed out — the controls simply don't render */ }
}

async function setCompany(board, action){
  try{
    const r = await fetch('/companies', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ board, action })
    });
    if (!r.ok) return false;
    myCompanies = await r.json();
    return true;
  }catch(e){ return false; }
}

function capRows(rows, target){
  const listId = target || 'results';
  const seen = new Map(), firstOf = new Map();
  const withheld = new Map();
  capOverflow.set(listId, withheld);
  const chunks = [];
  rows.forEach((r, i) => {
    const board = boardOf(r.id);
    // `(r, i) => …` and an explicit third argument, never a bare `rows.map(jobCard)`: map passes
    // the array as a third argument, which would land on `canHide` and put a × on every list.
    const html = jobCard(r, i, !target, true);
    if (!board){ chunks.push(html); return; }
    const n = (seen.get(board) || 0) + 1;
    seen.set(board, n);
    if (n === 1) firstOf.set(board, r.company);
    // Not under a company hand-off: every row is that company's, and folding them into "18
    // more at Google" after two hid the list the reader asked for.
    if (n <= COMPANY_CAP || (!target && searched && searched.scope)){ chunks.push(html); return; }
    if (!withheld.has(board)){
      withheld.set(board, []);
      // The expander sits where this company's next result would have been, so the list still
      // reads in rank order rather than collecting the leftovers at the bottom.
      chunks.push({ board });
    }
    withheld.get(board).push(html);
  });
  return chunks.map(c =>
    typeof c === 'string' ? c : moreRow(listId, c.board, firstOf.get(c.board)));
}

function moreRow(listId, board, company){
  const n = ((capOverflow.get(listId) || new Map()).get(board) || []).length;
  return `<div class="more-row" data-board="${esc(board)}" data-list="${esc(listId)}">
      <button class="ghost" data-more>
        ${n} more at ${esc(company || board)}
      </button>
    </div>`;
}

function expandCompany(holder){
  const board = holder.dataset.board, listId = holder.dataset.list;
  const rows = (capOverflow.get(listId) || new Map()).get(board);
  if (!rows) return;
  holder.outerHTML = rows.join('');
  // The cards were rendered when the list was drawn, and a star changed since never reached
  // them — while the click toggles by `savedByJob`, so a stale glyph did the opposite.
  paintStars();
}

function draw(rows, target){
  // Only the Search list is labelled with the bracket's currency — see `convTo`.
  convTo = target ? '' : (currentFilters().salary_currency || '');
  // No client-side reorder any more. It only ever sorted the twenty rows already fetched,
  // which reads as "sort my results" and is not: the server now orders the whole result set
  // (issue #275), so by the time rows arrive they are already in the asked-for order.
  rows.forEach(r => { if (r.id) drawnRows.set(r.id, r); });   // starring needs the row later
  el(target || 'results').innerHTML = capRows(rows, target).join('');
  setResultRows(rows.length, target);
  if (!target) drawHidden(pageRows = rows);
}

/* ---- Saved sets (ADR-0043): the Matches tab runs one live; "Save this search" creates
   one from the controls the user is looking at. All strip actions ride ONE delegated
   listener + data attributes — never inline handlers with interpolated names. ---- */
let mySets = null, activeSetId = null;
let matchesRequest = 0;

async function loadSets(){
  const request = ++matchesRequest;
  let sets;
  try{
    const r = await fetch('/sets');
    if (request !== matchesRequest) return;
    if (!r.ok){ el('matches-msg').textContent = 'Couldn\'t load your sets.'; return; }
    sets = await r.json();
  }catch(e){ if (request === matchesRequest) el('matches-msg').textContent = 'Couldn\'t load your sets.'; return; }
  if (request !== matchesRequest) return;
  mySets = sets;
  if (activeSetId && !mySets.some(s => s.id === activeSetId)) activeSetId = null;
  renderSets();
  if (mySets.length) runSet(activeSetId || mySets[0].id);
}

function renderSets(){
  const strip = el('sets-strip');
  if (!mySets.length){
    strip.innerHTML = '';
    el('matches-msg').textContent = '';
    setResultRows(1, 'matches-results');
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
  const request = ++matchesRequest;
  const s = (mySets || []).find(x => x.id === id); if (!s) return;
  activeSetId = id; renderSets();
  el('matches-msg').textContent = 'searching…';
  const p = new URLSearchParams({ q: s.query, k: 20 });
  for (const [key, value] of Object.entries(s.search_filters || {})) p.set(key, value);
  for (const [key, value] of Object.entries(matchesRange())) p.set(key, value);
  let rows, r;
  try { r = await fetch('/search?'+p); rows = await r.json(); }
  catch(e){ if (request === matchesRequest) el('matches-msg').textContent = 'That search didn\'t go through.'; return; }
  if (request !== matchesRequest) return;
  if (!Array.isArray(rows)){ el('matches-msg').textContent = r.status === 401
    ? 'Your session expired — sign in again to see your matches.'
    : 'A saved filter isn\'t valid — refine the set.'; return; }
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
  syncSalarySlider();
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
  // A refusal changed nothing, so there is nothing to reload — and the reload's un-awaited set
  // re-run overwrote the refusal the caller then showed in #matches-msg.
  if (!r || r.ok){ mySets = null; await loadSets(); }
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
/* ---- Dismissed rows. Every result leaves for the employer's own board, so the return trip
   lands on a list with no memory of what has already been dealt with — the main cost of the
   loop. `:visited` on the title says "opened"; this says "done with". Browser-local on
   purpose: it is a scanning aid over one session's list, not a preference worth an account
   round trip, and storage can throw outright in a private window. Rows are hidden rather than
   dropped, so the server's own "showing 1-20 of N" stays true and one click puts them back.
   ---- */
const DISMISS_KEY = 'hs.dismissed';
let revealDismissed = false;
const dismissed = new Set((() => {
  try { return JSON.parse(localStorage.getItem(DISMISS_KEY) || '[]'); } catch(e){ return []; }
})());
function saveDismissed(){
  try { localStorage.setItem(DISMISS_KEY, JSON.stringify([...dismissed])); } catch(e){}
}
const dismissBtn = id => !id ? '' :
  `<button class="dismiss" data-dismiss="${esc(id)}" title="Hide this job"
    aria-label="Hide this job from the results">\u00d7</button>`;

// The count of what is hidden, beside the result count that no longer matches what is on
// screen. Written on every draw, including when it is zero — a stale "3 hidden" is worse
// than none. It counts the Search page on screen, never `drawnRows`: that holds every row of
// every page and both lists drawn this session.
let pageRows = [];
function drawHidden(rows){
  const node = el('hidden'), box = el('results'); if (!node || !box) return;
  const n = rows.filter(r => r.id && dismissed.has(r.id)).length;
  node.innerHTML = !n ? '' :
    `${n} hidden <button class="linkish" onclick="toggleDismissed()">` +
    `${revealDismissed ? 'hide again' : 'show'}</button>`;
  box.classList.toggle('reveal', revealDismissed);
}
function toggleDismissed(){
  revealDismissed = !revealDismissed;
  drawHidden(pageRows);
}
function dismissRow(id){
  if (dismissed.has(id)) dismissed.delete(id); else dismissed.add(id);
  saveDismissed();
  document.querySelectorAll('[data-dismiss]').forEach(b => {
    if (b.dataset.dismiss === id) b.closest('.card').classList.toggle('dismissed', dismissed.has(id));
  });
  drawHidden(pageRows);
}

const CAN_STAR = !!el('saved-results');   // the Saved tab only renders when configured
// Follow/hide is gated separately from starring: the local dev renderer serves /companies over
// one in-memory record even though it has no Accounts at all (ADR-0042 keeps the two mirrors).
const CAN_COMPANIES = !!el('my-companies');
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

// The one thing outside this file that reads the stars: the Résumé tab's "Tailor for a job"
// picker (ADR-0133), which names a version after a job the visitor already saved. A hand-off,
// not a second GET /saved — the rows are on the page already, and this list is the one every
// star and unstar keeps current, so the picker can never disagree with the Saved tab.
//
// Null, never [], while there is no list to offer: a deployment with no account store, a
// signed-out visitor, or a /saved that has not answered (or failed). Empty means signed in
// with nothing starred, which is a different thing and the picker says it out loud.
//
// A copy, so a reader sorting or trimming it cannot reorder the Saved tab underneath itself.
window.savedJobs = () => (mySaved ? mySaved.slice() : null);

// Repaint every star button in place — cheaper than redrawing three results lists.
function paintStars(){
  document.querySelectorAll('button[data-star]').forEach(b => {
    const on = savedByJob.has(b.dataset.star);
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', on);
    // The same two strings starBtn draws, or a starred job still announces "Save this job".
    b.setAttribute('title', on ? 'Remove from saved' : 'Save this job');
    b.setAttribute('aria-label', on ? 'Remove this job from saved' : 'Save this job');
    b.textContent = on ? '★' : '☆';
  });
}

function renderSaved(){
  const box = el('saved-results');
  if (!box || mySaved == null) return;
  if (!mySaved.length){
    el('saved-msg').textContent = '';
    setResultRows(1, 'saved-results');
    box.innerHTML = '<div class="empty"><div class="big">Nothing saved yet</div>' +
      'Hit the ☆ on any result to keep it here — the copy stays even after the posting closes.</div>';
    return;
  }
  const jobs = mySaved.slice().sort((a,b) => (b.starred_at||'').localeCompare(a.starred_at||''));
  el('saved-msg').textContent = jobs.length + ' saved job' + (jobs.length===1?'':'s');
  setResultRows(jobs.length, 'saved-results');
  // No "hide" here, and no capping: the Saved tab lists jobs this Account chose one at a time,
  // so a company-level control would either do nothing visible or remove something deliberately
  // kept. It is the one list that does NOT go through `draw`/`capRows`.
  box.innerHTML = jobs.map((j, i) => jobCard(savedRow(j), i, false, false)).join('');
}

// A stored star, in the shape jobCard reads. The record is a display copy taken at star time
// (SavedJob, alerts/store.py), so it carries no score and none of the derived columns — the
// card renders whichever tags it can and leaves the rest out, exactly as it does for a search
// row whose board published no salary. `open` is the server's own key for "still in the
// index"; `closed` is what the card shows, and they are opposites, so the flip happens here
// rather than being read the wrong way round somewhere downstream.
const savedRow = j => ({
  id: j.job_id, title: j.title, company: j.company, location: j.location,
  url: j.url, remote: j.remote, salary: j.salary, starred_at: j.starred_at,
  closed: j.open === false, score: null,
});

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
  const left = typeof p.parses_left === 'number' ? p.parses_left : null;
  el('pparses').textContent = left === null ? ''
    : left > 0 ? `${left} of ${left + (p.parses_used || 0)} résumé reads left`
    : 'No résumé reads left — edit by hand below.';
  el('pparse').disabled = left === 0;
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
   openings" = live stock, "New this week" = rows first seen inside the flow window) and three
   units: Change (each family's own count indexed to 100 at the window's start), Share (of the
   whole index) and Count (raw openings). Change is the default (ADR-0119) — the panel's
   heading asks which roles are GROWING, and only an indexed axis answers that for eight
   families whose latest counts differ by 7x. Its one cost is that raw counts move with our
   coverage, so the plot draws the whole index's own growth as a dashed reference line and the
   caption says to read a family against it. Share removes the confound instead, for a reader
   who wants that rather than disclosure; Count is the literal level. Only Share is meaningless
   against "New this week" — a share of "new" openings over all live ones has no reading — so
   only Share is withdrawn there, never the whole group.

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
// trendUnit must agree with the aria-checked/tabindex the Unit radiogroup ships in
// trends.html — nothing calls setUnit before the first paint, so the markup IS the initial
// state and a drift here would check one button and draw another unit.
// The unit token is 'change', not 'index': CONTEXT.md's "index" is the served corpus, and this
// same file labels the reference line "whole index" in that sense. One word, two meanings, in
// one function was a grep hazard. The UI has always called this unit Change.
let trendMetric = 'stock', trendUnit = 'change', trendSplit = 'bands', trendCoverage = 'all';
let trendDays = 'all';      // the date-range preset: 7 | 30 | 90 | 'all'; a custom bound beats it
let hoveredSeries = null;   // legend/chart hover-focus name — dims every other line (§ emphasis)
let hoverIndex = null;      // the stamp the crosshair is parked on — the keyboard walks this
let tableView = false;      // the WCAG-clean twin of the chart, independent of the SVG
let lastGeom = null;        // scales + resolved values from the last drawTrends() — hover reads this
let trendReq = null;        // the /trends request in flight, so a newer one can cancel it
const hiddenSeries = new Set();   // legend toggle-to-hide; keyed by name, so a re-rank keeps it
const CHART_MAX = 8;        // matches the 8-slot validated categorical palette
// Picked companies (ADR-0185), in the order they were added. `key` is any board_key of a
// directory entry, which is all `/trends?company=` needs; `label` is null until an answer names
// it, because a pick that arrives by link or from the follow list carries only a key.
let trendPicks = [];
// The top level's "Break down by" under a pick. `chosen` is the reader's choice of 'families',
// 'total' or 'company', or 'auto' while they have not chosen. Auto is Company for two or more
// picks — a reader who picks Google and Microsoft is comparing them, and a summed category view
// under "at 2 companies" answered a question nobody asked. For one pick it is Total when the
// company is small (`autoTotal`) and Category otherwise, decided afresh whenever the picks
// change (`stale`).
const topSplit = { chosen: 'auto', autoTotal: false, stale: false };
let trendRaw = null;              // the last /trends payload, partial reads dropped; trendData is what is drawn
const pickLabels = new Map();     // board_key -> the name the link that picked it already showed

const seriesColorAssignment = new Map();   // family/role name -> slot index, for what is drawn now
const slotMemory = new Map();              // name -> the slot it last held, kept for the session
const slotOwner = new Map();               // slot -> the name that last held it, ditto
let freedSlots = [];                       // slots vacated, longest-vacated first
let recoloredNames = new Set();           // names that took over ANOTHER entity's slot this draw
function seriesColor(slot){
  return getComputedStyle(document.documentElement).getPropertyValue(`--series-${slot + 1}`).trim();
}
// Keep whatever slot a name already had; hand a name that is new to the screen a slot in this
// order: the one it held earlier in the session, then one nothing has ever held, then the
// longest-vacated one. `slotMemory` is what makes the first of those work across a whole
// round trip — drill into a family and back out and the eight categories come back in their
// own colours, where the previous "lowest free slot" allocator dealt them out by rank.
// Eight slots and twenty-four families means a slot must eventually change hands anyway, and
// that is the one moment colour lies: a reader who learned "yellow is web development" watches
// yellow become data engineering with nothing saying so. When it happens the entrant is
// recorded and its legend row fades in, so the substitution is announced rather than silent.
// A drill replaces all eight at once — nothing carries over, so nothing is marked there: a
// change of subject is not a swap.
function assignSeriesColors(names){
  recoloredNames = new Set();
  for (const name of [...seriesColorAssignment.keys()]){
    if (names.includes(name)) continue;
    const slot = seriesColorAssignment.get(name);
    seriesColorAssignment.delete(name);
    freedSlots = freedSlots.filter(s => s !== slot).concat(slot);
  }
  const used = new Set(seriesColorAssignment.values());
  const carriedOver = used.size > 0;
  const all = Array.from({ length: CHART_MAX }, (_, i) => i);
  for (const name of names){
    if (seriesColorAssignment.has(name)) continue;
    const free = all.filter(s => !used.has(s));
    if (!free.length) continue;
    const mine = slotMemory.get(name);
    const slot = (free.includes(mine) ? mine : undefined)
      ?? free.find(s => !slotOwner.has(s))
      ?? freedSlots.find(s => free.includes(s))
      ?? free[0];
    if (carriedOver && slotOwner.has(slot) && slotOwner.get(slot) !== name) recoloredNames.add(name);
    seriesColorAssignment.set(name, slot);
    slotMemory.set(name, slot);
    slotOwner.set(slot, name);
    used.add(slot);
    freedSlots = freedSlots.filter(s => s !== slot);
  }
}

// The legend/table key mirrors the mark it stands for — a line, not a box (dataviz skill,
// interaction.md). It is an <svg> rather than a coloured <span> so `data-slot` reaches a real
// stroke: that attribute is what the texture channel keys off under `forced-colors` and print,
// where hue is gone and the dash pattern is the only thing left telling two series apart.
function swatchHtml(color, slot){
  return `<svg class="swatch" viewBox="0 0 14 4" width="14" height="4" aria-hidden="true"
    ><line data-slot="${slot}" x1="1.5" y1="2" x2="12.5" y2="2" stroke="${color}"
     stroke-width="3" stroke-linecap="round"/></svg>`;
}
// Everything past CHART_MAX, folded into one honest aggregate row instead of the N real-looking
// legend rows the previous design left permanently inert (a documented dead-click: dataviz
// skill anti-patterns, "cycling past 8" — fold the tail into "Other," don't seat a 9th).
// A stamp where every hidden series is unmeasured stays null, so the aggregate's line breaks at
// the same points a real series' would; where some measured and others didn't, an unmeasured
// series contributes 0 rather than being excluded — a display approximation, not a data claim.
function buildOtherSeries(rest){
  if (!rest.length) return null;
  const points = sumPoints(rest, trendData.stamps);
  const latest = rest.reduce((sum, s) => sum + (s.latest || 0), 0);
  const noun = trendData.split_by === 'company'
    ? (rest.length === 1 ? 'company' : 'companies')
    : (rest.length === 1 ? 'category' : 'categories');
  // Folded company lines are a share of their own companies, so Other is a share of theirs.
  const denoms = rest.every(s => s.denoms)
    ? sumPoints(rest.map(s => ({ points: s.denoms })), trendData.stamps) : undefined;
  return { name: '__other__', label: `Other (${rest.length} smaller ${noun})`, points, latest, denoms };
}
function sumPoints(list, stamps){
  return stamps.map((_, j) => list.some(s => s.points[j] != null)
    ? list.reduce((sum, s) => sum + (s.points[j] || 0), 0) : null);
}

// What the chart draws from what the Space sent (ADR-0185). Total is not asked for: the
// category lines partition a pick's tech openings, so their sum is exactly its tech total. A
// Company line carries its own company's total as its Share denominator — dividing every pick
// by all of them combined would make the largest look like it dominates every category.
function trendView(raw, family){
  if (raw.split_by === 'company') return { ...raw, series: raw.series.map(s =>
    ({ ...s, denoms: (raw.company_totals || {})[s.name] })) };
  if (family || !trendPicks.length || topSplitNow() !== 'total' || !raw.series.length) return raw;
  const latest = raw.series.filter(s => s.latest != null);
  return { ...raw, total: true, series: [{ name: '__total__', label: 'All tech roles',
    points: sumPoints(raw.series, raw.stamps),
    latest: latest.length ? latest.reduce((sum, s) => sum + s.latest, 0) : null }] };
}
function topSplitNow(){
  if (topSplit.chosen !== 'auto') return topSplit.chosen;
  return trendPicks.length > 1 ? 'company' : topSplit.autoTotal ? 'total' : 'families';
}
// Fewer than two categories big enough to index is a set of picks the category view cannot
// show: its legend would be "not indexed" rows over one line or none. The median Board holds
// three tech openings (ADR-0185), so for a single pick this is the usual case, not the edge.
function fewIndexable(raw){
  return raw.series.filter(startsIndexable).length < 2;
}
// Whether a line's first measured count reaches the Change floor (INDEX_BASE_FLOOR).
const startsIndexable = s => (s.points.find(v => v != null) ?? 0) >= INDEX_BASE_FLOOR;

// Picks live in the hash (`#trends?company=…&by=…`), so a view can be shared and a Hot-tab row
// or a search result can link straight into it. A bare `#trends` — the tab strip's own link —
// is not "no picks": it keeps whatever is picked, as every other control on the tab does.
// Returns whether the hash changed the picks, so the caller knows to fetch.
function readTrendHash(){
  const at = location.hash.indexOf('?');
  if (at < 0 || location.hash === trendHash()) return null;
  const q = new URLSearchParams(location.hash.slice(at + 1));
  const keys = [...new Set(q.getAll('company'))];
  if (keys.join('\n') !== trendPicks.map(p => p.key).join('\n')){
    replacePicks(keys.map(key => ({ key, label: pickLabels.get(key) || null })));
    setCoNote('');
  }
  topSplit.chosen = keys.length && ['families', 'total', 'company'].includes(q.get('by')) ? q.get('by') : 'auto';
  trendSplit = ['roles', 'company'].includes(q.get('split')) ? q.get('split') : 'bands';
  unitWanted = null;
  trendUnit = ['share', 'count', 'change'].includes(q.get('unit')) ? q.get('unit') : 'change';
  trendMetric = q.get('metric') === 'new' ? 'new' : 'stock';
  trendCoverage = q.get('coverage') === 'comparable' ? 'comparable' : 'all';
  // A custom range rides in the link as UTC minutes; the fields show them in local time.
  const since = q.get('since'), until = q.get('until');
  const hotNet = Number(q.get('hot'));
  hotOrigin = q.get('hot_board') && since && Number.isFinite(hotNet) && q.get('hot') !== ''
    ? { board: q.get('hot_board'), net: hotNet, since, built: q.get('hot_at') || null } : null;
  if (since || until){
    const local = v => { if (!v) return ''; const t = new Date(v + 'Z');
      return isNaN(t) ? '' : new Date(t - t.getTimezoneOffset() * 6e4).toISOString().slice(0, 16); };
    if (el('trends-since')) el('trends-since').value = local(since);
    if (el('trends-until')) el('trends-until').value = local(until);
    setRangePreset('custom');
  } else {
    const days = ['7', '30', '90'].includes(q.get('days')) ? q.get('days') : 'all';
    if (days !== trendDays){
      ['trends-since', 'trends-until'].forEach(id => { if (el(id)) el(id).value = ''; });
      setRangePreset(days);
    }
  }
  syncSeg('trends-metric', 'metric', trendMetric);
  syncSeg('trends-coverage', 'coverage', trendCoverage);
  return { family: q.get('family') || null };
}
function syncSeg(id, attr, value){
  const seg = el(id); if (!seg) return;
  seg.querySelectorAll('button').forEach(b => setRadioChecked(b, b.dataset[attr] === value));
}
// The one place the pick list changes: every change re-decides the auto breakdown.
function replacePicks(picks){
  trendPicks = picks;
  topSplit.stale = true;
  if (!picks.length) topSplit.chosen = 'auto';
  drawPicks();
}
// Keys compared folded: Board keys differ only in casing across ledgers (CLAUDE.md), and a
// followed or linked key need not be spelled the way the directory spells it.
const pickedKeys = () => new Set(trendPicks.map(p => p.key.toLowerCase()));
// The whole view, not only the picks: a shared or reopened link lands on the same drill, unit,
// measure, window and coverage. A drill is a history entry of its own, so Back leaves it.
function trendHash(){
  const q = new URLSearchParams();
  trendPicks.forEach(p => q.append('company', p.key));
  if (trendPicks.length && topSplit.chosen !== 'auto') q.set('by', topSplit.chosen);
  if (trendDrill){
    q.set('family', trendDrill);
    if (trendSplit !== 'bands') q.set('split', trendSplit);
  }
  if ((unitWanted || trendUnit) !== 'change') q.set('unit', unitWanted || trendUnit);
  if (trendMetric !== 'stock') q.set('metric', trendMetric);
  if (trendDays === 'custom'){
    const r = trendRange();
    if (r.since) q.set('since', r.since.slice(0, 16));
    if (r.until) q.set('until', r.until.slice(0, 16));
  } else if (trendDays !== 'all') q.set('days', trendDays);
  if (trendCoverage !== 'all') q.set('coverage', trendCoverage);
  if (hotOriginPick()){
    q.set('hot', String(hotOrigin.net)); q.set('hot_board', hotOrigin.board);
    if (hotOrigin.built) q.set('hot_at', hotOrigin.built);
  }
  return '#trends' + (q.size ? '?' + q : '');
}
function writeTrendHash(push){
  if (currentTab() !== 'trends' || typeof history === 'undefined') return;
  const hash = trendHash();
  if (location.hash === hash) return;
  if (push) history.pushState(null, '', hash); else history.replaceState(null, '', hash);
}

// The six views the tab can be in, and everything that names one. `viewKind` decides which
// view a payload is, once; the scope line, the chart's label, the KPI tile, the Break-down-by
// control and whether a legend row opens anything all read it here instead of re-deciding it.
const VIEWS = {
  families: { split: 'families', column: 'Category', drills: true, tracked: 'Categories tracked', grouping: () => 'category',
    scope: (n, where, measured) => n ? `${n} categories${where} · ${measured} — click any of the top ${CHART_MAX} to break it down`
      : `nothing counted${where} · ${measured}` },
  total: { split: 'total', column: 'Line', drills: false, tracked: null, grouping: null,
    scope: (n, where, measured) => `all tech roles${where} · ${measured}` },
  company: { split: 'company', column: 'Company', drills: false, tracked: 'Companies compared', grouping: () => 'company',
    scope: (n, where, measured) => `${n} ${n === 1 ? 'company' : 'companies'} · ${measured}` },
  bands: { split: 'bands', column: 'Level', drills: true, tracked: 'Levels tracked', grouping: f => `experience level inside ${f}`,
    scope: (n, where, measured) => `by experience level${where} · ${measured}` },
  roles: { split: 'roles', column: 'Role', drills: true, tracked: 'Roles tracked', grouping: f => `tracked role inside ${f}`,
    scope: (n, where, measured) => `tracked roles${where} · ${measured}` },
  drillCompany: { split: 'company', column: 'Company', drills: true, tracked: 'Companies compared', grouping: f => `company inside ${f}`,
    scope: (n, where, measured) => `by company · ${measured}` },
};
function viewKind(d){
  if (trendDrill) return trendSplit === 'roles' ? 'roles' : trendSplit === 'company' ? 'drillCompany' : 'bands';
  return d.split_by === 'company' ? 'company' : d.total ? 'total' : 'families';
}

// The whole the picks are measured against: the dashed line's label, its name in prose, and
// what a share is a share of. The index with no pick; the company, or all picks, with some.
function pickScope(){
  const n = trendPicks.length;
  return !n ? { ref: 'whole index', whole: 'the whole index', share: 'a share of the index' }
    : n === 1 ? { ref: 'whole company', whole: 'the company’s own total', share: 'a share of the company’s openings' }
    : { ref: 'all picked', whole: 'the picked companies’ combined total', share: 'a share of the picked companies’ openings' };
}

// What a company chart must say about its own line. It begins at the first run that counted
// its Boards — the ledger's start (2026-09-13) or later, as it was for 9,981 companies
// (measured 2026-09-24) — so a short line must not read as the company's whole history. The
// date is the Space's `counted_since`, each pick's own first tick, not the window's first point,
// which a "7 days" window or a drill would move. A comparable cohort's start is its own base,
// so the sentence is left out there.
function companyNote(d){
  if (!trendPicks.length) return '';
  const parts = [uncountedNote(d)];
  if (!d.series.length) return parts.join(' ');
  // Above the chart only what changes how it reads. When the company sentences are up
  // (drawVerdict) they carry the counted-since date, and the steps are explained under the
  // chart (stepNote): stacked here, four sentences put a phone's chart below its first screen.
  if (!verdictLines(d).length){
    const counted = datedPicks(d.counted_since, 'since');
    if (counted) parts.push(`HeadStart has counted ${counted}; there is nothing before that.`);
  }
  // Under New, every Board's first week is held out by the Space (its backlog reads as new), so
  // a line begins a week after counting did and must say why.
  const hot = hotNote(d);
  if (hot) parts.push(hot);
  if (d.partial) parts.push(`${d.partial} run${d.partial === 1 ? '' : 's'} where a board was read only partly — a leap one run put straight back — ${d.partial === 1 ? 'is' : 'are'} left out of the lines.`);
  const fresh = trendMetric === 'new' && datedPicks(d.new_counted_from, 'from');
  if (fresh) parts.push(`New openings count for ${fresh}: a board's first week reads its whole backlog as new, so none counts before then.`);
  return parts.filter(Boolean).join(' ');
}

// How the marked steps are drawn and counted, for the caption under the chart.
// `marked`: which markers the chart actually drew (drawTrends), so no sentence here points at a
// line that is not on it.
function stepNote(d, marked){
  if (!trendPicks.length || !d.series.length) return '';
  const parts = [];
  if (marked.found)
    parts.push(`A solid grey vertical line marks openings that joined or left the count at once — ${
      notesOf(d).some(n => n.evicted && n.withhold) ? 'duplicate postings removed, ' : ''}boards found later, or a company counted from a later date — not hiring.`);
  // Said only where a line carries a step and has a figure read off it: Zomato, with no
  // percentage, and a Count view, whose lines are real levels, both got the Change sentence.
  // Only where a step is marked on the chart ("Point at a marked line" over a chart with none
  // pointed at nothing) and a line has a percentage read net of it.
  if ((marked.epoch || marked.found)
      && chartedAndOther(d).charted.some(s => stepsFor(s).length && lineMove(s).dl != null))
    parts.push(trendUnit === 'change'
      ? 'Lines and percentages leave out the jumps at marked lines, so they show hiring between them. Point at a marked line, or open “Marked changes” below the chart, to see what changed there.'
      : 'Lines break at each marked jump, and the percentages leave the jumps out, so they measure hiring between them. Point at a marked line, or open “Marked changes” below the chart, to see what changed there.');
  return parts.filter(Boolean).join(' ');
}

// Every marked line, listed under the chart: a phone has no pointer to hover a 1px line with,
// and a tap on one read the run beside it ("Left out: the run after a counting change").
function drawChangeList(d, list){
  const host = el('trends-changes'); if (!host) return;
  host.hidden = !list.length;
  const sorted = list.slice().sort((a, b) => a.i - b.i);
  host.innerHTML = `<summary>Marked changes in this window (${sorted.length})</summary><ul>${sorted.map(g =>
    `<li><b>${esc(stampLabel(d.stamps[g.i]))}</b> ${esc(g.texts.join(' · '))}${
      g.sizes && g.sizes.length ? ` — ${esc(g.sizes.join(', '))}` : ''}</li>`).join('')}</ul>`;
}

// How Hot's figure reads on the trend its row opened, while the trend still shows Hot's week of
// that company's openings. A Hot row is one Board and its trend the whole company: Bosch Group
// read +440 on Hot and +442 here, from a second Board nothing on the page named.
// The pick Hot's row opened, while the view still shows Hot's week of that one company.
function hotOriginPick(){
  const o = hotOrigin;
  // Both in UTC minutes: the link carries Hot's base as that, and the range field reads it back.
  if (!o || trendMetric !== 'stock' || (trendRange().since || '').slice(0, 16) !== o.since || trendPicks.length !== 1) return null;
  const pick = trendPicks[0];
  // Hot's `boschgroup`, the directory's `BoschGroup`
  return (pick.boardKeys || [pick.key]).some(b => b.toLowerCase() === o.board.toLowerCase()) ? pick : null;
}
function hotNote(d){
  const pick = hotOriginPick(); if (!pick) return '';
  const o = hotOrigin, boards = pick.boardKeys || [pick.key];
  const figure = `${o.net < 0 ? '−' : '+'}${Math.abs(o.net).toLocaleString()} net tech roles`;
  if (boards.length > 1)
    return `Hot’s ${figure} is one of ${pick.label || 'this company'}’s ${boards.length} boards; this line sums all of them.`;
  const line = { name: '__total__', points: sumPoints(d.series, d.stamps) };
  // A Board counted for hours has no week of its own here: SiTime read "too new" in its sentence
  // beside "this line reads +0 openings" under Hot's +59.
  if (isYoung(line, d)) return `Hot’s ${figure} is its board’s first days, counted since ${stampLabel(countedSince(d)[0], true)}: too new for this line to give a week’s change.`;
  const m = trendMove(line);
  if (!m) return '';
  const n = Math.round(m.change);
  // Never silent when they differ, and never a guessed reason: Hot's list is built once a run by
  // the pipeline, so its build time is the fact a reader can weigh against this line's runs.
  return n === o.net
    ? `Hot’s ${figure} is this line’s change: both leave out the runs where HeadStart changed how it counts.`
    : `Hot measured ${figure} on this board over the same week${o.built ? `, in its list built ${stampLabel(o.built + 'Z')} UTC` : ''}; this line reads ${signedOpenings(n)}.`;
}

// Picks this answer has nothing for, named with the reason, so the chart never shows fewer
// companies than the chips above it: Razorpay under Comparable coverage vanished with nothing
// but "1 companies" to say so.
function uncountedNote(d){
  const out = (d.uncounted || []).map(k => trendPicks.find(p => p.key === k)).filter(Boolean);
  if (!out.length) return '';
  const names = out.map(p => p.label || 'a picked company');
  const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
  const [isnt, them, their] = out.length === 1 ? ['isn’t', 'it', 'its'] : ['aren’t', 'them', 'their'];
  // The reason is read off the data, never off which control happens to be on: a pick the ATS
  // selection dropped under Comparable was told it "began counting after" the base.
  const since = d.counted_since || {};
  if (trendCoverage === 'comparable' && d.base && out.every(p => since[p.key] && since[p.key] > d.base))
    return `${list} ${isnt} in this view: HeadStart began counting ${them} after ${stampLabel(d.base, true)}.`;
  if (trendAtsSelected() && out.every(p => !(p.atses || []).some(a => trendAtsSelected().includes(a))))
    return `${list} ${isnt} in this view: none of ${their} boards are on the selected sources.`;
  // A window ending before counting began read "0 companies · 0 measurements" with no date.
  const until = trendRange().until;
  const began = out.map(p => since[p.key] || d.ledger_start).filter(Boolean).sort()[0];
  if (until && began && until < began)
    return `${list} ${isnt} in this view: it ends before HeadStart began counting ${them}, on ${stampLabel(began, true)}.`;
  return `${list} ${isnt} in this view: HeadStart counted no openings at ${them} in this window.`;
}

// Comparable coverage holds one set of Boards fixed from a base, and per-Board counting has a
// start of its own (the Space moves an earlier base up to it), so the base is always named.
function comparableNote(d){
  if (trendCoverage !== 'comparable' || !d.base || !d.stamps.length) return '';
  const asked = trendRange().since;
  const moved = asked && asked < d.base;
  return `Comparable follows only the boards HeadStart counted at ${stampLabel(d.base, true)}${
    moved ? ', the first run it counted by board, rather than at the window’s start' : ''}.`;
}
function viewNotes(d){ return [comparableNote(d), companyNote(d)].filter(Boolean).join(' '); }

// One sentence per company above the tiles — the answer a reader came for, in words: how many
// tech openings it has and which way they moved, in openings as well as percent. An indexed
// chart asks the reader to work that out; this says it. Net of the marked steps like every
// figure here, and a line under MOVER_FLOOR gets its change in openings without a direction
// word, because a handful of openings moving is not a trend. Only the picks this answer counts:
// under Comparable, "These 2 companies" was said of one.
function verdictLines(d){
  if (!trendPicks.length || !d.series.length || !d.stamps.length) return [];
  const kind = viewKind(d);
  const counted = trendPicks.filter(p => !(d.uncounted || []).includes(p.key));
  // Inside a category: a sentence per company, or the category's own total, which its levels
  // add up to.
  if (kind === 'drillCompany')
    return d.series.slice(0, CHART_MAX).map(s => ({ name: `${s.label} · ${drillLabel()}`, key: s.name, ...verdictOf(s, d) }));
  // Tracked roles get one sentence on what they are, not a move each: in their place the view
  // read only "HeadStart has counted Google since Sep 13", with nothing it qualified.
  if (kind === 'roles')
    return [{ name: `${counted.length === 1 ? counted[0].label || 'This company' : `These ${counted.length} companies`} · ${drillLabel()}`,
      days: 0, text: 'roles tracked by their titles inside this category. A job can match more than one, so they need not add up to the category — its levels view has the category’s total and move.' }];
  // Several picks summed: the sum of each company's own netted line (the Space's `pick_series`),
  // so the move is exactly the Company breakdown's total. It read only "summed here — break down
  // by Company", no move and no direction.
  const whole = { name: '__total__', points: sumPoints(d.series, d.stamps) };
  const who = counted.length === 1 ? counted[0].label || 'This company' : `These ${counted.length} companies`;
  if (kind === 'bands') return [{ name: `${who} · ${drillLabel()}`, ...verdictOf(whole, d) }];
  if (kind !== 'company' && counted.length > 1)
    return [{ name: who, ...verdictOf(kind === 'total' ? d.series[0] : whole, d) }];
  const lines = kind === 'company' ? d.series.slice(0, CHART_MAX).map(s => [s.label, s, s.name])
    : [[counted.length === 1 ? (counted[0].label || 'This company') : 'This company',
        kind === 'total' ? d.series[0] : { name: '__total__', points: sumPoints(d.series, d.stamps) }]];
  return lines.map(([name, s, key]) => ({ name, key, ...verdictOf(s, d) }));
}
// How old the newest count may be before the page says so: four of the pipeline's own usual
// gaps between runs (the median, about an hour on 2026-09-25), and never under three hours. A
// flat twelve left the five-hour pause of a classifier warming up unsaid.
function staleAfterHours(stamps){
  const gaps = stamps.slice(1).map((ts, k) => (new Date(ts) - new Date(stamps[k])) / 36e5).sort((a, b) => a - b);
  const median = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 1;
  return Math.max(3, 4 * median);
}
// Under this many days of measurements a line names no direction and no tile headlines it:
// AMD, counted for a few hours, was "Biggest riser +0.1%".
const MIN_SPAN_DAYS = 3;
function spanDays(s, d){
  const first = s.points.findIndex(v => v != null);
  return first < 0 ? 0 : (new Date(d.stamps[d.stamps.length - 1]) - new Date(d.stamps[first])) / 864e5;
}
function verdictOf(s, d){
  const now = latestOf(s);
  const days = spanDays(s, d);
  const over = `over ${Math.round(days)} days`;
  const one = now != null && Math.round(now) === 1;   // "Near AI: 1 tech openings"
  const what = `tech opening${one ? '' : 's'}${trendMetric === 'new' ? ' first seen in the last 7 days' : ''}`;
  const m = trendMove(s);
  // Short because of the window, or because of the company: a company counted for 11 days
  // under a 2-day custom range was called "too new".
  // A company line is judged by its own counting, a summed line by its youngest company's:
  // NewCo beside Google read "this window is too short" off Google's date.
  const young = isYoung(s, d);
  let move;
  const other = countingMove(s) || 0;
  // "; not hiring: −2,041 from duplicate postings removed, +230 from changes in how HeadStart
  // counts" — the rest of the chart's move, by cause. It read "the chart's other +292 openings
  // came from outside hiring: +292 openings from…", twice the words for one figure, and
  // "outside hiring" read as hiring from outside.
  const counting = other ? `; not hiring: ${causesOf(s, other)}` : '';
  if (!m || days < MIN_SPAN_DAYS){
    const hours = Math.max(1, Math.round(days * 24));
    const span = days < 1.5 ? `over the last ${hours} hour${hours === 1 ? '' : 's'}` : over;
    // An older company with one run in the window has no change to give; it is the window
    // that is short, not the company that is new.
    move = young ? 'too new to show a direction yet' + counting
      : !m ? `this window is too short to call a direction${counting}`
      : `${signedOpenings(Math.round(m.change))} ${span} — too short a window to call a direction${counting}`;
  }
  else {
    const n = Math.round(m.change), pct = m.head ? m.change / m.head * 100 : 0;
    // A weekly rate is the figure a reader can hold ("about +77 a week"); the window's own
    // length changes with the date range, a week does not. Not under New, whose figure is
    // already a week's count: a weekly rate of it, drawn from four days, was noise.
    // Nor over a window of about a week, where it restates the change: HCLTech read "−1,282
    // openings, about −1,281 a week".
    const weekly = trendMetric === 'new' || Math.round(days) === 7 ? 0 : Math.round(m.change / days * 7);
    const count = signedOpenings(n) + (weekly ? `, about ${weekly < 0 ? '−' : '+'}${Math.abs(weekly).toLocaleString()} a week` : '');
    move = m.real < MOVER_FLOOR ? (n ? `${signedOpenings(n)} ${over}, too few to call a trend` : `unchanged ${over}`)
      : Math.abs(shown(pct)) < FLAT_PCT ? `about flat ${over} (${pct < 0 ? '−' : '+'}${Math.abs(pct).toFixed(1)}%, ${count})`
      : `${pct > 0 ? 'up' : 'down'} ${Math.abs(pct).toFixed(1)}% ${over} (${count})`;
    // The part of the chart's move that is not hiring, as the difference between the chart's
    // own move and the hiring one, so the two figures add up to what the chart shows. Google's
    // Count line climbed 1,540 → 1,802 under "about flat (+3 openings)" with only a footnote
    // saying why.
    // Under the floor too, or the parts stop adding up to the chart: Paytm's "−11" sat over a
    // line that went 14 → 6.
    move += counting;
  }
  return { text: `${now == null ? 'no' : Math.round(now).toLocaleString()} ${what}; ${move}.`, days };
}
function drawVerdict(d){
  const host = el('trends-verdict'); if (!host) return;
  const lines = verdictLines(d);
  host.hidden = !lines.length;
  if (!lines.length){ host.innerHTML = ''; return; }
  // How long HeadStart has counted them, from `counted_since` — not the window's span, which
  // under "7 days" said "counted since Sep 13 — 7 days". Eleven days is too short to tell a
  // trend from noise, and the chart cannot say so by itself (ADR-0185).
  const dates = Object.fromEntries(countedPicks().map(p => [p.key, countedSince(d, p.key)]).filter(([, v]) => v));
  const firsts = countedSince(d);
  const last = d.stamps[d.stamps.length - 1];
  const days = firsts.length ? Math.round((new Date(last) - new Date(firsts[firsts.length - 1])) / 864e5)
    : Math.round(Math.max(...lines.map(l => l.days)));
  const counted = datedPicks(dates, 'since');
  const who = counted || (trendPicks.length === 1 ? 'this company' : 'these companies');
  // When a month of counting arrives, the question this tab exists for gets an answer; saying
  // when turns "too short" from a dead end into a date.
  const month = firsts.length ? stampLabel(new Date(new Date(firsts[firsts.length - 1]).getTime() + 30 * 864e5).toISOString(), true) : '';
  const early = `HeadStart has counted ${who}${days < 14
    ? ` — too short to tell a trend from noise, so read this as an early sign.${month ? ` A month of counting arrives ${month}.` : ''}`
    : '; there is nothing before that.'}`;
  const tail = early ? `<p class="verdict-early">${esc(early)}</p>` : '';
  // A few sentences, then the rest folded: four companies' took twelve lines above the chart,
  // more than a phone's screen. In pick order, not by size, with the first pick and the lines
  // the tiles headline kept out of the fold: the tile read "Biggest riser Microsoft" while
  // Microsoft's sentence, and Google's, the first pick, were folded under "3 more companies".
  const item = l => `<li><b>${esc(l.name)}</b>: ${esc(l.text)}</li>`;
  const order = trendPicks.map(p => p.key);
  const rank = l => { const k = order.indexOf(l.key); return k < 0 ? order.length : k; };
  lines.sort((a, b) => rank(a) - rank(b));
  const { riser, faller } = tileMovers(d, chartedAndOther(d).charted);
  const unfolded = new Set([lines[0], ...lines.filter(l => l.key && [riser, faller].some(m => m && m.name === l.key))]);
  if (unfolded.size < 2 && lines[1]) unfolded.add(lines[1]);
  const shownLines = lines.filter(l => unfolded.has(l)), more = lines.filter(l => !unfolded.has(l));
  // A redraw (a legend toggle) rebuilds this; an opened fold stays open.
  const wasOpen = !!(host.querySelector && host.querySelector('details[open]'));
  host.innerHTML = `<ul>${shownLines.map(item).join('')}</ul>${more.length
    ? `<details class="verdict-more"${wasOpen ? ' open' : ''}><summary>${more.length} more compan${more.length === 1 ? 'y' : 'ies'}</summary><ul>${more.map(item).join('')}</ul></details>` : ''}${tail}`;
}

// A run where one line leaps and the next run puts it straight back is a partial read of a
// Board, not hiring: Newyorklife went 26 → 104 → 26, headlined "+292.3%" at the leap and kept
// as the table's maximum after it. Such a point — off both neighbours by at least half and 20
// openings, with the neighbours within a tenth of each other — is dropped as unmeasured. The
// newest run has no next one to tell by, so a leap there stands until the next run. Returns how
// many points were dropped.
function dropPartialReads(series){
  const runs = new Set();
  series.forEach(s => {
    const at = s.points.map((v, j) => v == null ? -1 : j).filter(j => j >= 0);
    // Judged against the last point kept, not the one just dropped: [26, 104, 26, 0] dropped
    // the second 26 as well, reading the null it had just made as the level before it.
    let prev = at.length ? s.points[at[0]] : null;
    for (let k = 1; k < at.length - 1; k++){
      const v = s.points[at[k]], next = s.points[at[k + 1]];
      if (Math.abs(v - prev) >= Math.max(20, prev * 0.5) && Math.abs(next - prev) <= Math.max(1, prev * 0.1)){
        s.points[at[k]] = null; runs.add(at[k]);
      } else prev = v;
    }
  });
  return runs.size;   // runs, not points: two lines leaping on one run is one partial read
}

// Every point in the window where lines move for a reason that is not hiring, each with the
// sentence the crosshair shows there (`text`), whether it gets a found-marker line (`found`),
// whether it is taken out of the lines it moves (`withhold`), and which picks' lines those are
// (`company`, one pick; `companies`, several; neither, every line).
//   - A counting change (ADR-0164) is listed on every chart with no pick. Under a pick it is
//     listed only where it moves the lines, and taken out of them: a taxonomy refit, a
//     family-list or family-assignment change (ADR-0215, ADR-0220) or a tech-filter change —
//     measured, Wipro's "+74.7%" held about +25% from the Sep 17 filter step alone — an
//     extraction change on a Level breakdown, whose lines it re-sorts, or duplicate removal
//     (ADR-0188) at a pick it can touch: it parks copies within one Tenant's Boards, Taleo
//     Enterprise sections and Workday sites, so only a pick holding several Boards there can
//     step, and only that pick's line does. A change that cannot move a company's lines drew a
//     marker there that explained nothing.
//   - A Board found later lands its backlog at once (the Space's `discovered`).
//   - In a view that sums several picks, one counted from a later date joins the sum at once:
//     NVIDIA with AMD (counted from Sep 24) named "engineering-management +442.9%".
// Keyed on the epoch's `fields`, never its display text. Only charted points (index > 0) count
// as crossed: a change at the window's first point is already in every line's start.
// Mirrors role_trends.NEW_WINDOW_DAYS: how long a posting counts as new.
const NEW_WINDOW_DAYS = 7;
const LINE_MOVING = [
  'centroid_version', 'family_map_fingerprint', 'family_classifier_version', 'tech_filter_version',
];
// Mirrored by hot_boards `_DEDUP_SIBLING_ATSES`/`_DEDUP_MIRROR_ATS`, so Hot leaves out the runs
// this leaves out: change one, change the other.
const DEDUP_ATSES = ['taleo_enterprise', 'workday'];
// Eightfold Boards are aliased onto, or have their rows dropped against, the Board they
// mirror on another ATS (#632, #649), so any pick holding one can step. That includes a
// directory entry that is only the Eightfold Board ("Micron Technology"): #649 drops almost
// all of its rows on one run, which left in would read as the company collapsing.
const MIRROR_ATS = 'eightfold';
function stepNotes(d){
  const notes = [];
  const picked = trendPicks.length > 0;
  const bands = viewKind(d) === 'bands';
  // Duplicate removal parks copies among one Tenant's Boards, so a pick it can touch holds two
  // or more Boards on one of those ATSes — not one there and one elsewhere.
  const touched = trendPicks.filter(p => {
    const keys = p.boardKeys || [];
    return DEDUP_ATSES.some(a => keys.filter(k => k.startsWith(a + ':')).length > 1)
      || keys.some(k => k.startsWith(MIRROR_ATS + ':'));
  }).map(p => p.key);
  (d.epochs || []).forEach(e => {
    // Under New a tech-filter change counts twice: the openings it lets in read as new at once,
    // and stop reading as new a week later, when they age out of the window. That second drop
    // read as NVIDIA's "down 45.1%" and the index's unmarked −19,600. Only that change: a
    // refit or a duplicate removal adds no newly-seen postings, so it has nothing to age out.
    // The change itself can sit before the window, with its echo inside it.
    if (trendMetric === 'new' && (e.fields || []).includes('tech_filter_version')){
      const echo = new Date(new Date(e.ts).getTime() + NEW_WINDOW_DAYS * 864e5);
      const k = d.stamps.findIndex(ts => new Date(ts) >= echo);
      // `source`: the change it echoes, so countingChanges counts the two as one change.
      if (k > 0) notes.push({ i: k, found: false, epoch: true, withhold: picked, fields: ['tech_filter_version'],
        source: e.ts, touched, echo: true,
        text: `A week after a counting change (${e.changed.join(', ')}): the openings it let in stop counting as new here — not hiring` });
    }
    const i = d.stamps.indexOf(e.ts); if (i < 0) return;
    const fields = e.fields || [];
    // Under picks, a duplicate-removal change is named only where a pick can be touched: Google's
    // marker read "duplicate removal changed", which moved nothing at Google.
    const said = e.changed.filter((c, k) => !picked || fields[k] !== 'dedup_version' || touched.length > 0);
    const text = `Counting changed here: ${said.join(', ')}`;
    if (!picked){ notes.push({ i, text, found: false, epoch: true, withhold: false }); return; }
    const linesMove = fields.some(f => LINE_MOVING.includes(f)) || (bands && fields.includes('derivations_version'));
    const dedup = !linesMove && touched.length > 0 && fields.includes('dedup_version');
    if (!(linesMove || dedup)) return;
    const companies = dedup ? touched : null;
    // An extraction change re-sorts a category's levels, never its total: the drill's own
    // sentence took it out anyway and read −6.8% against the category row's −6.3%.
    const bandsOnly = !fields.some(f => LINE_MOVING.includes(f) || f === 'dedup_version');
    // A change on the window's first run is already in every line's start; its settling run
    // (below) is not.
    if (i > 0) notes.push({ i, text: `${text} — not hiring, so the jump it makes is left out of the lines it moves`,
      found: false, epoch: true, withhold: true, companies, bandsOnly,
      // What changed, for countingChanges to name, with the picks duplicate removal can touch.
      fields, source: e.ts, touched,
      dedupOnly: fields.length === 1 && fields[0] === 'dedup_version' });
    // A change can land over two runs — Amazon's Sep 17 filter change was +308 at its run and
    // −439 at the next, which left in read as hiring and turned Amazon from +1.2% to −2.9% —
    // so the run after it is left out too, at the cost of one run of ordinary change.
    // Only for a change inside the window. At its first run the change's own jump is not on the
    // chart, so its settling run cannot be told from an ordinary one; taking it out regardless
    // cut Amazon's real −7 with no marker to say why. The cost: a change landing over two runs
    // exactly at the window's start leaves its second half in.
    if (i > 0 && i + 1 < d.stamps.length) notes.push({ i: i + 1, found: false, withhold: true, companies, settle: true, settles: i, bandsOnly,
      dedupOnly: fields.length === 1 && fields[0] === 'dedup_version',
      text: 'Left out: the run after a counting change, which can still be settling' });
  });
  (d.discovered || []).forEach(f => {
    const i = d.stamps.indexOf(f.ts); if (i < 0) return;
    const who = (trendPicks.find(p => p.key === f.company) || {}).label || 'a picked company';
    // Found openings are added back, not scaled: they were open all along, so the history is
    // lifted by them rather than multiplied, and a sum of companies moves by the sum of their moves.
    // A handful of found openings is left in the line, unmarked: a marker for one opening was
    // furniture, and a step taken out with no marker was a change the reader could not see.
    notes.push({ i, found: true, withhold: f.openings >= INDEX_BASE_FLOOR, company: f.company, size: f.openings,
      text: `${f.boards} more board${f.boards === 1 ? '' : 's'} of ${who} found here: `
        + `${f.openings.toLocaleString()} tech opening${f.openings === 1 ? '' : 's'} across the company, already open, arrive at once — not new hiring` });
  });
  // Duplicate rows removed at a pick's Boards (#649, the Space's `evicted`): the same postings
  // served twice, so their removal is not closures. Sized exactly, but per Board and not per
  // category, so only a whole company's line can take them out (`wholeOnly`); a category line
  // would have to guess which of its runs they fell in.
  if (trendMetric === 'stock') (d.evicted || []).forEach(e => {
    const i = d.stamps.indexOf(e.ts); if (i <= 0) return;
    const who = (trendPicks.find(p => p.key === e.company) || {}).label || 'a picked company';
    if (e.count < INDEX_BASE_FLOOR) return;   // likewise a handful of duplicates
    notes.push({ i, found: false, evicted: true, withhold: true, company: e.company, size: -e.count, wholeOnly: true,
      text: `${e.count.toLocaleString()} duplicate posting${e.count === 1 ? '' : 's'} of ${who} removed here — the same jobs listed twice, not closures` });
  });
  // Not under comparable coverage: a pick counted after the cohort's base has no Boards in it.
  // Under New a pick joins when its first week ends (`new_counted_from`), not when counted.
  if (trendPicks.length > 1 && VIEWS[viewKind(d)].split !== 'company' && trendCoverage !== 'comparable'){
    const since = (trendMetric === 'new' ? d.new_counted_from : d.counted_since) || {};
    trendPicks.forEach(p => {
      const i = since[p.key] ? d.stamps.findIndex(s => s >= since[p.key]) : -1;
      if (i <= 0) return;
      notes.push({ i, found: true, withhold: true, company: p.key,
        text: `Counting for ${p.label || 'a picked company'} starts here: its openings join these lines at once — not new hiring` });
    });
  }
  return notes;
}

// Each pick with its date from `dates` (pick key -> ISO stamp): "Acme since Sep 13", "these
// companies since Sep 13" when they agree, else each named. '' when none is dated.
function datedPicks(dates, word){
  const map = dates || {};
  const dated = trendPicks.filter(p => map[p.key]);
  if (!dated.length) return '';
  const on = p => `${word} ${stampLabel(map[p.key], true)}`;
  if (dated.length === 1) return `${dated[0].label || 'this company'} ${on(dated[0])}`;
  if (dated.every(p => map[p.key] === map[dated[0].key])) return `these companies ${on(dated[0])}`;
  return dated.map(p => `${p.label || 'a company'} ${on(p)}`).join(', ');
}

// A company's history starts where HeadStart began counting it, so a preset longer than that
// draws exactly what All draws. Those presets are disabled with the reason, not left to look
// like three different answers (30 days, 90 days and All were one chart).
function drawRangeLimits(d){
  const seg = el('trends-range'); if (!seg) return;
  const firsts = countedSince(d);
  const first = trendPicks.length && firsts.length ? firsts[0] : null;
  const had = first ? (Date.now() - new Date(first)) / 864e5 : Infinity;
  let off = false;
  seg.querySelectorAll('button').forEach(b => {
    const days = Number(b.dataset.days);
    const short = !!days && days > had && b.getAttribute('aria-checked') !== 'true';
    off = off || short;
    b.disabled = short;
    b.title = short ? `HeadStart has counted ${trendPicks.length === 1 ? 'this company' : 'these companies'} only since ${stampLabel(first, true)}, so this would show the same as All` : '';
  });
  // The reason on screen, not only in a tooltip a touch screen never shows.
  const note = el('trends-range-static');
  if (note){
    note.hidden = !off;
    note.textContent = off ? `Longer ranges are off: HeadStart has counted ${
      trendPicks.length === 1 ? 'this company' : 'these companies'} only since ${stampLabel(first, true)}${
      new Set(firsts).size > 1 ? ' at the earliest' : ''}.` : '';
  }
}

// Compared company by company, the question is how each is hiring, not which roles grow.
function drawTitle(){
  if (!el('trends-title')) return;
  const where = pickPhrase(), several = countedPicks().length > 1;
  const kind = trendData ? viewKind(trendData) : null;
  el('trends-title').textContent =
    trendDrill && trendRaw && trendRaw.family_known === false ? `No category called “${trendDrill}”`
    : kind === 'drillCompany' && several ? `How ${drillLabel()} hiring compares ${where}`
    : kind === 'roles' ? `Tracked roles in ${drillLabel()}${where ? ' ' + where : ''}`
    : trendDrill && where ? `How ${drillLabel()} hiring is moving ${where}`
    : !trendDrill && several && topSplitNow() === 'company' ? `How tech hiring compares ${where}`
    : !trendDrill && where && topSplitNow() === 'total' ? `How tech hiring is moving ${where}`
    : 'Which tech roles are growing' + (where ? ' ' + where : '');
}

// "at Stripe" / "at 3 companies": how the text around the chart names what the picks scope.
// The drilled family's name as the Space labels it, else its slug.
function drillLabel(){
  return (trendRaw && trendRaw.family_label) || trendDrill || '';
}

// Only the picks the answer counts: under Comparable, "at 2 companies" sat over "1 company".
function countedPicks(){
  const out = (trendData && trendData.uncounted) || [];
  return trendPicks.filter(p => !out.includes(p.key));
}
function pickPhrase(){
  // With none counted (a window before counting began) the picks still name the chart.
  const counted = countedPicks(), picks = counted.length ? counted : trendPicks, n = picks.length;
  return !n ? '' : n === 1 ? `at ${picks[0].label || 'the picked company'}` : `at ${n} companies`;
}
// When HeadStart began counting the picks this answer counts, earliest first — or one pick's
// own date with `key`. Every "counted since" figure reads it here.
function countedSince(d, key){
  const since = d.counted_since || {};
  if (key) return since[key] || null;
  return countedPicks().map(p => since[p.key]).filter(Boolean).sort();
}

// The chart and the table view both need "the top CHART_MAX, plus Other" — one place computes
// it so the split can't quietly drift between the two renderers.
function chartedAndOther(d){
  const charted = d.series.slice(0, CHART_MAX);
  const other = buildOtherSeries(d.series.slice(CHART_MAX));
  return { charted, other, shown: other ? [...charted, other] : charted };
}

// The window goes to the server — a range must filter before the share denominator is computed,
// not after. Presets carry the common case (the axis is measured in days; the two
// `datetime-local` fields were 365px each at minute precision). A custom bound always beats the
// preset, so the two can never both be in force and disagree. `datetime-local` reads in the
// browser's local zone; converted to UTC so the filter lines up with the chart's own UTC axis
// (stampLabel already renders in UTC).
function trendRange(){
  const r = {};
  const since = el('trends-since') && el('trends-since').value;
  const until = el('trends-until') && el('trends-until').value;
  if (since) r.since = new Date(since).toISOString();
  if (until) r.until = new Date(until).toISOString();
  if (!r.since && !r.until && ['7', '30', '90'].includes(trendDays))
    r.since = new Date(Date.now() - Number(trendDays) * 864e5).toISOString();
  return r;
}

// Selecting the preset from code — the path a custom date takes, which has to drop the preset
// back to "All" so the segment never claims a window the request is not using.
function setRangePreset(v){
  trendDays = v;
  const seg = el('trends-range'); if (!seg) return;
  seg.querySelectorAll('button').forEach(b => setRadioChecked(b, b.dataset.days === v));
}

// Checked ATS names, or null for "no filter" — the only spelling of it (ADR-0075): sending
// all of them explicitly would exclude pre-ship, undecomposed rows.
//
// BOTH ends of the range mean that. Every box checked is the obvious one. Every box UNchecked
// is the same thing and always has been, because `/trends` narrows on the `ats` params it is
// given and an empty selection appends none — so the panel answers with every ATS. It used to
// come back as `[]`, which is a truthy array, and each of the three places that ask "is a
// filter on?" then said yes: the trigger read "0 ATS", the chart named itself "0 of the ATS
// sources", and the short-history note blamed a narrow selection — all three over the
// unfiltered figure. Answering `null` states once, here, what the request already did.
function trendAtsSelected(){
  const menu = el('trends-ats-menu'); if (!menu) return null;
  // Only the boxes on show: one a pick narrowed away is no part of the reader's selection, and
  // sending its unchecked state emptied the chart with nothing on screen to say why.
  const boxes = [...menu.querySelectorAll('input[type=checkbox]')]
    .filter(b => !(b.parentElement && b.parentElement.hidden));
  const checked = boxes.filter(b => b.checked).map(b => b.value);
  return checked.length && checked.length !== boxes.length ? checked : null;
}

// The trigger reads its state off trendAtsSelected rather than counting the boxes a second
// time, so "no filter" is decided in exactly one place: both ends of the range — every box
// checked and none checked — say "All ATS", because both are what the panel is showing.
function trendAtsLabel(){
  const menu = el('trends-ats-menu'); if (!menu) return;
  const sel = trendAtsSelected();
  el('trends-ats-trigger').textContent = (sel ? `${sel.length} ATS` : 'All ATS') + ' ▾';
}

function toggleAtsPopover(force){
  const open = force ?? el('trends-ats-menu').hidden;
  el('trends-ats-menu').hidden = !open;
  el('trends-ats-trigger').setAttribute('aria-expanded', open);
}

async function loadTrends(family){
  // Exactly one request owns the panel. Every Trends control calls this, and the ATS picker
  // calls it once per checkbox — so unchecking 20 of 21 boxes to reach "1 ATS" starts 20
  // round trips that all used to run to completion and paint, in arrival order. Measured in
  // Chromium against a stub serving the real templates at the Space's own latency: 20
  // requests, 20 repaints, and the numbers churning non-monotonically (32k, 33k, 35k, 30k,
  // 37k, 28k …) because 6-11 of the 20 answers arrived out of the order they were asked in.
  // Worse than the flicker, the panel then settled on whichever answer landed last, which in
  // 3 runs of 5 was NOT the last request's — the chart named a scope the SOURCE control did
  // not. Cancelling the previous request is what makes the loser deterministic: an aborted
  // fetch can never resolve, so it can neither paint nor be raced. That takes the same
  // interaction to 1 repaint, and takes it there at any click rate FASTER than the round
  // trip, which is the rate that produced the churn.
  //
  // What it deliberately does not do is bound the repaints when the reader clicks SLOWER than
  // the round trip: nothing overlaps, so there is nothing to cancel, and 20 unchecks still
  // paint 20 times (measured). Those 20 are in order and each is the true answer to a click
  // just made — the panel updating per action, not churning — so the unconditional bound
  // (committing the selection when the popover closes) is a change to what the control MEANS
  // and is left as a product call rather than smuggled in with a race fix.
  if (trendReq) trendReq.abort();
  const req = trendReq = new AbortController();
  const push = picksPushed; picksPushed = false;   // this load's, whatever becomes of it
  const q = new URLSearchParams();
  // The roles split only exists for families that HAVE watched roles. Carrying a sticky
  // 'roles' into one that doesn't returns an empty series with its toggle hidden — nothing
  // to click, nothing to go back from, and only a reload escapes. Company is the same for a
  // pick list that has shrunk below two.
  // Only where it knows: a cold link into a roles drill has no answer yet to say otherwise.
  if (family && trendSplit === 'roles' && trendData && !(trendData.watch_parents || []).includes(family)) trendSplit = 'bands';
  if (trendPicks.length < 2){
    if (trendSplit === 'company') trendSplit = 'bands';
    if (topSplit.chosen === 'company') topSplit.chosen = 'auto';
  }
  trendPicks.forEach(p => q.append('company', p.key));
  if (family) { q.set('family', family); q.set('split', trendSplit); }
  else if (topSplitNow() === 'company') q.set('split', 'company');
  if (trendMetric !== 'stock') q.set('metric', trendMetric);
  const range = trendRange();
  if (trendCoverage === 'comparable') {
    q.set('coverage', trendCoverage);
    if (range.since) q.set('base', range.since);
  } else if (range.since) q.set('since', range.since);
  if (range.until) q.set('until', range.until);
  const ats = trendAtsSelected();
  if (ats) ats.forEach(a => q.append('ats', a));
  // Refetch keeps the frame (dataviz skill, interaction.md): the previous render stays up,
  // dimmed, rather than the panel blanking — a filter change must never read as "it broke"
  // while the round trip is in flight, and a genuine failure must never look like a dead toggle.
  if (el('trends-viz')) el('trends-viz').classList.add('loading');
  // First open only: a refetch keeps the previous chart up, dimmed, which is already the
  // right answer (a filter change must never read as "it broke"). With nothing to keep, a
  // skeleton in the same grid holds the same space rather than letting the panel jump.
  setTrendsBusy(!trendData);
  // A refetch keeps the previous answer up, dimmed and marked busy — the sentences and tiles
  // with it, which read as the new company's for the length of the round trip.
  if (trendData){
    if (el('trends')) el('trends').setAttribute('aria-busy', 'true');
    dimAnswer(true);
  }
  // The outcome is decided first and acted on second, so there is ONE place a response may
  // touch the panel and one abort check guarding it. Reading the body is inside the try
  // because an abort mid-download rejects r.json() exactly as it rejects the fetch — which
  // also closes a hole that was already there: a malformed 200 body used to reject nowhere at
  // all, leaving the panel dimmed for good instead of saying anything.
  let payload, err, refused;
  try {
    const r = await fetch('/trends' + (q.size ? '?' + q : ''), { signal: req.signal });
    if (r.ok) payload = await r.json();
    else if (trendPicks.length && (r.status === 400 || r.status === 503))
      refused = { status: r.status, error: ((await r.json().catch(() => null)) || {}).error || '' };
    else err = r.status === 401
      ? 'Your session expired — sign in again to see trends.'
      : 'Trends didn’t load. Try again.';
  } catch(e){ err = 'That request didn’t go through.'; }
  // Cancelled by a newer request, which now owns the panel: say nothing, paint nothing. An
  // abort lands in the catch above like a dropped connection, and reporting it would put
  // "that request didn't go through" over a render that is about to be replaced anyway.
  if (req.signal.aborted) return;
  if (refused && dropRefusedPicks(refused)) return loadTrends(family);
  if (refused) err = 'Trends didn’t load. Try again.';
  if (err){ showTrendsError(err); return; }
  hideTrendsError();
  // The Space names each pick, disambiguated against the others (ADR-0185), and answers with
  // the directory's own key even when the pick came in by another of the company's Boards.
  // The Space lists them sorted by key, so they are laid back onto the reader's own order: a
  // pick keeps its place, and one that came in by another Board of its company goes last.
  if (payload.companies){
    const byKey = new Map(payload.companies.map(c => [c.key,
      { key: c.key, label: c.label, boardKeys: c.board_keys, atses: c.atses }]));
    const kept = trendPicks.map(p => byKey.get(p.key)).filter(Boolean);
    trendPicks = [...kept, ...[...byKey.values()].filter(c => !kept.includes(c))];
  }
  // Decided from an answer with lines in it: an empty one (a comparable window before per-Board
  // counting began) says nothing about the company's size, and deciding there stuck on Total.
  if (topSplit.stale && !family && payload.split_by === 'family' && payload.series.length){
    topSplit.autoTotal = fewIndexable(payload); topSplit.stale = false;
  }
  // The family as the Space read it: an old link's name, or a new name before its data lands,
  // arrives as the one the data holds (ADR-0220's renames).
  if (family && payload.family) family = payload.family;
  const drilled = (family || null) !== trendDrill;
  trendRaw = payload; trendDrill = family || null;
  if (trendPicks.length && payload.metric !== 'new') payload.partial = dropPartialReads(payload.series);
  trendData = trendView(payload, trendDrill);
  drawPicks(); applyUnitLocks(); writeTrendHash(drilled || push);
  drawTrends();
}

// A pick the Space refuses leaves with a sentence rather than failing the chart. A search
// result's Board is a guess from its id (ADR-0049), and about 2% of them name no Board in the
// directory (measured 2026-09-24 over the 514,163-row served table); a Space whose directory has
// not been built yet answers 503 to any pick at all. Returns false when nothing can be dropped,
// so a 400 about something else can never loop.
function dropRefusedPicks({ status, error }){
  const named = status === 400 && error.startsWith('unknown company: ')
    ? new Set(error.slice('unknown company: '.length).split(', ')) : null;
  // A 503 is the picks' fault only when the directory is what is missing; with no trend data
  // at all, dropping the picks would retry into the same 503 and blame the wrong thing.
  if (!named && !(status === 503 && error.includes('company directory'))) return false;
  const gone = named ? trendPicks.filter(p => named.has(p.key)) : trendPicks;
  if (!gone.length) return false;
  replacePicks(trendPicks.filter(p => !gone.includes(p)));
  setCoNote(!named ? 'Company trends aren’t available here yet.'
    // Not "yet", which promised an arrival: the directory, rebuilt each run, holds no such
    // company now, and nothing says it will.
    : gone.length === 1 ? `HeadStart has no trend for ${gone[0].label || gone[0].key}: it isn’t in the company directory.`
    : `HeadStart has no trend for ${gone.length} of those companies: they aren’t in the company directory, so they were left out.`);
  return true;
}

// A failed fetch used to hide the whole #trends section — every toggle then looked locked with
// no explanation. Now only the chart/legend area is replaced; the header, toggles and Retry stay
// reachable, and Retry replays the exact same request `loadTrends` just made.
function showTrendsError(msg){
  setTrendsBusy(false);
  dimAnswer(false);
  if (el('trends-viz')){ el('trends-viz').classList.remove('loading'); el('trends-viz').hidden = true; }
  // Everything that describes data goes with the data. The tiles, the table and the "how to
  // read this" block used to survive a failed fetch, so a panel with no chart still carried
  // 190 words about how to read one. The scope line goes with them: "24 categories · 471
  // measurements" under a "didn't load" banner describes the payload that is NOT on screen.
  ['trends-kpi', 'trends-table-wrap', 'trends-chart-note'].forEach(id => {
    if (el(id)) el(id).hidden = true;
  });
  if (el('trends-scope')) el('trends-scope').textContent = '';
  // The chart's space, held by a neutral box rather than by the alert itself. The alert used
  // to carry `min-height:min(46vh,360px)`, which drew a 360px alert-tinted void around six
  // words; the panel still must not collapse under the reader, so the space is reserved next
  // to the message instead of inside it.
  if (el('trends-void')) el('trends-void').hidden = false;
  if (el('trends-error')){ el('trends-error').hidden = false; el('trends-error-msg').textContent = msg; }
}
// Everything that states the answer in words, dimmed together while a new one is fetched.
const ANSWER_TEXT = ['trends-verdict', 'trends-kpi', 'trends-empty', 'trends-chart-note'];
function dimAnswer(on){ ANSWER_TEXT.forEach(id => { if (el(id)) el(id).classList.toggle('loading', on); }); }
function hideTrendsError(){
  setTrendsBusy(false);
  dimAnswer(false);
  if (el('trends-error')) el('trends-error').hidden = true;
  if (el('trends-void')) el('trends-void').hidden = true;
  if (el('trends-chart-note')) el('trends-chart-note').hidden = false;
  if (el('trends-table-wrap')) el('trends-table-wrap').hidden = !tableView;
  if (el('trends-viz')){ el('trends-viz').hidden = tableView; el('trends-viz').classList.remove('loading'); }
}
// The skeleton swaps for the chart rather than sitting above it, so nothing moves when the
// data lands; aria-busy is what says "working" to a screen reader, which a shimmer cannot.
function setTrendsBusy(on){
  const skel = el('trends-skel'), viz = el('trends-viz'), sec = el('trends');
  if (sec) sec.setAttribute('aria-busy', String(!!on));
  if (skel) skel.hidden = !on;
  if (viz && on) viz.hidden = true;
}

// A series' LEVEL at a stamp: share of the index for stock, else the raw count. Share divides
// by that stamp's WHOLE table (families + non-tech), so index growth cancels.
//
// This is not always what the chart plots — under Change the plot is indexed (seriesValues
// below) — but it is always what the legend, the table and the tooltip report, because those
// are the magnitude surfaces. ADR-0119 splits the work that way on purpose: shapes on the
// plot, magnitudes in the text beside it.
function levelValue(v, j, s){
  if (v == null) return null;
  if (trendUnit !== 'share') return v;      // Count and Change both stand on raw openings
  // A company line is a share of its own company's openings (see trendView).
  const t = ((s && s.denoms) || trendData.totals)[j];
  return t ? v / t * 100 : null;
}

// What the PLOT draws for a series. Under Share and Count that is the level. Under Change each
// series is divided by its own first measured level and multiplied by 100, so every line starts
// together at 100 and traces its own movement (ADR-0119).
//
// Change indexes the raw COUNT, not the share. That is a deliberate choice with a cost: the
// index grows as scraping coverage does, so a run that adds a board lifts every line at once
// without a single job having been posted. Indexing the share would cancel that, at the price
// of answering a different question — "did this family gain ground on the others" rather than
// "are there more of these jobs than there were". The second is the question the panel's
// heading asks, so it is the one the chart answers, and the caption carries the caveat.
//
// Indexing is per-series, which is why this takes a series and not a point: the same raw number
// means a different plotted value depending on where its family started. A family whose base is
// zero or missing cannot be indexed at all and is drawn as a gap rather than as a spike.
const INDEX_BASE_FLOOR = 5;   // openings; below this an index is arithmetic, not a reading

// The one place that decides a series' index base, so the chart, the legend and the KPI tiles
// cannot disagree about it. They did: the chart gated on the first measured level while
// trendDelta gated on the mean of the first three, so [4, 20, 30, ...] was drawn as a gap and
// labelled "not indexed" in the legend while the tile above it read "Biggest riser +233.3%".
// Returns null when there is no usable base.
// The floor is read off the real first count, the base off the adjusted one (netLevels), so
// the index line is the same shape the percentage beside it is read from.
function indexBase(s){
  const first = s.points.map((v, j) => levelValue(v, j, s)).find(v => v != null);
  if (first == null || first < INDEX_BASE_FLOOR) return null;
  return netLevels(s).find(v => v != null);
}
function netLevels(s){ return netOfSteps(s.points.map((v, j) => levelValue(v, j, s)), s); }

// Under Share and Count the plot draws the real levels, which a reader reads off the axis, and
// breaks the line at each marked step (drawTrends) rather than drawing a jump as a climb.
// Change is a shape, not a level, so it draws the levels with the steps taken out.
function seriesValues(s){
  const level = s.points.map((v, j) => levelValue(v, j, s));
  if (trendUnit !== 'change') return level;
  // The base is the first MEASURED level, not the first truthy one: `find(v => v)` skipped a
  // real measurement of zero and indexed off a later point, so a family sitting at 0 early was
  // drawn starting at 0 rather than 100, spiked to 800, and dragged the axis to 0-800 —
  // crushing every other line into ~24px. That is the exact pathology ADR-0119 removed. A base
  // under the floor is no base: two openings would turn one posting into +50%.
  const base = indexBase(s);
  if (base == null) return level.map(() => null);
  return netLevels(s).map(v => (v == null ? null : v / base * 100));
}

// Whether a series can be indexed in the current window — the legend uses this to say why a
// row has no line, and the KPI tiles use it to avoid headlining one.
function hasIndexBase(s){
  return trendUnit !== 'change' || indexBase(s) != null;
}

// A series' movement across the window, in the displayed unit, first run to last (headTail),
// net of the marked steps. A thin run at either end is not averaged away; the steps that used
// to swing a headline are taken out instead, and the run after each counting change with them.
function trendDelta(points, s){
  const ends = headTail(netOfSteps(points.map((v, j) => levelValue(v, j, s)), s));
  if (!ends) return null;
  const { head, tail } = ends;
  // A percentage off a head of one or two openings is arithmetic, not a reading: it produced
  // a "Biggest riser +1300.0%" headline off a category that went from 1 opening to 14.
  if (!head || (trendUnit !== 'share' && head < INDEX_BASE_FLOOR)) return null;
  return (tail - head) / head * 100;
}
// The first and last measured values, or null under two. Endpoints, not averages of three:
// the Change line ends at 100 × last / first, so a percentage off averages never quite matched
// its own line (107.7 against +7.6%), and a sentence's "+121 hiring" and "+477 counting
// changes" did not add up to the +583 the chart showed. The steps the averaging guarded
// against are taken out now, and the run after each counting change with them.
function headTail(values){
  const seen = values.filter(v => v != null);
  if (seen.length < 2) return null;
  return { head: seen[0], tail: seen[seen.length - 1] };
}
// A line's movement in openings, net of the marked steps, read the way trendDelta reads its
// percentage. `head` is the net head the percentage divides by; `real` is the openings it
// really had at the window's start, which MOVER_FLOOR is held to — held to the adjusted head,
// a company at 12 whose found Board doubled it cleared the floor it exists to stop.
function trendMove(s){
  const ends = headTail(netOfSteps(s.points, s));
  const real = headTail(s.points);
  return ends && { head: ends.head, real: real ? real.head : ends.head, change: ends.tail - ends.head };
}
// Below this many openings at the window's start a line's move is stated in openings, never as
// a percentage, and no tile names it: Stripe's "Biggest riser: sre-platform +18.2%" was 11
// openings becoming 13, and Paytm's −65.7% was six openings. The Change floor (5) decides
// whether a line can be drawn indexed at all; this one decides whether its percentage is news.
const MOVER_FLOOR = 20;
// The legend's and the table's figure for a line: its percentage, or under the floor its
// change in openings (`count`), which is what a reader can actually weigh.
function lineMove(s){
  // Under MIN_SPAN_DAYS of a young company a line has no direction to state, in the legend as
  // in the sentence: "too new to show a direction" sat beside "↑ +1.5%". A short window over an
  // older company states its change in openings, as the sentence does — Hot's "+18" had to be
  // checkable on the trend its row opens.
  const m = trendMove(s);
  const since = firstSeen(s, trendData);
  if (since) return recountBorn(s) ? { since, recount: true } : { since };
  // Under Share a change in openings is another unit beside shares, so those lines show none.
  if (trendData && spanDays(s, trendData) < MIN_SPAN_DAYS){
    if (isYoung(s, trendData)) return { tooNew: true };
    return !m || trendUnit === 'share' ? { small: true, short: true } : { count: Math.round(m.change), short: true };
  }
  if (m && m.real < MOVER_FLOOR) return trendUnit === 'share' ? { small: true } : { count: Math.round(m.change) };
  // A change under half an opening has no direction: Micron's Engineering Management read
  // "↑ +1.7%" beside "+0 openings", a quarter-opening off a netted base of 15.
  if (m && trendUnit !== 'share' && Math.round(m.change) === 0) return { count: 0 };
  return { dl: trendDelta(s.points, s) };
}
// A legend row's move. Under Count the plot is in openings, so every row's move is too, the
// Other row's included: "Product Management ↑ +12 openings" sat among percentages. The tiles
// still rank by percentage (tileMovers).
function legendMove(s){
  const moved = lineMove(s), net = trendUnit === 'count' && 'dl' in moved && trendMove(s);
  return net ? { count: Math.round(net.change) } : moved;
}
// The stamp a picked company's category first held openings, when that is inside the window and
// after every pick was counted: Micron's Architecture, new at the Sep 24 refit, read "→ +0
// openings" over what looked like twelve flat days. Stock only — under New a line also starts
// where a Board's first-week hold ends — and never a company's own line, which says when it
// was counted.
function firstSeen(s, d){
  if (!d || !trendPicks.length || trendMetric !== 'stock' || !s || s.name === '__total__' || s.name === '__other__'
    || VIEWS[viewKind(d)].split === 'company') return null;
  const first = s.points.findIndex(v => v != null);
  if (first < 1) return null;
  const counted = countedSince(d);
  const youngest = counted[counted.length - 1];
  return youngest && d.stamps[first] > youngest ? d.stamps[first] : null;
}
// Whether a line first seen inside the window appeared at a counting change (or the run after
// it): Stripe's "Web & .NET Development 16 new since Sep 24" was the Sep 24 family-assignment
// change sorting 16 existing jobs into it, which "new since" read as hiring.
function recountBorn(s){
  const first = s.points.findIndex(v => v != null);
  return stepsFor(s).some(n => n.epoch && noteKind(n) === 'counting' && (n.i === first || n.i + 1 === first));
}
// Whether HeadStart has counted the line's company (a summed line: its youngest) for under
// MIN_SPAN_DAYS, as against the window being short.
function isYoung(s, d){
  const all = countedSince(d);
  const began = countedSince(d, s.name) || all[all.length - 1];
  return !began || (new Date(d.stamps[d.stamps.length - 1]) - new Date(began)) / 864e5 < MIN_SPAN_DAYS;
}
// A count has no dead band: one opening either way is a direction, where deltaClass reads
// ±1 as flat because it was written for percentages.
function moveClass(mv){
  if (mv.tooNew || mv.small || mv.since) return 'flat';
  if (mv.count == null) return deltaClass(mv.dl);
  return mv.count > 0 ? 'up' : mv.count < 0 ? 'down' : 'flat';
}
function moveText(mv){
  if (mv.tooNew) return 'too new';
  if (mv.since) return mv.recount ? `sorted in by a counting change, ${stampLabel(mv.since, true)}` : `new since ${stampLabel(mv.since, true)}`;
  if (mv.small) return '—';
  if (mv.count == null) return deltaText(mv.dl);
  const n = mv.count;
  return `${n > 0 ? '↑' : n < 0 ? '↓' : '→'} ${signedOpenings(n)}`;
}
// "+3 openings", "−1 opening": every place a change is given in openings.
function signedOpenings(n){
  return `${n < 0 ? '−' : '+'}${Math.abs(n).toLocaleString()} opening${Math.abs(n) === 1 ? '' : 's'}`;
}

// A line's levels with the marked steps taken out (stepNotes' `withhold`: a counting change and
// the run after it, a found Board, a later pick joining a sum, duplicate removals). Adjusted
// backwards, the way a price history is adjusted: the latest value stays the real one, and the
// history before a step is brought to it, so the line reads as if the new counting had applied
// all along. Wipro's "+74.7%" carried +500 and +258 openings landing on two tech-filter markers;
// Google with BAE Systems "+1170.2%" was BAE joining the sum.
//
// By openings, on every line: the history before a step is shifted by the step's size, never
// scaled. Categories were once taken out by ratio (a refit re-sorts a share of a category), and
// their hiring then did not add up to the company's: Micron's categories summed to +62 under a
// company +160, Google's to +15 under −32, the gap filed as "Between categories" (critic round
// 13). The user chose sums that add up over ratio-true percentages (2026-09-25): a category a
// refit halved now reads its percentage against the pre-refit base (Google's software
// engineering read −11.8% where ratio gave −5.3%).
//
// The one exception is a shift that would push the history below zero — a change from 200 to 20
// after growth from 50 — which scales instead, where both sides hold RATIO_FLOOR openings, so the
// change cannot erase the line's start. Where it cannot scale either, the line starts after that
// step instead of inventing a zero base.
//
// The Change plot draws these levels and every percentage is read off them, so the line and its
// number cannot disagree. With no pick there are no such steps, so the index chart is unchanged.
// `only` limits the steps to those kinds (causesOf).
const RATIO_FLOOR = 20;
function netOfSteps(levels, s, only){
  // A line summing several picks is the sum of each pick's own netted line, so a company's
  // step comes out of its own part only and Total reads the sum of the Company breakdown. In
  // openings only: under Share every level is divided by the whole.
  const picks = summedPicks(s);
  if (picks && trendUnit !== 'share'){
    // Before a pick's first run its part of the sum holds at its first value: a company counted
    // from a later date joins the sum as a step taken out by openings, as the join note took it
    // out before — read as 0 there, NVIDIA's whole level landed in Total as hiring.
    const nets = picks.map(p => {
      const net = netOfSteps(p.points, p, only);
      const first = net.find(v => v != null);
      let seen = false;
      return net.map(v => { if (v != null) seen = true; return seen ? v : first; });
    });
    return levels.map((v, j) => v == null ? null
      : nets.some(n => n[j] != null) ? nets.reduce((sum, n) => sum + (n[j] || 0), 0) : null);
  }
  const jumps = stepJumps(levels, s, only);
  if (!jumps.size) return levels;
  // The same steps read off the openings themselves, for the floor.
  const counts = s && s.points && levels !== s.points ? stepJumps(s.points, s, only) : jumps;
  const out = levels.slice();
  // Shifted, a company's line and Hot's sum are one figure for a company of one Board: scaled,
  // Squircle read +513 where Hot read +459 (measured 2026-09-25).
  let lowest = Infinity;
  const lowestBefore = levels.map(v => { const at = lowest; if (v != null) lowest = Math.min(lowest, v); return at; });
  let scale = 1, lift = 0, cut = false;
  for (let j = levels.length - 1; j >= 0; j--){
    if (levels[j] == null) continue;
    const v = levels[j] * scale + lift;
    if (cut || v < 0){ cut = true; out[j] = null; continue; }
    out[j] = v;
    const jump = jumps.get(j);
    if (!jump) continue;
    const size = counts.get(j) || jump;
    const known = jump.kinds.has('found') || jump.kinds.has('duplicates');
    const shift = scale * (jump.lift ?? (jump.after - jump.before));
    const erases = lowestBefore[j] * scale + lift + shift < 0;
    if (erases && jump.lift == null && !known && size.before >= RATIO_FLOOR && size.after >= RATIO_FLOOR)
      scale *= jump.after / jump.before;
    else lift += shift;
  }
  return out;
}
// Where each withheld step lands on series `s` (a step on a gap lands on the next measured
// point), with the level on either side of it: landing index -> {before, after, kinds, lift}.
// `lift` is a step's size when it is known exactly and the line is a whole company's
// (isWholeLine): found openings, duplicate removals. Taking out that size rather than the run's
// whole jump keeps the run's ordinary hiring in the line.
// A line that is a whole company's tech openings under All openings: the Total, or a company's
// line at the top level. Only these can take out a step whose size is known per company.
// Each pick's own line, when `s` sums several (the Space's `pick_series`), else null.
function summedPicks(s){
  const d = trendData;
  if (!s || s.pick || s.name !== '__total__' || !d || !d.pick_series || Object.keys(d.pick_series).length < 2) return null;
  return Object.entries(d.pick_series).map(([name, points]) => ({ name, points, pick: true }));
}
function isWholeLine(s){
  // Never inside a category: a drill's summed line is one category, which a whole company's
  // found openings or removals would overshoot (NVIDIA's 2,045 removals against its AI/ML 300).
  return trendMetric === 'stock' && !!s && !trendDrill
    && (s.name === '__total__' || !!s.pick || VIEWS[viewKind(trendData)].split === 'company');
}
function stepJumps(levels, s, only){
  const steps = new Map(), jumps = new Map();
  const whole = isWholeLine(s);
  stepsFor(s).forEach(n => {
    const kind = noteKind(n);
    if (only && !only.has(kind)) return;
    const at = steps.get(n.i) || { size: 0, sized: true, kinds: new Set() };
    if (n.size == null) at.sized = false; else at.size += n.size;
    at.kinds.add(kind);
    steps.set(n.i, at);
  });
  if (!steps.size) return jumps;
  let last = null, pending = null;
  levels.forEach((v, j) => {
    if (steps.has(j)){
      const at = steps.get(j);
      pending = pending ? { size: pending.size + at.size, sized: pending.sized && at.sized,
                            kinds: new Set([...pending.kinds, ...at.kinds]) } : { ...at, kinds: new Set(at.kinds) };
    }
    if (v == null) return;
    const lift = pending && whole && pending.sized ? pending.size : null;
    // Removing duplicates cannot add openings. A rise over a duplicates-only step with no known
    // size is that run's ordinary hiring, and stays in the line: taken out, and folded into
    // the other causes, it read as "+N from changes in how HeadStart counts".
    const hiring = pending && lift == null && v > last
      && pending.kinds.size === 1 && pending.kinds.has('duplicates');
    if (pending && last != null && !hiring) jumps.set(j, { before: last, after: v, kinds: pending.kinds, lift });
    pending = null;
    last = v;
  });
  return jumps;
}
// What a step is, for its label: duplicates removed (sized from the Space's `evicted`, or under
// New a duplicate-removal change, which has no sizes there), a Board found later or a pick
// joining, else a change in how HeadStart counts. Under All openings a duplicate-removal change
// stays a counting change, so the sized removals beside it keep their exact figure.
function noteKind(n){
  if (n.evicted || (n.dedupOnly && trendMetric === 'new')) return 'duplicates';
  return n.found ? 'found' : 'counting';
}
// The indices of the withheld steps that move series `s`. A company's own step (a Board found
// at it, it joining a sum, duplicate removal at its Boards) moves every line of a summed view
// but only its own line under a Company breakdown, where taking it out of another company's
// line would erase real change — and made Google read −0.2% beside Stripe and −0.5% beside
// Micron, whose Workday sites duplicate removal had touched. `s` null is the dashed reference
// line, which sums every pick. Notes are worked out once per answer (a WeakMap on the data),
// not once per legend row.
const notesCache = new WeakMap();
function notesOf(d){
  if (!notesCache.has(d)) notesCache.set(d, stepNotes(d));
  return notesCache.get(d);
}
function stepsFor(s){
  if (!trendData) return [];
  const perCompany = !!s && (!!s.pick || VIEWS[viewKind(trendData)].split === 'company');
  const whole = isWholeLine(s);
  return notesOf(trendData)
    .filter(n => n.withhold && !(n.wholeOnly && !whole) && !(n.bandsOnly && (!s || s.name === '__total__'))
      && !(perCompany && ((n.company && s.name !== n.company)
      || (n.companies && !n.companies.includes(s.name))))
      // A counting change that did not move this line at its own run is no step of this line,
      // nor is its settling run: Stripe's sentence named one filter change while its settling
      // runs, left out, moved it −1 and −3 more, so the list and the sentence disagreed.
      && !((n.epoch || n.settle) && s && s.points && ownJump(n.settle ? n.settles : n.i, s) === 0));
}
// The line's change at run `i` in openings, rounded (a step on a gap lands on the next measured
// point), or null where the line has no point on either side of it.
function ownJump(i, s){
  let j = i; while (j < s.points.length && s.points[j] == null) j++;
  let k = Math.min(i, j) - 1; while (k >= 0 && s.points[k] == null) k--;
  if (j >= s.points.length || k < 0) return null;
  return Math.round(s.points[j] - s.points[k]);
}

// "Aug 12 09:00" from an ISO stamp — enough to anchor the axis without a timezone lecture.
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
// `terse` now means "the day alone is enough", which is true of the axis in two cases, not one:
// a narrow plot whose labels would touch, and ANY window wider than two days — 12:57 on a
// 27-day axis is precision the reader cannot use, and the tooltip still carries the clock.
// Under two days it comes back, because without it every tick would print the same date.
function stampLabel(ts, terse){
  const d = new Date(ts);
  if (isNaN(d)) return '';
  const day = `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
  if (terse) return day;
  return `${day} ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;
}


// The legend's own rendering of a value. Under Count, `45,174` is six characters taken straight
// out of the name beside it — every legend label ellipsized the moment Count was selected. The
// tooltip and the table still carry the exact figure, so nothing is lost, only shortened.
function fmtCompact(v){
  if (trendUnit === 'share') return fmtLevel(v);
  const n = Math.round(v);
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (n >= 1e4) return Math.round(n / 1e3) + 'k';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}

// A nice-number y axis (Heckbert): the step is 1/2/2.5/5 x 10^n, the top rounds UP to a whole
// step, and every label prints at the precision that step needs. Dividing the data max by four
// gave `0.0% / 6.8% / 14% / 20% / 27%` — five labels at three precisions, which read as five
// unrelated numbers rather than one scale. Returns the top of the axis and its tick values.
// The same nice-number stepping over a range that need not start at zero. Change is the case
// that needs it: indexed values sit around 100, so a zero-based axis would put every line in
// the top tenth of the plot and hand back the empty band ADR-0119 exists to remove.
function niceBounds(min, max){
  const span = Math.max(max - min, Math.abs(max) * 0.02, 1e-6);
  // Six gaps, not five. A two-sided axis pays the rounding twice — once at each end — so the
  // slack a zero-based axis only takes at the top is doubled here. Measured on this window
  // (data 84.6-219.4): five gaps framed it 60-240 and gave a quarter of the plot to nothing;
  // six frames it 75-225, which is 10%.
  const want = span / 6;
  const mag = Math.pow(10, Math.floor(Math.log10(want || 1)));
  // 3 and 4 are in the ladder here, unlike the zero-based axis: an indexed range of 84.6-219.4
  // wants a step near 27, and a ladder of 1/2/2.5/5/10 jumps it to 50 — framing 135 units of
  // data in a 200-unit axis and handing back a third of the plot as dead space.
  const mult = [1, 2, 2.5, 3, 4, 5, 10].find(m => want <= m * mag) ?? 10;
  const step = mult * mag;
  let bot = Math.floor(min / step) * step, top = Math.ceil(max / step) * step;
  // A flat line on a whole step (a company's Total holding at 19, indexed to 100 throughout)
  // rounds both ends to the same tick, and a zero-height axis drew every y as NaN.
  if (top === bot){ bot -= step; top += step; }
  const dec = Math.max(0, (mult === 2.5 ? 1 : 0) - Math.round(Math.log10(mag)));
  const ticks = [];
  for (let k = 0; bot + k * step <= top + step / 1e6; k++) ticks.push(bot + k * step);
  return { bot, top, ticks, dec };
}

function niceAxis(hi, whole){
  const want = hi / 5;                                    // the smallest step that fits ~5 gaps
  const mag = Math.pow(10, Math.floor(Math.log10(want || 1)));
  const mult = [1, 2, 2.5, 5, 10].find(m => want <= m * mag) ?? 10;
  // A count is whole openings: a 2-opening company's axis read 0.5 and 1.5.
  const step = whole ? Math.max(1, Math.round(mult * mag)) : mult * mag;
  const top = Math.ceil(hi / step) * step;
  // Decimals the step itself needs: 2.5 costs one more than its magnitude does.
  const dec = whole ? 0 : Math.max(0, (mult === 2.5 ? 1 : 0) - Math.round(Math.log10(mag)));
  const ticks = [];
  for (let k = 0; k * step <= top + step / 1e6; k++) ticks.push(k * step);
  return { bot: 0, top, ticks, dec };
}

// One precision for the whole axis, unlike fmtLevel's per-value choice.
function fmtAxis(v, dec){
  if (trendUnit === 'share') return v.toFixed(dec) + '%';
  if (trendUnit === 'change') return v.toFixed(dec);   // an index is a bare number, not a count
  return v.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

// A hover row's reading. Under Change the plotted number and the magnitude are different
// numbers, so both are given, the index named as one — a bare "1,739 · 113" left the reader to
// guess what 113 was. Where a marked step lands on this line, its size in openings is given
// too: "Counting changed here" alone never said by how much.
function rowText(r){
  const lvl = r.value == null ? '—' : fmtLevel(r.value);
  const read = trendUnit === 'change' && r.index != null ? `${lvl} · index ${r.index.toFixed(0)}` : lvl;
  const n = r.jump ? Math.round(r.jump.after - r.jump.before) : 0;
  if (!n) return read;
  return `${read} · jumped ${n < 0 ? '−' : '+'}${Math.abs(n).toLocaleString()} here`;
}

// The legend, table and tooltip always speak the level, whatever the plot is drawing.
function fmtLevel(v){
  if (trendUnit !== 'share') return Math.round(v).toLocaleString();
  return v >= 10 ? v.toFixed(0) + '%' : v.toFixed(1) + '%';
}

// Direction rides an ARROW, not a hue. --alert (#FB7185) sits Delta E 5.7 from --series-8
// (#E66767) — 2.8 under a tritan simulation — and the two land on the SAME legend row, so a red
// figure beside a red line-key read as one object rather than two. The figure is neutral ink
// now; the glyph is the channel that survives every CVD, and the sign is in the glyph so the
// magnitude prints unsigned.
// One band for "flat", shared by the arrows, the tiles and the sentences: at ±1% and ±2% apart,
// Amazon's legend read "↑ +1.3%" beside "about flat", and a tile called −0.6% a "faller".
const FLAT_PCT = 1;
// Judged on the figure as printed, so "+1.0%" never shows one arrow at 0.96 and another at 1.0.
const shown = dl => Math.round(dl * 10) / 10;
function deltaClass(dl){ return dl == null ? 'flat' : shown(dl) >= FLAT_PCT ? 'up' : shown(dl) <= -FLAT_PCT ? 'down' : 'flat'; }
function deltaText(dl){
  if (dl == null) return '—';
  // The sign is IN the number. It used to live only in the arrow, which has three states and
  // two signs — so on a 7-day window five rows reading -0.28%, +0.89%, -0.09%, +0.49% and
  // +0.76% all rendered `→ 0.x%`, and the "Biggest faller" tile printed `→ 0.3%`.
  const glyph = shown(dl) >= FLAT_PCT ? '↑' : shown(dl) <= -FLAT_PCT ? '↓' : '→';
  return `${glyph} ${dl < 0 ? '−' : '+'}${Math.abs(dl).toFixed(1)}%`;
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
  // Measured from the box it is drawn into rather than fixed at 720: the chart used to
  // ignore the width the page had, so on a wide display every one of the 471 stamps was
  // squeezed into a 720-unit space and then scaled up, which is what made the line work
  // look coarse.
  const host = el('trends-chart') && el('trends-chart').parentElement;  // .trends-chart-wrap — the legend is a sibling column, not part of the plot
  // 1 SVG unit == 1 CSS pixel wherever the container allows it, so the axis type renders at
  // the size the stylesheet asks for. A floor above the real container width does not make a
  // phone's chart bigger — it makes the whole viewBox scale down, and 12px labels arrive as
  // 6px. The floor is only a guard against a zero/undetached measurement.
  const W = Math.max(280, Math.round(host && host.clientWidth ? host.clientWidth : 720));
  const { charted, other, shown } = chartedAndOther(d);

  assignSeriesColors(charted.map(s => s.name));   // Other is a bucket, not an entity — no slot

  const runs = d.stamps.length;
  // The measurement window, stated on the page: "19 measurements" alone could be two hours
  // or two years, and every judgement about a trend depends on which.
  const spanMs = runs > 1 ? new Date(d.stamps[runs-1]) - new Date(d.stamps[0]) : 0;
  const spanDays = spanMs / 864e5;
  const span = runs < 2 ? '' :
    spanDays >= 1.5 ? ` over ${Math.round(spanDays)} days` : ` over ${Math.round(spanMs/36e5)} hours`;
  const measured = `${runs} measurement${runs===1?'':'s'}${span}`;
  // As of when: Search is live and the trend is not, and a ledger paused for hours (a new
  // classifier warming up, 2026-09-25) read as "right now" with nothing to say otherwise.
  const ageHours = runs ? (Date.now() - new Date(d.stamps[runs - 1])) / 36e5 : 0;
  // Only for a window running to now: a custom range ending last week is old by choice.
  const asOf = !runs ? '' : ` · latest ${stampLabel(d.stamps[runs - 1])} UTC${
    !trendRange().until && ageHours >= staleAfterHours(d.stamps) ? ` — ${Math.round(ageHours)} hours ago: the pipeline has written no newer count, though Search, which reads the job list itself, can be newer` : ''}`;

  // The legend is built BEFORE the plot, not after: it is a pure function of the series, and
  // the plot's height floor is measured off it (below). Built after, the first paint would
  // have had nothing to measure.
  // A company line and the Total line are not categories — nothing opens below them, so their
  // rows are not buttons (trendClick ignores them too). Inside a drill a row still goes back up.
  const view = VIEWS[viewKind(d)];
  const at = pickPhrase();
  const legendRows = charted.map(s => {
    const slot = seriesColorAssignment.get(s.name);
    const c = seriesColor(slot);
    const mv = legendMove(s);
    const j = d.stamps.length - 1;
    const latest = latestOf(s) == null ? null : levelValue(latestOf(s), j, s);
    // Mark the categories that hold named roles, so the by-role drill is DISCOVERABLE from the
    // top level. Without it the split toggle only appears after drilling in, which means the
    // one place that advertises the feature is the one place you reach by already knowing it
    // exists.
    const hasRoles = !trendDrill && (d.watch_parents || []).includes(s.name);
    const off = hiddenSeries.has(s.name);
    // A family too small at the window's start to index has no line, so the row says why
    // rather than leaving a swatch pointing at nothing.
    // "Too new" first: Zomato's row read "started under 5" beside a sentence saying too new.
    // Its change in openings, where it has one, as the table gives it: "Research Scientist
    // started under 5" in the legend read "↑ +3 openings" in the table.
    const noBase = !hasIndexBase(s) && !mv.tooNew;
    // data-name + the delegated listener below, NOT an inline onclick: esc() is HTML-entity
    // escaping, and inside onclick="...'${name}'..." the parser decodes entities back
    // before the JS parses — a name with a quote would break out of the string.
    return `<li class="charted${recoloredNames.has(s.name) ? ' recolored' : ''}${off ? ' off' : ''}${noBase ? ' nobase' : ''}"
      ><span class="row" data-name="${esc(s.name)}"${view.drills ? ' role="button" tabindex="0"' : ''}
      >${swatchHtml(c, slot)}
      <span class="nm" title="${esc(s.label)}">${esc(s.label)}</span>
      <span class="ct">${latest == null ? '—' : fmtCompact(latest)}</span>
      ${noBase && 'dl' in mv ? '<span class="dl flat" title="Under 5 openings at the start of this window, too few to index against">started under 5</span>'
               : `<span class="dl ${moveClass(mv)}"${mv.count == null ? '' : mv.short ? ' title="Too short a window for a percentage to mean much, so the change in openings"' : ' title="Too few openings for a percentage to mean much, so the change in openings"'}>${moveText(mv)}</span>`}
      ${hasRoles ? '<span class="drill" role="img" aria-label="has tracked roles" title="Opens the named roles tracked inside this category">▸ roles</span>' : ''}</span>
      ${view === VIEWS.roles && trendPicks.length ? `<button class="linkish role-jobs" type="button" data-role="${esc(s.name)}"
        data-role-label="${esc(s.label)}" aria-label="See ${esc(s.label)} jobs in Search">jobs</button>` : ''}
      <button class="vis" type="button" data-hide="${esc(s.name)}" aria-pressed="${off}"
        title="${off ? 'Show' : 'Hide'} this line" aria-label="${off ? 'Show' : 'Hide'} ${esc(s.label)}"
        >${off ? '○' : '●'}</button></li>`;
  });
  if (other){
    // Everything past CHART_MAX, as one honestly-labeled, honestly-inert row — not N rows that
    // look like real categories but silently do nothing on click (the dead-click this replaces).
    // No role/tabindex: `trendClick` already no-ops on a name absent from trendData.series
    // (findIndex returns -1), so `__other__` is inert by the SAME guard real unknown names use,
    // not a new special case. The hide button is a sibling, not a child of that row.
    const mv = legendMove(other);
    const j = d.stamps.length - 1;
    const latest = other.points[j] == null ? null : levelValue(other.points[j], j, other);
    // No hide toggle and no swatch: since ADR-0119 Other is on neither the plot nor its scale,
    // so there is no line to hide and no colour to key. The row is a reconciliation figure —
    // it says where the rest of the index went — and it reads as one.
    legendRows.push(`<li class="other"><span class="row" data-name="__other__"
      ><span class="nm" title="${esc(other.label)}">${esc(other.label)}</span>
      <span class="ct">${latest == null ? '—' : fmtCompact(latest)}</span>
      <span class="dl ${moveClass(mv)}">${moveText(mv)}</span></span></li>`);
  }
  el('trends-legend').innerHTML = legendRows.join('');

  // Landscape where there is room, but the height now follows the space the panel HAS rather
  // than a fixed 0.36 of the width. Measured before this changed: 390px gave a plot 15% of the
  // panel's height and 900px gave 25.6%, because `W * 0.36` hit its floor while the legend,
  // the controls and the caveat took the rest. Three floors, largest wins — the aspect the
  // plot wants, the legend column beside it, and a fraction of the viewport.
  const legendH = legendColumnHeight(host);
  const viewportH = Math.round((typeof window !== 'undefined' && window.innerHeight ? window.innerHeight : 900) * 0.42);
  const H = Math.max(240, legendH, Math.min(520, Math.max(Math.round(W * 0.36), Math.min(viewportH, Math.round(W * 0.8)))));
  const PAD_L = 46, PAD_B = 22, PAD_T = 10;

  // Other is not drawn (ADR-0119). It is a reconciliation bucket, not a category: it was the
  // topmost line at 27% and, because the maximum is taken across everything drawn, it alone
  // set the scale that squashed the eight real categories. It keeps its legend and table rows,
  // so the share it accounts for is still on screen — it just stops dictating the axis.
  // A hidden series leaves the plot AND the scale — rescaling is the point of hiding one.
  const drawn = charted.filter(s => !hiddenSeries.has(s.name));
  const vals = drawn.flatMap(s => seriesValues(s)).filter(v => v != null);
  // The whole index's own growth, indexed on the same base as the series (ADR-0119). Count
  // indexing rides on raw openings, so a run that adds a board lifts every family at once:
  // over this window `totals` runs 277,754 -> 335,355, +20.7%, and 7 of 8 families end above
  // 100 largely because of it. Without this line a reader cannot tell "this role is hiring
  // more" from "we scraped more" — and since Change is the default view, that distinction is
  // on the first screen every visitor sees. Computed here, before the axis and before
  // end-label placement, because both have to account for it.
  // Only where the lines are parts of that whole. A Total line or a whole company's line is
  // itself the whole, so the dashed line lay almost on top of it and explained nothing.
  const refShown = trendUnit === 'change' && d.totals && !['total', 'company'].includes(viewKind(d));
  const refVals = [];
  if (refShown){
    const net = netOfSteps(d.totals, null);
    const rb = net.find(v => v != null);
    if (rb) net.forEach(t => refVals.push(t == null ? null : t / rb * 100));
  }
  const refLast = [...refVals].reverse().find(v => v != null) ?? null;

  let axis;
  if (trendUnit === 'change'){
    // Indexed lines cluster around 100 and the interesting part is how far they stray, so the
    // axis frames the data rather than the origin. 100 is always inside it: it is the baseline
    // every line starts from, and an axis that cropped it would hide the comparison.
    // No extra padding: niceBounds already rounds outward to a whole step, and doing both
    // stacked two slacks on top of each other — data spanning 78-185 was framed 50-200, which
    // is a quarter of the plot height given to nothing.
    const all = vals.concat(refVals.filter(v => v != null));
    axis = niceBounds(all.reduce((a, b) => Math.min(a, b), 100),
                      all.reduce((a, b) => Math.max(a, b), 100));
  } else {
    let hi = Math.max(trendUnit === 'share' ? 0.1 : 1, ...vals);
    // Datawrapper's line-chart guidance: extend the axis to 100% once a share reading comes
    // close to it, so the reader sees the true ceiling rather than an axis that implies more
    // headroom exists. Left alone otherwise — most shares here are single digits, and forcing
    // every chart to a 0-100 scale would flatten the small-value comparisons this page is for.
    if (trendUnit === 'share' && hi > 80) hi = 100;
    axis = niceAxis(hi, trendUnit === 'count');
  }
  const y = v => PAD_T + (H - PAD_T - PAD_B) * (1 - (v - axis.bot) / (axis.top - axis.bot));

  // Direct end-labels, but only where endpoints actually separate (dataviz skill,
  // marks-and-anatomy.md: "When end-labels collide, don't stack them"). Labelling all nine
  // stacked them into an unreadable column against the right edge; a label is drawn here only
  // where the endpoint clears both its neighbours by a full line-height, at most three of
  // them, and only when the plot is wide enough to hold a gutter. y does not depend on PAD_R,
  // so the decision can be made before the gutter is reserved.
  const ends = drawn.map(s => {
    const vs = seriesValues(s);
    for (let j = vs.length - 1; j >= 0; j--) if (vs[j] != null) return { s, v: vs[j], y: y(vs[j]) };
    return null;
  }).filter(Boolean).sort((a, b) => a.y - b.y);
  const CLEAR = 15;
  // The reference line always gets its label, so the series labels are the ones that move: an
  // endpoint has to clear it as well as its neighbours. Without this the two overprinted.
  const refY = refLast == null ? null : y(refLast);
  const labelled = W < 640 ? [] : ends
    .filter((e, i) => (i === 0 || e.y - ends[i-1].y >= CLEAR)
                   && (i === ends.length - 1 || ends[i+1].y - e.y >= CLEAR)
                   && (refY == null || Math.abs(e.y - refY) >= CLEAR))
    .sort((a, b) => b.v - a.v).slice(0, 3);
  const GUTTER = 140;
  const PAD_R = labelled.length ? GUTTER : 14;
  const x = i => PAD_L + (d.stamps.length < 2 ? 0 : i * (W - PAD_L - PAD_R) / (d.stamps.length - 1));

  let svg = '';
  // The whole index's own growth, indexed on the same base as the series (ADR-0119). Count
  // indexing rides on raw openings, so a run that adds a board lifts every family at once:
  // measured over this fixture's window `totals` runs 277,754 -> 335,355, +20.7%, and 7 of 8
  // families end above 100 largely because of it. Without this line the reader cannot tell
  // "this role is hiring more" from "we scraped more", and since Change is the default view
  // that distinction is on the first screen every visitor sees.
  let refPath = '', pen = 'M';   // the reference series computed above
  refVals.forEach((v, j) => {
    if (v == null){ pen = 'M'; return; }
    refPath += `${pen}${x(j).toFixed(1)},${y(v).toFixed(1)} `;
    pen = 'L';
  });

  // Under Change, 100 is where every line starts, so it is a datum and not just another tick:
  // whether a line sits above or below it IS the reading. It gets a stronger rule than the
  // grid, drawn first so the series cross over it.
  if (trendUnit === 'change'){
    const yb = y(100);
    svg += `<line class="baseline" x1="${PAD_L}" y1="${yb.toFixed(1)}"
             x2="${(W - PAD_R + 6).toFixed(1)}" y2="${yb.toFixed(1)}"/>`;
  }
  axis.ticks.forEach(v => {                          // horizontal gridlines + y labels
    const yy = y(v);
    svg += `<line class="gridline" x1="${PAD_L}" y1="${yy.toFixed(1)}" x2="${(W - PAD_R + 6).toFixed(1)}" y2="${yy.toFixed(1)}"/>`;
    svg += `<text class="axis-label" x="0" y="${(yy+3).toFixed(1)}">${fmtAxis(v, axis.dec)}</text>`;
  });
  // Time axis. Still sparse — a tick per stamp would be 471 of them — but the count now
  // follows the width the plot actually got: three ticks across a 1,500px window left the
  // reader interpolating four weeks between two marks.
  if (d.stamps.length > 1){
    const plotW = W - PAD_L - PAD_R;
    // The clock goes when the day alone identifies the tick — any span over two days, or a
    // plot too narrow to hold "Aug 11 12:57" three times without the labels touching.
    const terse = spanMs > 48 * 36e5 || plotW < 420;
    const per = terse ? 150 : 260;
    const nTicks = Math.max(2, Math.min(7, Math.floor(plotW / per) + 1));
    const ticks = Array.from({ length: nTicks }, (_, k) =>
      Math.round(k * (d.stamps.length - 1) / (nTicks - 1)));
    ticks.forEach((ti, k) => {
      const anchor = k === 0 ? 'start' : k === nTicks - 1 ? 'end' : 'middle';
      svg += `<text class="axis-label" x="${x(ti).toFixed(1)}" y="${H - 6}"
               text-anchor="${anchor}">${stampLabel(d.stamps[ti], terse)}</text>`;
    });
  }

  // Methodology boundaries (ADR-0164): the taxonomy, tech filter, extraction or duplicate
  // removal (ADR-0188) changed at this stamp, not the market. Drawn as a vertical note behind
  // the series, same as the reference line and the gridlines — an epoch's `ts` is written by
  // the same tick as a trends row, so it
  // is normally an exact stamp match; one that predates this feature or fell outside the window
  // simply finds no index and is skipped rather than guessed at.
  // Under a pick only the changes that move its lines are listed (stepNotes).
  const notes = notesOf(d);
  // Under a pick, a change marked where no drawn line moved ("jumped +0 here") explains nothing.
  // Under a pick, a change marked where no drawn line moved ("jumped +0 here") explains nothing;
  // "moved" is the sentence's own test (stepMoved), so a marker and a named cause agree.
  const markerMoves = n => !trendPicks.length || drawn.some(s => stepsFor(s).includes(n) && stepMoved(n, s));
  const marked = { epoch: false, found: false, list: [] };
  // One marker per day and kind over a window of days: four filter changes and their settling
  // runs on one day drew a comb of lines a pixel apart, whose titles no pointer could tell apart.
  // Its title says every change it stands for; the crosshair still names each run's own.
  const byDay = d.stamps.length > 1 && (new Date(d.stamps[d.stamps.length - 1]) - new Date(d.stamps[0])) / 864e5 > 2;
  // Drawn at the day's run whose drawn lines moved most: at the day's first, Google with Micron's
  // Sep 24 marker sat on an 18:00 run that moved one opening, 8px left of the 21:19 run that
  // moved 2,199.
  // The marked jump itself, over every line and not just those drawn, so hiding a line in the
  // legend does not move a marker; with no pick nothing is taken out, so the run's own change.
  const stepSizes = trendPicks.length ? d.series.map(s => stepJumps(s.points, s)) : null;
  const movedAt = i => i < 1 ? 0 : d.series.reduce((sum, s, k) => {
    if (stepSizes){ const jump = stepSizes[k].get(i); return sum + (jump ? Math.abs(jump.after - jump.before) : 0); }
    const a = s.points[i], b = s.points[i - 1];
    return sum + (a != null && b != null ? Math.abs(a - b) : 0);
  }, 0);
  const drawMarkers = (list, cls) => {
    const groups = new Map();
    list.forEach(n => {
      const key = byDay ? stampLabel(d.stamps[n.i], true) : n.i;
      if (!groups.has(key)) groups.set(key, { i: n.i, notes: [], texts: new Map() });
      const g = groups.get(key);
      if (movedAt(n.i) > movedAt(g.i)) g.i = n.i;
      g.notes.push(n);
      // Counted, not collapsed: three filter changes on one day listed as one entry beside a
      // sentence naming three.
      g.texts.set(n.text, (g.texts.get(n.text) || 0) + 1);
    });
    groups.forEach(g => {
      const texts = [...g.texts].map(([t, k]) => k > 1 ? `${t} (×${k})` : t);
      // Each drawn line's size at these changes, their settling runs included — the figure the
      // sentence's cause is made of (stepSize).
      const sizes = !trendPicks.length ? [] : drawn.map(s => {
        const own = g.notes.filter(n => stepsFor(s).includes(n));
        const n = own.length ? stepSize(own, s) : 0;
        return n ? `${s.label} ${signedOpenings(n)}` : '';
      }).filter(Boolean);
      marked.list.push({ i: g.i, texts, sizes });
      const gx = x(g.i).toFixed(1);
      svg += `<line class="${cls}" x1="${gx}" y1="${PAD_T}"
               x2="${gx}" y2="${H - PAD_B}"><title>${esc(texts.join('\n'))}</title></line>`;
    });
  };
  const epochMarks = notes.filter(n => n.epoch && markerMoves(n));
  marked.epoch = epochMarks.length > 0;
  drawMarkers(epochMarks, 'epoch-marker');
  // Boards of a pick that HeadStart found after its line began (ADR-0185). Each lands its
  // whole backlog at once — openings that were already open — so the step is marked where it
  // lands rather than left to read as hiring, the reason the Hot tab leaves new Boards out.
  // Found Boards and duplicate removals, each a step taken out of the lines it moves; below the
  // floor neither is taken out, so neither is marked (a marker for one opening was furniture).
  const foundMarks = notes.filter(n => (n.found || n.evicted) && n.withhold);
  marked.found = foundMarks.length > 0;
  drawMarkers(foundMarks, 'found-marker');

  // Drawn before the series: context to read them against, not one of them. Dashed because
  // dashing should mean exactly this — a reference, not a measurement — which is also why the
  // gridlines stay solid.
  if (refPath){
    svg += `<path class="ref-line" d="${refPath.trim()}" fill="none"/>`;
    // Labelled at its own end, in the gutter beside the series labels. It used to be printed
    // at the 100 datum, where eight lines ran straight through the words.
    // Under a pick `totals` is the picks' own total, not the index's (ADR-0185).
    if (refY != null && PAD_R > 20) svg += `<text class="ref-label" x="${(W - PAD_R + 10).toFixed(1)}"
             y="${(refY + 3.5).toFixed(1)}">${pickScope().ref}</text>`;
  }

  const geomSeries = [];   // color + per-index resolved value, read by the hover layer below
  svg += '<g id="lines-layer">';
  drawn.forEach(s => {
    const slot = seriesColorAssignment.get(s.name);
    const c = seriesColor(slot);
    const values = seriesValues(s);
    // Each step this line carries, sized in openings for the tooltip. Under Share and Count the
    // line is the real level, so it breaks at a step rather than drawing the jump as a climb.
    const jumps = stepJumps(s.points, s);
    // break the path at gaps rather than bridging them — an unmeasured run is not a value
    let path = '', pen = 'M', lastPt = null;
    values.forEach((u, j) => {
      if (u == null) { pen = 'M'; return; }
      if (trendUnit !== 'change' && jumps.has(j)) pen = 'M';
      const px = x(j), py = y(u);
      path += `${pen}${px.toFixed(1)},${py.toFixed(1)} `;
      lastPt = [px, py];
      pen = 'L';
    });
    path = path.trim();
    // No area fill. The skill's wash is for a *single* series; eight of them overlapping
    // in the narrow band where the small categories run compounded into brown murk that
    // read as a stacked chart the data is not. The line alone carries the series.
    svg += `<path class="series-line" data-slot="${slot}" data-name="${esc(s.name)}" d="${path}" fill="none" stroke="${c}"
             stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    if (lastPt) svg += `<circle class="series-end" data-name="${esc(s.name)}" cx="${lastPt[0].toFixed(1)}"
             cy="${lastPt[1].toFixed(1)}" r="4" fill="${c}" stroke="var(--raise)" stroke-width="2"/>`;
    // Both, on purpose: `values` is what the line was drawn from and so is what the
    // crosshair dot must sit on, while `levels` is the magnitude the tooltip reports.
    // Under Change those differ, and reading a value off the wrong one would put the
    // dot somewhere the line is not, or announce an index as if it were a share.
    geomSeries.push({ name: s.name, label: s.label, color: c, values, jumps,
                      levels: s.points.map((v, j) => levelValue(v, j, s)) });
  });
  svg += '</g>';
  // The chosen end-labels, each tied to its own line by a leader. Text wears an ink token, not
  // the series colour (dataviz skill: a light categorical hue is illegible as label text) — the
  // coloured leader and dot beside it carry the identity.
  labelled.forEach(e => {
    const c = seriesColor(seriesColorAssignment.get(e.s.name));
    const x0 = W - PAD_R, tx = x0 + 10;
    const fits = Math.floor((W - tx - 2) / 5.9);
    const full = e.s.label;
    const text = full.length > fits ? full.slice(0, Math.max(1, fits - 1)) + '…' : full;
    svg += `<line class="end-leader" x1="${(x0 + 4).toFixed(1)}" y1="${e.y.toFixed(1)}"
             x2="${(tx - 2).toFixed(1)}" y2="${e.y.toFixed(1)}" stroke="${c}"/>`;
    svg += `<text class="end-label" x="${tx.toFixed(1)}" y="${(e.y + 3.5).toFixed(1)}">${esc(text)}</text>`;
  });
  // The hover layer: a crosshair line + one dot per series, hidden until pointermove positions
  // them (dataviz skill, interaction.md — "an HTML chart is interactive by default"). Built once
  // per redraw, in the same order as geomSeries, so the pointermove handler below can index
  // straight into `dotEls` with no per-event DOM query.
  svg += `<g id="trends-crosshair" style="display:none">
    <line class="crosshair-line" x1="0" y1="${PAD_T}" x2="0" y2="${H - PAD_B}"/>
    ${geomSeries.map(s => `<circle class="ch-dot" r="4" fill="${s.color}" stroke="var(--raise)" stroke-width="2"/>`).join('')}
  </g>`;
  svg += `<rect id="trends-hitrect" x="0" y="0" width="${W}" height="${H}" fill="transparent" pointer-events="all"/>`;
  // The viewBox has to follow the geometry we just drew in. It used to be a static
  // `0 0 720 260` in the template, so the moment the plot was measured from its container
  // every coordinate landed in a space twice the box's width — lines over the caption,
  // legend across the plot. Set both together, always.
  el('trends-chart').setAttribute('viewBox', `0 0 ${W} ${H}`);
  el('trends-chart').style.aspectRatio = `${W} / ${H}`;
  el('trends-chart').innerHTML = svg;
  const dotEls = [...el('trends-chart').querySelectorAll('.ch-dot')];
  lastGeom = { x, y, W, stamps: d.stamps, series: geomSeries, dotEls, notes, marks: marked.list.map(g => g.i) };
  positionHoverLayer(null);
  hoveredSeries = null;

  // Every category is now represented — individually if it's one of the top CHART_MAX, folded
  // into Other otherwise — so this no longer needs a "top N of M" caveat. The drill's way out
  // is the breadcrumb above, not a sentence at the end of this line.
  const where = at ? ` ${at}` : '';
  // No runs in the window: say so under the picked chips, not "0 companies · 0 measurements".
  el('trends-scope').textContent = !runs && trendPicks.length
    ? `${trendPicks.length} compan${trendPicks.length === 1 ? 'y' : 'ies'} picked · no measurements in this window`
    : view.scope(d.series.length, where, measured + asOf);
  // The SVG's own name for itself, written from the same facts. It was a fixed "Open roles over
  // time by category" in the template, which stayed that after every Measure, Unit, ATS and
  // drill change — right in exactly one state and stale in every other.
  const atsPick = trendAtsSelected();
  el('trends-chart').setAttribute('aria-label',
    `Line chart. ${trendMetric === 'new' ? 'Openings first seen in the last 7 days' : 'All live openings'}${where}`
    + `${view.grouping ? ` by ${view.grouping(trendDrill)}` : ', every category summed into one line'}, as ${
        trendUnit === 'share' ? (view.split === 'company' ? 'a share of each company’s own openings' : pickScope().share)
        : trendUnit === 'change' ? 'an index against each line’s own count at the window’s start'
        : 'a count'}`
    + `${atsPick ? `, ${atsPick.length} of the ATS sources` : ''}, over ${measured}.`
    + ` ${drawn.length} line${drawn.length === 1 ? '' : 's'}.`
    + ' Arrow keys read the values; Table view lists them all.');
  // A narrow ATS selection has its own reason for a short history (ADR-0075): per-ATS rows
  // only exist from the run this shipped in forward, not because the pipeline itself is new —
  // conflating the two would read as "the pipeline is broken" rather than "you narrowed it."
  el('trends-empty').textContent = runs === 0
    ? (trendCoverage === 'comparable' && d.ledger_start
        && (!trendRange().since || trendRange().since < d.ledger_start)
      // Comparable keeps the Boards counted at the window's start, and per-Board counting has
      // a start of its own: a window opening before it has no cohort at all, so "widen" is the
      // wrong advice there.
      ? `Comparable coverage follows only boards already counted when the window starts, and counting by board began ${stampLabel(d.ledger_start, true)} — choose a window that starts after that.`
      // A window ending before counting began: the date it could not reach, not just "widen".
      : trendPicks.length && d.ledger_start && trendRange().until && trendRange().until < d.ledger_start
      ? `This window ends before HeadStart began counting companies, on ${stampLabel(d.ledger_start, true)} — choose dates after that.`
      : 'No measurements inside this window — widen the dates, or clear them to see the whole history.')
    : runs < 2 && trendPicks.length
    ? `${viewNotes(d)} A trend line needs a few more runs.`.trim()
    : runs < 2
    ? (atsPick
        ? `Only ${runs} measurement${runs === 1 ? '' : 's'} for this ATS selection — per-ATS history starts from when this filter shipped, not before. Broaden the selection to see more.`
        : 'Only one measurement so far — trend lines appear once the pipeline has run a few more times.')
    : (trendDrill && trendSplit === 'roles' && !d.series.length
      // "not tracked" would be wrong and self-contradictory next to a marker that just said
      // roles ARE tracked here: the watchlist is config, the rows are measurements, and between
      // a deploy and the next pipeline run the first exists without the second.
      ? 'These roles have not been measured yet — they appear after the next pipeline run.'
      : trendDrill && trendRaw && trendRaw.family_known === false
      // A name the data holds no category by, said as such, not as "no openings counted".
      ? `HeadStart has no category called “${trendDrill}”. Go back to all categories to pick one.`
      : trendPicks.length && !d.series.length
      ? (trendMetric === 'new'
        // The Space holds a found Board's first week out of `new`: its backlog is not hiring.
        ? `Nothing counts as new${where} yet. A board's openings count as new only after HeadStart has read it for a week, so a backlog is not mistaken for hiring.`
        : `No openings counted${where} in this window. ${viewNotes(d)}`.trim())
      : viewNotes(d));
  drawVerdict(d);
  drawTitle();
  drawRangeLimits(d);
  // An axis with no lines under it is the "broken chart" reading the message above exists to
  // replace, so with nothing measured the message stands on its own — and so do the tiles,
  // which would otherwise print four em-dashes under it.
  if (el('trends-viz')) el('trends-viz').hidden = runs === 0 || tableView;
  if (el('trends-kpi')) el('trends-kpi').hidden = runs === 0 || !d.series.length || !buildKpis(d, charted, measured);
  if (runs === 0 && el('trends-verdict')) el('trends-verdict').hidden = true;
  // The reassignment caveat is about a job's category moving, which a Total line, a whole
  // company's line and a title-matched role's line cannot show.
  if (el('trends-how')) el('trends-how').hidden = ['total', 'company', 'roles'].includes(viewKind(d));
  const nt = d.non_tech.filter(v => v != null).pop();
  const parts = [];
  // "7 days" mirrors role_trends.NEW_WINDOW_DAYS — change one and this sentence starts lying.
  parts.push(trendMetric === 'new'
    ? 'Openings first seen in the last 7 days, re-measured every pipeline run.'
    : (trendUnit === 'share'
      ? (view.split === 'company'
        ? 'Each line is a share of that company’s own live openings, so companies of very different size compare directly.'
        : trendPicks.length
        ? `Each line is a category’s share of all live openings ${at}.`
        : 'Each line is a category’s share of all live openings in the index — immune to the index itself growing or shrinking.')
      : trendUnit === 'change'
      ? `Each line starts at 100 — its own count of live openings at ${stampLabel(d.stamps[0], true)}, or at its own first measurement if it has none there — so ${
          view.split === 'company' ? 'companies' : 'lines'} of very different size become comparable shapes. 120 means a fifth more openings than at the start, not 120 openings; the count itself is in the legend and the table.${
          refShown ? ` The index grows as coverage does, and a run that adds a board lifts every line without a job having been posted — so read a ${
            trendDrill ? 'line' : 'category'} against the dashed line, which is ${pickScope().whole} on the same base.` : ''} Move the window and every line is re-based to the new start.`
      : 'Counts are live openings in the index, re-measured every pipeline run. The index itself grows as coverage does, which lifts every count.'));
  const steps = stepNote(d, marked);
  if (steps) parts.push(steps);
  drawChangeList(d, runs === 0 ? [] : marked.list);
  if (nt && trendMetric === 'stock') parts.push(`${nt.toLocaleString()} further ${nt === 1 ? 'row sits' : 'rows sit'} in non-tech categories and ${nt === 1 ? 'is' : 'are'} excluded here.`);
  // With nothing measured there is no line to explain, and the Change sentence named a start
  // date that did not exist ("live openings at , or…").
  el('trends-foot').textContent = runs === 0 ? '' : parts.join(' ');

  // The breadcrumb is the way out of a drill; the by-level / by-role toggle only exists inside
  // a family that has watched roles.
  if (el('trends-crumb')) el('trends-crumb').hidden = !trendDrill;
  if (el('trends-crumb-here')) el('trends-crumb-here').textContent = trendDrill ? drillLabel() : '';
  // A pick adds Total and Company to the top level and Company to a drill (ADR-0185). An
  // option that does not apply is hidden AND disabled, as setUnit does: hidden keeps it off the
  // screen, disabled keeps it out of the radiogroup's arrow-key walk.
  const seg = el('trends-split');
  if (seg) {
    const many = trendPicks.length > 1;
    const offered = trendDrill
      ? ['bands', ...((d.watch_parents || []).includes(trendDrill) ? ['roles'] : []), ...(many ? ['company'] : [])]
      : trendPicks.length ? ['families', 'total', ...(many ? ['company'] : [])] : [];
    // The reader's breakdown, even when an empty answer drew the default view.
    const current = trendDrill ? trendSplit : trendPicks.length ? topSplitNow() : view.split;
    seg.hidden = offered.length < 2;
    if (el('trends-split-ctl')) el('trends-split-ctl').hidden = seg.hidden;
    seg.querySelectorAll('button').forEach(b => {
      const off = !offered.includes(b.dataset.split);
      b.hidden = off; b.disabled = off;
      setRadioChecked(b, b.dataset.split === current);
    });
  }
  if (tableView) renderTrendsTables();
}

// The legend column's own content height, and only when it IS a column — stacked under the
// plot below the 760px breakpoint it constrains nothing. Summed from the rows rather than read
// off the <ul>, because the column is `align-self:stretch`: its box already reports whatever
// height the chart gave the grid row, and feeding that back would ratchet the plot taller on
// every redraw.
function legendColumnHeight(host){
  const ul = el('trends-legend');
  if (!ul || !ul.children || !ul.children.length || !host) return 0;
  const first = ul.firstElementChild, last = ul.lastElementChild;
  if (!first || !last || ul.offsetTop > host.offsetTop + 4) return 0;
  return (last.offsetTop + last.offsetHeight - first.offsetTop) || 0;
}

// The tiles above the plot. The two movers are what a reader is scanning the chart for, and the
// two totals say what the shares are shares OF — questions the lines answer only by arithmetic.
// Returns false when there is nothing to state, so the caller can hide the row rather than
// print a line of em-dashes. Figures are proportional, not tabular (dataviz skill,
// marks-and-anatomy.md § Figures): tabular-nums pads every digit to a `0` and reads loose at
// tile size. Nothing here wears a hue — direction is the arrow glyph, as in the legend.
function buildKpis(d, charted, measured){
  const host = el('trends-kpi'); if (!host) return false;
  // A tile must never headline a series the chart refuses to draw. It did: a family based at 4
  // openings was a gap on the plot and "not indexed" in the legend, while the tile above read
  // "Biggest riser +233.3%" — the same tile class the floor was added to stop.
  // One Total line has no rival to rise or fall against; its own movement is in its legend row.
  const kind = viewKind(d);
  // Summed from the series, NOT `totals - non_tech`: the ledger writes non_tech under the STOCK
  // metric only, so that subtraction reports the stock figure whatever the Measure says —
  // measured against this payload, 266,008 under a "New this week" label whose series sum to
  // 31,143. The sum agrees with the subtraction exactly on stock, where both are defined.
  const openings = d.series.reduce((sum, s) => {
    const last = latestOf(s);
    return sum + (last || 0);
  }, 0);
  const tiles = [];
  // Each mover tile has to earn its own name: on "New this week" every category is falling, so
  // the top of the range is a -39.2% and calling it the biggest riser would be a lie the tile
  // itself contradicts two words later. The extreme is stated only when its sign agrees.
  const { riser, faller } = tileMovers(d, charted);
  if (riser) tiles.push({ label: 'Biggest riser', value: riser.label, dl: riser.dl });
  if (faller) tiles.push({ label: 'Biggest faller', value: faller.label, dl: faller.dl });
  if (d.series.length) tiles.push({
    label: trendDrill ? (trendSplit === 'roles' ? 'Openings in tracked roles' : 'Openings in this category')
      : trendMetric === 'new' ? 'New tech openings' : 'Tech openings',
    value: openings.toLocaleString(), note: measured });
  const { tracked } = VIEWS[kind];
  if (tracked) tiles.push({ label: tracked, value: String(d.series.length) });
  if (!tiles.length) return false;
  host.innerHTML = tiles.map(t => `<div class="kpi"><span class="kpi-label">${esc(t.label)}</span>
    <span class="kpi-value">${esc(t.value)}</span>
    ${t.dl == null ? (t.note ? `<span class="kpi-note">${esc(t.note)}</span>` : '')
                   : `<span class="kpi-delta ${deltaClass(t.dl)}">${deltaText(t.dl)}</span>`}</div>`).join('');
  return true;
}
// The lines the tiles headline, or none. Movers are read off trendDelta, which is net of the
// marked steps (netOfSteps), so a found Board or a counting change is never what a tile
// headlines; and never off a line under MOVER_FLOOR, whose percentage is a handful of openings.
// Each extreme only when its sign agrees: on "New this week" every category is falling, and the
// top of the range, a −39.2%, is no riser.
function tileMovers(d, charted){
  if (viewKind(d) === 'total') return {};
  const moves = charted.filter(s => hasIndexBase(s) && 'dl' in lineMove(s))
    .map(s => ({ name: s.name, label: s.label, dl: trendDelta(s.points, s) }))
    .filter(m => m.dl != null && Math.abs(shown(m.dl)) >= FLAT_PCT).sort((a, b) => b.dl - a.dl);   // a flat line is no mover
  const top = moves[0], bottom = moves[moves.length - 1];
  return { riser: top && top.dl > 0 ? top : null, faller: bottom && bottom.dl < 0 ? bottom : null };
}
// ---- hover: crosshair + one-tooltip-for-every-series (dataviz skill, interaction.md) --------

// `opts.py` is the pointer's y in SVG units, used to mark the row nearest it; `opts.announce`
// speaks the readout, which only the keyboard path wants — a mouse hover pushing every stamp
// it crosses into a live region would be a stream of interruptions.
function positionHoverLayer(index, opts){
  const svgEl = el('trends-chart'), tip = el('trends-tooltip');
  if (!svgEl || !lastGeom) return;
  const group = svgEl.querySelector('#trends-crosshair');
  if (!group) return;
  hoverIndex = index;
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
    rows.push({ label: s.label, color: s.color, value: s.levels[index], index: v,
      jump: s.jumps && s.jumps.get(index),
      dy: (opts && opts.py != null) ? Math.abs(y(v) - opts.py) : null });
  });
  // Nine rows in one tooltip is a list to search, not a readout. The row the pointer is
  // actually beside is emphasised, so the answer is already where the eye is.
  let near = -1, best = Infinity;
  rows.forEach((r, i) => { if (r.dy != null && r.dy < best){ best = r.dy; near = i; } });
  if (opts && opts.announce && el('trends-readout')){
    el('trends-readout').textContent = rows.length
      ? `${stampLabel(stamps[index])}. ` + (lastGeom.notes || []).filter(n => n.i === index && n.text).map(n => n.text + '. ').join('')
        + rows.map(r => `${r.label} ${rowText(r)}`).join(', ')
      : `${stampLabel(stamps[index])}. Nothing measured.`;
  }
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
    // What a marker at this stamp means, where the crosshair already is — a 1px line's own
    // <title> was the only place it was said, and hovering a line that thin rarely finds it.
    (lastGeom.notes || []).filter(n => n.i === index && n.text).forEach(n => {
      const note = document.createElement('div'); note.className = 'tt-note'; note.textContent = n.text;
      tip.appendChild(note);
    });
    rows.forEach((r, i) => {
      const row = document.createElement('div'); row.className = 'tt-row' + (i === near ? ' near' : '');
      const key = document.createElement('span'); key.className = 'tt-key'; key.style.background = r.color;
      const val = document.createElement('span'); val.className = 'tt-val'; val.textContent = rowText(r);
      const nm = document.createElement('span'); nm.className = 'tt-name'; nm.textContent = r.label;
      row.append(key, val, nm); tip.appendChild(row);
    });
    const wrap = svgEl.parentElement, wrapRect = wrap.getBoundingClientRect();
    const svgRect = svgEl.getBoundingClientRect();
    const scaleX = lastGeom.W ? svgRect.width / lastGeom.W : 1;
    const px = x(index) * scaleX;
    // Flip at the MIDPOINT, not at the right edge. Flipping only on overflow left the tooltip
    // sitting over the stretch of chart the reader is moving towards for the whole second half
    // of the plot; past halfway it belongs on the other side of the crosshair. Width is
    // measured now the rows are in, rather than assumed at 200.
    const tw = tip.offsetWidth || 200;
    // On a phone a tooltip beside the crosshair covered the whole plot and clipped the axis, so
    // it sits under the chart there, where the crosshair and its dots stay in view.
    const under = wrapRect.width < 560;
    tip.classList.toggle('under', under);
    if (under){
      tip.style.left = '0px';
      tip.style.top = (svgRect.height + 6) + 'px';
      return;
    }
    const left = px > wrapRect.width / 2 ? Math.max(4, px - tw - 12)
                                         : Math.min(Math.max(4, wrapRect.width - tw - 4), px + 12);
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
  // The legend's half of the same gesture. Measured before this changed: `opacity:.4` on the
  // whole row put its label at ~1.6:1 against the surface — the de-emphasised rows became
  // unreadable rather than secondary. Only the line-key fades now, and the focused row lifts
  // its own surface; the text never moves.
  const legend = el('trends-legend');
  if (legend) legend.querySelectorAll('.row[data-name]').forEach(row => {
    row.classList.toggle('dim', !!hoveredSeries && row.dataset.name !== hoveredSeries);
    row.classList.toggle('on', !!hoveredSeries && row.dataset.name === hoveredSeries);
  });
}

// ---- the table view: the WCAG-clean twin of the chart (dataviz skill, components.md) --------

// The row header both tables share — a line-key plus the label, so a reader who is here
// BECAUSE the light-mode chart's contrast sent them can still tell which line is which.
function tableRowHead(s){
  if (s.name === '__total__') return `<th scope="row"><b>${esc(s.label)}</b></th>`;
  const isOther = s.name === '__other__';
  const slot = isOther ? 'other' : seriesColorAssignment.get(s.name);
  return `<th scope="row">${swatchHtml(isOther ? 'var(--ink-3)' : seriesColor(slot), slot)}${esc(s.label)}</th>`;
}

// One row per series: what it is now, how far it moved, and the shape of the window around it.
// This was 471 columns wide — one per measurement — which made the chart's a11y twin and the
// documented relief for the sub-3:1 light-mode slots something nobody could read either. The
// full grid is still here, one disclosure down (buildTrendsFull), so nothing is gated.
function buildTrendsTable(){
  const d = trendData; if (!d) return '';
  const { shown: rows } = chartedAndOther(d);
  // The change column leaves the marked steps out and the counts are as counted, so both are
  // named and the steps get a column of their own: 764 → 1,018 beside "−0.2%" read as a bug.
  const head = `<tr><th scope="col">${VIEWS[viewKind(d)].column}</th><th scope="col">Latest</th>`
    // Under Share the percentage is the share's own change, which can fall while openings rise.
    + `<th scope="col">${trendUnit === 'share' ? 'Share, change' : 'Hiring, %'}</th>`
    + '<th scope="col">Hiring, openings</th><th scope="col">Counting changes, openings</th>'
    + '<th scope="col">Start, as counted</th><th scope="col">Min</th><th scope="col">Max</th></tr>';
  const cell = v => `<td>${v == null ? '—' : esc(fmtLevel(v))}</td>`;
  // Under a pick, the company's own line heads the table, so a reader summing the categories has
  // the figure they are checking against — and the note says why the sum need not reach it.
  const kind = viewKind(d);
  const withTotal = trendPicks.length && (kind === 'families' || kind === 'bands') && rows.length > 1;
  const total = withTotal ? [{ name: '__total__', label: kind === 'bands' ? `All of ${drillLabel()}` : 'All tech roles',
    points: sumPoints(d.series, d.stamps) }] : [];
  const body = [...total, ...rows].map(s => {
    const vals = s.points.map((v, j) => levelValue(v, j, s)).filter(v => v != null);
    const mv = lineMove(s);
    // The latest run's figure, as the legend reads it: a category emptied by a refit read its
    // last count before (Syms' systems engineering, 46 in the table beside 0 in the legend).
    const now = latestOf(s);
    return `<tr${s.name === '__other__' ? ' class="other"' : s.name === '__total__' ? ' class="total"' : ''}>` + tableRowHead(s)
      + cell(now == null ? null : levelValue(now, s.points.length - 1, s))
      + hiringCells(s, mv)
      + `<td>${countingChange(s)}</td>`
      + cell(vals.length ? vals[0] : null)
      + cell(vals.length ? Math.min(...vals) : null)
      + cell(vals.length ? Math.max(...vals) : null) + '</tr>';
  }).join('');
  // What makes the rows add up to the first: Google's categories summed to +15 of hiring under a
  // company row of −32, and a caption saying they "need not" was no answer to which is right.
  // The first row is. With every line netted by openings the rows add up, so the row shows only
  // what cannot: rows too new to read, and a step scaled so it would not erase a line's start.
  const [one, many] = kind === 'bands' ? ['level', 'levels'] : ['category', 'categories'];
  const whose = trendPicks.length > 1 ? 'the companies’' : 'the company’s';
  let between = '';
  if (withTotal){
    const whole = hiringOpenings(total[0], lineMove(total[0]));
    const parts = rows.map(s => hiringOpenings(s, lineMove(s)) || 0).reduce((a, b) => a + b, 0);
    const gap = whole == null ? 0 : whole - parts;
    if (gap) between = `<tr class="between"><th scope="row" title="The first row’s hiring less the rows’ sum: ${many} too new to read, or a counting change scaled so it would not erase a ${one}’s start">Between ${many}</th>`
      + `<td></td><td></td><td class="${gap > 0 ? 'up' : 'down'}">${esc(signedOpenings(gap))}</td><td></td><td></td><td></td><td></td></tr>`;
  }
  const note = withTotal ? `<caption>The first row is ${whose} hiring, and the ${many} add up to it${between ? ' with the last row, which holds what no single row can read' : ''}.</caption>` : '';
  return `${note}<thead>${head}</thead><tbody>${body}${between}</tbody>`;
}

// How many openings a line's marked steps moved it, as counted: the chart's move less the
// hiring one, so under Count a row's start, its hiring change in openings and this add up to
// its latest count. Always in openings, whatever the unit — the column says so.
// How many of the picks' served jobs the classifier sets aside as non-tech at the latest run:
// each company's whole total less its tech openings.
function nonTechAt(d){
  if (!d || !d.company_totals) return 0;
  const whole = Object.values(d.company_totals).reduce((sum, t) => sum + (t[t.length - 1] || 0), 0);
  const tech = d.series.reduce((sum, s) => sum + (latestOf(s) || 0), 0);
  return Math.max(0, whole - tech);
}

// A line's figure at the latest run. The Space leaves a stock series' point empty where a run
// counted none of it, so a line with earlier counts reads 0 there — one rule for the tile, the
// sentence and the legend, which disagreed ("227" beside 144).
function latestOf(s){
  // A summed line built here (a Total) carries no `latest`; its last point is it.
  const last = s.latest !== undefined ? s.latest : s.points[s.points.length - 1];
  if (last != null) return last;
  return trendMetric === 'stock' && s.points.some(v => v != null) ? 0 : null;
}

// One unit per column: "↑ +6.8%" and "+12 openings" in one column read as two scales for one
// question. A line under the floor has no percentage worth printing ("—", with the reason); a
// line too new or first seen in the window has neither.
function hiringCells(s, mv){
  const n = hiringOpenings(s, mv);
  const pct = mv.tooNew || mv.since ? `<td class="flat">${esc(moveText(mv))}</td>`
    : mv.dl != null ? `<td class="${deltaClass(mv.dl)}">${deltaText(mv.dl)}</td>`
    : `<td class="flat" title="Under ${MOVER_FLOOR} openings at the start, or too short a window, for a percentage to mean much">—</td>`;
  return pct + (n == null ? '<td class="flat">—</td>'
    : `<td class="${n > 0 ? 'up' : n < 0 ? 'down' : 'flat'}">${esc(signedOpenings(n))}</td>`);
}
// A line's hiring in openings, as its table row gives it: none for a line too new to read;
// all of a category first seen inside the window, which is hiring; and from its first run for
// one sorted in by a counting change, whose starting openings are that change's.
function hiringOpenings(s, mv){
  if (mv.tooNew) return null;
  if (mv.since && !mv.recount) return Math.round(latestOf(s) || 0);
  const m = trendMove(s);
  return m ? Math.round(m.change) : null;
}
// What the non-hiring part of a line's move was, by cause and size, from the steps taken out of
// it: "−2,041 duplicate postings removed, +230 from counting changes". The payload knew NVIDIA's
// −1,811 was mostly one duplicate removal; the sentence named three possible causes and none.
// A run holding two kinds of step is named by both, with one size.
function causesOf(s, total){
  // Peeled a kind at a time — duplicates, then found Boards, then the rest — each kind's part
  // being how much it moves the line's change. With ratio steps a known size is not its own
  // part: a removal of 2,041 before a ×1.2 step moved the line by 1.2 × 2,041, and read off the
  // size the rest landed in "counting changes". Peeled, the parts add up to the total exactly.
  const change = only => { const e = headTail(netOfSteps(s.points, s, only)); return e ? e.tail - e.head : 0; };
  const raw = change(new Set());
  const noDup = change(new Set(['duplicates']));
  const noFound = change(new Set(['duplicates', 'found']));
  const duplicates = Math.round(raw - noDup);
  const found = Math.round(noDup - noFound);
  const counting = total - duplicates - found;
  const parts = [
    duplicates && `${signedOpenings(duplicates)} from duplicate postings removed`,
    found && `${signedOpenings(found)} from boards found later`,
    // With the removals given their own figure, the change that made them is not named again.
    counting && `${signedOpenings(counting)} from ${countingChanges(s, duplicates !== 0)}`,
  ].filter(Boolean);
  return parts.join(', ');
}
// Which counting changes a line carries, named and counted: "4 tech filter changes and a role
// family assignment change". "Changes in how HeadStart counts" left a +290 for Google with
// nothing to check it against. Keyed on the epoch's fields, never the Space's display text.
const CAUSE_NAMES = {
  centroid_version: ['a role taxonomy refit', 'role taxonomy refits'],
  family_map_fingerprint: ['a role family map edit', 'role family map edits'],
  tech_filter_version: ['a tech filter change', 'tech filter changes'],
  derivations_version: ['an experience/salary extraction change', 'experience/salary extraction changes'],
  dedup_version: ['a duplicate removal change', 'duplicate removal changes'],
  family_classifier_version: ['a role family assignment change', 'role family assignment changes'],
};
// `dupNamed`: the line's removals already have their own figure.
function countingChanges(s, dupNamed){
  const perCompany = !!s && VIEWS[viewKind(trendData)].split === 'company';
  const bands = viewKind(trendData) === 'bands';
  // A duplicate-removal change is a cause only at a line holding a pick it can touch: beside
  // Micron, Google's line named one.
  const touches = n => n.touched.length > 0 && (!perCompany || n.touched.includes(s.name));
  // Only a change that moved this line, at its run or the run after: every company read "4 tech
  // filter changes" although Google's Sep 24 16:23 one moved it by nothing.
  const moved = n => stepMoved(n, s);
  const seen = new Map();  // field -> the changes (their stamps) that moved it
  const echoes = new Set();  // the changes seen here only by their week-later echo under New
  stepsFor(s).filter(n => n.fields && noteKind(n) === 'counting' && moved(n)).forEach(n => n.fields.forEach(f => {
    if (!CAUSE_NAMES[f] || (f === 'derivations_version' && !bands)
      || (f === 'dedup_version' && (dupNamed || !touches(n)))) return;
    if (n.echo){ echoes.add(n.source); return; }
    seen.set(f, (seen.get(f) || new Set()).add(n.source));
  }));
  // One change and its echo are one change; an echo whose change fell before the window is
  // named as an echo — "4 tech filter changes" over a 4-day window held three.
  const tech = seen.get('tech_filter_version') || new Set();
  const lone = [...echoes].filter(src => !tech.has(src)).length;
  if (!seen.size && !lone) return 'changes in how HeadStart counts';
  const said = [...seen].map(([f, changes]) =>
    changes.size === 1 ? CAUSE_NAMES[f][0] : `${changes.size} ${CAUSE_NAMES[f][1]}`);
  if (lone) said.push(lone === 1 ? 'the week-later echo of an earlier tech filter change'
    : `the week-later echoes of ${lone} earlier tech filter changes`);
  return said.length === 1 ? said[0] : `${said.slice(0, -1).join(', ')} and ${said[said.length - 1]}`;
}
// The runs a withheld note moves line `s` at: where it lands (a step on a gap lands on the next
// measured point), and, for a counting change, where its settling run lands.
function stepRuns(n, s){
  const landing = i => { let j = i; while (j < s.points.length && s.points[j] == null) j++; return j; };
  return n.epoch && !n.echo ? [landing(n.i), landing(n.i + 1)] : [landing(n.i)];
}
// How many openings notes `ns` moved line `s` by, each run counted once. A run holding other
// steps of known size (duplicates removed, Boards found) leaves those out: NVIDIA's list gave its
// counting change −2,138 beside "2,041 duplicate postings removed", the one inside the other.
function stepSize(ns, s){
  const jumps = stepJumps(s.points, s);
  const runs = new Set(ns.flatMap(n => stepRuns(n, s)));
  const others = stepsFor(s).filter(n => !ns.includes(n) && n.size != null && isWholeLine(s));
  return Math.round([...runs].reduce((sum, j) => {
    const jump = jumps.get(j); if (!jump) return sum;
    const known = others.filter(n => stepRuns(n, s)[0] === j).reduce((a, n) => a + n.size, 0);
    return sum + (jump.after - jump.before) - known;
  }, 0));
}
// Whether a note moved line `s` at its own run — the one test the sentence, the markers and the
// list share. Not at the run after: that one is left out in case the change is still settling,
// and a move there is as likely ordinary hiring, or another change's own step.
function stepMoved(n, s){
  const jump = stepJumps(s.points, s).get(stepRuns(n, s)[0]);
  return !!jump && Math.round(jump.after - jump.before) !== 0;
}
function countingMove(s){
  const raw = headTail(s.points), net = trendMove(s);
  return raw && net ? Math.round(raw.tail - raw.head) - Math.round(net.change) : null;
}
function countingChange(s){
  // A line sorted in by a counting change arrived with its openings by that change.
  const arrived = firstSeen(s, trendData) && recountBorn(s) ? s.points.find(v => v != null) || 0 : 0;
  const n = (countingMove(s) || 0) + arrived;
  return n ? esc(signedOpenings(n)) : '—';
}

// The whole time grid, one column per measurement — behind a disclosure because 471 columns
// is a data dump, not a table view, and only some readers want it.
function buildTrendsFull(){
  const d = trendData; if (!d) return '';
  const { shown: rows } = chartedAndOther(d);
  const head = `<tr><th scope="col">${VIEWS[viewKind(d)].column}</th>` +
    d.stamps.map(ts => `<th scope="col">${esc(stampLabel(ts))}</th>`).join('') + '</tr>';
  const body = rows.map(s => `<tr${s.name === '__other__' ? ' class="other"' : ''}>` + tableRowHead(s) +
    s.points.map((v, j) => { const u = levelValue(v, j, s); return `<td>${u == null ? '—' : esc(fmtLevel(u))}</td>`; }).join('') +
    '</tr>').join('');
  return `<thead>${head}</thead><tbody>${body}</tbody>`;
}

function renderTrendsTables(){
  if (el('trends-table')) el('trends-table').innerHTML = buildTrendsTable();
  if (el('trends-full-table')) el('trends-full-table').innerHTML = buildTrendsFull();
}

function toggleTrendsTable(force){
  // Not while the panel is showing a failure. The toggle used to reveal the table built from
  // the last SUCCESSFUL fetch, so a "that didn't load" banner sat directly above a full set of
  // numbers — the one arrangement that makes the error look like the lie.
  if (el('trends-error') && !el('trends-error').hidden) return;
  tableView = force ?? !tableView;
  if (el('trends-table-toggle')) el('trends-table-toggle').setAttribute('aria-pressed', tableView);
  if (el('trends-viz')) el('trends-viz').hidden = tableView;
  if (el('trends-table-wrap')){
    el('trends-table-wrap').hidden = !tableView;
    if (tableView) renderTrendsTables();
  }
}

function trendClick(name, split){
  if (trendDrill) { trendSplit = 'bands'; loadTrends(null); return; }   // drilled in — go back up
  // A company line and the Total line are not categories: nothing opens below them.
  if (!VIEWS[viewKind(trendData)].drills) return;
  // Only charted rows drill. `< 0` as well as `>= CHART_MAX`: findIndex returns -1 for a name
  // that is not in the series at all, and -1 passes a bare upper-bound check.
  const i = trendData.series.findIndex(x => x.name === name);
  if (i < 0 || i >= CHART_MAX) return;
  // A row opens its levels, which add up to the category: landing on watched roles, a few named
  // titles inside it, showed "155" under a row that had just said 243. The "▸ roles" marker
  // opens the roles it names (`split`), so that affordance still leads where it says.
  trendSplit = split === 'roles' && (trendData.watch_parents || []).includes(name) ? 'roles' : 'bands';
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
// openings over all live ones is a number with no reading. "New this week" used to grey the
// segment out and explain itself only in a `title`, which is a control the reader can see,
// cannot use, and is given no visible reason for. The group is replaced by the answer and its
// one-line why instead. `disabled` stays on the buttons underneath: `hidden` keeps them off
// the screen, and `disabled` keeps them off the tab order in every browser.
function setUnit(value, shareLocked, changeLocked){
  trendUnit = value;
  const seg = el('trends-unit'); if (!seg) return;
  // A unit with no reading in this view is withdrawn, with its one-line why in place of the
  // button (shareLock, changeLock). Count always has a reading, so it is never withdrawn: under
  // "New this week", a share of "new" openings over all live ones has none, but "312 fresh
  // AI/ML roles" is a number, and indexing those counts is the only way to compare eight
  // families running from 8.4k down to 961. Hiding the whole group took Change away in the
  // view that needs it most, and since Change is now the default (ADR-0119) it also meant the
  // metric toggle silently changed the unit under the reader.
  const why = [shareLocked, changeLocked].filter(Boolean).join(' ');
  if (el('trends-unit-static')){
    if (why) el('trends-unit-static').textContent = why;
    el('trends-unit-static').hidden = !why;
  }
  seg.querySelectorAll('button').forEach(b => {
    const off = !!(shareLocked && b.dataset.unit === 'share') || !!(changeLocked && b.dataset.unit === 'change');
    b.disabled = off;
    b.hidden = off;
    setRadioChecked(b, b.dataset.unit === value);
  });
}
// Why Share has no reading in the current view, or '' where it has one. A top-level Company
// line is a whole company, so as a share of that company it is 100% on every line — measured
// on the live data (Stripe and Datadog both read 100%).
function shareLock(){
  if (trendMetric === 'new') return 'Share is off here — a share of “new” openings over all live ones has no reading.';
  if (!trendDrill && topSplitNow() === 'company' && trendPicks.length > 1)
    return 'Share is off here — each line is a whole company, so as a share of itself it is always 100%.';
  // All of a company's tech roles as a share of all its openings read "96%": a tech filter's
  // measure, not a hiring one.
  if (!trendDrill && trendPicks.length && topSplitNow() === 'total')
    return 'Share is off here — one line of all tech roles is nearly all of the company’s openings, so its share says nothing.';
  return '';
}
// Why Change has no reading here: when no line starts at the floor, every line is "not
// indexed" and the plot is empty. That is the usual company, not the edge — 22,863 of the
// directory's 33,966 hold fewer than 5 tech openings (measured 2026-09-24), and Razorpay drew
// one "not indexed" row over a blank chart.
function changeLock(){
  if (!trendData) return '';
  const { charted } = chartedAndOther(trendData);
  return !charted.length || charted.some(startsIndexable) ? ''
    : `Change is off here — no line starts with ${INDEX_BASE_FLOOR} or more openings to index against, so counts are shown.`;
}
// A lock moves the reader off a unit only while it holds: the unit they chose is kept in
// `unitWanted` and comes back when the view can show it again.
let unitWanted = null;
function applyUnitLocks(){
  const share = shareLock(), change = changeLock();
  const locked = u => (u === 'share' && share) || (u === 'change' && change);
  let unit = unitWanted || trendUnit;
  if (locked(unit)){
    unitWanted = unit;
    unit = unit === 'share' && !change ? 'change' : 'count';
  } else unitWanted = null;
  setUnit(unit, share, change);
}
trendSeg('trends-metric', 'metric', v => {
  trendMetric = v;
  // Share's lock follows the metric alone, so it applies before the answer lands; Change's is
  // checked against the data again when it does (loadTrends).
  applyUnitLocks();
  loadTrends(trendDrill);
});
trendSeg('trends-coverage', 'coverage', v => { trendCoverage = v; loadTrends(trendDrill); });
trendSeg('trends-unit', 'unit', v => pickUnit(v));
// The reader's own choice clears any unit a lock was holding for them.
function pickUnit(v){ unitWanted = null; trendUnit = v; applyUnitLocks(); writeTrendHash(); drawTrends(); }
trendSeg('trends-split', 'split', v => trendSplitSelect(v));
function trendSplitSelect(v){
  if (trendDrill){ trendSplit = v; loadTrends(trendDrill); return; }
  // Category and Total are two drawings of one answer, so moving between them is a redraw;
  // Company is a different request.
  const refetch = v === 'company' || topSplitNow() === 'company';
  topSplit.chosen = v;
  // A breakdown is a view a reader can step back out of: Company → Total → Category added no
  // history entry, so Back skipped all three.
  if (refetch || !trendRaw){ picksPushed = true; loadTrends(null); return; }
  trendData = trendView(trendRaw, null);
  writeTrendHash(true); applyUnitLocks();
  drawTrends();
}
// A preset and a custom bound are two spellings of the same window, so setting either clears
// the other — a segment reading "30 days" beside a request that used a typed date is a lie the
// reader has no way to spot.
trendSeg('trends-range', 'days', v => {
  trendDays = v;
  ['trends-since', 'trends-until'].forEach(id => { if (el(id)) el(id).value = ''; });
  loadTrends(trendDrill);
});
['trends-since', 'trends-until'].forEach(id => {
  // A typed range is neither preset, so none stays checked: "All" lit over Sep 18–21 was a lie.
  if (el(id)) el(id).addEventListener('change', () => {
    const any = ['trends-since', 'trends-until'].some(f => el(f) && el(f).value);
    setRangePreset(any ? 'custom' : 'all'); loadTrends(trendDrill);
  });
});
if (el('trends-range-clear')) el('trends-range-clear').addEventListener('click', () => {
  ['trends-since', 'trends-until'].forEach(id => { if (el(id)) el(id).value = ''; });
  setRangePreset('all');
  loadTrends(trendDrill);
});
if (el('trends-back')) el('trends-back').addEventListener('click', () => { trendSplit = 'bands'; loadTrends(null); });
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
/* ---- The company picker (ADR-0185): the WAI-ARIA combobox pattern over /companies/suggest.
   Typing is only ever a query. A company reaches the chart when the reader picks it, and each
   pick is a chip with its own remove button, so what scopes the chart is always on screen. Each
   suggestion shows its openings and Board count, so a real employer is told apart from a
   one-posting slug collision of the same name. ---- */
const SUGGEST_WAIT = 150;   // ms after the last keystroke — a query per key would race itself
let coTimer = null, coReq = null, coOptions = [], coActive = -1;
// Enter pressed before the suggestions came back: pick the top one when they do. It was
// swallowed, and the next name typed ran on into the first ("amazonnvimicrosoft").
let coEnterPending = false;

function setCoNote(text){ if (el('trends-co-note')) el('trends-co-note').textContent = text; }

function drawPicks(){
  const box = el('trends-co-chips'); if (!box) return;
  box.innerHTML = trendPicks.map((p, i) => `<span class="co-chip${p.label ? '' : ' pending'}"
    ><span class="co-name" title="${esc(p.label || '')}">${esc(p.label || 'Loading…')}</span
    ><button type="button" data-unpick="${i}" aria-label="Remove ${esc(p.label || 'this company')}"
    >×</button></span>`).join('');
  if (el('trends-co-q')) el('trends-co-q').placeholder = trendPicks.length ? 'Add another' : 'Add a company';
  // The heading is drawn with the answer (drawTrends), not with the pick: set here it read
  // "…at Amazon" over Microsoft's chart for the length of the round trip.
  // Every pick's Boards at once, within the Space's cap on one hand-off (CFG.max_scoped_boards);
  // past it the link says why rather than vanishing or sending a request the route refuses.
  const boards = trendPicks.flatMap(p => p.boardKeys || []);
  const roles = el('trends-co-roles');
  if (roles){
    const cap = CFG.max_scoped_boards || Infinity;
    // Not inside a category the answer has nothing in: an unknown `family=` offered "See its
    // nonsense-family roles".
    roles.hidden = !trendPicks.length || trendPicks.some(p => !p.boardKeys)
      || (!!trendDrill && !!trendData && !trendData.series.length);
    roles.disabled = boards.length > cap;
    // Inside a category the link names it and hands its name to Search as the query, so Google ›
    // AI / Machine Learning leads to Google's AI roles first rather than to every Google job.
    const its = trendPicks.length > 1 ? 'their' : 'its';
    roles.textContent = roles.disabled ? `Too many boards to list at once (${boards.length}) — remove a company`
      : trendDrill && trendSplit === 'roles' ? `See all ${its} ${drillLabel()} roles`
      : trendDrill ? `See ${its} ${drillLabel()} roles` : `See ${its} open roles`;
  }
  // Source offers only the picks' ATSes: one no pick is on answers with an empty chart. Hidden,
  // not unchecked, so clearing the picks gives back exactly the selection there was.
  const on = new Set(trendPicks.flatMap(p => p.atses || []));
  const boxes = el('trends-ats-menu') ? [...el('trends-ats-menu').querySelectorAll('input[type=checkbox]')] : [];
  const narrow = boxes.some(box => on.has(box.value));   // a pick on no listed ATS narrows nothing
  boxes.forEach(box => { if (box.parentElement) box.parentElement.hidden = narrow && !on.has(box.value); });
  trendAtsLabel();
}

// A change of picks is a history entry of its own, like a drill: Back used to undo every pick
// and filter in one step.
let picksPushed = false;
function setPicks(picks){
  setCoNote('');   // a refusal's sentence belongs to the pick it refused, not to the next one
  replacePicks(picks);
  picksPushed = true;
  loadTrends(trendDrill);
}

// The follow list (ADR-0171) as one option, offered on an empty query. Followed companies are
// stored as Board keys, so the page cannot name them before the Space does — one row that adds
// them all, named once the chart answers, rather than a list of raw keys. Uncounted, because a
// count of Boards is not a count of companies: two followed Boards of one employer are one.
function followedOption(){
  const picked = pickedKeys();
  const boards = (myCompanies.followed || []).filter(b => !picked.has(b.toLowerCase()));
  return CAN_COMPANIES && boards.length ? [{ followed: boards }] : [];
}

function optionHtml(o){
  if (o.followed) return '<span class="co-opt-name">Add the companies you follow</span>';
  const c = o.company;
  const meta = `${c.openings.toLocaleString()} tech opening${c.openings === 1 ? '' : 's'} · `
    + `${c.boards} board${c.boards === 1 ? '' : 's'} · ${c.atses.join(', ')}`;
  return `<span class="co-opt-name">${esc(c.label)}</span><span class="co-opt-meta">${esc(meta)}</span>`;
}

function openCoList(options){
  const list = el('trends-co-list'), input = el('trends-co-q');
  coOptions = options; coActive = -1;
  list.innerHTML = options.map((o, i) =>
    `<li role="option" id="co-opt-${i}" data-opt="${i}" aria-selected="false">${optionHtml(o)}</li>`).join('');
  list.hidden = !options.length;
  input.setAttribute('aria-expanded', String(!!options.length));
  input.removeAttribute('aria-activedescendant');
}
function closeCoList(){ openCoList([]); }

function setCoActive(i){
  const list = el('trends-co-list'), input = el('trends-co-q');
  coActive = i;
  list.querySelectorAll('[role="option"]').forEach(li =>
    li.setAttribute('aria-selected', String(li.dataset.opt === String(i))));
  if (i < 0){ input.removeAttribute('aria-activedescendant'); return; }
  input.setAttribute('aria-activedescendant', `co-opt-${i}`);
  const li = el(`co-opt-${i}`);
  if (li && li.scrollIntoView) li.scrollIntoView({ block: 'nearest' });
}

function chooseCo(i){
  const o = coOptions[i]; if (!o) return;
  const add = o.followed ? o.followed.map(key => ({ key, label: null }))
                         : [{ key: o.company.key, label: o.company.label }];
  setCoNote('');
  // Closed and cleared, focus kept in the field for the next name: left open on its query, the
  // list covered the Date range, Source and Coverage controls, which two critiques flagged.
  el('trends-co-q').value = '';
  closeCoList();
  setPicks([...trendPicks, ...add]);
}

async function suggestCompanies(q){
  if (coReq) coReq.abort();
  const req = coReq = new AbortController();
  let found = null, missing = false;
  try {
    const r = await fetch('/companies/suggest?' + new URLSearchParams({ q }), { signal: req.signal });
    if (r.ok) found = (await r.json()).companies || [];
    else missing = r.status === 503 || r.status === 404;
  } catch(e){ /* reported below, unless a newer query replaced this one */ }
  if (req.signal.aborted) return;
  const picked = pickedKeys();
  const options = (found || []).filter(c => !picked.has(c.key.toLowerCase())).map(company => ({ company }));
  openCoList(options);
  if (coEnterPending){ coEnterPending = false; if (options.length){ chooseCo(0); return; } }
  // Said in the status line, not as a fake option: a listbox should only hold choices.
  setCoNote(missing ? 'Company search isn’t available here yet.'
    : !found ? 'Suggestions didn’t load — keep typing to try again.'
    : !options.length ? `No company matches “${q}”. HeadStart may not read its board yet, or knows it by another name — try part of it.` : '');
}

if (el('trends-co-q')){
  const input = el('trends-co-q');
  input.addEventListener('input', () => {
    clearTimeout(coTimer);
    if (coReq) coReq.abort();
    const q = input.value.trim();
    coEnterPending = false;
    if (!q){ setCoNote(''); openCoList(followedOption()); return; }
    coTimer = setTimeout(() => suggestCompanies(q), SUGGEST_WAIT);
  });
  input.addEventListener('focus', () => { if (!input.value.trim()) openCoList(followedOption()); });
  input.addEventListener('blur', () => { coEnterPending = false; closeCoList(); });
  input.addEventListener('keydown', e => {
    const n = coOptions.length;
    if (e.key === 'ArrowDown' && n){ e.preventDefault(); setCoActive((coActive + 1) % n); }
    else if (e.key === 'ArrowUp' && n){ e.preventDefault(); setCoActive((coActive - 1 + n) % n); }
    // Enter takes the highlighted suggestion, or the top one: typing "amd" and pressing Enter
    // picked nothing, so the company a reader asked for silently never reached the chart.
    else if (e.key === 'Enter' && n){ e.preventDefault(); chooseCo(Math.max(coActive, 0)); }
    else if (e.key === 'Enter' && input.value.trim()){
      e.preventDefault(); clearTimeout(coTimer); coEnterPending = true; suggestCompanies(input.value.trim());
    }
    else if (e.key === 'Escape'){ coEnterPending = false; if (n) closeCoList(); else input.value = ''; }
    else if (e.key === 'Backspace' && !input.value && trendPicks.length)
      setPicks(trendPicks.slice(0, -1));
  });
  // mousedown, not click, keeps focus in the input — a blur first would close the list under
  // the pointer before its click landed.
  el('trends-co-list').addEventListener('mousedown', e => e.preventDefault());
  el('trends-co-list').addEventListener('click', e => {
    const li = e.target.closest('[data-opt]');
    if (li) chooseCo(Number(li.dataset.opt));
  });
  el('trends-co-chips').addEventListener('click', e => {
    const b = e.target.closest('[data-unpick]');
    if (!b) return;
    setPicks(trendPicks.filter((_, i) => i !== Number(b.dataset.unpick)));
    input.focus();   // the chip and its button are gone; the field is where the reader was
  });
}

// From a company's trend to its jobs: Search already owns the filters, ranking and job card,
// so the pick is handed over by its Board keys, like the Hot tab's "See roles".
// Inside a category the category goes too, as the Jobs the trend counted (the Space's
// `family=` clause), so Search lists the same openings the trend is made of, newest first.
// Where the Space cannot do that — no assignment snapshot, or more Jobs than it names by id —
// the category's name ranks the jobs instead and the pill does not claim a filter it is not
// applying.
if (el('trends-co-roles')) el('trends-co-roles').addEventListener('click', () => {
  if (!trendPicks.length) return;
  const name = trendDrill ? drillLabel() : null;
  const kind = trendData ? viewKind(trendData) : null;
  // The category's size, to keep inside the Space's cap: its levels add up to it; a watched-roles
  // view counts only a few titles, so the picks' whole totals bound it from above instead.
  const latest = list => list.reduce((sum, v) => sum + (v || 0), 0);
  const size = !trendData ? Infinity : kind === 'roles'
    ? latest(Object.values(trendData.company_totals || {}).map(t => t[t.length - 1]))
    : latest(trendData.series.map(s => s.latest));
  const exact = trendDrill && CFG.family_handoff && size <= (CFG.max_family_ids || 0);
  searchCompany(trendPicks.flatMap(p => p.boardKeys || []),
    trendPicks.length === 1 ? trendPicks[0].label : trendPicks.map(p => p.label).join(', '),
    // "Software Engineering (general)" asks for "(general)" too; the qualifier is not a role.
    trendDrill && !exact ? name.replace(/\s*\(.*\)\s*$/, '') : '',
    exact ? { family: trendDrill, label: name } : null,
    trendDrill ? 0 : nonTechAt(trendData),
    // Only where Search lists exactly what the trend counts: a whole company's tech openings, or
    // a category handed over by its ids.
    // Not under New, a week's count Search does not list, nor under a source filter Search does
    // not carry.
    trendData && trendData.stamps.length && (exact || !trendDrill) && trendMetric === 'stock' && !trendAtsSelected()
      ? { n: size, at: trendData.stamps[trendData.stamps.length - 1] } : null);
});

// "See trend" from a Hot-tab row or a search result (ADR-0185): the Board key the row already
// carries is a pick, and the name it already shows labels the chip until the Space answers.
// A link replaces the picks rather than adding to them — it asks about this one company.
// From the Hot tab, over Hot's own window: its "+25" was measured over hours, and a trend
// opened over All read "up 61.8%" for the same company.
// Hot's figure for the row a trend was opened from, so the trend can say how the two relate.
let hotOrigin = null;
function openCompanyTrend(board, name, since, net, built){
  if (name) pickLabels.set(board, name);
  // Hot's figure rides in the link (readTrendHash reads it back), so a reload keeps the note.
  location.hash = '#trends?company=' + encodeURIComponent(board)
    + (since ? '&since=' + encodeURIComponent(since.slice(0, 16)) : '')
    + (net != null && since ? `&hot=${encodeURIComponent(net)}&hot_board=${encodeURIComponent(board)}` : '')
    // In UTC minutes, like `since`: sliced off a local-offset stamp it read as 05:44 UTC in IST
    // and 18:14 UTC in Los Angeles for an 11:14 build.
    + (net != null && since && built && !isNaN(new Date(built))
      ? `&hot_at=${encodeURIComponent(new Date(built).toISOString().slice(0, 16))}` : '');
}

// Pointer tracking for the crosshair+tooltip lives on the SVG element itself, wired once: an
// innerHTML rewrite of its children (every drawTrends() call) leaves listeners on the element
// itself intact, so this never needs re-attaching.
if (el('trends-chart')) {
  el('trends-chart').addEventListener('pointermove', e => readChartAt(e));
  // A touch has no hover, so a tap (its click) reads the stamp it lands on, and lifting the
  // finger keeps the reading up rather than clearing it the instant it appeared.
  el('trends-chart').addEventListener('click', e => readChartAt(e));
  el('trends-chart').addEventListener('pointerleave', e => { if (e.pointerType !== 'touch') positionHoverLayer(null); });
  // A tapped reading stays until the finger moves on: a scroll, or a tap anywhere off the plot.
  // It stuck over the "Marked changes" list after scrolling to it, covering its entries.
  window.addEventListener('scroll', () => { if (hoverIndex != null) positionHoverLayer(null); }, { passive: true });
  document.addEventListener('click', e => {
    if (hoverIndex != null && !e.target.closest('#trends-chart')) positionHoverLayer(null);
  });
}
// The stamp nearest a pointer's x, read into the crosshair and tooltip.
function readChartAt(e){
    if (!lastGeom) return;
    const rect = el('trends-chart').getBoundingClientRect();
    const svgX = rect.width ? (e.clientX - rect.left) / rect.width * lastGeom.W : 0;
    const svgY = rect.width ? (e.clientY - rect.top) / rect.width * lastGeom.W : 0;
    let best = 0, bestDist = Infinity;
    lastGeom.stamps.forEach((_, j) => {
      const dist = Math.abs(lastGeom.x(j) - svgX);
      if (dist < bestDist){ bestDist = dist; best = j; }
    });
    // A finger lands on a marker, not a run: a tap on Sep 24's marker read the 22:10 run beside
    // it, three pixels away on a phone. Within a fingertip of a marked run, that run.
    // A finger only: a mouse click beside a marker keeps the run its crosshair was showing.
    // Safari's click has no pointerType, so a coarse pointer answers for it.
    const coarse = e.pointerType === 'touch' || (!e.pointerType && typeof matchMedia === 'function'
      && matchMedia('(pointer: coarse)').matches);
    if (coarse){
      const reach = rect.width ? 12 / rect.width * lastGeom.W : 0;   // 12 CSS px, in SVG units
      const mark = (lastGeom.marks || []).map(j => [j, Math.abs(lastGeom.x(j) - svgX)])
        .filter(([, dist]) => dist <= reach).sort((a, b) => a[1] - b[1])[0];
      if (mark) best = mark[0];
    }
    positionHoverLayer(best, { py: svgY });
}
if (el('trends-chart')) {
  // The keyboard path to the same values (dataviz skill, interaction.md: "same details on
  // keyboard focus as on hover"). The plot is tabbable; arrows walk the crosshair a stamp at a
  // time, Home/End jump to the ends, Escape parks it — and #trends-readout speaks each stop,
  // because a crosshair moving inside an SVG announces nothing on its own.
  el('trends-chart').addEventListener('keydown', e => {
    if (!lastGeom || !lastGeom.stamps.length) return;
    const last = lastGeom.stamps.length - 1;
    const at = hoverIndex == null ? last : hoverIndex;
    let next;
    if (e.key === 'ArrowRight') next = Math.min(last, at + 1);
    else if (e.key === 'ArrowLeft') next = Math.max(0, at - 1);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = last;
    else if (e.key === 'Escape'){ positionHoverLayer(null); return; }
    else return;
    e.preventDefault();
    positionHoverLayer(next, { announce: true });
  });
  el('trends-chart').addEventListener('blur', () => positionHoverLayer(null));
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
    // Hide/show is its own button beside the row, not inside it: the row is already
    // role="button" (it drills), and an interactive control nested in another is unreachable
    // for half the assistive tech that meets it.
    const vis = e.target.closest('button[data-hide]');
    if (vis){
      const name = vis.dataset.hide;
      if (hiddenSeries.has(name)) hiddenSeries.delete(name); else hiddenSeries.add(name);
      drawTrends();
      // The redraw replaced the button that was just pressed, taking the focus with it.
      const again = legend.querySelector(`button[data-hide="${CSS.escape(name)}"]`);
      if (again) again.focus();
      return;
    }
    // A tracked role's own jobs, by the title patterns it is counted by: "LLM / GenAI 84" at
    // Google had no way to those 84 (the Space's `role=`).
    const jobs = e.target.closest('[data-role]');
    if (jobs){
      searchCompany(trendPicks.flatMap(p => p.boardKeys || []),
        trendPicks.length === 1 ? trendPicks[0].label : trendPicks.map(p => p.label).join(', '),
        '', { role: jobs.dataset.role.replace(/^watch:/, ''), label: jobs.dataset.roleLabel });
      return;
    }
    const row = e.target.closest('.row[data-name]');
    if (row) trendClick(row.dataset.name, e.target.closest('.drill') ? 'roles' : 'bands');
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
if (el('salrmin')){
  // `input` for the live fill and read-out while a thumb is moving, `change` for the search —
  // one request per drag rather than one per pixel.
  el('salrmin').addEventListener('input', () => salSlide('min'));
  el('salrmax').addEventListener('input', () => salSlide('max'));
  el('salrmin').addEventListener('change', go);
  el('salrmax').addEventListener('change', go);
  // Typing moves the handle; it never moves the typed figure back.
  ['salmin', 'salmax'].forEach(id => el(id).addEventListener('input', syncSalarySlider));
  // The currency is part of the where-clause, not a label on it (ADR-0117): the bounds are
  // restated in it before anything is compared, so changing it changes which jobs match.
  // Bound to `syncSalarySlider` alone, it relabelled the read-out and left the previous
  // currency's results on screen underneath — USD rows under an INR heading. `go()` redraws
  // the read-out and the chips on its way through drawActive, so this is the whole fix.
  if (el('salcur')) el('salcur').addEventListener('change', go);
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
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-dismiss]');
  if (b) dismissRow(b.dataset.dismiss);
});
// Whole-row click, without an overlay. A real element is never covered, so text stays
// selectable and every title/tooltip underneath stays reachable. Three guards: a drag that
// selected text is not a click, anything already interactive handles itself, and a modified
// click keeps the browser's own open-in-new-tab behaviour.
document.addEventListener('click', e => {
  const card = e.target.closest('.card');
  if (!card || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  if (e.target.closest('a, button, input, select, textarea, label')) return;
  if (String(window.getSelection())) return;
  const link = card.querySelector('a.title');
  if (link) window.open(link.href, '_blank', 'noopener');
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

/* ---- "Hiring now" (hot_boards): a pre-ranked leaderboard of the Boards opening roles.

   The whole artifact arrives in one fetch — three lenses of at most 100 rows — so switching
   lens or revealing staffing firms is a re-render, never a round trip. It is a pipeline
   product read from a static file, so it is fetched once per visit and not re-polled. ---- */
let hotData = null;

const HOT_OPERATOR = {
  services: { label: 'staffing / services', hint: 'This board belongs to an IT services or staffing firm, so most roles are placements with its clients rather than jobs at the company itself.' },
  aggregator: { label: 'job board', hint: 'This board re-posts other companies’ jobs. The employer behind a given role is somebody else.' },
};

async function loadHot(){
  el('hot-msg').textContent = 'Loading…';
  try{
    const r = await fetch('/hot');
    if (!r.ok){
      // 503 is "no run has written one", which is a different thing from a failure and is the
      // only case the tab can be opened in without data.
      el('hot-msg').textContent = r.status === 503
        ? 'No ranking yet — the next pipeline run will build one.'
        : 'Couldn’t load the ranking.';
      return;
    }
    hotData = await r.json();
  }catch(e){ el('hot-msg').textContent = 'Couldn’t load the ranking.'; return; }
  el('hot-msg').textContent = '';
  drawHotProvenance();
  drawHot();
}

function hotLens(){
  const picked = document.querySelector('input[name="hot-lens"]:checked');
  return picked ? picked.value : 'expansion';
}

/* The number that *is* the ranking, per lens, plus how to say it. Each lens leads with its own
   measure and prints the other two small, so a row can be read against the question that
   ordered it rather than a single column that means something different on each tab. */
// Tech roles on this one Board: a row is a Board, and its "See trend" opens the whole company,
// whose other Boards the figures here do not include (HCLTech read −1,356 here, −605 there).
const HOT_MEASURE = {
  expansion: r => ({ big: (r.net > 0 ? '+' : '') + r.net, unit: 'net tech roles on this board', sub:
    `${r.new7} opened this week · ${r.stock} open now` }),
  volume:    r => ({ big: String(r.new7), unit: 'tech roles opened this week', sub:
    `${r.stock} open on this board · ${r.net >= 0 ? '+' : ''}${r.net} net` }),
  rate:      r => ({ big: r.rate + '%', unit: 'of its board is new', sub:
    `${r.new7} opened this week · ${r.stock} open now` }),
};

function drawHot(){
  if (!hotData) return;
  const lens = hotLens();
  const showAll = el('hot-show-all').checked;
  const all = hotData.lenses[lens] || [];
  const rows = showAll ? all : all.filter(r => r.operator === 'employer');
  const hiddenCount = all.length - rows.length;

  el('hot-filtered').textContent = hiddenCount
    ? `${hiddenCount} staffing ${hiddenCount === 1 ? 'firm or job board' : 'firms and job boards'} hidden`
    : (showAll ? '' : 'nothing filtered on this view');

  if (!rows.length){
    el('hot-results').innerHTML =
      '<li class="hot-empty">Nothing qualified on this view. Try another measure, or show staffing firms.</li>';
    return;
  }
  el('hot-results').innerHTML = rows.map((r, i) => hotRow(r, i, lens)).join('');
}

function hotRow(r, i, lens){
  const m = HOT_MEASURE[lens](r);
  const op = HOT_OPERATOR[r.operator];
  // Lowercased both sides, like `board_clause` — the index holds Board keys that differ only
  // in casing, and an exact check would offer "Follow" on a Board already being followed.
  const followed = (myCompanies.followed || []).some(
    b => b.toLowerCase() === (r.board || '').toLowerCase());
  // The rank is decorative — the list is already ordered and screen readers announce <ol>
  // position — so it is hidden from the accessibility tree rather than read out twice.
  return `
    <li class="hot-row${op ? ' flagged' : ''}" style="animation-delay:${Math.min(i,12)*30}ms">
      <span class="hot-rank" aria-hidden="true">${i + 1}</span>
      <div class="hot-who">
        <div class="hot-name">${esc(r.company)}</div>
        <div class="hot-tags">
          <span class="src" title="Read directly from this company’s ${esc(r.ats)} board">via ${esc(r.ats)}</span>
          ${op ? `<span class="tag flag" title="${esc(op.hint)}">${esc(op.label)}</span>` : ''}
        </div>
      </div>
      <div class="hot-measure">
        <b>${esc(m.big)}</b>
        <span class="hot-unit">${esc(m.unit)}</span>
        <span class="hot-sub">${esc(m.sub)}</span>
      </div>
      <div class="hot-actions">
        ${CAN_COMPANIES ? `<button class="ghost hot-track" data-track="${esc(r.board)}"
          aria-pressed="${followed}">${followed ? 'Following' : 'Follow'}</button>` : ''}
        <button class="ghost hot-see" data-board="${esc(r.board)}" data-company="${esc(r.company)}">See roles</button>
        ${el('trends') ? `<button class="ghost hot-trend" data-trend="${esc(r.board)}"
          data-trend-name="${esc(r.company)}" data-trend-since="${esc((hotData.window || {}).base || (hotData.window || {}).from || '')}"
          data-trend-net="${esc(String(r.net))}" data-trend-built="${esc(hotData.generated_at || '')}">See trend</button>` : ''}
      </div>
    </li>`;
}

function drawHotProvenance(){
  const w = hotData.window || {}, x = hotData.counts || {};
  const day = s => (s || '').slice(0, 10);
  // Its net change is over this window, which can be hours after a refit — say so in hours
  // then, beside rows that print "opened this week".
  const hours = w.from && w.to ? (new Date(w.to) - new Date(w.from)) / 36e5 : null;
  const span = hours != null && hours < 72 ? `the last ${Math.max(1, Math.round(hours))} hours`
    : `${day(w.from)} to ${day(w.to)}`;
  el('hot-provenance').textContent =
    `Net change measured over ${span}; "opened this week" is the last 7 days. ${x.ranked ?? 0} companies ranked; ` +
    `${x.below_min_stock ?? 0} with fewer than ${x.min_stock ?? '?'} open tech roles and ` +
    `${x.newly_discovered ?? 0} ` +
    `boards we had only just discovered were left out.`;
}

/* One delegated listener for the whole panel, like the sets strip — never an inline handler
   with an interpolated company name in it. */
if (el('hot-results')){
  document.querySelectorAll('input[name="hot-lens"]').forEach(input =>
    input.addEventListener('change', drawHot));
  el('hot-show-all').addEventListener('change', drawHot);
  el('hot-results').addEventListener('click', async ev => {
    const track = ev.target.closest('[data-track]');
    if (track){
      track.disabled = true;
      const board = track.dataset.track;
      // Folded, like `hotRow` and `board_clause` — an exact check here left the button
      // showing "Following" and then posting `follow` again, so it never cleared.
      const on = (myCompanies.followed || []).some(
        b => b.toLowerCase() === board.toLowerCase());
      if (await setCompany(board, on ? 'clear' : 'follow')) drawHot();
      else track.disabled = false;
      return;
    }
    const btn = ev.target.closest('.hot-see');
    if (!btn) return;
    // Hand the company to Search rather than filtering here: Search already owns the filter
    // vocabulary, the ranking and the job card, and a second place that lists jobs would be a
    // second place to keep them consistent.
    searchCompany([btn.dataset.board], btn.dataset.company);
  });
}

/* One delegated listener for the capped-row controls, wherever they render — Search and
   Matches, the two lists that re-run against the index. Never an inline handler with an
   interpolated Board key in it. */
document.addEventListener('click', async ev => {
  // "See trend", on a result card or a Hot-tab row — one handler for both, like the rest here.
  const trend = ev.target.closest('[data-trend]');
  if (trend){ openCompanyTrend(trend.dataset.trend, trend.dataset.trendName, trend.dataset.trendSince, trend.dataset.trendNet, trend.dataset.trendBuilt); return; }
  const more = ev.target.closest('[data-more]');
  if (more){ expandCompany(more.closest('.more-row')); return; }
  const hide = ev.target.closest('[data-hide-company]');
  if (!hide) return;
  hide.disabled = true;
  if (await setCompany(hide.dataset.hideCompany, 'hide')) {
    drawMyCompanies();
    // fetchPage redraws the Search list only; a hide clicked on Matches re-runs its Set too,
    // or the company just hidden stays on screen there.
    if (currentTab() === 'matches') runSet(activeSetId);
    await fetchPage();
  }
  else { hide.disabled = false; }
});

/* The hidden list is shown as a count with an undo, not a silent filter. A user who cannot see
   what was removed cannot tell "I hid this" from "the index has nothing". */
function drawMyCompanies(){
  const box = el('my-companies');
  if (!box) return;
  const n = (myCompanies.hidden || []).length;
  box.innerHTML = n
    ? `${n} ${n === 1 ? 'company' : 'companies'} hidden from your results ` +
      `<button class="linkish" id="unhide-all">show them again</button>`
    : '';
  if (el('unhide-all')) el('unhide-all').addEventListener('click', async () => {
    for (const board of [...(myCompanies.hidden || [])]) await setCompany(board, 'clear');
    drawMyCompanies();
    await fetchPage();
  });
}

if (el('mine')) el('mine').addEventListener('change', () => go());

try { applyDensity(!!localStorage.getItem(DENSITY_KEY)); } catch(e){ applyDensity(false); }
readSearchHash();   // a reloaded or shared hand-off (`#search?board=…`) scopes the first search
go();   // an empty query browses the newest jobs (ADR-0074) — the Search tab is never empty
whoAmI();
showTab(currentTab());
// Result cards need star states before the Saved tab is ever opened; landing ON the tab
// already loads via showTab above.
if (CAN_STAR && currentTab() !== 'saved') loadSaved();
loadCompanies().then(drawMyCompanies);
