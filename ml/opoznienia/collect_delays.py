#!/usr/bin/env python3
"""Zbiera prawdziwe opóźnienia pociągów — WYŁĄCZNIE na ręczne polecenie, bez
żadnego crona/pętli. Każde uruchomienie = jedno świadome pobranie.

Źródło: https://mkuran.pl/gtfs/polish_trains/updates.json (GTFS-Realtime).
Licencja: CC BY 4.0 (Mikołaj Kuranowski, dane PKP PLK), nieodwołalna dla już
pobranych danych — dowód warunków: PROVENANCE.md w tym katalogu.

Dopasowuje kursy do rozkładu (po ID, potem przewoźnik+numer), liczy opóźnienie
= rzeczywisty czas − rozkład, dopisuje do delays.jsonl tylko nowe/zmienione
obserwacje (bez duplikatów przy powtórnym uruchomieniu bez zmian w feedzie).

Uruchomienie: python3 ml/opoznienia/collect_delays.py
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "train_visualization" / "data"
OUT_DIR = Path(__file__).parent
DELAYS_PATH = OUT_DIR / "delays.jsonl"
STATE_PATH = OUT_DIR / ".collector_state.json"
PROVENANCE_PATH = OUT_DIR / "PROVENANCE.md"
LICENSE_SNAPSHOT_PATH = OUT_DIR / "mkuran_gtfs_page_snapshot.html"

FEED_URL = "https://mkuran.pl/gtfs/polish_trains/updates.json"
LICENSE_PAGE_URL = "https://mkuran.pl/gtfs/"
USER_AGENT = "train-visualization/1.0 delay-collector (manual, on-demand; CC BY 4.0 use)"


def log(msg):
    print(msg, file=sys.stderr)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def ensure_provenance():
    """Pierwsze uruchomienie: zapisz DOWÓD warunków licencji obowiązujących w tym momencie."""
    if PROVENANCE_PATH.exists():
        return
    now = datetime.now().astimezone().isoformat()
    log("Pierwsze uruchomienie — zapisuję dowód licencji (PROVENANCE.md + zrzut strony)...")
    try:
        req = urllib.request.Request(LICENSE_PAGE_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read()
        LICENSE_SNAPSHOT_PATH.write_bytes(html)
        snapshot_note = f"Zrzut strony zapisany: {LICENSE_SNAPSHOT_PATH.name}"
    except Exception as e:
        snapshot_note = f"NIE udało się zapisać zrzutu strony ({e}) — sam URL i data poniżej są nadal dowodem."

    PROVENANCE_PATH.write_text(
        f"""# Pochodzenie danych o opóźnieniach

- **Źródło**: {FEED_URL}
- **Licencja**: CC BY 4.0 — Mikołaj Kuranowski (mkuran.pl/gtfs), na podstawie
  danych PKP Polskie Linie Kolejowe S.A. Strona: {LICENSE_PAGE_URL}
- **Zbieranie rozpoczęte**: {now}
- **{snapshot_note}**

CC BY 4.0 jest **nieodwołalna** dla już pobranych danych — zmiana licencji lub
zamknięcie strony przez autora nie unieważnia praw do tego, co już ściągnęliśmy.

Wymagany podpis przy dalszym wykorzystaniu: *dane o opóźnieniach: Mikołaj
Kuranowski (mkuran.pl/gtfs), na podstawie danych PKP Polskie Linie Kolejowe
S.A., licencja CC BY 4.0.*

