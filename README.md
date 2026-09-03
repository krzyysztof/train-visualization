# Mapa Polski

Pociągi pasażerskie w Polsce na interaktywnej mapie (Leaflet / OpenStreetMap),
poruszające się zgodnie z rozkładem jazdy. `python main.py` uruchamia lokalny
serwer (sama biblioteka standardowa Pythona) i otwiera mapę w przeglądarce.

Pozycje pociągów są wyliczane **offline, z rozkładu jazdy**, a nie z GPS —
patrz [Dane o pociągach](#dane-o-pociągach), w tym
[kiedy dane są pobierane](#kiedy-dane-są-pobierane) (krótko: raz, skryptem,
nie przy uruchomieniu).

## Wymagania

- Python 3.9+ — **bez żadnych pakietów pip** (serwer to `http.server`, dane to `json`).
- Przeglądarka (Firefox/Chrome/Edge). Leaflet jest w repo, więc jedyny ruch
  sieciowy w trakcie pracy to kafelki mapy z OpenStreetMap.
- Tkinter potrzebny tylko do starego okienkowego UI (`python -m mapa_polski.app`),
  które zostało jako wersja zapasowa.

## Uruchomienie

```bash
python main.py
```

Serwer nasłuchuje na `http://127.0.0.1:8765/` (pierwszy wolny port od 8765;
`--port N` wymusza inny, `--no-browser` nie otwiera przeglądarki). `Ctrl+C` kończy.

## Obsługa mapy

- **Mapa**: zoom kółkiem/przyciskami, przesuwanie przeciąganiem; start w widoku
  województwa mazowieckiego, ale dane obejmują całą Polskę.
- **Pociągi**: strzałka w kolorze przewoźnika (legenda w rogu), obrócona w
  kierunku jazdy; etykieta „kategoria numer" od zoomu 11 (wybrany pociąg
  zawsze). Pociągi płynnie przesuwają się co sekundę.
- **Kliknięcie pociągu**: panel po lewej pokazuje przewoźnika, numer, relację,
  pełną listę stacji z godzinami, podświetloną następną stację i czas do niej,
  a na mapie rysuje się trasa pociągu. `✕` zamyka; kliknięcie w tło mapy odznacza.
