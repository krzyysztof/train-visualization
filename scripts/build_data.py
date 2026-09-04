#!/usr/bin/env python3
"""Regenerates train_visualization/data/ — the app only reads these files, never the network.
Stdlib only (urllib, zipfile, csv, json, math, argparse).

Usage:
    python3 scripts/build_data.py                    # both pipelines
    python3 scripts/build_data.py --trains           # stacje.json + pociagi.json only
    python3 scripts/build_data.py --tracks           # tory.geojson only
    python3 scripts/build_data.py --out DIR --keep-downloads --cache-dir DIR

Atomic writes (a failed download/validation never clobbers existing data). Exit codes:
0 ok, 1 validation failure, 2 download failed. ``wojewodztwa.geojson`` is third-party
(ppatrzyk/polska-geojson, MIT) and not touched here.

PIPELINE 1: TRACKS -> tory.geojson
Source: OpenStreetMap/Overpass (ODbL). Query: every ``railway=rail`` way in Poland
without a ``service=*`` tag (drops yards/sidings/spurs; ~28k ways, ~49MB response).
Processing: merge ways into chains through 2-way junctions only, simplify per chain
(local equirectangular projection, 12m prefilter + Douglas-Peucker 50m), round to
4 decimals, drop degenerate chains.
Schema: ``{"type":"FeatureCollection","features":[{"properties":{"railway":"rail"},
"geometry":{"type":"LineString","coordinates":[[lon,lat],...]}}, ...]}``
Consumer: ``web/static/layers.js`` (fetches ``/data/tory.geojson``).

PIPELINE 2: TRAINS -> stacje.json + pociagi.json
Source: mkuran.pl/gtfs/polish_trains.zip (CC BY 4.0, PKP PLK data). route_type=3
(bus substitutions) excluded. GTFS "HH:MM:SS" (HH may exceed 24) -> int seconds
since midnight of the service day.

stacje.json — array, ARRAY INDEX = station index used by trips:
    [{"id", "name", "lat", "lon"}, ...]   (only referenced stops, sorted by stop_id, 5 decimals)

pociagi.json:
    {"generated_from", "feed_start_date", "feed_end_date",   # int YYYYMMDD
     "calendars": [{"on":[YYYYMMDD,...], "off":[...]?}, ...],   # indexed by trips[].cal
     "shapes": [[[lon,lat],...], ...],                          # indexed by trips[].shape, DP 70m
     "trips": [{"id", "op", "route", "num", "dest",
                "cal":[cal_idx,...], "shape": idx?, "n": count?,
                "stops":[[station_idx, dep_sec, arr_sec], ...]},  # NOTE: dep THEN arr
               ...],
     "feed_version"}

  - Calendars: feed only has calendar_dates.txt (exception_type=1) -> emitted verbatim
    as "on"; identical (on,off) sets share one entry. A future calendar.txt would need
    expanding into explicit dates.
  - Dedup: GTFS trips identical in (op, route, num, dest, shape, full stop sequence)
    merge into one record; "cal" lists all their calendars, "n" = group size (omitted if 1).
  - Trips with <2 stops, unknown stop_id, or non-monotonic times are dropped (warned).
Consumer: ``trains.py`` (``TrainSchedule``).

VALIDATION: JSON round-trip + structural checks (indices in range, times monotonic,
coords within Poland's bbox) after each pipeline; trains pipeline also loads via the
app's own ``TrainSchedule``. Any violation -> exit 1.
"""
import argparse
import collections
import csv
import datetime as _dt
import http.client
import io
import json
import math
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "train_visualization" / "data"
DEFAULT_CACHE_DIR = Path(tempfile.gettempdir()) / "train-visualization-build-cache"

GTFS_URL = "https://mkuran.pl/gtfs/polish_trains.zip"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OVERPASS_QUERY = """[out:json][timeout:180];
area["ISO3166-1"="PL"][admin_level=2]->.pl;
( way["railway"="rail"]["service"!~"."](area.pl); );
out geom;
"""
USER_AGENT = "train-visualization build_data.py (Python stdlib urllib)"

TRACKS_FILE = "tory.geojson"
STATIONS_FILE = "stacje.json"
TRAINS_FILE = "pociagi.json"

