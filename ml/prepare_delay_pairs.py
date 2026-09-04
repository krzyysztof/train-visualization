#!/usr/bin/env python3
"""KROK 1: buduje ml/delay_pairs.csv z ml/opoznienia/delays.jsonl.

Etykieta = final_delay_sec: rzeczywiste opóźnienie na ostatniej stacji trasy —
tylko dla kursów, które w danej migawce faktycznie tam dotarły. Cechy (per
wcześniejszy, potwierdzony punkt trasy): current_delay_sec, remaining_scheduled_sec,
stops_remaining, route, op, hour.

(Pierwsza wersja przewidywała opóźnienie na następnej stacji — bez sensu, bo
stacje są za blisko w czasie żeby cokolwiek się zmieniło; stąd "meta trasy".)

Uruchomienie: python3 ml/prepare_delay_pairs.py
"""
import csv
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "train_visualization" / "data"
DELAYS_PATH = Path(__file__).parent / "opoznienia" / "delays.jsonl"
OUT_PATH = Path(__file__).parent / "delay_pairs.csv"

PLAUSIBLE_MAX_SEC = 3 * 3600


def find_scheduled(stops, station_idx, start_from):
    for i in range(start_from, len(stops)):
        if stops[i][0] == station_idx:
            return stops[i][1], stops[i][2], i
    return None


def delay_of(obs, prefer="dep"):
    keys = ("dep_delay_sec", "arr_delay_sec") if prefer == "dep" else ("arr_delay_sec", "dep_delay_sec")
    for k in keys:
        if k in obs:
            return obs[k]
    return None


def examples_for_trip(trip, obs):
    stops = trip["stops"]
    if len(obs) < 2:
        return []

    last_found = find_scheduled(stops, obs[-1]["station_idx"], 0)
    if last_found is None:
        return []
    _dep_last, arr_last, pos_last = last_found
    if pos_last != len(stops) - 1:
        return []  # ten kurs jeszcze nie dojechał do celu w tej migawce — pomiń

    final_delay = delay_of(obs[-1], prefer="arr")
    if final_delay is None or abs(final_delay) > PLAUSIBLE_MAX_SEC:
        return []

    rows = []
    search_from = 0
    for i in range(len(obs) - 1):  # bez samego punktu docelowego — to byłby dystans 0
        found = find_scheduled(stops, obs[i]["station_idx"], search_from)
        if found is None:
            continue
        dep_i, _arr_i, pos_i = found
        search_from = pos_i

        current_delay = delay_of(obs[i], prefer="dep")
        if current_delay is None or abs(current_delay) > PLAUSIBLE_MAX_SEC:
            continue

        remaining = arr_last - dep_i
        if remaining <= 0:
            continue

        rows.append(
            {
                "route": trip.get("route", "") or "BRAK",
                "op": trip.get("op", "") or "BRAK",
                "hour": (dep_i // 3600) % 24,
                "current_delay_sec": current_delay,
                "remaining_scheduled_sec": remaining,
                "stops_remaining": pos_last - pos_i,
                "final_delay_sec": final_delay,
            }
        )
    return rows


def main():
    trips = json.loads((DATA_DIR / "pociagi.json").read_text(encoding="utf-8"))["trips"]

    if not DELAYS_PATH.exists():
        print(f"Brak {DELAYS_PATH} — najpierw uruchom ml/opoznienia/collect_delays.py.")
        return

    rows = []
    complete_trips = 0
    total_trip_records = 0
    for line in DELAYS_PATH.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        total_trip_records += 1
        trip = trips[rec["trip_idx"]]
        trip_rows = examples_for_trip(trip, rec["observations"])
        if trip_rows:
            complete_trips += 1
            rows.extend(trip_rows)

    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "route", "op", "hour",
                "current_delay_sec", "remaining_scheduled_sec", "stops_remaining",
                "final_delay_sec",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Rekordów kursów w delays.jsonl: {total_trip_records}")
    print(f"Kursy, które w tej migawce już dojechały do celu: {complete_trips}")
    print(f"Zapisano {len(rows)} przykładów (wcześniejszy punkt -> opóźnienie na mecie) do {OUT_PATH}")
    if rows:
        print("Przykładowe 3 wiersze:")
        for r in rows[:3]:
            print(" ", r)


if __name__ == "__main__":
    main()
