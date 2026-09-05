/*
 * Module: light/dark theme toggle (#theme-toggle). The initial theme is set synchronously by
 * an inline <script> in index.html (before first paint); this just wires the button and
 * persists the choice. Map tiles are inverted via CSS (see app.css) — no tile reload needed.
 */
(function () {
  var button = document.getElementById('theme-toggle');

  function isDark() { return document.documentElement.dataset.theme === 'dark'; }

  function apply(dark) {
    if (dark) document.documentElement.dataset.theme = 'dark';
    else delete document.documentElement.dataset.theme;
    button.textContent = dark ? '☀️' : '🌙';
    button.title = dark ? 'Przełącz na jasny motyw' : 'Przełącz na ciemny motyw';
  }

  button.addEventListener('click', function () {
    var dark = !isDark();
    localStorage.setItem('theme', dark ? 'dark' : 'light');
    apply(dark);
  });

  apply(isDark());
})();
