"""Computes approximate live train positions by interpolating along the published timetable.

Positions are derived purely from a baked GTFS schedule (see data/pociagi.json,
data/stacje.json) and the system clock — no network access, no live GPS. A train
running late will appear further along its route than it actually is.

Speed is NOT measured either — it's derived from physics + track geometry (see
"speed profile" below), not from any real telemetry. It's an honest engineering
estimate, not a fact about the specific train.
"""
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

STATIONS_PATH = Path(__file__).parent / "data" / "stacje.json"
TRAINS_PATH = Path(__file__).parent / "data" / "pociagi.json"
DAY = 86400

# -- Speed-profile constants: turn a segment's fixed scheduled duration into a
# plausible motion shape (slow near stations/curves, faster on straights) while
# the total time still lands exactly on schedule. Engineering assumptions, not
# measured values — Poland doesn't publish per-line speed limits we could use.

LATERAL_ACCEL_MPS2 = 0.6  # comfort limit assumed for curve speed cap (unbanked-equivalent simplification)
LINEAR_ACCEL_MPS2 = 0.5  # acceleration/deceleration assumed for all trains (simplification)
MIN_SPEED_MPS = 1.0  # numerical floor only, not a claimed real minimum speed

# Orientacyjne, publicznie znane maksymalne prędkości eksploatacyjne wg kategorii
# pociągu (nie dane pomiarowe/telemetryczne) — używane jako sufit prędkości "w linii
# prostej", zanim krzywizna toru i rozpędzanie/hamowanie go obniżą.
_CRUISE_KMH_EXACT = {
    "EIP": 200,
    "EIC": 160,
    "ICN": 160,
    "IC": 140,
    "TLK": 140,
    "MP": 120,
    "KD Sprinter": 120,
}
# Sprawdzane w tej kolejności (pierwsze trafienie wygrywa) dla kategorii spoza
# powyższej listy — dopasowanie po prefiksie kodu linii.
_CRUISE_KMH_PREFIXES = [
    ("SKA", 110),  # Koleje Małopolskie
    ("PKM", 110),  # Koleje Wielkopolskie
    ("RL", 100),  # KM, linie lokalne
    ("REG", 110),  # PolRegio
    ("S", 100),  # SKM/miejskie (S1..S9, S18, S40...)
    ("Ł", 100),  # Łódzka Kolej Aglomeracyjna
    ("D", 110),  # Koleje Dolnośląskie (D3, D62...)
    ("R", 110),  # KM regionalne (R1..R91, RE1/RE2...)
    ("K", 110),  # PolRegio (K5, K7), Koleje Śląskie (KŚ)
]
_CRUISE_KMH_DEFAULT = 120


def _cruise_speed_mps(route):
    route = (route or "").strip()
    kmh = _CRUISE_KMH_EXACT.get(route)
    if kmh is None:
        for prefix, value in _CRUISE_KMH_PREFIXES:
            if route.startswith(prefix):
                kmh = value
                break
    return (kmh if kmh is not None else _CRUISE_KMH_DEFAULT) / 3.6


def _date_to_int(d):
    return d.year * 10000 + d.month * 100 + d.day


def _dist(ax, ay, bx, by):
    return math.hypot(bx - ax, by - ay)


def _meters(lon1, lat1, lon2, lat2):
    """Real-world distance in metres (flat-earth approximation — fine at track-segment scale)."""
    kx = math.cos(math.radians((lat1 + lat2) / 2)) * 111320
    ky = 111320
    return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * ky)


def _radius_of_curvature_m(prev, cur, nxt, lat_ref):
    """Menger curvature radius at `cur` (metres) from 3 track points; None if (near-)straight."""
    kx = math.cos(math.radians(lat_ref)) * 111320
    ky = 111320
    ax, ay = prev[0] * kx, prev[1] * ky
    bx, by = cur[0] * kx, cur[1] * ky
    cx, cy = nxt[0] * kx, nxt[1] * ky
    a = math.hypot(bx - cx, by - cy)
    b = math.hypot(ax - cx, ay - cy)
    c = math.hypot(ax - bx, ay - by)
    cross2 = abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay))  # = 2 * triangle area
    if cross2 < 1e-6 or a == 0 or b == 0 or c == 0:
        return None  # collinear (or degenerate) -> effectively straight
    return (a * b * c) / (2 * cross2)