TRACK_PREFILTER_M = 12.0
TRACK_TOLERANCE_M = 50.0
TRACK_DECIMALS = 4
SHAPE_TOLERANCE_M = 70.0
SHAPE_DECIMALS = 5
STATION_DECIMALS = 5
EARTH_RADIUS_M = 6371008.8
# Generous box around Poland (ways selected by area may poke over the border a little).
SANITY_BBOX = (12.0, 26.0, 48.0, 56.0)  # min_lon, max_lon, min_lat, max_lat
MIN_EXPECTED_WAYS = 1000  # an Overpass "success" with fewer elements is a truncated/errored answer

RETRY_STATUSES = {429, 500, 502, 503, 504}
DOWNLOAD_ATTEMPTS = 4
BACKOFF_BASE_S = 15
HTTP_TIMEOUT_S = 300
PROGRESS_EVERY_BYTES = 5 * 1024 * 1024

_T0 = time.monotonic()


def log(msg):
    sys.stderr.write(f"[{time.monotonic() - _T0:7.1f}s] {msg}\n")
    sys.stderr.flush()


class DownloadError(Exception):
    pass


class ValidationError(Exception):
    pass


# --------------------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------------------

def download(url, dest, *, post_data=None, label=""):
    """Streams ``url`` into ``dest`` (through ``dest.part``), retrying transient failures."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last_error = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, data=post_data, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp, open(part, "wb") as f:
                total = resp.headers.get("Content-Length")
                total_txt = f"{int(total) / 1e6:.1f} MB" if total else "unknown size"
                log(f"{label}: HTTP {resp.status} from {url} ({total_txt})")
                received = 0
                next_report = PROGRESS_EVERY_BYTES
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    if received >= next_report:
                        log(f"{label}: ... {received / 1e6:.1f} MB")
                        next_report += PROGRESS_EVERY_BYTES
            os.replace(part, dest)
            log(f"{label}: downloaded {received / 1e6:.1f} MB -> {dest}")
            return dest
        except urllib.error.HTTPError as e:
            snippet = ""
            try:
                snippet = e.read(300).decode("utf-8", "replace").strip().replace("\n", " ")
            except Exception:  # noqa: BLE001 - best-effort diagnostics only
                pass
            last_error = f"HTTP {e.code} {e.reason} {snippet}".strip()
            if e.code not in RETRY_STATUSES:
                raise DownloadError(f"{label}: {url} answered {last_error}") from None
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            last_error = f"{type(e).__name__}: {e}"
        if part.exists():
            part.unlink()
        if attempt < DOWNLOAD_ATTEMPTS:
            delay = BACKOFF_BASE_S * 2 ** (attempt - 1)
            log(f"{label}: attempt {attempt}/{DOWNLOAD_ATTEMPTS} failed ({last_error}); retrying in {delay}s")
            time.sleep(delay)
    raise DownloadError(f"{label}: giving up on {url} after {DOWNLOAD_ATTEMPTS} attempts ({last_error})")


def fetch(url, cache_path, keep, *, post_data=None, label=""):
    """Returns a local path holding ``url``'s body, honouring the download cache."""
    cache_path = Path(cache_path)
    if keep and cache_path.exists() and cache_path.stat().st_size > 0:
        log(f"{label}: using cached download {cache_path} ({cache_path.stat().st_size / 1e6:.1f} MB)")
        return cache_path
    return download(url, cache_path, post_data=post_data, label=label)


def discard_cache(path, keep):
    if not keep:
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass


def write_json_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return path.stat().st_size


def project_metres(coords):
    """Local equirectangular projection of [(lon, lat), ...] centred on the mean latitude."""
    lat0 = sum(lat for _, lat in coords) / len(coords)
    kx = EARTH_RADIUS_M * math.radians(1.0) * math.cos(math.radians(lat0))
    ky = EARTH_RADIUS_M * math.radians(1.0)
    return [(lon * kx, lat * ky) for lon, lat in coords]


def radial_filter(xy, min_dist):
    """Indices of points at least ``min_dist`` apart from the previously kept one (ends always kept)."""
    n = len(xy)
    if n < 3:
        return list(range(n))
    keep = [0]
    lx, ly = xy[0]
    d2_min = min_dist * min_dist
    for i in range(1, n - 1):
        x, y = xy[i]
        if (x - lx) ** 2 + (y - ly) ** 2 >= d2_min:
            keep.append(i)
            lx, ly = x, y
    keep.append(n - 1)
    return keep


