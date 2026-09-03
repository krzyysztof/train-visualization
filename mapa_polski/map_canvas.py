"""Tk Canvas widget that draws Poland's voivodeships, rail lines, and live trains."""
import json
import math
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from mapa_polski.trains import TrainSchedule

DATA_PATH = Path(__file__).parent / "data" / "wojewodztwa.geojson"
TRACKS_PATH = Path(__file__).parent / "data" / "tory.geojson"

FILL_COLOR = "#dce7f0"
OUTLINE_COLOR = "#3a3a3a"
TRACK_COLOR = "#b8a9c9"
TRAIN_FILL = "#d1495b"
TRAIN_OUTLINE = "#7a1f2b"
TRAIN_RADIUS = 3
TRAIN_SELECTED_FILL = "#f2a900"
TRAIN_SELECTED_OUTLINE = "#7a4f00"
TRAIN_SELECTED_RADIUS = 6
TRAIN_LABEL_FONT = ("TkDefaultFont", 8)
TRAIN_LABEL_COLOR = "#4a1a22"
# Below this zoom the dots are too dense (Warszawa) for labels to be readable, so
# only the selected train keeps its label; zoom in past it and every dot gets one.
LABEL_MIN_ZOOM = 3.0
CITY_COLOR = "#1a1a1a"
CITY_RADIUS = 4
CITY_FONT = ("TkDefaultFont", 9, "bold")
PADDING = 20
REDRAW_DELAY_MS = 80
TRAIN_REFRESH_MS = 5000
MIN_ZOOM = 1.0
MAX_ZOOM = 60.0
ZOOM_STEP = 1.2
ZOOM_SETTLE_MS = 150
# Every map-content item carries this tag, so pan/zoom (which act only on this
# tag) never touch the zoom buttons overlaid on the canvas.
MAP_TAG = "map"
# Train dots and labels share TRAIN_TAG (hover/click are bound once, at tag level, instead
# of per item — per-item tag_bind leaks a Tcl command per lambda on every redraw); dots
# additionally carry TRAIN_DOT_TAG so they can be kept above the wide labels.
TRAIN_TAG = "train"
TRAIN_DOT_TAG = "train_dot"

# Limit the map to one voivodeship for now — far less data to draw/track, so
# panning, zooming and the train refresh all stay snappy. Set to None for all of Poland.
FOCUS_REGION = "mazowieckie"

# A handful of major cities, shown as fixed reference points so it's easy to tell
# what part of the map you're looking at while panning/zooming.
CITIES = [
    {"name": "Warszawa", "lon": 21.0122, "lat": 52.2297},
    {"name": "Radom", "lon": 21.1471, "lat": 51.4027},
    {"name": "Płock", "lon": 19.7065, "lat": 52.5468},
    {"name": "Siedlce", "lon": 22.2901, "lat": 52.1676},
    {"name": "Ostrołęka", "lon": 21.5751, "lat": 53.0827},
    {"name": "Ciechanów", "lon": 20.6083, "lat": 52.8814},
]


