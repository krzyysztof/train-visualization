#!/usr/bin/env python3
"""Ćwiczenie: które kategorie pociągów i którzy przewoźnicy najczęściej i najmocniej
się spóźniają. Inna granulacja niż prepare_delay_pairs.py (tam: wiele punktów na
trasie na kurs, do regresji) — tu: dokładnie jeden wynik na ukończony kurs
(trip_id + start_date), do prostych statystyk częstości.

Nie jest to ML (żadnego uczenia) — to opisowa agregacja realnych opóźnień, punkt
wyjścia pod ewentualny wskaźnik "% opóźnień" / "szacunkowy czas" w appce.

Uruchomienie: python3 ml/opoznienia/analyze_frequency.py
"""
import json
from pathlib import Path
from statistics import mean, median

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "train_visualization" / "data"
DELAYS_PATH = Path(__file__).parent / "delays.jsonl"
OUT_PATH = Path(__file__).parent / "frequency_stats.json"

PLAUSIBLE_MAX_SEC = 3 * 3600
DELAY_THRESHOLD_SEC = 5 * 60  # >5 min uznajemy za "opóźniony" dla liczenia %
MIN_SAMPLES = 30              # grupy mniejsze pomijamy — za mało obserwacji na wniosek


def find_scheduled(stops, station_idx, start_from):
    for i in range(start_from, len(stops)):
        if stops[i][0] == station_idx:
            return stops[i][1], stops[i][2], i
    return None


def delay_of(obs, prefer="arr"):
    keys = ("arr_delay_sec", "dep_delay_sec") if prefer == "arr" else ("dep_delay_sec", "arr_delay_sec")
    for k in keys:
        if k in obs:
            return obs[k]
    return None


def final_delay_for(trip, obs):
    """Opóźnienie na mecie trasy, tylko jeśli ten kurs w tej migawce faktycznie dojechał do końca."""
    if not obs:
        return None
    stops = trip["stops"]
    found = find_scheduled(stops, obs[-1]["station_idx"], 0)
    if found is None:
        return None
    _dep, _arr, pos_last = found
    if pos_last != len(stops) - 1:
        return None
    fd = delay_of(obs[-1], prefer="arr")
    if fd is None or abs(fd) > PLAUSIBLE_MAX_SEC:
        return None
    return fd


def group_stats(rows, key):
    groups = {}
    for r in rows:
        groups.setdefault(r[key], []).append(r["final_delay_sec"])
    out = []
    for name, delays in groups.items():
        n = len(delays)
        if n < MIN_SAMPLES:
            continue
        delayed = [d for d in delays if d > DELAY_THRESHOLD_SEC]
        pct_delayed = 100 * len(delayed) / n
        out.append({
            "name": name,
            "n": n,
            "pct_delayed_over_5min": round(pct_delayed, 1),
            "mean_delay_min_all": round(mean(delays) / 60, 1),
            "median_delay_min_if_delayed": round(median(delays) / 60, 1) if delayed else 0.0,
        })
    out.sort(key=lambda r: -r["pct_delayed_over_5min"])
    return out


def main():
    trips = json.loads((DATA_DIR / "pociagi.json").read_text(encoding="utf-8"))["trips"]
    if not DELAYS_PATH.exists():
        print(f"Brak {DELAYS_PATH} — najpierw uruchom collect_delays.py.")
        return

    latest = {}  # (trip_id, start_date) -> ostatni znany kompletny wynik (jsonl jest chronologiczny)
    total_lines = 0
    for line in DELAYS_PATH.read_text(encoding="utf-8").splitlines():
        total_lines += 1
        rec = json.loads(line)
        trip = trips[rec["trip_idx"]]
        fd = final_delay_for(trip, rec["observations"])
        if fd is None:
            continue
        key = (rec["trip_id"], rec.get("start_date"))
        latest[key] = {
            "route": trip.get("route") or "BRAK",
            "op": trip.get("op") or "BRAK",
            "num": trip.get("num"),
            "dest": trip.get("dest"),
            "final_delay_sec": fd,
        }

    rows = list(latest.values())
    print(f"Rekordów (migawek) w delays.jsonl: {total_lines}")
    print(f"Unikalnych ukończonych kursów (trip_id+data): {len(rows)}")
    print(f"(uwaga: mniej niż surowa liczba migawek — ten sam kurs mógł być zapisany "
          f"wielokrotnie zanim dojechał do celu; liczy się tylko raz, jego finalny wynik)\n")

    overall_delayed = sum(1 for r in rows if r["final_delay_sec"] > DELAY_THRESHOLD_SEC)
    print(f"=== CAŁA SIEĆ ===")
    print(f"% kursów opóźnionych >5 min: {100*overall_delayed/len(rows):.1f}%")
    print(f"Mediana opóźnienia (wśród opóźnionych): "
          f"{median([r['final_delay_sec'] for r in rows if r['final_delay_sec'] > DELAY_THRESHOLD_SEC])/60:.1f} min\n")

    by_route = group_stats(rows, "route")
    by_op = group_stats(rows, "op")

    print(f"=== WG KATEGORII (route), min. {MIN_SAMPLES} kursów ===")
    print(f"{'kategoria':<10} {'n':>6} {'% opóźn. >5min':>15} {'śr. opóźn. (min)':>17} {'mediana gdy opóźn. (min)':>25}")
    for r in by_route:
        print(f"{r['name']:<10} {r['n']:>6} {r['pct_delayed_over_5min']:>14.1f}% "
              f"{r['mean_delay_min_all']:>16.1f} {r['median_delay_min_if_delayed']:>24.1f}")

    print(f"\n=== WG PRZEWOŹNIKA (op), min. {MIN_SAMPLES} kursów ===")
    print(f"{'przewoźnik':<10} {'n':>6} {'% opóźn. >5min':>15} {'śr. opóźn. (min)':>17} {'mediana gdy opóźn. (min)':>25}")
    for r in by_op:
        print(f"{r['name']:<10} {r['n']:>6} {r['pct_delayed_over_5min']:>14.1f}% "
              f"{r['mean_delay_min_all']:>16.1f} {r['median_delay_min_if_delayed']:>24.1f}")

    excluded_routes = len(set(r["route"] for r in rows)) - len(by_route)
    excluded_ops = len(set(r["op"] for r in rows)) - len(by_op)
    print(f"\n(pominięto {excluded_routes} kategorii i {excluded_ops} przewoźników "
          f"z <{MIN_SAMPLES} kursami — za mało obserwacji na wiarygodny wniosek)")

    OUT_PATH.write_text(json.dumps({
        "generated_from_snapshots": total_lines,
        "unique_completed_trips": len(rows),
        "delay_threshold_sec": DELAY_THRESHOLD_SEC,
        "min_samples": MIN_SAMPLES,
        "by_route": by_route,
        "by_operator": by_op,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nZapisano {OUT_PATH}")


if __name__ == "__main__":
    main()
