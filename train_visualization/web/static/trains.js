/*
 * Train markers: one per train from App 'positions', coloured by operator, rotated by bearing,
 * clickable (App.select), diffed by trip_idx per poll and glided to the new position over
 * TWEEN_MS (dropped during map zoom). Also draws a fading trail behind the selected train.
 * Public: window.TrainMarkers.getMarker/count/lastUpdateMs.
 */
window.TrainMarkers = (function () {
  var LABEL_ZOOM = 11;          // labels for every train from this zoom level up
  var TWEEN_MS = 1000;          // matches the poll interval so trains move continuously
  var SNAP_DEG2 = 0.009 * 0.009; // ~1 km: bigger jumps (time slider, tab was asleep) are not animated
  var SELECTED_Z = 100000;
  var FALLBACK_COLOR = '#7f7f7f';
  var TRAIL_MAX_POINTS = 45;    // ~45s of history at one poll/second
  var TRAIL_MAX_OPACITY = 0.85; // most recent segment; fades toward 0 for older ones
  var TRAIL_COLOR = '#e0a800';  // fixed gold (matches the selected-marker accent), not the
                                 // operator colour — the route line ahead is already drawn in
                                 // that colour, so a same-colour trail would be invisible on it

  var map = App.map;
  var markers = new Map();      // trip_idx -> rec
  var tweens = [];              // recs currently animating (rec.tweening === true)
  var rafId = null;
  var zooming = false;
  var generation = 0;
  var selectedIdx = null;
  var colors = {};              // operator code -> colour, from /api/meta
  var trailLayer = L.layerGroup().addTo(map);
  var trailHistory = [];        // [[lat, lon], ...] for the selected train only, oldest first

  var template = document.createElement('div');
  template.className = 'tr-train';
  template.innerHTML =
    '<svg class="tr-arrow" viewBox="-11 -11 22 22" aria-hidden="true">' +
      '<circle class="tr-ring" r="9.5"/>' +
      '<path class="tr-tri" d="M0,-8.5 L6.5,7.5 L0,4 L-6.5,7.5 Z"/>' +
      '<circle class="tr-circ" r="5.5"/>' +
    '</svg><span class="tr-label"></span>';

  function labelText(t) {
    return (t.route ? t.route + ' ' : '') + t.num;
  }

  function place(rec, lat, lon) {
    rec.pos.lat = lat;
    rec.pos.lng = lon;
    L.DomUtil.setPosition(rec.el, map.latLngToLayerPoint(rec.pos));
  }

  function setBearing(rec, bearing) {
    if (bearing === rec.bearing) return;
    rec.bearing = bearing;
    if (bearing === null) {
      rec.node.classList.add('tr-dot');
      rec.arrow.style.transform = '';
    } else {
      rec.node.classList.remove('tr-dot');
      rec.arrow.style.transform = 'rotate(' + bearing.toFixed(1) + 'deg)';
    }
  }

  function setAtStation(rec, atStation) {
    if (atStation === rec.atStation) return;
    rec.atStation = atStation;
    rec.node.classList.toggle('tr-at', atStation);
  }

  function setSelected(rec, on) {
    rec.node.classList.toggle('tr-selected', on);
    rec.marker.setZIndexOffset(on ? SELECTED_Z : 0);
  }

  function clearTrail() {
    trailHistory = [];
    trailLayer.clearLayers();
  }

  function redrawTrail() {
    trailLayer.clearLayers();
    var n = trailHistory.length;
    for (var i = 1; i < n; i++) {
      L.polyline([trailHistory[i - 1], trailHistory[i]], {
        color: TRAIL_COLOR, weight: 4, opacity: (i / n) * TRAIL_MAX_OPACITY, interactive: false
      }).addTo(trailLayer);
    }
  }

  function pushTrail(t) {
    trailHistory.push([t.lat, t.lon]);
    if (trailHistory.length > TRAIL_MAX_POINTS) trailHistory.shift();
    redrawTrail();
  }

  // Refreshed every poll — a stale speed would mislead once the train slows/stops.
  function setSpeedTitle(rec, t) {
    var rounded = t.at_station ? 0 : Math.round(t.speed_kmh);
    if (rounded === rec.speedKmh && t.at_station === rec.atStationForTitle) return;
    rec.speedKmh = rounded;
    rec.atStationForTitle = t.at_station;
    var suffix = t.at_station ? ' (na stacji)' : ' (≈ ' + rounded + ' km/h)';
    rec.el.title = labelText(t) + ' → ' + t.dest + suffix;
  }

  function create(t) {
    var node = template.cloneNode(true);
    node.style.setProperty('--tr-c', colors[t.op] || FALLBACK_COLOR);
    node.lastChild.textContent = labelText(t);
    var marker = L.marker([t.lat, t.lon], {
      icon: L.divIcon({ className: 'tr-marker', html: node, iconSize: null, iconAnchor: [0, 0] }),
      keyboard: false
    }).addTo(map);
    marker.on('click', function () { App.select(t.trip_idx); });
    var rec = {
      marker: marker,
      el: marker.getElement(),
      node: node,
      arrow: node.firstChild,
      pos: L.latLng(t.lat, t.lon),
      curLat: t.lat, curLon: t.lon,     // what is drawn right now
      toLat: t.lat, toLon: t.lon,       // where the server says the train is
      fromLat: t.lat, fromLon: t.lon, t0: 0,
      tweening: false,
      bearing: undefined,
      atStation: undefined,
      speedKmh: undefined,
      atStationForTitle: undefined,
      gen: 0
    };
    setBearing(rec, t.bearing);
    setAtStation(rec, t.at_station);
    setSpeedTitle(rec, t);
    if (t.trip_idx === selectedIdx) setSelected(rec, true);
    return rec;
  }

  function moveTo(rec, lat, lon, now) {
    var dLat = lat - rec.curLat, dLon = (lon - rec.curLon) * 0.62; // cos(52°)
    rec.toLat = lat;
    rec.toLon = lon;
    rec.marker.setLatLng([lat, lon]); // Leaflet's model is always the true position (zoom uses it)
    if (zooming || dLat * dLat + dLon * dLon > SNAP_DEG2) {
      rec.curLat = lat;
      rec.curLon = lon;
      rec.tweening = false;
      return;
    }
    rec.fromLat = rec.curLat;
    rec.fromLon = rec.curLon;
    rec.t0 = now;
    place(rec, rec.curLat, rec.curLon); // undo setLatLng's jump; the tween takes over from here
    if (!rec.tweening) {
      rec.tweening = true;
      tweens.push(rec);
    }
    if (rafId === null) rafId = requestAnimationFrame(tick);
  }

  function tick(now) {
    rafId = null;
    var keep = 0;
    for (var i = 0; i < tweens.length; i++) {
      var rec = tweens[i];
      if (!rec.tweening) continue;
      var p = (now - rec.t0) / TWEEN_MS;
      if (p >= 1) {
        rec.tweening = false;
        place(rec, rec.toLat, rec.toLon);
        rec.curLat = rec.toLat;
        rec.curLon = rec.toLon;
      } else {
        if (p < 0) p = 0;
        rec.curLat = rec.fromLat + (rec.toLat - rec.fromLat) * p;
        rec.curLon = rec.fromLon + (rec.toLon - rec.fromLon) * p;
        place(rec, rec.curLat, rec.curLon);
        tweens[keep++] = rec;
      }
    }
    tweens.length = keep;
    if (keep) rafId = requestAnimationFrame(tick);
  }

  function cancelTweens() {
    for (var i = 0; i < tweens.length; i++) {
      var rec = tweens[i];
      rec.tweening = false;
      rec.curLat = rec.toLat;
      rec.curLon = rec.toLon;
    }
    tweens.length = 0;
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
  }

  function remove(rec) {
    rec.tweening = false;
    map.removeLayer(rec.marker);
  }

  function applyPositions(trains) {
    var start = performance.now();
    var hidden = App.state.hiddenOps;
    generation++;
    for (var i = 0; i < trains.length; i++) {
      var t = trains[i];
      if (hidden.has(t.op)) continue;
      var rec = markers.get(t.trip_idx);
      if (rec) {
        if (t.lat !== rec.toLat || t.lon !== rec.toLon) moveTo(rec, t.lat, t.lon, start);
        setBearing(rec, t.bearing);
        setAtStation(rec, t.at_station);
        setSpeedTitle(rec, t);
      } else {
        rec = create(t);
        markers.set(t.trip_idx, rec);
      }
      rec.gen = generation;
      if (t.trip_idx === selectedIdx) pushTrail(t);
    }
    markers.forEach(function (rec, idx) {
      if (rec.gen !== generation) {
        remove(rec);
        markers.delete(idx);
      }
    });
    api.lastUpdateMs = performance.now() - start;
  }

  function updateLabelClass(zoom) {
    map.getContainer().classList.toggle('tr-labels', zoom >= LABEL_ZOOM);
  }

  App.on('meta', function (meta) {
    colors = {};
    meta.operators.forEach(function (op) { colors[op.code] = op.color; });
  });
  App.on('positions', function (data) { applyPositions(data.trains); });
  App.on('filter', function () { applyPositions(App.state.positions); });
  App.on('select', function (idx) {
    var prev = selectedIdx !== null && markers.get(selectedIdx);
    selectedIdx = idx;
    if (prev) setSelected(prev, false);
    var next = idx !== null && markers.get(idx);
    if (next) setSelected(next, true);
    clearTrail();
  });
  App.on('zoom', updateLabelClass);
  map.on('zoomstart', function () { zooming = true; cancelTweens(); });
  map.on('zoomend', function () { zooming = false; });
  map.on('click', function () { App.select(null); });
  updateLabelClass(map.getZoom());

  var api = {
    getMarker: function (tripIdx) {
      var rec = markers.get(tripIdx);
      return rec && rec.marker;
    },
    count: function () { return markers.size; },
    lastUpdateMs: 0
  };
  return api;
})();
