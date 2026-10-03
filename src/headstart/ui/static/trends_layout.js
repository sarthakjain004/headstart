/* Keep the new coverage summary before the desktop plot and after it on a phone.
   Moving the native region keeps visual and keyboard reading order together. */
(() => {
  const summary = document.getElementById('trends-coverage-summary');
  if (!summary) return; // Trends is capability-gated by the renderer.
  const plot = document.getElementById('trends-viz');
  const place = document.createComment('desktop coverage summary');
  summary.before(place);
  const compact = matchMedia('(max-width:760px)');
  function adapt() {
    const focused = document.activeElement;
    const inside = summary.contains(focused);
    if (compact.matches) plot.after(summary);
    else place.after(summary);
    if (inside) focused.focus({ preventScroll: true });
  }
  compact.addEventListener('change', adapt);
  adapt();
})();
