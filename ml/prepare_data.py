#!/usr/bin/env python3
"""KROK 1: buduje ml/dataset.csv — jeden wiersz na odcinek trasy (stacja A -> B).
Cechy: odległość, kategoria, godzina. Etykieta: zaplanowany czas przejazdu (sekundy).

Uruchomienie: python3 ml/prepare_data.py
"""
import csv
import json
import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "train_visualization" / "data"
OUT_PATH = Path(__file__).parent / "dataset.csv"

# Ten sam obszar co reszta appki — mniej danych, szybciej się uczy, wciąż mnóstwo przykładów.
BBOX = (19.2592, 51.0131, 23.1284, 53.4809)  # min_lon, min_lat, max_lon, max_lat


def haversine_km(lon1, lat1, lon2, lat2):
    """Odległość w linii prostej między dwoma punktami na Ziemi (nie po torach!)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def in_bbox(station):
    min_lon, min_lat, max_lon, max_lat = BBOX
    return min_lon <= station["lon"] <= max_lon and min_lat <= station["lat"] <= max_lat


def main():
    stations = json.loads((DATA_DIR / "stacje.json").read_text(encoding="utf-8"))
    trips_data = json.loads((DATA_DIR / "pociagi.json").read_text(encoding="utf-8"))
    trips = trips_data["trips"]

    touching = [t for t in trips if any(in_bbox(stations[s[0]]) for s in t["stops"])]

    rows = []
    for trip in touching:
        route = trip.get("route", "") or "BRAK"
        stops = trip["stops"]
        for i in range(len(stops) - 1):
            a_idx, dep_sec, _ = stops[i]
            b_idx, _, arr_sec = stops[i + 1]
            duration = arr_sec - dep_sec
            if duration <= 0:
                continue  # dane nieużyteczne (np. postój bez ruchu zliczony jako "odcinek")
            a, b = stations[a_idx], stations[b_idx]
            distance = haversine_km(a["lon"], a["lat"], b["lon"], b["lat"])
            if distance <= 0:
                continue
            hour = (dep_sec // 3600) % 24
            rows.append(
                {
                    "route": route,
                    "distance_km": round(distance, 3),
                    "hour": hour,
                    "duration_sec": duration,
                    "from": a["name"],
                    "to": b["name"],
                }
            )

    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["route", "distance_km", "hour", "duration_sec", "from", "to"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Zapisano {len(rows)} wierszy do {OUT_PATH}")
    print("Przykładowe 3 wiersze:")
    for r in rows[:3]:
        print(" ", r)


if __name__ == "__main__":
    main()
