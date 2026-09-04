"""Local web server: serves the Leaflet front-end and a small JSON API over TrainSchedule.

Everything is computed from the baked schedule files; the only network traffic the
browser generates on its own is for OpenStreetMap map tiles.

API (JSON, localhost only, no auth). `t=HH:MM` (or HH:MM:SS) on any endpoint means
"pretend it's that time today" — used by the time slider; omit it for the real clock.

  GET /api/meta
      {feed_start_date, feed_end_date, focus_bbox: [min_lon, min_lat, max_lon, max_lat],
       operators: [{code, name, color, count}], now: "HH:MM:SS"}
  GET /api/positions?t=HH:MM
      {time: "HH:MM:SS", simulated: bool,
       trains: [{trip_idx, num, route, dest, origin, op, lon, lat,
                 bearing (deg, 0=N, or null), at_station, stop_i, fraction}]}
  GET /api/trip/<trip_idx>?t=HH:MM
      {trip_idx, num, route, dest, origin, op,
       stops: [{name, lat, lon, arr, dep}],          # seconds since midnight of the service day
       shape: [[lon, lat], ...] | null,
       current: {lon, lat, bearing, stop_i, at_station, fraction, next_stop_i, eta_sec, now_sec} | null}
  GET /api/search?q=<text>&t=HH:MM
      {results: [{trip_idx, num, route, dest, origin, op, running, today}]}

Static files:
  /                      -> web/index.html
  /static/...            -> web/static/...
  /data/tory.geojson, /data/stacje.json -> train_visualization/data/  (rail lines, stations)
"""
import json
import mimetypes
import threading
from datetime import datetime
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from train_visualization.trains import TrainSchedule

WEB_DIR = Path(__file__).parent / "web"
DATA_DIR = Path(__file__).parent / "data"
REGIONS_PATH = DATA_DIR / "wojewodztwa.geojson"
FOCUS_REGION = "mazowieckie"  # initial view; the map itself covers all of Poland
DEFAULT_PORT = 8765

# agency_id in the GTFS feed -> display name and marker colour. Unknown codes fall back to grey.
OPERATORS = {
    "IC": {"name": "PKP Intercity", "color": "#d1495b"},
    "KM": {"name": "Koleje Mazowieckie", "color": "#2e8b57"},
    "PR": {"name": "Polregio", "color": "#1f6fb4"},
    "SKM": {"name": "SKM Warszawa", "color": "#ff8c00"},
    "SKMT": {"name": "PKP SKM Trójmiasto", "color": "#0097a7"},
    "LKA": {"name": "Łódzka Kolej Aglomeracyjna", "color": "#8e44ad"},
    "KW": {"name": "Koleje Wielkopolskie", "color": "#17a2b8"},
    "KD": {"name": "Koleje Dolnośląskie", "color": "#9aa100"},
    "KS": {"name": "Koleje Śląskie", "color": "#e377c2"},
    "KML": {"name": "Koleje Małopolskie", "color": "#5d6d7e"},
    "AR": {"name": "Arriva RP", "color": "#8c564b"},
    "LEO": {"name": "Leo Express", "color": "#111111"},
    "RJ": {"name": "RegioJet", "color": "#f0c000"},
}
UNKNOWN_OPERATOR_COLOR = "#7f7f7f"


class MapBackend:
    """Owns the schedule and answers API queries; a lock serialises access from server threads."""

    def __init__(self):
        self.schedule = TrainSchedule()
        self.lock = threading.Lock()
        self.focus_bbox = self._region_bbox(FOCUS_REGION)
        self.operators = self._operator_table()

    @staticmethod
    def _region_bbox(name):
        with REGIONS_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        for feature in data["features"]:
            if feature["properties"]["nazwa"] != name:
                continue
            geom = feature["geometry"]
            polygons = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
            points = [pt for poly in polygons for pt in poly[0]]
            lons = [p[0] for p in points]
            lats = [p[1] for p in points]
            return [min(lons), min(lats), max(lons), max(lats)]
        return None

    def _operator_table(self):
        counts = {}
        for trip in self.schedule.trips:
            counts[trip.get("op", "")] = counts.get(trip.get("op", ""), 0) + 1
        table = []
        for code, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            info = OPERATORS.get(code, {})
            table.append(
                {
                    "code": code,
                    "name": info.get("name", code),
                    "color": info.get("color", UNKNOWN_OPERATOR_COLOR),
                    "count": count,
                }
            )
        return table

    @staticmethod
    def parse_time(value):
        """`HH:MM` / `HH:MM:SS` -> (today at that time, True); anything else -> (now, False)."""
        if value:
            for fmt in ("%H:%M:%S", "%H:%M"):
                try:
                    t = datetime.strptime(value, fmt)
                    return datetime.now().replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0), True
                except ValueError:
                    continue
        return datetime.now(), False

    def meta(self):
        return {
            "feed_start_date": self.schedule.feed_start_date,
            "feed_end_date": self.schedule.feed_end_date,
            "focus_bbox": self.focus_bbox,
            "operators": self.operators,
            "now": datetime.now().strftime("%H:%M:%S"),
        }

    def positions(self, t=None):
        now, simulated = self.parse_time(t)
        with self.lock:
            trains = self.schedule.active_positions(now)
        return {"time": now.strftime("%H:%M:%S"), "simulated": simulated, "trains": trains}

    def trip(self, trip_idx, t=None):
        now, _ = self.parse_time(t)
        with self.lock:
            return self.schedule.trip_details(trip_idx, now)

    def search(self, query, t=None):
        now, _ = self.parse_time(t)
        with self.lock:
            return {"results": self.schedule.find_trips(query, now)}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, backend, **kwargs):
        self.backend = backend
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path.startswith("/api/"):
            try:
                self._handle_api(url.path, query)
            except (ValueError, IndexError, KeyError) as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if url.path.startswith("/data/"):
            self._serve_data(url.path[len("/data/"):])
            return
        if url.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def _handle_api(self, path, query):
        t = query.get("t")
        if path == "/api/meta":
            self._send_json(self.backend.meta())
        elif path == "/api/positions":
            self._send_json(self.backend.positions(t))
        elif path.startswith("/api/trip/"):
            trip_idx = int(path[len("/api/trip/"):])
            if not 0 <= trip_idx < len(self.backend.schedule.trips):
                self._send_json({"error": "unknown trip"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json(self.backend.trip(trip_idx, t))
        elif path == "/api/search":
            self._send_json(self.backend.search(query.get("q", ""), t))
        else:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _serve_data(self, name):
        target = DATA_DIR / name
        if "/" in name or name.startswith(".") or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        content_type = "application/geo+json" if name.endswith(".geojson") else mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, status=HTTPStatus.OK):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_request(self, code="-", size="-"):
        # The front-end polls once a second; only errors are worth a line in the terminal.
        if str(code).startswith(("2", "3")):
            return
        super().log_request(code, size)


def create_server(port=None):
    """Binds the server (first free port from DEFAULT_PORT unless one is given) and returns (server, url)."""
    backend = MapBackend()
    handler = partial(Handler, backend=backend)
    candidates = [port] if port else range(DEFAULT_PORT, DEFAULT_PORT + 20)
    for candidate in candidates:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            break
        except OSError:
            continue
    else:
        raise RuntimeError("Nie udało się znaleźć wolnego portu dla serwera mapy")
    return server, f"http://127.0.0.1:{server.server_address[1]}/"
