/*
 * Module: controls — search box (#search, /api/search), time slider (#time, App.setSimTime),
 * operator filter checkboxes (#filters, App.setOperatorHidden) and a colour legend on the map.
 *
 * Every change goes through App's setters and every widget reacts to App's events, so the
 * controls stay in sync with changes made elsewhere (marker clicks, panel actions).
 */
(function () {
  var LEGEND_TOP = 8;            // operators shown in the legend before "+N innych"
  var SLIDER_DEBOUNCE_MS = 150;  // slider drag -> App.setSimTime
  var MIN_PAN_ZOOM = 11;         // zoom used when panning to a search result
  var opsByCode = {};            // code -> {code, name, color, count} from /api/meta

  App.on('meta', function (meta) {
    opsByCode = {};
    meta.operators.forEach(function (op) { opsByCode[op.code] = op; });
  });

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function operator(code) {
    return opsByCode[code] || { code: code, name: code, color: '' };
  }

  // Small square in the operator colour (falls back to the CSS default when unknown).
  function chip(code) {
    var node = el('span', 'ct-chip');
    var color = operator(code).color;
    if (color) node.style.background = color;
    return node;
  }

  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function minutesToHHMM(minutes) { return pad2(Math.floor(minutes / 60)) + ':' + pad2(minutes % 60); }
  function hhmmToMinutes(text) {
    var parts = text.split(':');
    return Number(parts[0]) * 60 + Number(parts[1]);
  }

  /* ------------------------------------------------------------------ search */
  function initSearch() {
    var root = document.getElementById('search');
    var form = el('form', 'ct-search');
    var input = el('input', 'ct-search-input');
    input.type = 'search';
    input.placeholder = 'Numer pociągu lub kierunek…';
    input.autocomplete = 'off';
    input.setAttribute('aria-label', 'Numer pociągu lub kierunek');
    var button = el('button', 'ct-btn', 'Szukaj');
    button.type = 'submit';
    var list = el('ol', 'ct-results');
    form.appendChild(input);
    form.appendChild(button);
    root.appendChild(form);
    root.appendChild(list);

    var lastQuery = '';
    var lastResults = [];
    var requestSeq = 0;  // ignores responses of superseded requests

    function clear() {
      lastQuery = '';
      lastResults = [];
      requestSeq++;
      list.innerHTML = '';
    }

    function tagFor(result) {
      if (result.running) return el('span', 'ct-tag ct-tag--run', 'w trasie');
      if (result.today) return el('span', 'ct-tag ct-tag--today', 'dziś');
      return el('span', 'ct-tag ct-tag--off', 'nie kursuje dziś');
    }

    function render(results) {
      lastResults = results.slice(0, 20);
      list.innerHTML = '';
      if (!lastResults.length) {
        list.appendChild(el('li', 'ct-empty muted', 'Brak wyników'));
        return;
      }
      lastResults.forEach(function (r) {
        var row = el('li', 'ct-result' + (r.today ? '' : ' ct-result--off') +
          (r.trip_idx === App.state.selected ? ' ct-result--selected' : ''));
        row.dataset.idx = r.trip_idx;
        row.tabIndex = 0;
        row.setAttribute('role', 'button');
        row.title = r.origin + ' → ' + r.dest;
        row.appendChild(chip(r.op));
        var label = el('span', 'ct-result-label');
        label.appendChild(el('b', 'ct-result-num', r.route + ' ' + r.num));
        label.appendChild(document.createTextNode(' → ' + r.dest));
        row.appendChild(label);
        row.appendChild(tagFor(r));
        list.appendChild(row);
      });
    }

    function run(query) {
      lastQuery = query;
      var seq = ++requestSeq;
      var timeQuery = App.timeQuery();  // '?t=HH:MM' or '' — becomes the first parameter
      App.api('/api/search' + (timeQuery ? timeQuery + '&' : '?') + 'q=' + encodeURIComponent(query))
        .then(function (data) {
          if (seq === requestSeq) render(data.results);
        })
        .catch(function (err) {
          console.error(err);
          if (seq !== requestSeq) return;
          list.innerHTML = '';
          list.appendChild(el('li', 'ct-empty muted', 'Błąd wyszukiwania'));
        });
    }

    function panTo(lat, lon) {
      App.map.setView([lat, lon], Math.max(App.map.getZoom(), MIN_PAN_ZOOM));
    }

    function choose(result) {
      App.select(result.trip_idx);
      if (!result.running) return;
      var pos = App.state.positions.find(function (t) { return t.trip_idx === result.trip_idx; });
      if (pos) {
        panTo(pos.lat, pos.lon);
        return;
      }
      // Not in the latest poll yet (e.g. it started since) — ask the server where it is now.
      App.api('/api/trip/' + result.trip_idx + App.timeQuery())
        .then(function (trip) { if (trip.current) panTo(trip.current.lat, trip.current.lon); })
        .catch(function (err) { console.error(err); });
    }

    function resultAt(target) {
      var row = target.closest('.ct-result');
      if (!row) return null;
      var idx = Number(row.dataset.idx);
      return lastResults.find(function (r) { return r.trip_idx === idx; }) || null;
    }

    function submit(e) {
      e.preventDefault();
      var query = input.value.trim();
      if (query) run(query); else clear();
    }
    form.addEventListener('submit', submit);
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') submit(e);  // explicit, so Enter works even without implicit form submission
    });
    input.addEventListener('input', function () {
      if (!input.value.trim()) clear();
    });
    list.addEventListener('click', function (e) {
      var result = resultAt(e.target);
      if (result) choose(result);
    });
    list.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var result = resultAt(e.target);
      if (!result) return;
      e.preventDefault();
      choose(result);
    });

    App.on('select', function (tripIdx) {
      Array.prototype.forEach.call(list.children, function (row) {
        row.classList.toggle('ct-result--selected', Number(row.dataset.idx) === tripIdx);
      });
    });
    // "w trasie" / "dziś" depend on the (simulated) time — refresh the list when it changes.
    App.on('time', function () {
      if (lastQuery) run(lastQuery);
    });
  }

  /* ------------------------------------------------------------- time slider */
  function initTime() {
    var root = document.getElementById('time');
    var wrap = el('div', 'ct-time');
    var head = el('div', 'ct-time-head');
    var label = el('span', 'ct-time-label', 'Wczytywanie…');
    var nowButton = el('button', 'ct-btn ct-btn--ghost', 'Teraz');
    nowButton.type = 'button';
    nowButton.title = 'Wróć do bieżącego czasu';
    nowButton.disabled = true;
    var slider = el('input', 'ct-time-slider');
    slider.type = 'range';
    slider.min = '0';
    slider.max = '1439';
    slider.step = '1';
    slider.value = '0';
    slider.setAttribute('aria-label', 'Godzina symulacji');
    var scale = el('div', 'ct-time-scale');
    ['00:00', '06:00', '12:00', '18:00', '24:00'].forEach(function (t) { scale.appendChild(el('span', null, t)); });
    head.appendChild(label);
    head.appendChild(nowButton);
    wrap.appendChild(head);
    wrap.appendChild(slider);
    wrap.appendChild(scale);
    root.appendChild(wrap);

    var liveHHMM = null;  // last "HH:MM" of the live clock seen from the server
    var timer = null;     // pending debounced App.setSimTime

    function showLive() {
      wrap.classList.remove('ct-time--sim');
      nowButton.disabled = true;
      if (!liveHHMM) return;
      slider.value = hhmmToMinutes(liveHHMM);
      label.textContent = 'Na żywo ' + liveHHMM;
    }

    function showSim(hhmm) {
      wrap.classList.add('ct-time--sim');
      nowButton.disabled = false;
      slider.value = hhmmToMinutes(hhmm);
      label.textContent = 'Symulacja ' + hhmm;
    }

    function setLive(hhmmss) {
      liveHHMM = hhmmss.slice(0, 5);
      if (!App.state.simTime && !timer) showLive();
    }

    slider.addEventListener('input', function () {
      var hhmm = minutesToHHMM(Number(slider.value));
      // Immediate feedback; the actual state change is debounced.
      wrap.classList.add('ct-time--sim');
      nowButton.disabled = false;
      label.textContent = 'Symulacja ' + hhmm;
      clearTimeout(timer);
      timer = setTimeout(function () {
        timer = null;
        App.setSimTime(hhmm);
      }, SLIDER_DEBOUNCE_MS);
    });
    nowButton.addEventListener('click', function () {
      clearTimeout(timer);
      timer = null;
      App.setSimTime(null);
    });

    App.on('time', function (hhmm) {
      clearTimeout(timer);
      timer = null;
      if (hhmm) showSim(hhmm); else showLive();
    });
    App.on('meta', function (meta) { setLive(meta.now); });
    App.on('positions', function (data) {
      if (!data.simulated) setLive(data.time);
    });
  }

  /* -------------------------------------------------------- operator filters */
  function initFilters() {
    var root = document.getElementById('filters');
    var head = el('div', 'ct-filters-head');
    head.appendChild(el('span', 'ct-section-title', 'Przewoźnicy'));
    var links = el('span', 'ct-filters-links');
    var allLink = el('a', 'ct-link', 'wszystkie');
    allLink.href = '#';
    var noneLink = el('a', 'ct-link', 'żaden');
    noneLink.href = '#';
    links.appendChild(allLink);
    links.appendChild(document.createTextNode(' · '));
    links.appendChild(noneLink);
    head.appendChild(links);
    var list = el('ul', 'ct-filters-list');
    root.appendChild(head);
    root.appendChild(list);

    var boxes = {};  // code -> checkbox

    function setAll(hidden) {
      Object.keys(boxes).forEach(function (code) {
        if (App.state.hiddenOps.has(code) !== hidden) App.setOperatorHidden(code, hidden);
      });
    }

    function reflect(hidden) {
      Object.keys(boxes).forEach(function (code) {
        boxes[code].checked = !hidden.has(code);
        boxes[code].closest('.ct-filter').classList.toggle('ct-filter--hidden', hidden.has(code));
      });
    }

    allLink.addEventListener('click', function (e) { e.preventDefault(); setAll(false); });
    noneLink.addEventListener('click', function (e) { e.preventDefault(); setAll(true); });

    App.on('meta', function (meta) {
      list.innerHTML = '';
      boxes = {};
      meta.operators.forEach(function (op) {
        var item = el('li', 'ct-filter');
        var lab = el('label', 'ct-filter-label');
        var box = el('input');
        box.type = 'checkbox';
        box.checked = !App.state.hiddenOps.has(op.code);
        box.addEventListener('change', function () { App.setOperatorHidden(op.code, !box.checked); });
        lab.appendChild(box);
        lab.appendChild(chip(op.code));
        lab.appendChild(el('span', 'ct-filter-name', op.name));
        lab.appendChild(el('span', 'ct-filter-count muted', '(' + op.count + ')'));
        item.appendChild(lab);
        list.appendChild(item);
        boxes[op.code] = box;
      });
      reflect(App.state.hiddenOps);
    });
    App.on('filter', reflect);
  }

  /* ------------------------------------------------------------------ legend */
  function othersLabel(n) {
    var tens = n % 100;
    var ones = n % 10;
    if (n === 1) return '+1 inny';
    if (ones >= 2 && ones <= 4 && (tens < 12 || tens > 14)) return '+' + n + ' inne';
    return '+' + n + ' innych';
  }

  function initLegend() {
    var container = L.DomUtil.create('div', 'ct-legend');
    L.DomEvent.disableClickPropagation(container);
    L.DomEvent.disableScrollPropagation(container);
    var control = L.control({ position: 'bottomright' });
    control.onAdd = function () { return container; };
    control.addTo(App.map);
    container.hidden = true;

    var entries = [];     // [{code, count}] sorted by count desc — operators with trains right now
    var signature = '';   // ordered codes of the last render
    var expanded = false;

    function itemTitle(code) {
      return (App.state.hiddenOps.has(code) ? 'Pokaż ' : 'Ukryj ') + operator(code).name;
    }

    function render() {
      container.innerHTML = '';
      container.hidden = !entries.length;
      if (!entries.length) return;
      container.appendChild(el('div', 'ct-legend-title', 'Przewoźnicy w trasie'));
      var shown = expanded ? entries : entries.slice(0, LEGEND_TOP);
      shown.forEach(function (entry) {
        var op = operator(entry.code);
        var item = el('button', 'ct-legend-item' + (App.state.hiddenOps.has(entry.code) ? ' ct-legend-item--hidden' : ''));
        item.type = 'button';
        item.dataset.code = entry.code;
        item.title = itemTitle(entry.code);
        var arrow = el('span', 'ct-legend-arrow');
        if (op.color) arrow.style.background = op.color;
        item.appendChild(arrow);
        item.appendChild(el('span', 'ct-legend-name', op.name));
        item.addEventListener('click', function () {
          App.setOperatorHidden(entry.code, !App.state.hiddenOps.has(entry.code));
        });
        container.appendChild(item);
      });
      if (entries.length > LEGEND_TOP) {
        var more = el('a', 'ct-link ct-legend-more', expanded ? 'mniej' : othersLabel(entries.length - shown.length));
        more.href = '#';
        more.addEventListener('click', function (e) {
          e.preventDefault();
          expanded = !expanded;
          render();
        });
        container.appendChild(more);
      }
    }

    App.on('positions', function (data) {
      var counts = {};
      data.trains.forEach(function (t) { counts[t.op] = (counts[t.op] || 0) + 1; });
      var sorted = Object.keys(counts)
        .map(function (code) { return { code: code, count: counts[code] }; })
        .sort(function (a, b) { return b.count - a.count || a.code.localeCompare(b.code); });
      var next = sorted.map(function (e) { return e.code; }).join(',');
      if (next === signature) return;  // same operators in the same order — nothing to redraw
      signature = next;
      entries = sorted;
      render();
    });
    App.on('filter', function (hidden) {
      Array.prototype.forEach.call(container.querySelectorAll('.ct-legend-item'), function (item) {
        item.classList.toggle('ct-legend-item--hidden', hidden.has(item.dataset.code));
        item.title = itemTitle(item.dataset.code);
      });
    });
  }

  initSearch();
  initTime();
  initFilters();
  initLegend();
})();
