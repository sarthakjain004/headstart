/* Home examples use the existing form hand-off in app.js, including focus and filters. */
document.querySelectorAll('[data-home-query]').forEach(button => {
  button.addEventListener('click', () => {
    document.getElementById('home-q').value = button.dataset.homeQuery;
    document.getElementById('home-search').requestSubmit();
  });
});
