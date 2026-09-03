/*
 * Module: static map layers — rail lines (/data/tory.geojson) and stations (/data/stacje.json).
 * Contract: see app.js.
 *
 * Rail lines: one L.geoJSON on its own canvas renderer, non-interactive so it never steals
 * clicks from trains, always visible, a little thinner below zoom 8.
 * Stations: fetched once and collapsed to one point per station (the feed lists every platform).
 * From zoom 11 the stations inside the padded viewport are drawn as small canvas circles with a
 * hover tooltip; from zoom 14 the names are shown permanently. The subset is rebuilt (diffed, not
 * recreated) after every App 'move'/'zoom'. Both layers sit in overlayPane, below train markers,
 * and can be toggled from the layers control in the top-right corner.
 */
(function () {
  var map = App.map;

  var LINE_COLOR = '#9b8fb5';
  var LINE_THIN_BELOW_ZOOM = 8;
  var STATION_MIN_ZOOM = 11;
  var NAMES_MIN_ZOOM = 14;
  var MAX_NAMES = 80;          // permanent labels only while at most this many stations are in view
  var VIEW_PAD = 0.25;         // fraction of the viewport kept ready around the edges for panning
  var MERGE_DISTANCE_M = 400;  // platforms of one station are listed separately in the feed

  // Two renderers so a station rebuild never redraws the 2,300 rail polylines; added in this
  // order so the station canvas stays above the rail canvas regardless of which data arrives first.
  var railRenderer = L.canvas({ padding: 0.5 }).addTo(map);
  var stationRenderer = L.canvas({ padding: 0.5, tolerance: 4 }).addTo(map);

  function lineStyle(zoom) {
    return {
      color: LINE_COLOR,
      weight: zoom < LINE_THIN_BELOW_ZOOM ? 0.8 : 1.2,
      opacity: 0.75,
      lineCap: 'round',
      lineJoin: 'round'
    };
  }

  var railLayer = L.geoJSON(null, {
    style: function () { return lineStyle(map.getZoom()); },
    interactive: false,
    renderer: railRenderer
  }).addTo(map);
  var stationLayer = L.layerGroup().addTo(map);

  L.control.layers(null, { 'Linie kolejowe': railLayer, 'Stacje': stationLayer }, {
    position: 'topright',
    collapsed: true
  }).addTo(map);

  /* ---- rail lines ---- */

  var lineThin = null;
  function updateLineStyle(zoom) {
    var thin = zoom < LINE_THIN_BELOW_ZOOM;
    if (thin === lineThin) return;
    lineThin = thin;
    railLayer.setStyle(lineStyle(zoom));
  }

  App.api('/data/tory.geojson').then(function (geojson) {
    railLayer.addData(geojson);
    lineThin = map.getZoom() < LINE_THIN_BELOW_ZOOM;
  }).catch(function (err) {
    console.error('Nie udało się wczytać linii kolejowych (/data/tory.geojson)', err);
  });

  /* ---- stations ---- */

  var stations = [];     // deduplicated [{name, lat, lon}]
  var shown = {};        // station index -> CircleMarker currently in stationLayer
  var shownMode = null;  // 'dots' | 'names' — what kind of markers `shown` holds

  function distanceM(a, b) {
    // Flat-earth approximation: accurate to a few percent for a few hundred metres.
    var dy = (a.lat - b.lat) * 111320;
    var dx = (a.lon - b.lon) * 111320 * Math.cos(a.lat * Math.PI / 180);
    return Math.sqrt(dx * dx + dy * dy);
  }

  function mergePlatforms(rows) {
    var byName = {};
    var result = [];
    rows.forEach(function (row) {
      var group = byName[row.name] || (byName[row.name] = []);
      for (var i = 0; i < group.length; i++) {
        if (distanceM(group[i], row) < MERGE_DISTANCE_M) return;
      }
      var station = { name: row.name, lat: row.lat, lon: row.lon };
      group.push(station);
      result.push(station);
    });
    return result;
  }

  function makeMarker(station, mode) {
    var marker = L.circleMarker([station.lat, station.lon], {
      renderer: stationRenderer,
      radius: 3,
      color: '#3b3550',
      weight: 1.2,
      fillColor: '#ffffff',
      fillOpacity: 1
    });
    if (mode === 'names') {
      marker.bindTooltip(station.name, {
        permanent: true,
        direction: 'right',
        offset: [6, 0],
        opacity: 1,
        className: 'ly-station-name'
      });
    } else {
      marker.bindTooltip(station.name, { direction: 'top', offset: [0, -6], className: 'ly-station-tip' });
    }
    return marker;
  }

  function clearStations() {
    stationLayer.clearLayers();
    shown = {};
    shownMode = null;
  }

  function rebuildStations() {
    if (!stations.length || !map.hasLayer(stationLayer)) return;
    var zoom = map.getZoom();
    if (zoom < STATION_MIN_ZOOM) { clearStations(); return; }

    var b = map.getBounds().pad(VIEW_PAD);
    var south = b.getSouth(), north = b.getNorth(), west = b.getWest(), east = b.getEast();
    var inView = [];
    for (var i = 0; i < stations.length; i++) {
      var s = stations[i];
      if (s.lat >= south && s.lat <= north && s.lon >= west && s.lon <= east) inView.push(i);
    }

    var mode = zoom >= NAMES_MIN_ZOOM && inView.length <= MAX_NAMES ? 'names' : 'dots';
    if (mode !== shownMode) { clearStations(); shownMode = mode; }

    var keep = {};
    inView.forEach(function (idx) {
      keep[idx] = true;
      if (!shown[idx]) {
        shown[idx] = makeMarker(stations[idx], mode);
        stationLayer.addLayer(shown[idx]);
      }
    });
    Object.keys(shown).forEach(function (idx) {
      if (!keep[idx]) {
        stationLayer.removeLayer(shown[idx]);
        delete shown[idx];
      }
    });
  }

  // 'zoom' and 'move' fire back to back after a zoom; collapse them into one rebuild per frame.
  var rebuildPending = false;
  function scheduleRebuild() {
    if (rebuildPending) return;
    rebuildPending = true;
    L.Util.requestAnimFrame(function () {
      rebuildPending = false;
      rebuildStations();
    });
  }

  App.api('/data/stacje.json').then(function (rows) {
    stations = mergePlatforms(rows);
    scheduleRebuild();
  }).catch(function (err) {
    console.error('Nie udało się wczytać stacji (/data/stacje.json)', err);
  });

  App.on('zoom', function (zoom) {
    updateLineStyle(zoom);
    scheduleRebuild();
  });
  App.on('move', scheduleRebuild);

  // Layers control: keep the group empty while hidden, refill it when it comes back.
  map.on('overlayadd', function (e) { if (e.layer === stationLayer) scheduleRebuild(); });
  map.on('overlayremove', function (e) { if (e.layer === stationLayer) clearStations(); });
})();