def _lerp(a, b, t):
    """Interpolates a (lon, lat, maxspeed_kmh|None) point; maxspeed is a step function (nearer
    endpoint's value), not blended, since a speed limit doesn't average across a boundary."""
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, (a[2] if t < 0.5 else b[2])


def _ease_in_out(t):
    """Fallback-path easing (no shape data): linear time-fraction -> slow-start/slow-end fraction."""
    return 0.5 - 0.5 * math.cos(math.pi * t)


def _bearing(ax, ay, bx, by):
    """Compass bearing in degrees (0 = north, 90 = east) from lon/lat A to lon/lat B."""
    east = (bx - ax) * math.cos(math.radians((ay + by) / 2))
    north = by - ay
    if east == 0 and north == 0:
        return None
    return math.degrees(math.atan2(east, north)) % 360


def _seconds_since_midnight(now):
    return now.hour * 3600 + now.minute * 60 + now.second


class TrainSchedule:
    """Loads the baked timetable once and answers "where is every train right now"."""

    def __init__(self):
        with STATIONS_PATH.open(encoding="utf-8") as f:
            self.stations = json.load(f)
        with TRAINS_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        self.feed_start_date = data.get("feed_start_date")
        self.feed_end_date = data.get("feed_end_date")
        self.shapes = self._zip_shape_maxspeed(data["shapes"], data.get("shape_maxspeed"))
        self.trips = data["trips"]
        self.calendar_sets = [set(cal["on"]) - set(cal.get("off", ())) for cal in data["calendars"]]

        self._candidates_date = None
        self._candidates = []  # list of (trip_idx, origin_date_int)
        self._origins_by_trip = {}  # trip_idx -> [origin_date_int, ...]
        self._shape_anchor_cache = {}  # trip_idx -> per stop (segment index, t within it, along-track distance)
        self._speed_profile_cache = {}  # (trip_idx, stop_i) -> _build_speed_profile() result

    @staticmethod
    def _zip_shape_maxspeed(shapes, shape_maxspeed):
        """[[lon,lat],...] + [[kmh|None,...],...] -> [[(lon,lat,kmh|None),...],...]; tolerates a
        missing/mismatched shape_maxspeed (older data file) by treating every point as unlimited."""
        if not shape_maxspeed or len(shape_maxspeed) != len(shapes):
            shape_maxspeed = [None] * len(shapes)
        zipped = []
        for shape, speeds in zip(shapes, shape_maxspeed):
            if not speeds or len(speeds) != len(shape):
                speeds = [None] * len(shape)
            zipped.append([(lon, lat, kmh) for (lon, lat), kmh in zip(shape, speeds)])
        return zipped

    # -- candidate trips for a given day ---------------------------------------

    def _refresh_candidates(self, today):
        today_int = _date_to_int(today)
        yesterday_int = _date_to_int(today - timedelta(days=1))
        candidates = []
        origins = {}
        for idx, trip in enumerate(self.trips):
            active = set()
            for cal_idx in trip["cal"]:
                active |= self.calendar_sets[cal_idx]
            # Yesterday's service only matters if the trip runs past midnight into today.
            origins_to_check = (today_int, yesterday_int) if trip["stops"][-1][2] > DAY else (today_int,)
            for origin in origins_to_check:
                if origin in active:
                    candidates.append((idx, origin))
                    origins.setdefault(idx, []).append(origin)
        self._candidates = candidates
        self._origins_by_trip = origins
        self._candidates_date = today

    def _ensure_candidates(self, now):
        if self._candidates_date != now.date():
            self._refresh_candidates(now.date())

    def _now_sec_for_origin(self, now, origin_date_int):
        seconds = _seconds_since_midnight(now)
        return seconds if origin_date_int == _date_to_int(now.date()) else seconds + DAY

    # -- public queries ----------------------------------------------------------

    def active_positions(self, now=None):
        """Trains currently en route: trip identity, lon/lat, bearing and where along the route they are."""
        now = now or datetime.now()
        self._ensure_candidates(now)
        results = []
        for trip_idx, origin_date_int in self._candidates:
            now_sec = self._now_sec_for_origin(now, origin_date_int)
            trip = self.trips[trip_idx]
            stops = trip["stops"]
            if not (stops[0][1] <= now_sec <= stops[-1][2]):
                continue
            state = self._locate(trip_idx, trip, stops, now_sec)
            if state is None:
                continue
            results.append({**self._identity(trip_idx, trip), **state})
        return results

    def trip_details(self, trip_idx, now=None):
        """Full route of one trip (stops with times, track shape) plus its current progress, if running."""
        now = now or datetime.now()
        trip = self.trips[trip_idx]
        stops = [
            {
                "name": self.stations[station_idx]["name"],
                "lat": self.stations[station_idx]["lat"],
                "lon": self.stations[station_idx]["lon"],
                "arr": arr_sec,
                "dep": dep_sec,
            }
            for station_idx, dep_sec, arr_sec in trip["stops"]
        ]
        shape_idx = trip.get("shape")
        details = {
            **self._identity(trip_idx, trip),
            "stops": stops,
            "shape": self.shapes[shape_idx] if shape_idx is not None else None,
            "current": self._current_progress(trip_idx, now),
        }
        return details

    def find_trips(self, query, now=None, limit=20):
        """Trips whose number (or destination) matches the query; running ones first, then today's."""
        now = now or datetime.now()
        self._ensure_candidates(now)
        q = query.strip().lower()
        if not q:
            return []
        running = {p["trip_idx"] for p in self.active_positions(now)}
        scored = []
        for trip_idx, trip in enumerate(self.trips):
            num = str(trip.get("num", "")).lower()
            route = str(trip.get("route", "")).lower()
            dest = str(trip.get("dest", "")).lower()
            origin = self.stations[trip["stops"][0][0]]["name"].lower()
            if q == num:
                rank = 0
            elif num.startswith(q):
                rank = 1
            elif q in num or q in dest or q in origin:
                rank = 2
            elif q == route or f"{route} {num}".startswith(q):
                # The label on the map reads "EIC 5350/1", so people search for the category too.
                rank = 3
            else:
                continue
            is_running = trip_idx in running
            is_today = trip_idx in self._origins_by_trip
            # Match quality first (an exact train number must win), then what is on the map now.
            scored.append((rank, 0 if is_running else 1, 0 if is_today else 1, trip_idx, is_running, is_today))
        scored.sort()
        return [
            {**self._identity(trip_idx, self.trips[trip_idx]), "running": is_running, "today": is_today}
            for _r, _t, _k, trip_idx, is_running, is_today in scored[:limit]
        ]

    # -- internals -----------------------------------------------------------------

    def _identity(self, trip_idx, trip):
        return {
            "trip_idx": trip_idx,
            "num": trip.get("num", "?"),
            "route": trip.get("route", ""),
            "dest": trip.get("dest", ""),
            "origin": self.stations[trip["stops"][0][0]]["name"],
            "op": trip.get("op", ""),
        }

    def _current_progress(self, trip_idx, now):
        self._ensure_candidates(now)
        trip = self.trips[trip_idx]
        stops = trip["stops"]
        for origin in self._origins_by_trip.get(trip_idx, ()):
            now_sec = self._now_sec_for_origin(now, origin)
            if not (stops[0][1] <= now_sec <= stops[-1][2]):
                continue
            state = self._locate(trip_idx, trip, stops, now_sec)
            if state is None:
                continue
            next_i = state["stop_i"] if state["at_station"] else state["stop_i"] + 1
            if state["at_station"] and state["stop_i"] + 1 < len(stops):
                next_i = state["stop_i"] + 1
            state["next_stop_i"] = min(next_i, len(stops) - 1)
            state["now_sec"] = now_sec
            state["eta_sec"] = max(0, stops[state["next_stop_i"]][2] - now_sec)
            return state
        return None

    def _locate(self, trip_idx, trip, stops, now_sec):
        for i, (station_idx, dep_sec, arr_sec) in enumerate(stops):
            if arr_sec <= now_sec <= dep_sec:
                station = self.stations[station_idx]
                bearing = None
                if i + 1 < len(stops):
                    nxt = self.stations[stops[i + 1][0]]
                    bearing = _bearing(station["lon"], station["lat"], nxt["lon"], nxt["lat"])
                return {
                    "lon": station["lon"], "lat": station["lat"], "bearing": bearing, "speed_kmh": 0.0,
                    "stop_i": i, "at_station": True, "fraction": 0.0,
                }
            if i + 1 < len(stops):
                next_arr_sec = stops[i + 1][2]
                if dep_sec <= now_sec < next_arr_sec:  # the arrival second belongs to the station
                    span = next_arr_sec - dep_sec
                    fraction = 0.0 if span <= 0 else (now_sec - dep_sec) / span
                    # `fraction` stays linear in time (part of the public shape, used for ETA-style
                    # reasoning); the position/speed physics profile is independent of it.
                    lon, lat, bearing, speed_kmh = self._interpolate(trip_idx, trip, stops, i, now_sec)
                    return {
                        "lon": lon, "lat": lat, "bearing": bearing, "speed_kmh": speed_kmh,
                        "stop_i": i, "at_station": False, "fraction": fraction,
                    }
        return None

    def _interpolate(self, trip_idx, trip, stops, stop_i, now_sec):
        a = self.stations[stops[stop_i][0]]
        b = self.stations[stops[stop_i + 1][0]]
        dep_sec, arr_sec = stops[stop_i][1], stops[stop_i + 1][2]
        shape_idx = trip.get("shape")
        if shape_idx is not None:
            point = self._interpolate_along_shape(trip_idx, shape_idx, stops, stop_i, now_sec, dep_sec, arr_sec, trip.get("route", ""))
            if point is not None:
                return point
        # Fallback for a trip with no track shape (none currently occur in the data, but
        # a straight line beats crashing): same eased eyeball curve as before, no curvature.
        span = arr_sec - dep_sec
        fraction = _ease_in_out(0.0 if span <= 0 else (now_sec - dep_sec) / span)
        lon = a["lon"] + (b["lon"] - a["lon"]) * fraction
        lat = a["lat"] + (b["lat"] - a["lat"]) * fraction
        bearing = _bearing(a["lon"], a["lat"], b["lon"], b["lat"])
        dist_m = _meters(a["lon"], a["lat"], b["lon"], b["lat"])
        speed_kmh = (dist_m / span) * 3.6 if span > 0 else 0.0
        return lon, lat, bearing, speed_kmh

    def _interpolate_along_shape(self, trip_idx, shape_idx, stops, stop_i, now_sec, dep_sec, arr_sec, route):
        shape = self.shapes[shape_idx]
        if len(shape) < 2:
            return None
        anchors = self._shape_anchor_cache.get(trip_idx)
        if anchors is None:
            anchors = self._anchor_stops(shape, [self.stations[s[0]] for s in stops])
            self._shape_anchor_cache[trip_idx] = anchors
        points = self._sub_polyline(shape, anchors[stop_i], anchors[stop_i + 1])
        if len(points) < 2:
            return None
        span = arr_sec - dep_sec
        if span <= 0:
            return None
        cache_key = (trip_idx, stop_i)
        profile = self._speed_profile_cache.get(cache_key)
        if profile is None:
            profile = self._build_speed_profile(points, span, route)
            self._speed_profile_cache[cache_key] = profile
        return self._locate_on_profile(profile, now_sec - dep_sec)

    @staticmethod
    def _build_speed_profile(points, span_sec, route):
        """Speed cap at each point = min(category cruise, real OSM limit if known, curve radius,
        accel/decel kinematics), then the whole profile is rescaled so total time matches the
        schedule exactly — only redistributes real scheduled duration, never invents time."""
        n = len(points)
        lat_ref = points[0][1]
        lengths_m = [_meters(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1]) for i in range(n - 1)]

        cum_m = [0.0] * n
        for i in range(1, n):
            cum_m[i] = cum_m[i - 1] + lengths_m[i - 1]
        total_m = cum_m[-1]  # same running sum as cum_m, so total_m - cum_m[i] can't go negative below
        if total_m <= 0:
            return {"points": points, "cum_time": [0.0] * n, "v_kmh": [0.0] * n}

        cruise_mps = _cruise_speed_mps(route)

        v_target = [cruise_mps] * n
        for i in range(n):
            limit_kmh = points[i][2]
            if limit_kmh is not None:
                v_target[i] = min(v_target[i], limit_kmh / 3.6)
            if 0 < i < n - 1:
                radius = _radius_of_curvature_m(points[i - 1], points[i], points[i + 1], lat_ref)
                if radius is not None:
                    v_target[i] = min(v_target[i], math.sqrt(LATERAL_ACCEL_MPS2 * radius))
            v_accel = math.sqrt(2 * LINEAR_ACCEL_MPS2 * max(0.0, cum_m[i]))
            v_decel = math.sqrt(2 * LINEAR_ACCEL_MPS2 * max(0.0, total_m - cum_m[i]))
            v_target[i] = max(MIN_SPEED_MPS, min(v_target[i], v_accel, v_decel))

        # Per-segment "effort" (time it would take at the average of its two endpoint speeds),
        # then rescale every segment's share of that effort onto the real scheduled duration.
        weight = [
            lengths_m[i] / max((v_target[i] + v_target[i + 1]) / 2, MIN_SPEED_MPS)
            for i in range(n - 1)
        ]
        total_weight = sum(weight)
        scale = span_sec / total_weight if total_weight > 0 else 1.0

        cum_time = [0.0] * n
        for i in range(n - 1):
            cum_time[i + 1] = cum_time[i] + weight[i] * scale
        cum_time[-1] = span_sec  # guard against float drift so the segment ends exactly on time

        # v_target/scale is the real (schedule-calibrated) speed AT each vertex — the same
        # scale factor that turned "weight" into real seconds turns relative target speed into
        # real km/h. Interpolating this per-vertex value (instead of reporting one flat average
        # per segment) is what makes the speed shown to the user change every second, not just
        # when the train crosses to the next point of the (coarse, simplified) track geometry.
        v_kmh = [v / scale * 3.6 for v in v_target] if scale > 0 else [0.0] * n

        return {"points": points, "cum_time": cum_time, "v_kmh": v_kmh}

    @staticmethod
    def _locate_on_profile(profile, elapsed_sec):
        points, cum_time, v_kmh = profile["points"], profile["cum_time"], profile["v_kmh"]
        elapsed_sec = max(0.0, min(elapsed_sec, cum_time[-1]))
        for i in range(len(cum_time) - 1):
            if cum_time[i] <= elapsed_sec <= cum_time[i + 1]:
                seg_span = cum_time[i + 1] - cum_time[i]
                t = 0.0 if seg_span <= 0 else (elapsed_sec - cum_time[i]) / seg_span
                ax, ay = points[i][0], points[i][1]
                bx, by = points[i + 1][0], points[i + 1][1]
                lon, lat = ax + (bx - ax) * t, ay + (by - ay) * t
                speed_kmh = v_kmh[i] + (v_kmh[i + 1] - v_kmh[i]) * t
                return lon, lat, _bearing(ax, ay, bx, by), speed_kmh
        ax, ay = points[-1][0], points[-1][1]
        return ax, ay, None, 0.0

    @staticmethod
    def _sub_polyline(shape, start, end):
        """Track between two anchors in travel order: start point, the vertices between, end point."""
        forward = start[2] <= end[2]
        a, b = (start, end) if forward else (end, start)
        a_seg, a_t, _ = a
        b_seg, b_t, _ = b
        points = [_lerp(shape[a_seg], shape[a_seg + 1], a_t)]
        points.extend(shape[a_seg + 1 : b_seg + 1])
        points.append(_lerp(shape[b_seg], shape[b_seg + 1], b_t))
        if not forward:
            points.reverse()
        return points

    @staticmethod
    def _anchor_stops(shape, stations):
        """Projects each stop onto the closest point ON the shape (not nearest vertex — DP
        simplification can put that km away, causing teleports). Returns (segment index, t,
        along-track distance) per stop; picks the pass that keeps stops monotonic along the track."""
        kx = math.cos(math.radians(shape[0][1]))  # shrink longitudes so distances are isotropic
        pts = [(p[0] * kx, p[1]) for p in shape]
        seg_len, cum = [], [0.0]
        for i in range(len(pts) - 1):
            seg_len.append(_dist(*pts[i], *pts[i + 1]))
            cum.append(cum[-1] + seg_len[-1])
        last_seg = len(pts) - 2

        def project(station, first_seg, end_seg):
            px, py = station["lon"] * kx, station["lat"]
            best = None
            for i in range(first_seg, end_seg + 1):
                ax, ay = pts[i]
                bx, by = pts[i + 1]
                length = seg_len[i]
                t = 0.0 if length == 0 else ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / (length * length)
                t = min(1.0, max(0.0, t))
                d = _dist(px, py, ax + (bx - ax) * t, ay + (by - ay) * t)
                if best is None or d < best[0]:
                    best = (d, i, t, cum[i] + t * length)
            return best[1:]

        anchors = [project(station, 0, last_seg) for station in stations]
        if len(anchors) < 2:
            return anchors
        forward = anchors[-1][2] >= anchors[0][2]
        for i in range(1, len(anchors)):
            prev_seg, _, prev_along = anchors[i - 1]
            if forward and anchors[i][2] < prev_along:
                anchors[i] = project(stations[i], prev_seg, last_seg)
            elif not forward and anchors[i][2] > prev_along:
                anchors[i] = project(stations[i], 0, prev_seg)
        return anchors
