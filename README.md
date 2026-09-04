# Mapa Polski

Pociągi pasażerskie w Polsce na mapie (Leaflet/OpenStreetMap), poruszające się
zgodnie z rozkładem. `python main.py` startuje lokalny serwer i otwiera mapę.

Pozycje liczone **offline z rozkładu**, nie z GPS. Nigdy nie łączy się z siecią
sam — patrz [Dane o pociągach](#dane-o-pociągach).

## Wymagania

Python 3.9+, **bez pip**. Przeglądarka. Leaflet jest w repo — jedyny ruch
sieciowy w trakcie pracy to kafelki mapy z OpenStreetMap.

## Uruchomienie

```bash
python main.py
```

`http://127.0.0.1:8765/` (pierwszy wolny port od 8765; `--port N`,
`--no-browser`). `Ctrl+C` kończy.

## Obsługa mapy

- **Mapa**: zoom kółkiem/przyciskami, przeciąganie; start na Mazowszu, dane = cała Polska.
- **Pociągi**: strzałka w kolorze przewoźnika, kierunek jazdy, etykieta od zoomu 11.
  Prędkość liczona z fizyki (zakręty, rozpędzanie/hamowanie — patrz „Jak to działa"),
  nie GPS.
- **Klik**: panel z trasą, ETA, prędkością; trasa rysuje się na mapie. `✕` zamyka.
- **Szukaj**: numer, kategoria, nazwa, stacja — wybiera i centruje.
- **Filtr przewoźników**, **suwak czasu** (symulacja `HH:MM`), **warstwy**
  (tory zawsze, stacje od zoomu 11) w prawym górnym rogu.

## Struktura

```
train-visualization/
├── main.py                  wejście: serwer + przeglądarka
├── scripts/build_data.py    generuje dane (patrz „Odświeżanie danych")
├── train_visualization/
│   ├── server.py             HTTP + JSON API (opis w docstringu)
│   ├── trains.py             silnik: pozycje, prędkość, trasa, ETA
│   ├── web/static/           app.js (rdzeń) + trains/layers/panel/controls (.js/.css)
│   │                         + leaflet/ (biblioteka)
│   └── data/                 wojewodztwa.geojson, tory.geojson, stacje.json, pociagi.json
└── ml/                       materiał do nauki ML, osobno (ml/README.md)
```

## Jak to działa

`trains.py` liczy pozycję i prędkość z fizycznego profilu (nie stałej
prędkości): krzywizna toru ogranicza prędkość na zakrętach, kinematyka
rządzi rozpędzaniem/hamowaniem, sufit prędkości zależy od kategorii (EIP
~200 km/h, REG ~110 km/h — wartości orientacyjne, nie pomiar). Całość
przeskalowana do dokładnego czasu z rozkładu. Pozycja idzie wzdłuż
prawdziwego kształtu trasy (GTFS `shapes`), nie po prostej.

`server.py` wystawia to jako JSON (`/api/meta`, `/api/positions`,
`/api/trip/<id>`, `/api/search`; `t=HH:MM` = suwak czasu). Front (`app.js`)
odpytuje co sekundę i rozgłasza zdarzenia; moduły reagują i zmieniają stan
przez `App.select`/`setSimTime`/`setOperatorHidden`.

## Dane geograficzne

- Granice województw: [ppatrzyk/polska-geojson](https://github.com/ppatrzyk/polska-geojson) (MIT, dane GUGiK).
- Tory (`tory.geojson`): OpenStreetMap/Overpass, uproszczone (Douglas-Peucker ~50m).
  Licencja ODbL: „© OpenStreetMap contributors”, <https://www.openstreetmap.org/copyright>.
- Kafelki mapy: OpenStreetMap (`tile.openstreetmap.org`) — OK do użytku
  osobistego; publiczne wdrożenie wymaga własnego dostawcy kafelków.
- Leaflet: BSD-2-Clause (© Volodymyr Agafonkin), LICENSE obok plików.

## Dane o pociągach

Rozkład (`stacje.json`, `pociagi.json`): [mkuran.pl/gtfs](https://mkuran.pl/gtfs/)
(**CC BY 4.0**), agreguje dane PKP PLK i przewoźników. Wymagany podpis: *dane
rozkładowe: Mikołaj Kuranowski (mkuran.pl/gtfs), na podstawie danych PKP
Polskie Linie Kolejowe S.A.*

Pozycja = wyliczona z rozkładu, nie GPS — spóźniony pociąg wygląda jakby był
dalej na trasie niż faktycznie jest.

### Kiedy dane są pobierane?

Nigdy automatycznie. `scripts/build_data.py` pobiera je raz, ręcznie; serwer
tylko czyta pliki z dysku. Jedyny ruch sieciowy w trakcie pracy to kafelki
mapy (przeglądarka). Rozkład ma okno ważności (`feed_start_date`/`feed_end_date`
w `pociagi.json`, ~miesiąc) — po nim mapa będzie pusta, dopóki nie odświeżysz.

### Odświeżanie danych

```bash
python3 scripts/build_data.py            # rozkład + tory
python3 scripts/build_data.py --trains   # tylko rozkład (~10 s)
python3 scripts/build_data.py --tracks   # tylko tory (~2 min, Overpass)
```

Opcje: `--keep-downloads`, `--cache-dir`, `--out`. Zapis atomowy (nieudane
pobranie nie nadpisuje istniejących danych). Kod wyjścia 0/1/2 = sukces/błąd
walidacji/brak pobrania. Pełny opis formatu — docstring skryptu.

### Realne opóźnienia (opcjonalnie)

Oficjalne API PLK „Otwarte Dane Kolejowe” (<https://pdp-api.plk-sa.pl>) daje
opóźnienia (bez GPS) — wymaga własnego klucza (rejestracja e-mail, 3-5 dni).
Integracja: `server.py` (pobieranie) + `trains.py` `_locate` (korekta czasu).

Zobacz też `ml/` — tam już zbieramy realne opóźnienia z legalnego, darmowego
źródła bez rejestracji (`ml/opoznienia/collect_delays.py`).

## Rozbudowa

Nowe API → `server.py`/`trains.py`. Nowa warstwa/kontrolka na mapie → plik w
`web/static/`, dopisany do `index.html`, reagujący na zdarzenia `App.on(...)`.
