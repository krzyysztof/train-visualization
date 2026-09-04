#!/usr/bin/env python3
"""Ten sam problem co train.py, przez scikit-learn: regresja liniowa + las losowy
(model nieliniowy, do porównania). Wymaga `.venv` (numpy/pandas/scikit-learn/matplotlib).

Uruchomienie: source .venv/bin/activate && python3 ml/train_sklearn.py
"""
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.compose import ColumnTransformer

DATASET_PATH = Path(__file__).parent / "dataset.csv"
PLOT_PATH = Path(__file__).parent / "wyniki.png"
TEST_FRACTION = 0.2
SEED = 42


def load_dataframe():
    import pandas as pd

    rows = list(csv.DictReader(DATASET_PATH.open(encoding="utf-8")))
    df = pd.DataFrame(rows)
    df["distance_km"] = df["distance_km"].astype(float)
    df["hour"] = df["hour"].astype(int)
    df["duration_sec"] = df["duration_sec"].astype(float)
    return df


def main():
    df = load_dataframe()
    X = df[["distance_km", "hour", "route"]]
    y = df["duration_sec"]
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=TEST_FRACTION, random_state=SEED)
    print(f"Wiersze: {len(df)} (trening: {len(X_train)}, test: {len(X_test)})\n")

    # Te same dwa kroki co ręcznie w train.py (standaryzacja liczb, one-hot dla
    # kategorii) — tu ColumnTransformer robi to deklaratywnie w jednej linii na cechę.
    preprocess = ColumnTransformer(
        [
            ("liczby", StandardScaler(), ["distance_km", "hour"]),
            ("kategoria", OneHotEncoder(handle_unknown="ignore"), ["route"]),
        ]
    )

    models = {
        "Regresja liniowa (sklearn)": LinearRegression(),
        "Las losowy (100 drzew)": RandomForestRegressor(n_estimators=100, max_depth=12, random_state=SEED, n_jobs=-1),
    }

    results = {}
    for name, model in models.items():
        pipeline = Pipeline([("prep", preprocess), ("model", model)])
        pipeline.fit(X_train, y_train)
        preds = pipeline.predict(X_test)
        mae_min = mean_absolute_error(y_test, preds) / 60
        results[name] = (mae_min, preds)
        print(f"{name:<30} MAE: {mae_min:.2f} min")

    print("\n(dla porównania: ręczna regresja liniowa w train.py wyszła ~1.31 min,")
    print(" baseline 'średnia prędkość per kategoria' ~1.60 min)")

    # Wykres: przewidywane vs rzeczywiste, dla najlepszego modelu.
    best_name = min(results, key=lambda k: results[k][0])
    best_mae, best_preds = results[best_name]
    y_test_min = np.array(y_test) / 60
    preds_min = np.array(best_preds) / 60

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.scatter(y_test_min, preds_min, s=4, alpha=0.15, color="#1f6fb4")
    limit = max(y_test_min.max(), preds_min.max())
    ax.plot([0, limit], [0, limit], color="#d1495b", linewidth=1, label="idealne przewidywanie")
    ax.set_xlabel("rzeczywisty czas przejazdu (min)")
    ax.set_ylabel("przewidziany czas przejazdu (min)")
    ax.set_title(f"{best_name}\nMAE = {best_mae:.2f} min, {len(y_test)} przykładów testowych")
    ax.legend()
    fig.tight_layout()
    fig.savefig(PLOT_PATH, dpi=140)
    print(f"\nWykres zapisany do {PLOT_PATH}")


if __name__ == "__main__":
    main()
