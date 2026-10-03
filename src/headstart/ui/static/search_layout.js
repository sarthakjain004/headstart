/* Move the actual common filter fields, never copies. Values, labels, native focus and
   existing listeners survive resize. Advanced filters remain a labelled disclosure. */
(() => {
  const quick = document.getElementById('quick-filters');
  const disclosure = document.getElementById('search-filters');
  const fields = ['country', 'maxyears', 'remote'].map(id => {
    const field = document.getElementById(id).closest('.f');
    const place = document.createComment('filter home: ' + id);
    field.before(place);
    return { field, place };
  });
  const wide = matchMedia('(min-width:1100px)');
  function adapt() {
    const focused = document.activeElement;
    const commonFocus = fields.some(({ field }) => field.contains(focused));
    const advancedFocus = disclosure.contains(focused) && !commonFocus;
    fields.forEach(({ field, place }) => {
      if (wide.matches) place.after(field);
      else quick.append(field);
    });
    disclosure.open = wide.matches;
    if (commonFocus) focused.focus({ preventScroll: true });
    else if (advancedFocus && !wide.matches) disclosure.querySelector('summary').focus({ preventScroll: true });
  }
  wide.addEventListener('change', adapt);
  adapt();
})();
