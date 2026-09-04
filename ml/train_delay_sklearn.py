#!/usr/bin/env python3
"""train_delay.py przez scikit-learn: regresja liniowa + las losowy. Wymaga `.venv`.

Uruchomienie: source .venv/bin/activate && python3 ml/train_delay_sklearn.py
"""
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

DATASET_PATH = Path(__file__).parent / "delay_pairs.csv"
PLOT_PATH = Path(__file__).parent / "wyniki_opoznienia.png"
TEST_FRACTION = 0.2
SEED = 42
NUMERIC = ["current_delay_sec", "remaining_scheduled_sec", "stops_remaining", "hour"]
CATEGORICAL = ["route", "op"]


def load_dataframe():
    import pandas as pd

    rows = list(csv.DictReader(DATASET_PATH.open(encoding="utf-8")))
    df = pd.DataFrame(rows)
    for col in NUMERIC:
        df[col] = df[col].astype(float)
    df["final_delay_sec"] = df["final_delay_sec"].astype(float)
    return df


def main():
    df = load_dataframe()
    X = df[NUMERIC + CATEGORICAL]
    y = df["final_delay_sec"]
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=TEST_FRACTION, random_state=SEED)
    print(f"Przykłady: {len(df)} (trening: {len(X_train)}, test: {len(X_test)})\n")

    baseline_preds = X_test["current_delay_sec"].values
    baseline_mae = mean_absolute_error(y_test, baseline_preds) / 60
    print(f"{'Baseline (opóźnienie się nie zmienia)':<38} MAE: {baseline_mae:.2f} min")

    preprocess = ColumnTransformer(
        [
            ("liczby", StandardScaler(), NUMERIC),
            ("kategoria", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL),
        ]
    )
    models = {
        "Regresja liniowa (sklearn)": LinearRegression(),
        "Las losowy (200 drzew)": RandomForestRegressor(n_estimators=200, max_depth=14, random_state=SEED, n_jobs=-1),
    }

    results = {}
    for name, model in models.items():
        pipeline = Pipeline([("prep", preprocess), ("model", model)])
        pipeline.fit(X_train, y_train)
        preds = pipeline.predict(X_test)
        mae_min = mean_absolute_error(y_test, preds) / 60
        results[name] = (mae_min, preds)
        vs_baseline = (1 - mae_min / baseline_mae) * 100
        znak = "lepszy" if vs_baseline > 0 else "gorszy"
        print(f"{name:<38} MAE: {mae_min:.2f} min  ({abs(vs_baseline):.1f}% {znak} niż baseline)")

    best_name = min(results, key=lambda k: results[k][0])
    best_mae, best_preds = results[best_name]
    y_test_min = np.array(y_test) / 60
    preds_min = np.array(best_preds) / 60

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.scatter(y_test_min, preds_min, s=4, alpha=0.15, color="#1f6fb4")
    lo, hi = min(y_test_min.min(), preds_min.min()), max(y_test_min.max(), preds_min.max())
    ax.plot([lo, hi], [lo, hi], color="#d1495b", linewidth=1, label="idealne przewidywanie")
    ax.set_xlabel("rzeczywiste opóźnienie na mecie (min)")
    ax.set_ylabel("przewidziane opóźnienie na mecie (min)")
    ax.set_title(f"{best_name}\nMAE = {best_mae:.2f} min (baseline: {baseline_mae:.2f} min), {len(y_test)} przykładów")
    ax.legend()
    fig.tight_layout()
    fig.savefig(PLOT_PATH, dpi=140)
    print(f"\nWykres zapisany do {PLOT_PATH}")

    # `preprocess` i model lasu losowego są już dopasowane (ten sam obiekt co w pętli
    # powyżej — Pipeline trzyma referencje, nie kopie) — czytamy z nich bez ponownego fit().
    cat_names = preprocess.named_transformers_["kategoria"].get_feature_names_out(CATEGORICAL)
    feat_names = NUMERIC + list(cat_names)
    importances = models["Las losowy (200 drzew)"].feature_importances_
    print("\nNajważniejsze cechy wg lasu losowego:")
    for name, imp in sorted(zip(feat_names, importances), key=lambda p: -p[1])[:8]:
        print(f"  {name:<28} {imp:.3f}")


if __name__ == "__main__":
    main()
