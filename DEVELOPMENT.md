# Rozwój projektu train-visualization

```bash
python3 main.py --no-browser --port 8765
```

## Struktura
- `server.py` — HTTP + JSON API
- `trains.py` — silnik pozycji/prędkości (fizyka + GTFS)
- `web/` — Leaflet 1.9.4, front w `static/` (app.js + moduły)
- `scripts/build_data.py` — generuje `data/` (GTFS, tory, województwa)
- `ml/` — materiał do nauki ML, osobno od appki (`ml/README.md`)

## Dane
`data/` jest w `.gitignore` (regulaminy źródeł zabraniają redystrybucji).
Odtwórz: `python3 scripts/build_data.py --trains`. Feed ważny ~miesiąc
(`feed_start_date`/`feed_end_date` w `pociagi.json`).

## Zasady
- Tylko legalne źródła — zero scrapowania portalpasazera.pl.
- Backend stdlib-only, bez pip.
- UI po polsku. Front: Web+Leaflet (nie Tkinter — usunięty).

## Zrobione
Kolory/legenda, strzałki kierunku, stacje od zoomu, odświeżanie co 1s, panel
z ETA, wyszukiwarka, filtry, suwak czasu, `build_data.py`, fizyczny profil
prędkości (krzywizna toru, kinematyka, sufit wg kategorii) widoczny w
panelu/dymku, `ml/`: regresja czasu przejazdu + przewidywanie opóźnień na
realnych danych (patrz `ml/README.md`).

## Backlog
- Realne opóźnienia z API PLK (własny klucz)
- `ml/`: więcej dni danych, klastrowanie stacji, anomalie
