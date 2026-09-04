# ML na danych z projektu (materiał do nauki, poza główną appką)

Zero zależności do wersji podstawowej (`train.py`, `train_delay.py` — regresja
liniowa od zera). Wersje `*_sklearn.py` wymagają `.venv`:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install numpy pandas scikit-learn matplotlib
```

## Ćwiczenie 1: czas przejazdu odcinka

```bash
python3 ml/prepare_data.py && python3 ml/train.py
python3 ml/train_sklearn.py   # + las losowy, wymaga .venv
```

Etykieta = to, co już wiemy z rozkładu (ćwiczenie na mechanikę, nie realna wartość).

| model | MAE |
|---|---|
| baseline (śr. prędkość/kategoria) | 1.60 min |
| regresja ręczna | 1.31 min |
| regresja sklearn | 1.24 min |
| **las losowy** | **0.58 min** |

Wykres: `wyniki.png`.

## Ćwiczenie 2: opóźnienie na mecie trasy (prawdziwe dane)

```bash
python3 ml/opoznienia/collect_delays.py   # RĘCZNIE, tylko na Twoje polecenie
python3 ml/prepare_delay_pairs.py
python3 ml/train_delay.py
python3 ml/train_delay_sklearn.py         # wymaga .venv
```

**Źródło**: `mkuran.pl/gtfs` (CC BY 4.0, dane PKP PLK) — **nie** portalpasazera.pl
(regulamin zabrania zbierania danych, nawet ręcznego). Dowód warunków licencji:
`ml/opoznienia/PROVENANCE.md`. Zbieranie wyłącznie ręczne, bez cronów/pętli.

Etykieta = realne opóźnienie, którego nikt nie zna z góry.

| model | MAE |
|---|---|
| baseline ("bez zmian") | 2.10 min |
| regresja sklearn | 2.40 min (gorsza) |
| las losowy | 2.08 min (+0,7%) |

Żaden model sensownie nie bije baseline'u — `feature_importances_` pokazuje,
że 81% decyzji to samo `current_delay_sec`. Wniosek: z jednego dnia danych nie
da się nauczyć, kiedy pociąg odrabia opóźnienie (zależy od zapasu w rozkładzie,
ruchu innych pociągów, pogody). Potrzeba wielu dni, nie lepszego modelu —
uruchamiaj `collect_delays.py` w kolejne dni. Wykres: `wyniki_opoznienia.png`.

(Pierwsza wersja pytała o NASTĘPNĄ stację — bez sensu, bo stacje są za blisko
w czasie żeby cokolwiek się zmieniło; stąd przejście na "opóźnienie na mecie".)

## Struktura

```
ml/
├── prepare_data.py / train.py / train_sklearn.py / wyniki.png       — ćw. 1
├── prepare_delay_pairs.py / train_delay*.py / wyniki_opoznienia.png — ćw. 2
└── opoznienia/collect_delays.py + PROVENANCE.md                     — zbieracz
```

`dataset.csv`, `delay_pairs.csv`, `opoznienia/delays.jsonl` i inne dane —
w `.gitignore`, nie w repo. Kod je odtwarza.

## Backlog

- Więcej dni opóźnień (ręcznie, `collect_delays.py`)
- Klastrowanie stacji / wykrywanie anomalii — niezaczęte
- Realne opóźnienia z API PLK (wymaga własnego klucza)