def douglas_peucker(xy, tolerance):
    """Iterative (stack-based) Douglas-Peucker; returns the indices to keep, ascending."""
    n = len(xy)
    if n < 3:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[-1] = True
    tol2 = tolerance * tolerance
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        ax, ay = xy[a]
        bx, by = xy[b]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        best_d2, best_i = -1.0, -1
        for i in range(a + 1, b):
            px, py = xy[i]
            if seg2 > 0.0:
                t = ((px - ax) * dx + (py - ay) * dy) / seg2
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                ex, ey = ax + t * dx - px, ay + t * dy - py
            else:
                ex, ey = ax - px, ay - py
            d2 = ex * ex + ey * ey
            if d2 > best_d2:
                best_d2, best_i = d2, i
        if best_d2 > tol2:
            keep[best_i] = True
            stack.append((a, best_i))
            stack.append((best_i, b))
    return [i for i in range(n) if keep[i]]


def simplify_line(coords, tolerance_m, decimals, prefilter_m=0.0):
    """Simplifies [(lon, lat), ...] -> [[lon, lat], ...] rounded, without consecutive duplicates."""
    if not coords:
        return []
    xy = project_metres(coords)
    idx = radial_filter(xy, prefilter_m) if prefilter_m > 0 else list(range(len(xy)))
    kept = [idx[i] for i in douglas_peucker([xy[i] for i in idx], tolerance_m)]
    out = []
    for i in kept:
        lon, lat = coords[i]
        point = [round(lon, decimals), round(lat, decimals)]
        if not out or out[-1] != point:
            out.append(point)
    return out


# --------------------------------------------------------------------------------------
# Pipeline 1: tracks
# --------------------------------------------------------------------------------------

def load_overpass_answer(path):
    """Parses a saved Overpass answer; raises DownloadError if it is not a usable full answer.

    A 200 answer can still be a runtime error (a "remark" with an empty element list),
    e.g. when the server-side timeout hits, so the element count is checked too.
    """
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except ValueError as e:
            raise DownloadError(f"response is not JSON ({e})") from None
    n_ways = sum(1 for el in data.get("elements", ()) if el.get("type") == "way")
    if n_ways < MIN_EXPECTED_WAYS:
        raise DownloadError(f"only {n_ways} ways in answer ({data.get('remark', 'no remark')})")
    return data


def fetch_overpass(cache_dir, keep):
    """Returns (local_path, parsed_answer), trying each endpoint in turn."""
    cache_path = Path(cache_dir) / "overpass_railway_pl.json"
    if keep and cache_path.exists() and cache_path.stat().st_size > 0:
        log(f"tracks: using cached download {cache_path} ({cache_path.stat().st_size / 1e6:.1f} MB)")
        try:
            return cache_path, load_overpass_answer(cache_path)
        except DownloadError as e:
            log(f"tracks: cached answer unusable ({e}); downloading again")
            cache_path.unlink()
    post_data = urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode("utf-8")
    errors = []
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            path = download(endpoint, cache_path, post_data=post_data, label="tracks")
            return path, load_overpass_answer(path)
        except DownloadError as e:
            log(f"tracks: {endpoint}: {e}")
            errors.append(f"{endpoint}: {e}")
            if cache_path.exists():
                cache_path.unlink()
    raise DownloadError("tracks: all Overpass endpoints failed: " + " | ".join(errors))


def parse_overpass_ways(data):
    """{way_id: (node_ids, [(lon, lat), ...])} for every usable way in an Overpass answer."""
    ways = {}
    skipped = 0
    for el in data.get("elements", ()):
        if el.get("type") != "way":
            continue
        nodes = el.get("nodes") or []
        geometry = el.get("geometry") or []
        if len(nodes) != len(geometry):
            skipped += 1
            continue
        pairs = [(nid, (pt["lon"], pt["lat"])) for nid, pt in zip(nodes, geometry) if pt]
        if len(pairs) < 2:
            skipped += 1
            continue
        ways[el["id"]] = ([nid for nid, _ in pairs], [pt for _, pt in pairs])
    if skipped:
        log(f"tracks: skipped {skipped} ways with unusable geometry")
    return ways


