/* Navigation owns visibility, history, focus and the two navigation surfaces.
   Screen entry owns data. Neither layer needs to know the other's implementation. */
globalThis.HeadStartNavigation = {
  create({ document, window, location, storage, onEnter }) {
    const node = id => document.getElementById(id);
    let shown = null, source = 'history', focusTarget = null;
    const name = () => location.hash.replace('#', '').split('?')[0];
    const anchor = () => name() && !node('panel-' + name()) ? node(name()) : null;
    const current = () => node('panel-' + name()) ? name()
      : anchor()?.closest('.panel')?.id.slice(6) || 'home';

    function menu(open, restore = false) {
      const button = node('nav-more'), panel = node('mobile-menu');
      if (!button || !panel) return;
      panel.hidden = !open;
      button.setAttribute('aria-expanded', String(open));
      if (restore) button.focus?.();
    }
    function drawFold() {
      const button = node('nav-toggle');
      if (!button) return;
      const folded = document.documentElement.dataset.nav === 'collapsed';
      button.setAttribute('aria-expanded', String(!folded));
      button.setAttribute('data-tip', folded ? 'Expand navigation' : 'Collapse navigation');
    }
    function keepFocusVisible(target) {
      const bar = document.querySelector('.mobile-nav');
      if (!target?.closest?.('.shell') || target.closest('.mobile-nav') || !bar?.getClientRects().length) return;
      // A results list or heading is a reading target, not a control to expose in full.
      // Scrolling its bottom into view would jump past the first jobs after a hand-off.
      if (!target.matches('a,button,input,select,textarea,summary,[role="button"]')) return;
      const viewport = window.visualViewport;
      const bottom = Math.min(bar.getBoundingClientRect().top, viewport ? viewport.offsetTop + viewport.height : window.innerHeight);
      const bounds = target.getBoundingClientRect();
      if (bounds.bottom > bottom - 12) window.scrollBy(0, bounds.bottom - bottom + 16);
    }
    function sync(reason) {
      const screen = current(), changed = shown !== screen;
      document.querySelectorAll('.panel').forEach(panel => { panel.hidden = panel.id !== 'panel-' + screen; });
      document.querySelectorAll('.tabs [data-tab]').forEach(link => {
        link.setAttribute('aria-current', link.dataset.tab === screen ? 'page' : 'false');
      });
      document.documentElement.dataset.screen = screen;
      const secondary = node('mobile-menu')?.querySelector(`[data-tab="${screen}"]`);
      const more = node('nav-more');
      more?.setAttribute('data-current', secondary ? 'true' : 'false');
      more?.setAttribute('aria-label', secondary ? 'More sections, ' + secondary.getAttribute('aria-label') + ' selected' : 'More sections');
      menu(false);
      onEnter({ screen, source: reason, hash: location.hash });
      const target = anchor();
      if (target) {
        target.setAttribute('tabindex', '-1');
        target.scrollIntoView?.({ block: 'start' });
        target.focus?.({ preventScroll: true });
      }
      else if (changed && reason !== 'load') window.scrollTo(0, 0);
      if (reason !== 'load' && !target) {
        const heading = node('panel-' + screen)?.querySelector('[data-screen-heading], h2');
        const focus = focusTarget && node(focusTarget) || heading;
        focus?.setAttribute('tabindex', '-1');
        if (focusTarget) (focus?.closest?.('[data-focus-context]') || focus)?.scrollIntoView?.({ block: 'start' });
        focus?.focus?.({ preventScroll: true });
      }
      shown = screen; source = 'history'; focusTarget = null;
    }
    function navigate(hash, { focus = null } = {}) {
      source = 'handoff'; focusTarget = focus;
      if (location.hash === hash) sync(source);
      else location.hash = hash;
    }
    function start() {
      drawFold();
      node('nav-toggle')?.addEventListener('click', () => {
        const folded = document.documentElement.dataset.nav !== 'collapsed';
        if (folded) document.documentElement.dataset.nav = 'collapsed';
        else delete document.documentElement.dataset.nav;
        try { storage.setItem('hs.navCollapsed', folded ? '1' : ''); } catch (e) {}
        drawFold();
      });
      node('nav-more')?.addEventListener('click', () => menu(node('mobile-menu').hidden));
      document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && node('mobile-menu') && !node('mobile-menu').hidden) menu(false, true);
      });
      document.addEventListener('focusin', event => {
        if (!event.target.closest?.('.mobile-nav')) menu(false);
        keepFocusVisible(event.target);
      });
      window.visualViewport?.addEventListener('resize', () => keepFocusVisible(document.activeElement));
      document.addEventListener('click', event => {
        const link = event.target.closest?.('.tabs [data-tab]');
        if (link && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
          if (link.getAttribute('href') !== location.hash) source = 'navigation';
          menu(false);
        } else if (!event.target.closest?.('.mobile-nav')) menu(false);
      }, true);
      window.addEventListener('hashchange', () => sync(source));
      sync('load');
    }
    return { current, navigate, start };
  },
};