- **Wyszukiwarka**: numer pociągu lub kierunek → lista wyników („w trasie" /
  „dziś"); kliknięcie wybiera pociąg i centruje na nim mapę.
- **Filtr przewoźników**: pola wyboru, „wszystkie" / „żaden".
- **Suwak czasu**: przewiń dobę, żeby zobaczyć, gdzie pociągi są np. o 7:30
  (pasek statusu pokazuje „Symulacja HH:MM:SS"); „Teraz" wraca do zegara.
- **Warstwy**: linie kolejowe (zawsze) i stacje (od zoomu 11, nazwy od 14) —
  przełącznik w prawym górnym rogu.

## Struktura projektu

```
mapa-polski/
├── main.py                        punkt wejścia: serwer + przeglądarka
├── scripts/
│   └── build_data.py              regeneracja danych (GTFS + OSM) — patrz „Odświeżanie danych"
├── mapa_polski/
│   ├── server.py                  serwer HTTP i JSON API (opis endpointów w docstringu)
│   ├── trains.py                  silnik: pozycje, kierunek, trasa, ETA, wyszukiwanie
│   ├── web/
│   │   ├── index.html
│   │   └── static/
│   │       ├── app.js / app.css    rdzeń: mapa Leaflet, stan, szyna zdarzeń, odpytywanie co 1 s
│   │       ├── trains.js/.css      znaczniki pociągów (kolory, strzałki, etykiety, wybór)
│   │       ├── layers.js/.css      linie kolejowe i stacje
│   │       ├── panel.js/.css       panel trasy wybranego pociągu + trasa na mapie
│   │       ├── controls.js/.css    wyszukiwarka, suwak czasu, filtry, legenda
│   │       └── leaflet/            Leaflet 1.9.4 (BSD-2-Clause, plik LICENSE obok)
│   ├── data/
│   │   ├── wojewodztwa.geojson    granice województw (GUGiK, MIT)
│   │   ├── tory.geojson           sieć linii kolejowych (OpenStreetMap, ODbL)
│   │   ├── stacje.json            stacje: id, nazwa, lat, lon (GTFS)
│   │   └── pociagi.json           rozkład jazdy: kalendarze, kształty tras, kursy (GTFS)
│   ├── app.py, map_canvas.py      stare UI Tkinter (`python -m mapa_polski.app`)
└── README.md
```

## Jak to działa

- `trains.py` wczytuje rozkład raz i dla zadanej chwili wylicza, między którymi
  stacjami jest każdy kurs, interpolując położenie wzdłuż rzeczywistego kształtu
  trasy (GTFS `shapes`) proporcjonalnie do upływu czasu planowego. Zwraca też
  kierunek jazdy, pełną trasę z godzinami i czas do następnej stacji.
- `server.py` wystawia to jako JSON: `/api/meta`, `/api/positions`,
  `/api/trip/<id>`, `/api/search` (parametr `t=HH:MM` = „udawaj, że jest ta
  godzina" — tak działa suwak czasu) oraz serwuje pliki statyczne i dane.
- Front-end: `app.js` trzyma mapę i stan, odpytuje `/api/positions` co sekundę i
  rozgłasza zdarzenia (`positions`, `select`, `time`, `filter`, `zoom`, `move`);
  każdy moduł tylko na nie reaguje i zmienia stan przez `App.select`,
  `App.setSimTime`, `App.setOperatorHidden`. Kontrakt jest opisany na początku
  [app.js](mapa_polski/web/static/app.js).

## Dane geograficzne

Granice województw pochodzą z projektu
[ppatrzyk/polska-geojson](https://github.com/ppatrzyk/polska-geojson)
(licencja MIT), który udostępnia dane Głównego Urzędu Geodezji i Kartografii
w formacie GeoJSON.

Sieć linii kolejowych (`mapa_polski/data/tory.geojson`) pochodzi z bazy
OpenStreetMap — dane pobrano przez Overpass API (linie o tagu `railway=rail`
na terenie Polski, z pominięciem torów bocznicowych i manewrowych) i
uproszczono geometrię algorytmem Douglasa-Peuckera do tolerancji ok. 50 m.
Zgodnie z licencją danych OpenStreetMap (Open Database License, ODbL) należy
podać: „© OpenStreetMap contributors", z odnośnikiem do
<https://www.openstreetmap.org/copyright>.

Kafelki mapy w przeglądarce również pochodzą z OpenStreetMap
(`tile.openstreetmap.org`, atrybucja widoczna w rogu mapy). To serwery
utrzymywane przez wolontariuszy — użycie osobiste/hobbystyczne jest w porządku,
ale przy publicznym wdrożeniu należy przejść na własny/komercyjny dostawca kafelków
zgodnie z <https://operations.osmfoundation.org/policies/tiles/>.

Biblioteka Leaflet (`mapa_polski/web/static/leaflet/`) jest na licencji
BSD-2-Clause (© Volodymyr Agafonkin) — tekst licencji leży obok plików.

## Dane o pociągach

Pozycje pociągów (`mapa_polski/data/pociagi.json`, `stacje.json`) są wyliczane
offline na podstawie statycznego rozkładu jazdy GTFS pobranego z
[mkuran.pl/gtfs](https://mkuran.pl/gtfs/) (licencja **CC BY 4.0**),
agregującego dane rozkładowe PKP PLK oraz przewoźników (PolRegio, PKP
Intercity, Koleje Mazowieckie, PKP SKM Trójmiasto, Koleje Śląskie, Koleje
Dolnośląskie, Koleje Wielkopolskie, SKM Warszawa, Łódzka Kolej Aglomeracyjna,
Koleje Małopolskie, Arriva RP, RegioJet, Leo Express). Zgodnie z licencją
CC BY 4.0 należy podać źródło: **dane rozkładowe: Mikołaj Kuranowski
(mkuran.pl/gtfs), na podstawie danych PKP Polskie Linie Kolejowe S.A.**

**Uwaga:** wyświetlana pozycja pociągu wynika wyłącznie z rozkładu jazdy —
pociąg jadący z opóźnieniem będzie na mapie pokazywany dalej na trasie, niż
faktycznie się znajduje.

### Kiedy dane są pobierane?

**Ani przy starcie, ani podczas przeglądania mapy — aplikacja nigdy sama nie
pobiera rozkładu ani torów.** Zostały one pobrane i przetworzone skryptem
`scripts/build_data.py` do statycznych plików w `mapa_polski/data/`; serwer
tylko czyta te pliki z dysku. Jedyne, co w trakcie pracy idzie przez sieć, to
kafelki mapy z OpenStreetMap, które ściąga przeglądarka. Odpytywanie
`/api/positions` co sekundę to lokalne obliczenia na już wczytanym rozkładzie.

Rozkład ma okno ważności (`feed_start_date`–`feed_end_date` w `pociagi.json`,
zwykle ok. miesiąca) — po jego przekroczeniu mapa będzie pusta, dopóki nie
odświeżysz danych.

### Odświeżanie danych

Pliki `stacje.json`, `pociagi.json` i `tory.geojson` generuje
`scripts/build_data.py` (wyłącznie biblioteka standardowa Pythona 3.9+):

```bash
python3 scripts/build_data.py            # rozkład jazdy (GTFS z mkuran.pl) + tory (OSM/Overpass)
```

```bash
python3 scripts/build_data.py --trains   # tylko stacje.json + pociagi.json (~10 s)
```

```bash
python3 scripts/build_data.py --tracks   # tylko tory.geojson (zapytanie Overpass trwa ok. 2 min)
```

Przydatne opcje: `--keep-downloads` (zachowuje surowe pobrania w katalogu
cache — kolejne uruchomienia nie łączą się z siecią), `--cache-dir KATALOG`,
`--out KATALOG` (np. żeby najpierw wygenerować dane obok i porównać). Skrypt
loguje postęp na stderr, na końcu sprawdza spójność plików (indeksy
stacji/kalendarzy/tras, monotoniczność czasów) i wczytuje je przez
`TrainSchedule`; kończy się kodem 0 przy sukcesie, 1 przy błędzie walidacji,
2 gdy nie udało się nic pobrać. Pliki są zapisywane atomowo — nieudane
pobranie (np. przeciążony Overpass) nie nadpisuje istniejącego `tory.geojson`.
`--trains` warto uruchamiać mniej więcej co miesiąc. Pełny opis formatu plików
jest w docstringu skryptu.

### Dodanie realnych opóźnień (opcjonalnie)

PKP PLK udostępnia darmowe, oficjalne API „Otwarte Dane Kolejowe"
(<https://pdp-api.plk-sa.pl>) z opóźnieniami w czasie rzeczywistym (endpoint
`/api/v1/operations`). **Nie zawiera współrzędnych GPS** — tylko planowane i
rzeczywiste czasy per stacja — ale to wystarczy, by skorygować wyliczaną tu
pozycję (przesunąć czas używany do interpolacji o opóźnienie).

Klucz API trzeba założyć samodzielnie na stronie PLK (podanie adresu e-mail,
zatwierdzenie 3-5 dni roboczych). Po jego otrzymaniu naturalne miejsce na
integrację to `server.py` (okresowe pobieranie opóźnień) i
`TrainSchedule._locate` w `trains.py` (korekta czasu).

## Rozbudowa

- Nowa informacja w API → `server.py` (`_handle_api`) i `trains.py`.
- Nowa warstwa/kontrolka na mapie → osobny plik w `web/static/`, dopisany do
  `index.html`, reagujący na zdarzenia `App.on(...)` z `app.js`.
- Stare UI Tkinter (`app.py`, `map_canvas.py`) korzysta z tego samego
  `trains.py`, więc dalej działa, ale nowe funkcje trafiają tylko do wersji web.