class MapCanvas(tk.Canvas):
    """Draws voivodeships, rail lines and trains; supports zoom/pan and train selection."""

    def __init__(self, master, on_select=None, on_hover=None, **kwargs):
        super().__init__(master, background="white", highlightthickness=0, **kwargs)
        self.on_select = on_select
        self.on_hover = on_hover
        self._regions = self._load_regions()
        self._bounds = self._compute_bounds(self._regions)
        self._tracks = self._load_tracks()
        self._train_schedule = TrainSchedule()

        self._train_items = {}
        self._train_labels = {}
        self._train_meta = {}  # canvas item id -> (trip_id, info, hover_text)
        self._selected_train_id = None

        self._zoom = 1.0
        min_lon, max_lon, min_lat, max_lat = self._bounds
        self._center = ((min_lon + max_lon) / 2, (min_lat + max_lat) / 2)
        self._project = None
        self._lon_scale = None
        self._scale = None
        self._view_size = (0, 0)

        self._resize_job = None
        self._pan_last = None
        self._zoom_settle_job = None

        self.bind("<Configure>", self._schedule_redraw)
        self.bind("<ButtonPress-1>", self._on_pan_start)
        self.bind("<B1-Motion>", self._on_pan_motion)
        self.bind("<ButtonRelease-1>", self._on_pan_end)
        self.bind("<Button-4>", self._on_mousewheel)
        self.bind("<Button-5>", self._on_mousewheel)
        self.bind("<MouseWheel>", self._on_mousewheel)
        self.tag_bind(TRAIN_TAG, "<Enter>", self._on_train_enter)
        self.tag_bind(TRAIN_TAG, "<Leave>", self._on_train_leave)
        self.tag_bind(TRAIN_TAG, "<Button-1>", self._on_train_click)

        zoom_in_btn = ttk.Button(self, text="+", width=2, command=self._zoom_in_button)
        zoom_out_btn = ttk.Button(self, text="−", width=2, command=self._zoom_out_button)
        zoom_in_btn.place(relx=1.0, x=-8, y=8, anchor="ne")
        zoom_out_btn.place(relx=1.0, x=-8, y=38, anchor="ne")

        self.after(TRAIN_REFRESH_MS, self._train_tick)

    def _load_regions(self):
        with DATA_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        regions = []
        for feature in data["features"]:
            name = feature["properties"]["nazwa"]
            if FOCUS_REGION is not None and name != FOCUS_REGION:
                continue
            geom = feature["geometry"]
            polygons = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
            # Only the outer ring of each polygon is used; this dataset has no enclaves worth the extra complexity.
            outer_rings = [poly[0] for poly in polygons]
            regions.append(
                {
                    "id": feature["properties"]["id"],
                    "name": name,
                    "rings": outer_rings,
                }
            )
        return regions

    @staticmethod
    def _compute_bounds(regions):
        lons = [lon for region in regions for ring in region["rings"] for lon, lat in ring]
        lats = [lat for region in regions for ring in region["rings"] for lon, lat in ring]
        return min(lons), max(lons), min(lats), max(lats)

    def _load_tracks(self):
        with TRACKS_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        min_lon, max_lon, min_lat, max_lat = self._bounds
        tracks = []
        for feature in data["features"]:
            coords = feature["geometry"]["coordinates"]
            if any(min_lon <= lon <= max_lon and min_lat <= lat <= max_lat for lon, lat in coords):
                tracks.append(coords)
        return tracks

    def _schedule_redraw(self, _event):
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(REDRAW_DELAY_MS, self._redraw)

    def _base_scale(self, width, height):
        min_lon, max_lon, min_lat, max_lat = self._bounds
        lon_scale = math.cos(math.radians((min_lat + max_lat) / 2))
        span_x = (max_lon - min_lon) * lon_scale
        span_y = max_lat - min_lat
        avail_w = max(width - 2 * PADDING, 1)
        avail_h = max(height - 2 * PADDING, 1)
        return lon_scale, min(avail_w / span_x, avail_h / span_y)

    def _make_projector(self):
        width, height = self.winfo_width(), self.winfo_height()
        lon_scale, base_scale = self._base_scale(width, height)
        self._lon_scale = lon_scale
        self._scale = base_scale * self._zoom
        self._view_size = (width, height)
        return self._project_point

    def _project_point(self, lon, lat):
        # Reads live state rather than values captured at redraw time: a pan shifts
        # self._center without a full redraw, and the periodic train refresh must land
        # dots on the (moved) tracks, not where the tracks were before the pan.
        width, height = self._view_size
        center_lon, center_lat = self._center
        x = (lon - center_lon) * self._lon_scale * self._scale + width / 2
        y = (center_lat - lat) * self._scale + height / 2
        return x, y

    def _unproject(self, px, py):
        width, height = self.winfo_width(), self.winfo_height()
        center_lon, center_lat = self._center
        lon = center_lon + (px - width / 2) / (self._lon_scale * self._scale)
        lat = center_lat - (py - height / 2) / self._scale
        return lon, lat

    def _redraw(self):
        self._resize_job = None
        self.delete(MAP_TAG)
        self._train_items = {}
        self._train_labels = {}
        self._train_meta = {}

        width, height = self.winfo_width(), self.winfo_height()
        if width < 10 or height < 10:
            self._project = None
            return
        project = self._make_projector()
        self._project = project

        for region in self._regions:
            for ring in region["rings"]:
                points = [coord for lon, lat in ring for coord in project(lon, lat)]
                self.create_polygon(points, fill=FILL_COLOR, outline=OUTLINE_COLOR, width=1, tags=(MAP_TAG,))

        for line in self._tracks:
            points = [coord for lon, lat in line for coord in project(lon, lat)]
            if len(points) >= 4:
                self.create_line(points, fill=TRACK_COLOR, width=1, tags=(MAP_TAG,))

        for city in CITIES:
            x, y = project(city["lon"], city["lat"])
            self.create_oval(
                x - CITY_RADIUS, y - CITY_RADIUS, x + CITY_RADIUS, y + CITY_RADIUS,
                fill=CITY_COLOR, outline="", tags=(MAP_TAG,),
            )
            self.create_text(x + 7, y, text=city["name"], anchor="w", font=CITY_FONT, fill=CITY_COLOR, tags=(MAP_TAG,))

        self._update_trains()

    # -- Pan (click and drag) --------------------------------------------------

    def _on_pan_start(self, event):
        self._pan_last = (event.x, event.y)

    def _on_pan_motion(self, event):
        if self._pan_last is None or self._project is None:
            return
        last_x, last_y = self._pan_last
        dx, dy = event.x - last_x, event.y - last_y
        if dx == 0 and dy == 0:
            return
        self.move(MAP_TAG, dx, dy)
        self._pan_last = (event.x, event.y)
        self._center = (
            self._center[0] - dx / (self._lon_scale * self._scale),
            self._center[1] + dy / self._scale,
        )

    def _on_pan_end(self, _event):
        self._pan_last = None

    # -- Zoom (mouse wheel, centered on the cursor) -----------------------------

    def _on_mousewheel(self, event):
        if self._project is None:
            return
        zooming_in = getattr(event, "delta", 0) > 0 or getattr(event, "num", None) == 4
        self._zoom_at(ZOOM_STEP if zooming_in else 1 / ZOOM_STEP, event.x, event.y)

    def _zoom_at(self, factor, px, py):
        """Rescales what's already on screen immediately (cheap, native), and only
        reprojects everything from lon/lat (expensive, thousands of points) once
        scrolling settles — otherwise a fast scroll queues up a full redraw per
        tick and the map lags noticeably behind the mouse."""
        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, self._zoom * factor))
        if new_zoom == self._zoom:
            return
        actual_factor = new_zoom / self._zoom
        lon0, lat0 = self._unproject(px, py)
        self._zoom = new_zoom

        width, height = self.winfo_width(), self.winfo_height()
        lon_scale, base_scale = self._base_scale(width, height)
        scale = base_scale * self._zoom
        self._center = (
            lon0 - (px - width / 2) / (lon_scale * scale),
            lat0 + (py - height / 2) / scale,
        )
        self._lon_scale = lon_scale
        self._scale = scale

        self.scale(MAP_TAG, px, py, actual_factor, actual_factor)

        if self._zoom_settle_job is not None:
            self.after_cancel(self._zoom_settle_job)
        self._zoom_settle_job = self.after(ZOOM_SETTLE_MS, self._finish_zoom)

    def _finish_zoom(self):
        self._zoom_settle_job = None
        self._redraw()

    def _zoom_in_button(self):
        if self._project is None:
            return
        self._zoom_at(ZOOM_STEP, self.winfo_width() / 2, self.winfo_height() / 2)

    def _zoom_out_button(self):
        if self._project is None:
            return
        self._zoom_at(1 / ZOOM_STEP, self.winfo_width() / 2, self.winfo_height() / 2)

    # -- Trains -----------------------------------------------------------------

    def _train_tick(self):
        self._update_trains()
        self.after(TRAIN_REFRESH_MS, self._train_tick)

    def _train_style(self, trip_id):
        if trip_id == self._selected_train_id:
            return TRAIN_SELECTED_RADIUS, TRAIN_SELECTED_FILL, TRAIN_SELECTED_OUTLINE
        return TRAIN_RADIUS, TRAIN_FILL, TRAIN_OUTLINE

    def _label_state(self, trip_id):
        visible = self._zoom >= LABEL_MIN_ZOOM or trip_id == self._selected_train_id
        return "normal" if visible else "hidden"

    def _update_trains(self):
        if self._project is None or self._zoom_settle_job is not None:
            # A wheel-zoom is mid-flight: the canvas was scaled natively and the canonical
            # redraw is still pending (_finish_zoom). Skip this tick so dot radii and label
            # visibility aren't half-reset in between — the redraw places everything.
            return
        trains = self._train_schedule.active_positions(datetime.now())
        min_lon, max_lon, min_lat, max_lat = self._bounds
        seen_ids = set()
        for train in trains:
            # Trains currently outside the focused area (or abroad) would be dots floating
            # in blank space, so skip them.
            if not (min_lon <= train["lon"] <= max_lon and min_lat <= train["lat"] <= max_lat):
                continue
            trip_id = train["trip_idx"]
            seen_ids.add(trip_id)
            x, y = self._project(train["lon"], train["lat"])
            radius, fill, outline = self._train_style(trip_id)
            label_state = self._label_state(trip_id)

            if trip_id in self._train_items:
                self.coords(self._train_items[trip_id], x - radius, y - radius, x + radius, y + radius)
                self.itemconfig(self._train_items[trip_id], fill=fill, outline=outline)
                self.coords(self._train_labels[trip_id], x + radius + 3, y)
                self.itemconfig(self._train_labels[trip_id], state=label_state)
                continue

            hover_text = f"{train['route']} {train['num']} → {train['dest']}".strip()
            info = {"num": train["num"], "route": train["route"], "origin": train["origin"], "dest": train["dest"]}
            item = self.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill=fill, outline=outline, width=1, tags=(MAP_TAG, TRAIN_TAG, TRAIN_DOT_TAG),
            )
            label = self.create_text(
                x + radius + 3, y, text=f"{train['route']} {train['num']}".strip(), anchor="w",
                font=TRAIN_LABEL_FONT, fill=TRAIN_LABEL_COLOR, state=label_state, tags=(MAP_TAG, TRAIN_TAG),
            )
            self._train_items[trip_id] = item
            self._train_labels[trip_id] = label
            self._train_meta[item] = self._train_meta[label] = (trip_id, info, hover_text)

        # A label's hit-box is its whole text line, so a label anchored next to dot A would
        # otherwise sit on top of neighbouring dot B and hijack B's hover/click.
        self.tag_raise(TRAIN_DOT_TAG)

        for trip_id in list(self._train_items):
            if trip_id not in seen_ids:
                for item in (self._train_items.pop(trip_id), self._train_labels.pop(trip_id)):
                    self._train_meta.pop(item, None)
                    self.delete(item)
                if trip_id == self._selected_train_id:
                    self._selected_train_id = None

    def _hovered_train(self):
        current = self.find_withtag("current")
        return self._train_meta.get(current[0]) if current else None

    def _on_train_enter(self, _event):
        meta = self._hovered_train()
        if meta and self.on_hover:
            self.on_hover(meta[2])

    def _on_train_leave(self, _event):
        if self.on_hover:
            self.on_hover(None)

    def _on_train_click(self, _event):
        meta = self._hovered_train()
        if meta is None:
            return None
        return self._handle_train_click(meta[0], meta[1])

    def _handle_train_click(self, trip_id, info):
        previous_id = self._selected_train_id
        self._selected_train_id = trip_id
        if previous_id is not None and previous_id in self._train_items:
            self._restyle_train(previous_id)
        self._restyle_train(trip_id)
        if self.on_select:
            self.on_select(info)
        return "break"

    def _restyle_train(self, trip_id):
        radius, fill, outline = self._train_style(trip_id)
        item = self._train_items[trip_id]
        x0, y0, x1, y1 = self.coords(item)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.coords(item, cx - radius, cy - radius, cx + radius, cy + radius)
        self.itemconfig(item, fill=fill, outline=outline)
        self.coords(self._train_labels[trip_id], cx + radius + 3, cy)
        self.itemconfig(self._train_labels[trip_id], state=self._label_state(trip_id))