def merge_ways(ways):
    """Joins ways end-to-end into chains, only through nodes touched by exactly two ways."""
    touched_by = collections.Counter()
    for nodes, _ in ways.values():
        touched_by.update(set(nodes))
    ends = collections.defaultdict(list)
    for wid, (nodes, _) in ways.items():
        ends[nodes[0]].append(wid)
        ends[nodes[-1]].append(wid)

    def partner(node, current_way):
        if touched_by[node] != 2:
            return None
        candidates = ends.get(node)
        if not candidates or len(candidates) != 2 or candidates[0] == candidates[1]:
            return None
        a, b = candidates
        if a == current_way:
            return b
        if b == current_way:
            return a
        return None

    visited = set()
    chains = []
    for wid in sorted(ways):
        if wid in visited:
            continue
        visited.add(wid)
        nodes, coords = ways[wid]
        chain = list(coords)
        head_node, tail_node = nodes[0], nodes[-1]
        head_way = tail_way = wid

        while True:  # grow forward from the tail
            other = partner(tail_node, tail_way)
            if other is None or other in visited:
                break
            onodes, ocoords = ways[other]
            if onodes[0] == tail_node:
                chain.extend(ocoords[1:])
                tail_node = onodes[-1]
            elif onodes[-1] == tail_node:
                chain.extend(reversed(ocoords[:-1]))
                tail_node = onodes[0]
            else:
                break
            visited.add(other)
            tail_way = other

        while True:  # grow backward from the head
            other = partner(head_node, head_way)
            if other is None or other in visited:
                break
            onodes, ocoords = ways[other]
            if onodes[-1] == head_node:
                chain[:0] = ocoords[:-1]
                head_node = onodes[0]
            elif onodes[0] == head_node:
                chain[:0] = list(reversed(ocoords[1:]))
                head_node = onodes[-1]
            else:
                break
            visited.add(other)
            head_way = other

        chains.append(chain)
    return chains


def build_tracks(out_dir, cache_dir, keep):
    out_path = Path(out_dir) / TRACKS_FILE
    log("tracks: querying Overpass (railway=rail without service=*, Poland)")
    raw_path, data = fetch_overpass(cache_dir, keep)
    ways = parse_overpass_ways(data)
    raw_points = sum(len(coords) for _, coords in ways.values())
    log(f"tracks: {len(ways)} ways, {raw_points} points before merging")
    del data

    chains = merge_ways(ways)
    log(f"tracks: merged into {len(chains)} chains; simplifying (prefilter {TRACK_PREFILTER_M:g} m, "
        f"Douglas-Peucker {TRACK_TOLERANCE_M:g} m)")
    features = []
    for chain in chains:
        coords = simplify_line(chain, TRACK_TOLERANCE_M, TRACK_DECIMALS, prefilter_m=TRACK_PREFILTER_M)
        if len(coords) < 2:
            continue
        features.append({
            "type": "Feature",
            "properties": {"railway": "rail"},
            "geometry": {"type": "LineString", "coordinates": coords},
        })
    collection = {"type": "FeatureCollection", "features": features}
    size = write_json_atomic(out_path, collection)
    log(f"tracks: wrote {out_path} ({size / 1e3:.0f} KB)")
    discard_cache(raw_path, keep)
    validate_tracks(out_path)


def validate_tracks(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    problems = []
    if data.get("type") != "FeatureCollection":
        problems.append("root is not a FeatureCollection")
    features = data.get("features", [])
    min_lon, max_lon, min_lat, max_lat = SANITY_BBOX
    points = 0
    for i, feat in enumerate(features):
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [])
        if feat.get("type") != "Feature" or geom.get("type") != "LineString":
            problems.append(f"feature {i}: not a LineString feature")
        if feat.get("properties") != {"railway": "rail"}:
            problems.append(f"feature {i}: unexpected properties {feat.get('properties')}")
        if len(coords) < 2:
            problems.append(f"feature {i}: fewer than 2 points")
        for pt in coords:
            if len(pt) != 2 or not (min_lon <= pt[0] <= max_lon and min_lat <= pt[1] <= max_lat):
                problems.append(f"feature {i}: coordinate {pt} outside Poland bbox / malformed")
                break
        points += len(coords)
    if not features:
        problems.append("no features")
    log(f"tracks: VALIDATION features={len(features)} points={points} size={Path(path).stat().st_size} bytes")
    if problems:
        raise ValidationError("tracks: " + "; ".join(problems[:10]))


# --------------------------------------------------------------------------------------
# Pipeline 2: trains
# --------------------------------------------------------------------------------------

