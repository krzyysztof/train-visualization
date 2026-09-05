/*
 * Module: side panel details for the selected train (#details) — route with times, current
 * segment, ETA, a speed gauge + history sparkline and a whole-trip progress bar — plus the
 * route polyline drawn on the map. Uses /api/trip/<trip_idx>.
 *
 * 'select'    -> fetch the trip, render header + stop list, draw the route (or restore the placeholder)
 * 'positions' -> re-fetch the trip and update only the progress bits (highlighted row, next-station line)
 * 'time'      -> same refresh for the new simulated time
 * 'follow'    -> reflect follow.js's on/off state on the follow button
 * CSS prefix: pn-.
 */
(function () {
  var PLACEHOLDER = 'Kliknij pociąg na mapie, aby zobaczyć szczegóły.';
  var details = document.getElementById('details');

  var view = null;        // rendered trip: {trip, list, rows, next, hi, autoTop, userScrolled}
  var route = null;       // L.layerGroup with the polyline and stop markers
  var generation = 0;     // bumped on 'select' and 'time'; responses requested before that are dropped
  var wasRunning = false; // a non-null `current` was seen since the last select / time change
  var inflight = false;   // a progress refresh is in flight
  var queued = false;     // another refresh was requested meanwhile

  // ---- helpers -----------------------------------------------------------------

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function setContent(node, parts) {
    node.innerHTML = '';
    [].concat(parts).forEach(function (part) {
      node.appendChild(typeof part === 'string' ? document.createTextNode(part) : part);
    });
  }

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  // Seconds since midnight of the service day -> <span>HH:MM<sup>+1</sup></span>. Many stop times
  // are interpolated to the second, so they are rounded to the nearest minute.
  function timeNode(sec) {
    var minutes = Math.round(sec / 60);
    var day = Math.floor(minutes / 1440);
    var rest = minutes - day * 1440;
    var node = el('span', null, pad(Math.floor(rest / 60)) + ':' + pad(rest % 60));
    if (day > 0) node.appendChild(el('sup', null, '+' + day));
    return node;
  }

  function etaText(sec) {
    var minutes = Math.round(sec / 60);
    return minutes < 1 ? 'za chwilę' : 'za ' + minutes + ' min';
  }

  // Ring gauge for the current speed estimate; 100% = GAUGE_MAX_KMH (the fastest category's
  // cruise speed), so a slow regional train and a fast EIP visibly fill the ring differently.
  var GAUGE_MAX_KMH = 200;
  var SPARK_MAX_POINTS = 60; // ~60s of history at one poll/second
  function speedGauge(kmh) {
    var pct = Math.max(0, Math.min(100, (kmh / GAUGE_MAX_KMH) * 100));
    var ring = el('div', 'pn-gauge-ring');
    ring.style.setProperty('--pct', pct);
    var center = el('div', 'pn-gauge-center');
    center.appendChild(el('div', 'pn-gauge-num', String(Math.round(kmh))));
    center.appendChild(el('div', 'pn-gauge-unit', 'km/h'));
    ring.appendChild(center);
    var wrap = el('div', 'pn-gauge');
    wrap.appendChild(ring);
    return wrap;
  }

  // Speed history sparkline: an SVG area+line built fresh each update from `history`
  // (oldest first, capped in applyProgress), scaled 0..GAUGE_MAX_KMH like the gauge.
  var SPARK_W = 280, SPARK_H = 44, SPARK_PAD = 2;
  function renderSpark(history) {
    var svgNS = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + SPARK_W + ' ' + SPARK_H);
    svg.setAttribute('class', 'pn-spark-svg');
    svg.setAttribute('preserveAspectRatio', 'none');
    var n = history.length;
    if (n < 2) return svg;
    var pts = history.map(function (v, i) {
      var x = SPARK_PAD + (i / (n - 1)) * (SPARK_W - 2 * SPARK_PAD);
      var frac = Math.max(0, Math.min(1, v / GAUGE_MAX_KMH));
      var y = SPARK_H - SPARK_PAD - frac * (SPARK_H - 2 * SPARK_PAD);
      return x.toFixed(1) + ' ' + y.toFixed(1);
    });
    var line = document.createElementNS(svgNS, 'polyline');
    line.setAttribute('points', pts.join(' '));
    line.setAttribute('class', 'pn-spark-line');
    var area = document.createElementNS(svgNS, 'polyline');
    area.setAttribute('points', pts[0].split(' ')[0] + ' ' + SPARK_H + ' ' + pts.join(' ') +
      ' ' + pts[n - 1].split(' ')[0] + ' ' + SPARK_H);
    area.setAttribute('class', 'pn-spark-area');
    svg.appendChild(area);
    svg.appendChild(line);
    return svg;
  }

  function operatorInfo(code) {
    var ops = App.state.meta ? App.state.meta.operators : [];
    for (var i = 0; i < ops.length; i++) {
      if (ops[i].code === code) return ops[i];
    }
    var neutral = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim() || 'gray';
    return { code: code, name: code || 'Nieznany przewoźnik', color: neutral };
  }

  function closeButton() {
    var button = el('button', 'pn-close', '✕ Zamknij');
    button.type = 'button';
    button.addEventListener('click', function () { App.select(null); });
    return button;
  }

  function followButton() {
    var button = el('button', 'pn-follow', '');
    button.type = 'button';
    button.addEventListener('click', function () { Follow.toggle(); });
    reflectFollow(button);
    return button;
  }

  function reflectFollow(button) {
    var on = Follow.isFollowing();
    button.textContent = on ? '📍 Śledzenie' : '📍 Śledź';
    button.title = on ? 'Przestań śledzić pociąg' : 'Mapa podąży za pociągiem';
    button.classList.toggle('pn-follow--on', on);
  }

  function showMessage(text, closable) {
    view = null;
    details.innerHTML = '';
    details.appendChild(el('p', 'muted', text));
    if (closable) details.appendChild(closeButton());
  }

  // ---- stop list ---------------------------------------------------------------

  // First stop: departure; last: arrival; intermediate: "arr / dep" when the dwell is at least a
  // minute (so the two differ once rounded), otherwise just the departure.
  function stopTimeCell(stop, i, count) {
    var cell = el('span', 'pn-time');
    if (i === count - 1) {
      cell.appendChild(timeNode(stop.arr));
    } else if (i > 0 && stop.dep - stop.arr >= 60) {
      cell.appendChild(timeNode(stop.arr));
      cell.appendChild(document.createTextNode(' / '));
      cell.appendChild(timeNode(stop.dep));
    } else {
      cell.appendChild(timeNode(stop.dep));
    }
    return cell;
  }

  function render(data, op) {
    details.innerHTML = '';
    details.style.setProperty('--pn-color', op.color);

    var head = el('div', 'pn-head');
    head.appendChild(el('span', 'pn-chip'));
    var title = el('div', 'pn-title');
    title.appendChild(el('div', 'pn-op', op.name));
    title.appendChild(el('div', 'pn-train', (data.route ? data.route + ' ' : '') + data.num));
    title.appendChild(el('div', 'pn-rel', data.origin + ' → ' + data.dest));
    head.appendChild(title);
    var actions = el('div', 'pn-actions');
    actions.appendChild(closeButton());
    actions.appendChild(followButton());
    head.appendChild(actions);
    details.appendChild(head);

    var next = el('p', 'pn-next');
    details.appendChild(next);

    var speedLine = el('div', 'pn-speed');
    details.appendChild(speedLine);

    var sparkHolder = el('div', 'pn-spark-holder');
    var spark = el('div', 'pn-chart');
    spark.appendChild(el('div', 'pn-chart-title', 'Prędkość — ostatnie ~60 s'));
    spark.appendChild(sparkHolder);
    details.appendChild(spark);

    var firstDep = data.stops[0].dep;
    var tripSpan = data.stops[data.stops.length - 1].arr - firstDep;
    var progressTrack = el('div', 'pn-progress-track');
    var progressFill = el('div', 'pn-progress-fill');
    progressTrack.appendChild(progressFill);
    data.stops.forEach(function (stop, i) {
      if (i === 0 || i === data.stops.length - 1) return;
      var tick = el('span', 'pn-progress-tick');
      tick.style.left = (tripSpan > 0 ? (stop.arr - firstDep) / tripSpan * 100 : 0) + '%';
      tick.title = stop.name;
      progressTrack.appendChild(tick);
    });
    var progressNow = el('span', 'pn-progress-now');
    progressTrack.appendChild(progressNow);
    var progressWrap = el('div', 'pn-chart');
    progressWrap.appendChild(el('div', 'pn-chart-title', 'Postęp trasy'));
    progressWrap.appendChild(progressTrack);
    details.appendChild(progressWrap);

    var list = el('ol', 'pn-stops');
    var rows = data.stops.map(function (stop, i) {
      var row = el('li', 'pn-stop');
      row.appendChild(el('span', 'pn-dot'));
      row.appendChild(el('span', 'pn-name', stop.name));
      row.appendChild(stopTimeCell(stop, i, data.stops.length));
      list.appendChild(row);
      return row;
    });
    details.appendChild(list);

    view = {
      trip: data, list: list, rows: rows, next: next, speedLine: speedLine, hi: null, autoTop: 0, userScrolled: false,
      sparkHolder: sparkHolder, speedHistory: [],
      firstDep: firstDep, tripSpan: tripSpan, progressFill: progressFill, progressNow: progressNow
    };
    list.addEventListener('scroll', function () {
      // Anything other than our own programmatic scroll means the user took over.
      if (Math.abs(list.scrollTop - view.autoTop) > 1) view.userScrolled = true;
    });
    applyProgress(data.current);
    // The other panel sections push #details below the fold; reveal it (scrolls only the panel).
    details.scrollIntoView({ block: 'nearest' });
  }

  function scrollToRow(i) {
    var list = view.list;
    var row = view.rows[i];
    list.scrollTop = Math.max(0, row.offsetTop - (list.clientHeight - row.offsetHeight) / 2);
    view.autoTop = list.scrollTop;
  }

  // Updates only the progress-dependent bits: passed/current row classes and the next-station line.
  function applyProgress(current) {
    var stops = view.trip.stops;
    var last = stops.length - 1;
    var hi = -1;          // highlighted row: the stop we stand at, or the next one
    var passedBelow = 0;  // rows with index < passedBelow are muted
    var text;
    var idle = false;

    if (current) {
      wasRunning = true;
      hi = current.at_station ? current.stop_i : current.next_stop_i;
      passedBelow = hi;
      if (current.at_station && current.stop_i === last) {
        text = ['Stacja końcowa: ', el('strong', null, stops[last].name)];
      } else {
        var next = stops[current.next_stop_i];
        text = ['Następna stacja: ', el('strong', null, next.name), ' o ', timeNode(next.arr),
          ' (' + etaText(current.eta_sec) + ')'];
      }
    } else if (wasRunning) {
      passedBelow = stops.length;
      text = 'Pociąg zakończył bieg';
      idle = true;
    } else {
      text = 'Pociąg nie jest teraz w trasie';
      idle = true;
    }

    view.rows.forEach(function (row, i) {
      row.classList.toggle('pn-passed', i < passedBelow);
      row.classList.toggle('pn-current', i === hi);
    });
    setContent(view.next, text);
    view.next.classList.toggle('pn-idle', idle);

    if (current && !current.at_station) {
      setContent(view.speedLine, [
        speedGauge(current.speed_kmh),
        el('p', 'pn-speed-caption', 'prędkość szacowana (fizyka + rozkład), nie GPS')
      ]);
    } else if (current && current.at_station) {
      setContent(view.speedLine, el('p', 'pn-speed-caption pn-speed-stopped', '⏸ zatrzymany na stacji'));
    } else {
      setContent(view.speedLine, []);
    }

    if (current) {
      view.speedHistory.push(current.speed_kmh);
      if (view.speedHistory.length > SPARK_MAX_POINTS) view.speedHistory.shift();
      setContent(view.sparkHolder, [renderSpark(view.speedHistory)]);

      var pct = view.tripSpan > 0
        ? Math.max(0, Math.min(1, (current.now_sec - view.firstDep) / view.tripSpan)) * 100
        : 0;
      view.progressFill.style.width = pct + '%';
      view.progressNow.style.left = pct + '%';
    }

    if (hi !== view.hi) {
      var firstTime = view.hi === null;
      view.hi = hi;
      // Follow the train only until the user scrolls the list themselves.
      if (hi >= 0 && (firstTime || !view.userScrolled)) scrollToRow(hi);
    }
  }

  // ---- route on the map --------------------------------------------------------

  function clearRoute() {
    if (route) {
      App.map.removeLayer(route);
      route = null;
    }
  }

  function drawRoute(data, color) {
    var points = data.shape && data.shape.length
      ? data.shape
      : data.stops.map(function (s) { return [s.lon, s.lat]; });
    var latlngs = points.map(function (p) { return [p[1], p[0]]; });  // shape is [lon, lat]

    route = L.layerGroup().addTo(App.map);
    var line = L.polyline(latlngs, {
      color: color, weight: 4, opacity: 0.85, interactive: false, pane: 'overlayPane'
    }).addTo(route);
    line.bringToFront();
    // Added after bringToFront so the stop markers are drawn above the line.
    data.stops.forEach(function (stop, i) {
      var terminus = i === 0 || i === data.stops.length - 1;
      L.circleMarker([stop.lat, stop.lon], {
        radius: terminus ? 5 : 3.5, color: color, weight: 2, fillColor: '#fff', fillOpacity: 1, pane: 'overlayPane'
      }).bindTooltip(stop.name, { direction: 'top', offset: [0, -4] }).addTo(route);
    });
  }

  // ---- data flow -----------------------------------------------------------------

  function tripUrl(tripIdx) { return '/api/trip/' + tripIdx + App.timeQuery(); }

  function onSelect(tripIdx) {
    generation++;
    wasRunning = false;
    queued = false;
    clearRoute();
    if (tripIdx === null || tripIdx === undefined) {
      showMessage(PLACEHOLDER);
      return;
    }
    showMessage('Wczytywanie szczegółów pociągu…');
    var gen = generation;
    App.api(tripUrl(tripIdx))
      .then(function (data) {
        if (gen !== generation) return;
        var op = operatorInfo(data.op);
        render(data, op);
        drawRoute(data, op.color);
      })
      .catch(function (err) {
        if (gen !== generation) return;
        console.error(err);
        showMessage('Nie udało się wczytać szczegółów pociągu.', true);
      });
  }

  function refresh() {
    if (!view) return;
    if (inflight) {
      queued = true;
      return;
    }
    inflight = true;
    var gen = generation;
    App.api(tripUrl(view.trip.trip_idx))
      .then(function (data) {
        if (gen === generation && view) applyProgress(data.current);
      })
      .catch(function (err) { console.error(err); })
      .then(function () {
        inflight = false;
        if (queued) {
          queued = false;
          refresh();
        }
      });
  }

  App.on('select', onSelect);
  App.on('positions', function () { refresh(); });
  App.on('follow', function () {
    var button = details.querySelector('.pn-follow');
    if (button) reflectFollow(button);
  });
  App.on('time', function () {
    generation++;        // a response for the old clock must not be applied any more
    wasRunning = false;  // the clock jumped, so "finished" can no longer be inferred
    refresh();
  });
})();
