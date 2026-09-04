"""Computes approximate live train positions by interpolating along the published timetable.

Positions are derived purely from a baked GTFS schedule (see data/pociagi.json,
data/stacje.json) and the system clock — no network access, no live GPS. A train
running late will appear further along its route than it actually is.
"""
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

STATIONS_PATH = Path(__file__).parent / "data" / "stacje.json"
TRAINS_PATH = Path(__file__).parent / "data" / "pociagi.json"
DAY = 86400


def _date_to_int(d):
    return d.year * 10000 + d.month * 100 + d.day


def _dist(ax, ay, bx, by):
    return math.hypot(bx - ax, by - ay)


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
        self.shapes = data["shapes"]
        self.trips = data["trips"]
        self.calendar_sets = [set(cal["on"]) - set(cal.get("off", ())) for cal in data["calendars"]]

        self._candidates_date = None
        self._candidates = []  # list of (trip_idx, origin_date_int)
        self._origins_by_trip = {}  # trip_idx -> [origin_date_int, ...]
        self._shape_index_cache = {}  # trip_idx -> nearest shape-point index per stop

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
            for origin in (today_int, yesterday_int):
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
            dest = str(trip.get("dest", "")).lower()
            if q == num:
                rank = 0
            elif num.startswith(q):
                rank = 1
            elif q in num or q in dest:
                rank = 2
            else:
                continue
            is_running = trip_idx in running
            is_today = trip_idx in self._origins_by_trip
            scored.append((0 if is_running else 1, 0 if is_today else 1, rank, trip_idx, is_running, is_today))
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
                    "lon": station["lon"], "lat": station["lat"], "bearing": bearing,
                    "stop_i": i, "at_station": True, "fraction": 0.0,
                }
            if i + 1 < len(stops):
                next_arr_sec = stops[i + 1][2]
                if dep_sec <= now_sec <= next_arr_sec:
                    span = next_arr_sec - dep_sec
                    fraction = 0.0 if span <= 0 else (now_sec - dep_sec) / span
                    lon, lat, bearing = self._interpolate(trip_idx, trip, stops, i, fraction)
                    return {"lon": lon, "lat": lat, "bearing": bearing, "stop_i": i, "at_station": False, "fraction": fraction}
        return None

    def _interpolate(self, trip_idx, trip, stops, stop_i, fraction):
        a = self.stations[stops[stop_i][0]]
        b = self.stations[stops[stop_i + 1][0]]
        shape_idx = trip.get("shape")
        if shape_idx is not None:
            point = self._interpolate_along_shape(trip_idx, shape_idx, stops, stop_i, fraction)
            if point is not None:
                return point
        return (
            a["lon"] + (b["lon"] - a["lon"]) * fraction,
            a["lat"] + (b["lat"] - a["lat"]) * fraction,
            _bearing(a["lon"], a["lat"], b["lon"], b["lat"]),
        )

    def _interpolate_along_shape(self, trip_idx, shape_idx, stops, stop_i, fraction):
        shape = self.shapes[shape_idx]
        indices = self._shape_index_cache.get(trip_idx)
        if indices is None:
            indices = [self._nearest_shape_index(shape, self.stations[s[0]]) for s in stops]
            self._shape_index_cache[trip_idx] = indices

        i0, i1 = indices[stop_i], indices[stop_i + 1]
        points = shape[i0 : i1 + 1] if i0 <= i1 else list(reversed(shape[i1 : i0 + 1]))
        if len(points) < 2:
            return None
        return self._point_along(points, fraction)

    @staticmethod
    def _nearest_shape_index(shape, station):
        # Plain squared distance in degree-space is precise enough at this scale
        # to snap a station to the closest point already sampled from the same track.
        lon, lat = station["lon"], station["lat"]
        best_i, best_d = 0, None
        for i, (x, y) in enumerate(shape):
            d = (x - lon) ** 2 + (y - lat) ** 2
            if best_d is None or d < best_d:
                best_d, best_i = d, i
        return best_i

    @staticmethod
    def _point_along(points, fraction):
        """Point at `fraction` of the polyline's length, plus the bearing of the segment it lies on."""
        lengths = [_dist(*points[i], *points[i + 1]) for i in range(len(points) - 1)]
        total = sum(lengths)
        if total == 0:
            return points[0][0], points[0][1], None
        target = fraction * total
        cum = 0.0
        for i, seg_len in enumerate(lengths):
            if cum + seg_len >= target:
                t = 0.0 if seg_len == 0 else (target - cum) / seg_len
                ax, ay = points[i]
                bx, by = points[i + 1]
                return ax + (bx - ax) * t, ay + (by - ay) * t, _bearing(ax, ay, bx, by)
            cum += seg_len
        ax, ay = points[-2]
        bx, by = points[-1]
        return bx, by, _bearing(ax, ay, bx, by)
