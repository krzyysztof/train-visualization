# Rozwój projektu mapa-polski

## Uruchomienie
```bash
python3 main.py --no-browser --port 8765
```
(bez `--no-browser` otwiera od razu przeglądarkę)

## Struktura
- `mapa_polski/server.py` — stdlib HTTP server + JSON API (opis w docstringu)
- `mapa_polski/trains.py` — silnik pozycji pociągów (interpolacja z GTFS)
- `mapa_polski/web/` — front-end: Leaflet 1.9.4 (vendorowany, bez CDN)
  - `static/app.js` — rdzeń: mapa, stan, event bus, odpytywanie co 1 s
  - `static/trains.js`, `layers.js`, `panel.js`, `controls.js` — moduły
- `mapa_polski/app.py`, `map_canvas.py` — stara wersja Tkinter (legacy, `python -m mapa_polski.app`)
- `scripts/build_data.py` — generuje `mapa_polski/data/` (GTFS, tory, województwa)

## Dane
Dane w `mapa_polski/data/` są **ignorowane przez git** (patrz `.gitignore`) — to pliki
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
- Front-end to Web + Leaflet (świadomie wybrane zamiast Tkinter/tkintermapview).

## Zaplanowane / możliwe kierunki rozwoju
Z rozmów wynikało 8 planowanych usprawnień frontu — sprawdź, co już zrobione, zanim
zaczniesz nowe (część mogła już powstać):
1. kolory operatorów + legenda
2. strzałki kierunku jazdy
3. stacje pojawiające się przy zbliżeniu (zoom)
4. odświeżanie co 1 s
5. panel trasy z ETA
6. wyszukiwarka + filtry operatorów
7. suwak czasu
8. skrypt odświeżania danych (`scripts/build_data.py`)