Zbieranie wyłącznie ręczne (bez crona/pętli). `delays.jsonl` nie jest w git
(`.gitignore`).
""",
        encoding="utf-8",
    )
    log(f"Zapisano {PROVENANCE_PATH.name}")


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def build_lookup(trips):
    by_id = {}
    by_op_num = {}
    for idx, trip in enumerate(trips):
        by_id[trip["id"]] = idx
        for part in str(trip.get("num", "")).split("/"):
            by_op_num.setdefault((trip.get("op", ""), part), idx)
    return by_id, by_op_num


def match_trip(tu, by_id, by_op_num):
    idx = by_id.get(tu["trip_id"])
    if idx is not None:
        return idx
    for num in tu.get("numbers", []):
        idx = by_op_num.get((tu.get("agency_id", ""), num))
        if idx is not None:
            return idx
    return None


def compute_observations(trip, tu):
    """Zwraca listę {station_idx, arr_delay_sec?, dep_delay_sec?, confirmed} dla przystanków z realnym czasem."""
    stops = trip["stops"]
    start_date = tu.get("start_date")
    if not start_date:
        return []
    obs = []
    base = None
    for st in tu.get("stop_times", []):
        seq = st.get("stop_sequence")
        if seq is None or seq >= len(stops):
            continue
        station_idx, dep_sec, arr_sec = stops[seq]
        row = {"station_idx": station_idx, "confirmed": bool(st.get("confirmed"))}
        has_time = False
        if st.get("arrival"):
            actual = datetime.fromisoformat(st["arrival"])
            if base is None:
                base = datetime.fromisoformat(start_date + "T00:00:00").replace(tzinfo=actual.tzinfo)
            scheduled = base + timedelta(seconds=arr_sec)
            row["arr_delay_sec"] = round((actual - scheduled).total_seconds())
            has_time = True
        if st.get("departure"):
            actual = datetime.fromisoformat(st["departure"])
            if base is None:
                base = datetime.fromisoformat(start_date + "T00:00:00").replace(tzinfo=actual.tzinfo)
            scheduled = base + timedelta(seconds=dep_sec)
            row["dep_delay_sec"] = round((actual - scheduled).total_seconds())
            has_time = True
        if has_time:
            obs.append(row)
    return obs


def main():
    log("=" * 70)
    log("RĘCZNE pobranie danych o opóźnieniach — uruchomione przez użytkownika teraz.")
    log(f"Źródło: {FEED_URL}")
    log("Licencja: CC BY 4.0 (Mikołaj Kuranowski / mkuran.pl, na podst. danych PKP PLK)")
    log("Ten skrypt nic nie planuje ani nie zapętla — to jednorazowe pobranie.")
    log("=" * 70)

    ensure_provenance()

    trips = json.loads((DATA_DIR / "pociagi.json").read_text(encoding="utf-8"))["trips"]
    by_id, by_op_num = build_lookup(trips)

    log("Pobieram żywy feed...")
    feed = fetch_json(FEED_URL)
    feed_time = feed.get("timestamp")
    log(f"Feed z znacznikiem czasu: {feed_time}, kursów w odpowiedzi: {len(feed.get('trip_updates', []))}")

    state = load_state()
    collected_at = datetime.now().astimezone().isoformat()
    written = 0
    matched = 0

    with DELAYS_PATH.open("a", encoding="utf-8") as out:
        for tu in feed.get("trip_updates", []):
            trip_idx = match_trip(tu, by_id, by_op_num)
            if trip_idx is None:
                continue
            matched += 1
            trip = trips[trip_idx]
            obs = compute_observations(trip, tu)
            if not obs:
                continue

            fingerprint_key = f"{tu['trip_id']}|{tu.get('start_date')}"
            fingerprint = json.dumps(obs, sort_keys=True)
            if state.get(fingerprint_key) == fingerprint:
                continue  # identyczne jak przy ostatnim pobraniu — nic nowego, nie duplikuj

            record = {
                "collected_at": collected_at,
                "feed_timestamp": feed_time,
                "trip_id": tu["trip_id"],
                "start_date": tu.get("start_date"),
                "trip_idx": trip_idx,
                "op": trip.get("op"),
                "route": trip.get("route"),
                "num": trip.get("num"),
                "dest": trip.get("dest"),
                "observations": obs,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            state[fingerprint_key] = fingerprint
            written += 1

    save_state(state)
    log(f"Dopasowano do naszego rozkładu: {matched} kursów.")
    log(f"Zapisano {written} nowych/zmienionych obserwacji do {DELAYS_PATH.name}"
        f" (pominięto {matched - written} bez zmian od ostatniego pobrania).")
    log("Gotowe. Nic więcej się nie wydarzy, dopóki znowu ręcznie nie uruchomisz tego skryptu.")


if __name__ == "__main__":
    main()
