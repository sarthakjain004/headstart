/* "Take a tour" (ADR-0249): a handful of steps that point at the parts of the page a first-time
   visitor needs, one at a time, with Back, Next and Skip. A plain script like app.js — no
   library, no build step.

   It knows the page only through CSS selectors and the hash router. A step names the tab it
   lives on and the selectors that find its target, tried in order; a target that is missing or
   hidden is skipped rather than pointed at, so a restyle that renames something costs that one
   step and never the tour. While it runs, the rest of the page is `inert`: nothing behind the
   dimmed backdrop can be clicked or reached with Tab, and Escape always closes it. */
(function (root) {
  'use strict';

  const STEPS = [
    { tab: null, targets: ['nav.tabs', 'nav.mobile-nav'], title: 'Everything is one click away',
      body: 'Search, your saved jobs, hiring trends and the résumé builder all live here. On a ' +
            'wide screen, the button at the top folds it to icons and back.',
      bodyFolded: 'These icons lead to Search, your saved jobs, hiring trends and the résumé ' +
                  'builder. Hover one to see its name, or use the button at the top to show the names.' },
    { tab: 'search', targets: ['#q'], title: 'Describe the job you want',
      body: 'Write it the way you would say it, like “backend engineer at a climate startup”. ' +
            'Results are matched on meaning, not exact words.' },
    { tab: 'search', targets: ['#rail'], title: 'Narrow it down',
      body: 'Filters narrow the list — experience, salary, remote, location and how ' +
            'recent. Keep those out of the search box.' },
    { tab: 'search', targets: ['#results .card', '#results'], title: 'Open a job at the source',
      body: 'Each result opens on the company’s own careers page, where you apply. The ' +
            'percentage is how close it is to what you typed.' },
    { tab: null, targets: ['.tabs [data-tab="trends"]'], title: 'See where hiring is heading',
      body: 'Trends shows which kinds of tech roles are growing or shrinking over time.' },
  ];
  // How long a step waits for its target before skipping it: a result list can still be
  // loading when its tab opens.
  const WAIT_MS = 2500;

  let tour = null;   // the open tour's elements and state; null when closed
  let seq = 0;       // bumped on every move, so a slow wait cannot land after a newer one
  // Steps whose target never showed up this run. A tab's targets cannot be looked for until the
  // tab is open, so a step is counted until it is skipped, and not after.
  const skipped = new Set();

  const visible = node => !!node && node.getClientRects().length > 0;
  // The nav shows only its icons: folded, and wide enough to be a sidebar (the fold button is
  // hidden where it is a strip, which shows every name whatever was stored).
  const navFolded = () => document.documentElement.dataset.nav === 'collapsed'
    && visible(document.getElementById('nav-toggle'));
  const panelOf = tab => document.getElementById('panel-' + tab);
  // A step on a tab this deployment does not render, or a step on every tab whose target is
  // absent (the Trends link, where Trends is dark), is never shown and not counted in "2 of 5".
  const usable = step => !skipped.has(step) && (step.tab ? !!panelOf(step.tab) : !!targetOf(step));

  function targetOf(step){
    for (const sel of step.targets){
      const node = document.querySelector(sel);
      if (visible(node)) return node;
    }
    return null;
  }

  function waitForTarget(step){
    return new Promise(resolve => {
      const started = Date.now();
      (function poll(){
        const node = targetOf(step);
        if (node || Date.now() - started >= WAIT_MS) resolve(node);
        else setTimeout(poll, 100);
      })();
    });
  }

  function make(tag, cls, text){
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }

  function start(){
    if (tour) return;
    // Before the offer goes: when it started the tour, its button is the opener.
    const opener = document.activeElement;
    skipped.clear();
    const spot = make('div', 'tour-spot');
    const pop = make('div', 'tour-pop');
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-modal', 'true');
    pop.setAttribute('aria-labelledby', 'tour-title');
    pop.setAttribute('aria-describedby', 'tour-body');
    const count = make('p', 'tour-count');
    const title = make('h2', 'tour-title');
    title.id = 'tour-title';
    const body = make('p', 'tour-body');
    body.id = 'tour-body';
    body.setAttribute('aria-live', 'polite');
    const actions = make('div', 'tour-actions');
    const skip = make('button', 'tour-skip linkish', 'Skip tour');
    const back = make('button', 'ghost', 'Back');
    const next = make('button', 'btn-primary', 'Next');
    for (const b of [skip, back, next]) b.type = 'button';
    skip.addEventListener('click', () => close());
    back.addEventListener('click', () => move(-1));
    next.addEventListener('click', () => move(1));
    actions.append(skip, back, next);
    pop.append(count, title, body, actions);
    document.body.append(spot, pop);

    const shell = document.querySelector('.shell');
    if (shell) shell.inert = true;
    tour = { spot, pop, count, title, body, back, next, shell, at: -1, target: null, opener };
    document.addEventListener('keydown', onKey);
    root.addEventListener('resize', place);
    root.addEventListener('scroll', place, true);
    show(0, 1);
  }

  function move(dir){
    if (tour) show(tour.at + dir, dir);
  }

  // Shows the first usable step at or after `from` in direction `dir`. Past the last one the
  // tour is finished; before the first one it stays where it is.
  async function show(from, dir){
    const mine = ++seq;
    for (let i = from; i >= 0 && i < STEPS.length; i += dir){
      const step = STEPS[i];
      if (!usable(step)) continue;
      if (step.tab && panelOf(step.tab).hidden) location.hash = '#' + step.tab;
      if (step.targets.includes('#rail') && document.getElementById('search-filters')) document.getElementById('search-filters').open = true;
      const target = await waitForTarget(step);
      if (!tour || mine !== seq) return;
      if (target) { render(i, target); return; }
      skipped.add(step);
    }
    if (dir > 0) close();
  }

  function render(i, target){
    const shown = STEPS.filter(usable);
    const n = shown.indexOf(STEPS[i]);
    Object.assign(tour, { at: i, target });
    tour.count.textContent = (n + 1) + ' of ' + shown.length;
    tour.title.textContent = STEPS[i].title;
    tour.body.textContent = STEPS[i].bodyFolded && navFolded() ? STEPS[i].bodyFolded : STEPS[i].body;
    tour.back.disabled = n === 0;
    tour.next.textContent = n === shown.length - 1 ? 'Finish' : 'Next';
    // A target taller than the window (the filter column) is shown from its top, not its middle.
    const tall = target.getBoundingClientRect().height > root.innerHeight * 0.8;
    target.scrollIntoView({ block: tall ? 'start' : 'center', inline: 'nearest' });
    place();
    tour.next.focus();
  }

  // The spotlight is a box over the target whose enormous shadow dims everything else; the
  // card sits below the target, or above it when there is no room, or beside a tall target
  // such as the sidebar. On a phone it docks to the bottom edge instead of chasing the target.
  function place(){
    if (!tour || !tour.target) return;
    const r = tour.target.getBoundingClientRect();
    const pad = 6, gap = 12, edge = 16;
    const W = root.innerWidth, H = root.innerHeight;
    Object.assign(tour.spot.style, { top: (r.top - pad) + 'px', left: (r.left - pad) + 'px',
      width: (r.width + 2 * pad) + 'px', height: (r.height + 2 * pad) + 'px' });
    const docked = W < 640;
    tour.pop.classList.toggle('docked', docked);
    if (docked) { Object.assign(tour.pop.style, { top: '', left: '' }); return; }
    const pw = tour.pop.offsetWidth, ph = tour.pop.offsetHeight;
    let top, left;
    if (r.height > H / 2 && r.right + pad + gap + pw <= W - edge){
      left = r.right + pad + gap;
      top = r.top;
    } else {
      left = r.left;
      top = r.bottom + pad + gap;
      if (top + ph > H - edge) top = r.top - pad - gap - ph;
    }
    top = Math.min(Math.max(top, edge), H - ph - edge);
    left = Math.min(Math.max(left, edge), W - pw - edge);
    Object.assign(tour.pop.style, { top: top + 'px', left: left + 'px' });
  }

  function onKey(e){
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); move(1); }
    else if (e.key === 'ArrowLeft' && !tour.back.disabled) { e.preventDefault(); move(-1); }
  }

  function close(){
    if (!tour) return;
    const { spot, pop, shell, opener } = tour;
    tour = null;
    seq++;
    spot.remove();
    pop.remove();
    if (shell) shell.inert = false;
    document.removeEventListener('keydown', onKey);
    root.removeEventListener('resize', place);
    root.removeEventListener('scroll', place, true);
    // Back to the button that opened it; if that tab is gone, into the search box, which is
    // where a finished tour leaves the reader.
    const q = document.getElementById('q');
    if (opener !== document.body && visible(opener)) opener.focus();
    else if (visible(q)) q.focus();
  }

  // Every "Take a tour" button, wherever it renders, with no inline handler.
  document.addEventListener('click', e => {
    if (e.target.closest && e.target.closest('[data-tour-start]')) start();
  });

  root.GuidedTour = { start };
})(typeof globalThis !== 'undefined' ? globalThis : this);