def gtfs_rows(zf, name):
    """Yields dict rows of a GTFS table, or nothing if the table is absent."""
    if name not in zf.namelist():
        return
    with zf.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        yield from csv.DictReader(text)


def gtfs_time_to_seconds(value):
    parts = value.strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"bad GTFS time {value!r}")
    h, m, s = (int(p) for p in parts)
    return h * 3600 + m * 60 + s


def expand_calendar(row, feed_start, feed_end):
    """calendar.txt weekday pattern -> set of int YYYYMMDD within feed_start..feed_end."""
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    active = [row.get(day, "0").strip() == "1" for day in weekdays]
    start = max(int(row["start_date"]), feed_start)
    end = min(int(row["end_date"]), feed_end)
    dates = set()
    if start > end:
        return dates
    d = _dt.datetime.strptime(str(start), "%Y%m%d").date()
    last = _dt.datetime.strptime(str(end), "%Y%m%d").date()
    while d <= last:
        if active[d.weekday()]:
            dates.add(d.year * 10000 + d.month * 100 + d.day)
        d += _dt.timedelta(days=1)
    return dates


def build_trains(out_dir, cache_dir, keep):
    out_dir = Path(out_dir)
    zip_path = fetch(GTFS_URL, Path(cache_dir) / "polish_trains.zip", keep, label="trains")
    zf = zipfile.ZipFile(zip_path)
    names = set(zf.namelist())
    for required in ("routes.txt", "trips.txt", "stop_times.txt", "stops.txt"):
        if required not in names:
            raise ValidationError(f"trains: GTFS feed lacks {required}")

    feed_info = next(iter(gtfs_rows(zf, "feed_info.txt")), {})
    feed_version = feed_info.get("feed_version", "")

    routes = {}
    for r in gtfs_rows(zf, "routes.txt"):
        routes[r["route_id"]] = (r.get("agency_id", ""), r.get("route_short_name", ""), r.get("route_type", ""))

    kept = {}  # trip_id -> dict(op, route, num, dest, service_id, shape_id)
    total_trips = dropped_bus = dropped_route = 0
    for t in gtfs_rows(zf, "trips.txt"):
        total_trips += 1
        route = routes.get(t["route_id"])
        if route is None:
            dropped_route += 1
            continue
        agency, short_name, route_type = route
        if route_type == "3":
            dropped_bus += 1
            continue
        kept[t["trip_id"]] = {
            "op": agency,
            "route": short_name,
            "num": t.get("trip_short_name", "") or "",
            "dest": t.get("trip_headsign", "") or "",
            "service_id": t["service_id"],
            "shape_id": (t.get("shape_id") or "").strip() or None,
        }
    log(f"trains: trips.txt: {total_trips} trips, kept {len(kept)} "
        f"(dropped {dropped_bus} route_type=3 bus trips, {dropped_route} with unknown route)")

    stops_by_trip = collections.defaultdict(list)
    n_stop_times = 0
    for r in gtfs_rows(zf, "stop_times.txt"):
        if r["trip_id"] not in kept:
            continue
        n_stop_times += 1
        arr = gtfs_time_to_seconds(r["arrival_time"] or r["departure_time"])
        dep = gtfs_time_to_seconds(r["departure_time"] or r["arrival_time"])
        stops_by_trip[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"], dep, arr))
    log(f"trains: stop_times.txt: {n_stop_times} rows for kept trips")

    stops = {}
    for r in gtfs_rows(zf, "stops.txt"):
        stops[r["stop_id"]] = (r.get("stop_name", ""), float(r["stop_lat"]), float(r["stop_lon"]))

    # Per-trip stop lists, dropping trips the app could not use.
    trip_stops = {}
    dropped = collections.Counter()
    for trip_id in kept:
        rows = stops_by_trip.get(trip_id)
        if not rows or len(rows) < 2:
            dropped["fewer than 2 stops"] += 1
            continue
        rows.sort(key=lambda x: x[0])
        if any(stop_id not in stops for _, stop_id, _, _ in rows):
            dropped["unknown stop_id"] += 1
            continue
        monotonic = all(arr <= dep for _, _, dep, arr in rows) and all(
            rows[i][3] >= rows[i - 1][2] for i in range(1, len(rows)))
        if not monotonic:
            dropped["non-monotonic times"] += 1
            continue
        trip_stops[trip_id] = [(stop_id, dep, arr) for _, stop_id, dep, arr in rows]
    for reason, count in dropped.items():
        log(f"trains: WARNING dropped {count} trips: {reason}")
    del stops_by_trip
    if not trip_stops:
        raise ValidationError("trains: no usable trips")

    # Stations: only referenced stops, sorted by stop_id; array index == station index.
    used_stop_ids = sorted({stop_id for seq in trip_stops.values() for stop_id, _, _ in seq})
    station_index = {stop_id: i for i, stop_id in enumerate(used_stop_ids)}
    stations = [
        {"id": stop_id, "name": stops[stop_id][0],
         "lat": round(stops[stop_id][1], STATION_DECIMALS), "lon": round(stops[stop_id][2], STATION_DECIMALS)}
        for stop_id in used_stop_ids
    ]

    # Calendars.
    cal_add = collections.defaultdict(set)
    cal_remove = collections.defaultdict(set)
    for r in gtfs_rows(zf, "calendar_dates.txt"):
        (cal_add if r["exception_type"].strip() == "1" else cal_remove)[r["service_id"]].add(int(r["date"]))
    calendar_rows = {r["service_id"]: r for r in gtfs_rows(zf, "calendar.txt")}
    all_dates = [d for s in cal_add.values() for d in s]
    for r in calendar_rows.values():
        all_dates.extend((int(r["start_date"]), int(r["end_date"])))
    if feed_info.get("feed_start_date") and feed_info.get("feed_end_date"):
        feed_start, feed_end = int(feed_info["feed_start_date"]), int(feed_info["feed_end_date"])
    else:
        feed_start, feed_end = (min(all_dates), max(all_dates)) if all_dates else (0, 0)
        log(f"trains: WARNING feed_info.txt has no feed dates; using {feed_start}..{feed_end} from calendars")
    if calendar_rows:
        log(f"trains: calendar.txt present ({len(calendar_rows)} services) - expanding within {feed_start}..{feed_end}")

    calendars = []
    calendar_index = {}
    service_to_cal = {}
    missing_services = 0
    for service_id in sorted({t["service_id"] for tid, t in kept.items() if tid in trip_stops}):
        on = set(cal_add.get(service_id, ()))
        if service_id in calendar_rows:
            on |= expand_calendar(calendar_rows[service_id], feed_start, feed_end)
        off = set(cal_remove.get(service_id, ()))
        if not on and not off and service_id not in calendar_rows:
            missing_services += 1
        key = (tuple(sorted(on)), tuple(sorted(off)))
        if key not in calendar_index:
            calendar_index[key] = len(calendars)
            entry = {"on": list(key[0])}
            if key[1]:
                entry["off"] = list(key[1])
            calendars.append(entry)
        service_to_cal[service_id] = calendar_index[key]
    if missing_services:
        log(f"trains: WARNING {missing_services} service_ids have no calendar at all (trips never run)")
    log(f"trains: {len(service_to_cal)} services -> {len(calendars)} distinct calendars")

    # Shapes: only referenced ones, in shapes.txt order, simplified.
    used_shape_ids = {t["shape_id"] for tid, t in kept.items() if tid in trip_stops and t["shape_id"]}
    shape_points = collections.defaultdict(list)
    shape_order = []
    for r in gtfs_rows(zf, "shapes.txt"):
        sid = r["shape_id"]
        if sid not in used_shape_ids:
            continue
        if sid not in shape_points:
            shape_order.append(sid)
        shape_points[sid].append((int(r["shape_pt_sequence"]), float(r["shape_pt_lon"]), float(r["shape_pt_lat"])))
    raw_shape_points = sum(len(v) for v in shape_points.values())
    log(f"trains: shapes.txt: {len(shape_order)} referenced shapes, {raw_shape_points} points; "
        f"simplifying (Douglas-Peucker {SHAPE_TOLERANCE_M:g} m)")
    shapes = []
    shape_index = {}
    for sid in shape_order:
        pts = sorted(shape_points[sid])
        shape_index[sid] = len(shapes)
        shapes.append(simplify_line([(lon, lat) for _, lon, lat in pts], SHAPE_TOLERANCE_M, SHAPE_DECIMALS))
    missing_shapes = used_shape_ids - set(shape_index)
    if missing_shapes:
        log(f"trains: WARNING {len(missing_shapes)} shape_ids referenced by trips are absent from shapes.txt")
    log(f"trains: shapes simplified to {sum(len(s) for s in shapes)} points")

    # Trip records, merging identical trips.
    groups = {}
    for trip_id, seq in trip_stops.items():
        t = kept[trip_id]
        stops_rec = tuple((station_index[stop_id], dep, arr) for stop_id, dep, arr in seq)
        shape_idx = shape_index.get(t["shape_id"]) if t["shape_id"] else None
        key = (t["op"], t["route"], t["num"], t["dest"], shape_idx, stops_rec)
        group = groups.get(key)
        if group is None:
            group = groups[key] = {"ids": [], "cals": set()}
        group["ids"].append(trip_id)
        group["cals"].add(service_to_cal[t["service_id"]])
    trips = []
    for (op, route, num, dest, shape_idx, stops_rec), group in groups.items():
        rec = {
            "id": min(group["ids"]),
            "op": op,
            "route": route,
            "num": num,
            "dest": dest,
            "cal": sorted(group["cals"]),
            "stops": [list(s) for s in stops_rec],
        }
        if shape_idx is not None:
            rec["shape"] = shape_idx
        if len(group["ids"]) > 1:
            rec["n"] = len(group["ids"])
        trips.append(rec)
    trips.sort(key=lambda r: r["id"])
    log(f"trains: {len(trip_stops)} GTFS trips -> {len(trips)} trip records after dedup")

    payload = {
        "generated_from": GTFS_URL,
        "feed_start_date": feed_start,
        "feed_end_date": feed_end,
        "calendars": calendars,
        "shapes": shapes,
        "trips": trips,
        "feed_version": feed_version,
    }
    size_st = write_json_atomic(out_dir / STATIONS_FILE, stations)
    size_tr = write_json_atomic(out_dir / TRAINS_FILE, payload)
    log(f"trains: wrote {out_dir / STATIONS_FILE} ({size_st / 1e3:.0f} KB) and "
        f"{out_dir / TRAINS_FILE} ({size_tr / 1e6:.1f} MB)")
    zf.close()
    discard_cache(zip_path, keep)
    validate_trains(out_dir)


def validate_trains(out_dir):
    out_dir = Path(out_dir)
    with open(out_dir / STATIONS_FILE, encoding="utf-8") as f:
        stations = json.load(f)
    with open(out_dir / TRAINS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    problems = []

    def problem(msg):
        if len(problems) < 20:
            problems.append(msg)

    if not isinstance(stations, list) or not stations:
        problem("stacje.json is not a non-empty array")
    for i, st in enumerate(stations):
        if set(st) != {"id", "name", "lat", "lon"} or not isinstance(st["id"], str):
            problem(f"station {i}: bad keys")
            break
    for key in ("generated_from", "feed_start_date", "feed_end_date", "calendars", "shapes", "trips", "feed_version"):
        if key not in data:
            problem(f"pociagi.json lacks {key!r}")
    if problems:
        raise ValidationError("trains: " + "; ".join(problems))
    if not (isinstance(data["feed_start_date"], int) and isinstance(data["feed_end_date"], int)
            and data["feed_start_date"] <= data["feed_end_date"]):
        problem("feed_start_date/feed_end_date are not ordered ints")

    calendars, shapes, trips = data["calendars"], data["shapes"], data["trips"]
    for i, cal in enumerate(calendars):
        for field in ("on", "off"):
            dates = cal.get(field, [])
            if field == "on" and "on" not in cal:
                problem(f"calendar {i}: no 'on'")
            if dates != sorted(dates) or any(not (isinstance(d, int) and 19000101 <= d <= 29991231) for d in dates):
                problem(f"calendar {i}: {field!r} not sorted YYYYMMDD ints")
    for i, shape in enumerate(shapes):
        if not shape or any(len(p) != 2 for p in shape):
            problem(f"shape {i}: empty or malformed")
    n_stations, n_cal, n_shapes = len(stations), len(calendars), len(shapes)
    merged_from = 0
    for i, trip in enumerate(trips):
        for key in ("id", "op", "route", "num", "dest", "cal", "stops"):
            if key not in trip:
                problem(f"trip {i}: missing {key!r}")
        merged_from += trip.get("n", 1)
        if not trip["cal"] or any(not (0 <= c < n_cal) for c in trip["cal"]):
            problem(f"trip {i} ({trip.get('id')}): calendar index out of range")
        if "shape" in trip and not (0 <= trip["shape"] < n_shapes):
            problem(f"trip {i} ({trip.get('id')}): shape index out of range")
        stops = trip["stops"]
        if len(stops) < 2:
            problem(f"trip {i} ({trip.get('id')}): fewer than 2 stops")
        prev_dep = None
        for station_idx, dep, arr in stops:
            if not (0 <= station_idx < n_stations):
                problem(f"trip {i} ({trip.get('id')}): station index {station_idx} out of range")
                break
            if arr > dep or (prev_dep is not None and arr < prev_dep):
                problem(f"trip {i} ({trip.get('id')}): times not monotonic")
                break
            prev_dep = dep
    ids = [t["id"] for t in trips]
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        problem("trip ids are not unique and sorted")
    if not trips:
        problem("no trips")

    log(f"trains: VALIDATION stations={n_stations} trips={len(trips)} (merged from {merged_from} GTFS trips) "
        f"calendars={n_cal} shapes={n_shapes} shape_points={sum(len(s) for s in shapes)} "
        f"feed={data['feed_start_date']}..{data['feed_end_date']} version={data['feed_version']!r} "
        f"sizes: {STATIONS_FILE}={(out_dir / STATIONS_FILE).stat().st_size} B "
        f"{TRAINS_FILE}={(out_dir / TRAINS_FILE).stat().st_size} B")
    if problems:
        raise ValidationError("trains: " + "; ".join(problems))
    smoke_test_consumer(out_dir)


def smoke_test_consumer(out_dir):
    """Loads the files through the app's own TrainSchedule, exactly as the app does."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        import train_visualization.trains as trains_mod  # stdlib-only module, safe to import headless
    except ImportError as e:
        log(f"trains: consumer smoke test skipped (cannot import train_visualization.trains: {e})")
        return
    trains_mod.STATIONS_PATH = Path(out_dir) / STATIONS_FILE
    trains_mod.TRAINS_PATH = Path(out_dir) / TRAINS_FILE
    try:
        schedule = trains_mod.TrainSchedule()
        positions = schedule.active_positions()
        # Also exercise the shape interpolation path for a slice of trips at a busy hour.
        probe = _dt.datetime.combine(_dt.date.today(), _dt.time(8, 0))
        probe_positions = schedule.active_positions(probe)
    except Exception as e:  # noqa: BLE001 - any consumer failure is a validation failure
        raise ValidationError(f"trains: app consumer TrainSchedule failed on the new files: {e!r}") from e
    log(f"trains: consumer smoke test OK - TrainSchedule reports {len(positions)} trains en route now, "
        f"{len(probe_positions)} at 08:00 today")


# --------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="See the module docstring for the output schema.")
    parser.add_argument("--tracks", action="store_true", help="build tory.geojson (Overpass)")
    parser.add_argument("--trains", action="store_true", help="build stacje.json + pociagi.json (GTFS)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, metavar="DIR",
                        help=f"output directory (default: {DEFAULT_OUT_DIR})")
    parser.add_argument("--keep-downloads", action="store_true",
                        help="cache raw downloads in --cache-dir and reuse them on later runs")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, metavar="DIR",
                        help=f"download cache location (default: {DEFAULT_CACHE_DIR})")
    args = parser.parse_args(argv)
    if not args.tracks and not args.trains:
        args.tracks = args.trains = True

    args.out.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    log(f"output dir: {args.out.resolve()} | cache dir: {args.cache_dir.resolve()} "
        f"({'kept' if args.keep_downloads else 'discarded after use'})")

    failures = {}
    if args.trains:
        try:
            build_trains(args.out, args.cache_dir, args.keep_downloads)
        except (DownloadError, ValidationError) as e:
            log(f"ERROR {e}")
            failures["trains"] = e
    if args.tracks:
        try:
            build_tracks(args.out, args.cache_dir, args.keep_downloads)
        except (DownloadError, ValidationError) as e:
            log(f"ERROR {e}")
            if isinstance(e, DownloadError):
                log(f"tracks: existing {args.out / TRACKS_FILE} left untouched")
            failures["tracks"] = e

    if not failures:
        log("done: all requested outputs built and validated")
        return 0
    log("FAILED: " + ", ".join(f"{k} ({type(v).__name__})" for k, v in failures.items()))
    if all(isinstance(v, DownloadError) for v in failures.values()):
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
