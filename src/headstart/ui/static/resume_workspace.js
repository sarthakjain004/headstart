/* Keep the writing checks available without giving them the first phone screen. */
(() => {
  const checks = document.getElementById('rb-checks-disclosure');
  const compact = matchMedia('(max-width:1000px)');
  function adapt() {
    const focused = document.activeElement;
    const inside = checks.contains(focused);
    checks.open = !compact.matches;
    if (inside && compact.matches) checks.querySelector('summary').focus({ preventScroll: true });
  }
  compact.addEventListener('change', adapt);
  adapt();
})();
