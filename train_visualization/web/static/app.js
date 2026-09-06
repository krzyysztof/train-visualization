/*
 * Core: Leaflet map, shared state, event bus and the position polling loop.
 *
 * Feature modules (loaded after this file) subscribe with App.on(event, fn):
 *   'meta'      (meta)            -> /api/meta loaded: operators [{code,name,color,count}], focus_bbox, feed dates
 *   'positions' (data)            -> every poll: {time: "HH:MM:SS", simulated, trains: [...]} (see server.py for fields)
 *   'select'    (tripIdx | null)  -> the selected train changed (null = deselected)
 *   'time'      ("HH:MM" | null)  -> simulated time changed (null = live clock)
 *   'filter'    (Set of op codes) -> hidden operators changed
 *   'zoom'      (zoomLevel)       -> map zoom finished
 *   'move'      (bounds)          -> map pan/zoom finished
 *   'follow'    (boolean)         -> emitted by follow.js: is the map now tracking the selected train
 *
 * Shared state lives in App.state; modules must not mutate it directly — use the
 * setters below so the matching event fires for everyone.
 */
window.App = (function () {
  var POLL_MS = 1000;
  var listeners = {};
  var state = {
    meta: null,          // /api/meta payload
    positions: [],       // latest trains array
    time: null,          // "HH:MM:SS" of the latest positions payload
    simulated: false,
    selected: null,      // trip_idx or null
    simTime: null,       // "HH:MM" or null (live clock)
    hiddenOps: new Set() // operator codes currently filtered out
  };

  function on(event, fn) {
    if (!listeners[event]) listeners[event] = [];
    listeners[event].push(fn);
  }

  function emit(event, payload) {
    (listeners[event] || []).forEach(function (fn) {
      try { fn(payload); } catch (err) { console.error('[' + event + ']', err); }
    });
  }

  var map = L.map('map', { preferCanvas: true, zoomControl: true }).setView([52.23, 21.01], 9);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);
  map.on('zoomend', function () { emit('zoom', map.getZoom()); });
  map.on('moveend', function () { emit('move', map.getBounds()); });

  var statusEl = document.getElementById('status');
  function setStatus(text, isError) {
    statusEl.textContent = text;
    statusEl.classList.toggle('status--error', !!isError);
  }

  function api(path) {
    return fetch(path, { cache: 'no-store' }).then(function (response) {
      if (!response.ok) throw new Error(path + ' -> HTTP ' + response.status);
      return response.json();
    });
  }

  function timeQuery() {
    return state.simTime ? '?t=' + encodeURIComponent(state.simTime) : '';
  }

  function select(tripIdx) {
    if (state.selected === tripIdx) return;
    state.selected = tripIdx;
    emit('select', tripIdx);
  }

  function setSimTime(hhmm) {
    state.simTime = hhmm;
    emit('time', hhmm);
    poll();
  }

  function setOperatorHidden(code, hidden) {
    if (hidden) state.hiddenOps.add(code); else state.hiddenOps.delete(code);
    emit('filter', state.hiddenOps);
  }

  var polling = false;
  function poll() {
    if (polling) return Promise.resolve();
    polling = true;
    return api('/api/positions' + timeQuery())
      .then(function (data) {
        state.positions = data.trains;
        state.time = data.time;
        state.simulated = data.simulated;
        emit('positions', data);
        var visible = data.trains.filter(function (t) { return !state.hiddenOps.has(t.op); }).length;
        setStatus((data.simulated ? 'Symulacja ' : '') + data.time + ' · pociągów w trasie: ' + visible +
          (visible !== data.trains.length ? ' (z ' + data.trains.length + ')' : ''));
      })
      .catch(function (err) {
        console.error(err);
        setStatus('Brak połączenia z serwerem — sprawdź, czy aplikacja nadal działa', true);
      })
      .then(function () { polling = false; });
  }

  function fitToFocus(bbox) {
    // Leaflet computes the zoom from the container size, so a fit issued before the
    // browser has laid the pane out lands on zoom 0 (the whole world). Re-measure first,
    // and if the container is still unsized, wait for it rather than fitting to nothing.
    map.invalidateSize(false);
    var size = map.getSize();
    if (size.x < 50 || size.y < 50) {
      requestAnimationFrame(function () { fitToFocus(bbox); });
      return;
    }
    map.fitBounds([[bbox[1], bbox[0]], [bbox[3], bbox[2]]]);
  }

  function start() {
    api('/api/meta').then(function (meta) {
      state.meta = meta;
      if (meta.focus_bbox) fitToFocus(meta.focus_bbox);
      emit('meta', meta);
      poll();
      setInterval(poll, POLL_MS);
    }).catch(function (err) {
      console.error(err);
      setStatus('Nie udało się wczytać danych — odśwież stronę', true);
    });
  }

  window.addEventListener('resize', function () { map.invalidateSize(false); });
  document.addEventListener('DOMContentLoaded', start);

  return {
    map: map,
    state: state,
    on: on,
    emit: emit,
    api: api,
    timeQuery: timeQuery,
    select: select,
    setSimTime: setSimTime,
    setOperatorHidden: setOperatorHidden,
    poll: poll,
    setStatus: setStatus
  };
})();
