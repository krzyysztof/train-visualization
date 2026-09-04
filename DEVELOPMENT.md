# Rozwój projektu train-visualization

## Uruchomienie
```bash
python3 main.py --no-browser --port 8765
```
(bez `--no-browser` otwiera od razu przeglądarkę)

## Struktura
- `train_visualization/server.py` — stdlib HTTP server + JSON API (opis w docstringu)
- `train_visualization/trains.py` — silnik pozycji pociągów (interpolacja z GTFS)
- `train_visualization/web/` — front-end: Leaflet 1.9.4 (vendorowany, bez CDN)
  - `static/app.js` — rdzeń: mapa, stan, event bus, odpytywanie co 1 s
  - `static/trains.js`, `layers.js`, `panel.js`, `controls.js` — moduły
- `scripts/build_data.py` — generuje `train_visualization/data/` (GTFS, tory, województwa)

## Dane
Dane w `train_visualization/data/` są **ignorowane przez git** (patrz `.gitignore`) — to pliki
zawierające konkretne lokalizacje torów, stacji i rozkładów, więc nie trafiają na GitHuba.
Żeby je odtworzyć lokalnie:
```bash
python3 scripts/build_data.py --trains
```
Feed GTFS jest ważny ~miesiąc (`feed_start_date`/`feed_end_date` w `pociagi.json`) —
uruchamiaj `--trains` raz w miesiącu, żeby odświeżyć rozkład.

## Zasady, o których warto pamiętać przy dalszym rozwoju
- **Tylko legalne źródła danych**: żadnego scrapowania portalpasazera.pl (zabronione
  regulaminem). Pozycje pociągów liczone z rozkładu, nie z GPS.
  Oficjalne API PLK "Otwarte Dane Kolejowe" wymaga własnego klucza (rejestracja użytkownika)
  i nie ma GPS, tylko opóźnienia.
- Backend ma zostać **stdlib-only** (bez pip) — świadoma decyzja.
- UI po polsku.
- Front-end to Web + Leaflet (świadomie wybrane zamiast Tkinter/tkintermapview);
  stara wersja Tkinter została usunięta z projektu.

## Zrobione (front + silnik)
- Kolory operatorów + legenda
- Strzałki kierunku jazdy
- Stacje pojawiające się przy zbliżeniu (zoom)
- Odświeżanie co 1 s
- Panel trasy z ETA
- Wyszukiwarka (numer/kategoria/stacja) + filtry operatorów
- Suwak czasu
- Skrypt odświeżania danych (`scripts/build_data.py`)
- Silnik: pozycja rzutowana na rzeczywisty odcinek toru (nie na najbliższy
  wierzchołek uproszczonej geometrii) + krzywa przyspieszenia/hamowania
  (`_ease_in_out` w `trains.py`) zamiast stałej prędkości między stacjami

## Możliwe dalsze kierunki
- Realne opóźnienia z oficjalnego API PLK (patrz README, sekcja „Dodanie
  realnych opóźnień") — wymaga własnego klucza API użytkownika.
