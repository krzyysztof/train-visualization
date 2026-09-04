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


def _lerp(a, b, t):
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def _ease_in_out(t):
    """Maps a linear time-fraction (0..1) to a slow-start/slow-end travel-fraction.

    Real trains accelerate out of a station and brake into the next one rather than
    holding a constant speed; the schedule only gives us total travel time, not a
    speed profile, so this is a plausible curve, not a measured one. Zero derivative
    at both ends (t=0 and t=1) gives the "starts and ends at rest" look.
    """
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
        self.shapes = data["shapes"]
        self.trips = data["trips"]
        self.calendar_sets = [set(cal["on"]) - set(cal.get("off", ())) for cal in data["calendars"]]

        self._candidates_date = None
        self._candidates = []  # list of (trip_idx, origin_date_int)
        self._origins_by_trip = {}  # trip_idx -> [origin_date_int, ...]
        self._shape_anchor_cache = {}  # trip_idx -> per stop (segment index, t within it, along-track distance)

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
                    "lon": station["lon"], "lat": station["lat"], "bearing": bearing,
                    "stop_i": i, "at_station": True, "fraction": 0.0,
                }
            if i + 1 < len(stops):
                next_arr_sec = stops[i + 1][2]
                if dep_sec <= now_sec < next_arr_sec:  # the arrival second belongs to the station
                    span = next_arr_sec - dep_sec
                    fraction = 0.0 if span <= 0 else (now_sec - dep_sec) / span
                    # `fraction` itself stays linear in time (it's part of the public shape, used
                    # for ETA-style reasoning); only the position drawn on the map eases in/out.
                    lon, lat, bearing = self._interpolate(trip_idx, trip, stops, i, _ease_in_out(fraction))
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
        if len(shape) < 2:
            return None
        anchors = self._shape_anchor_cache.get(trip_idx)
        if anchors is None:
            anchors = self._anchor_stops(shape, [self.stations[s[0]] for s in stops])
            self._shape_anchor_cache[trip_idx] = anchors
        points = self._sub_polyline(shape, anchors[stop_i], anchors[stop_i + 1])
        if len(points) < 2:
            return None
        return self._point_along(points, fraction)

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
        """Projects each stop onto the closest point ON the shape, not the closest vertex.

        Shapes are Douglas-Peucker simplified, so on straight track the nearest vertex can be
        kilometres from a station; snapping to vertices made trains teleport and run backwards.
        Returns per stop (segment index, t within the segment, along-track distance). When a
        shape passes a station twice (out-and-back), the pass that keeps the stop sequence
        monotonic along the track wins.
        """
        kx = math.cos(math.radians(shape[0][1]))  # shrink longitudes so distances are isotropic
        pts = [(x * kx, y) for x, y in shape]
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
