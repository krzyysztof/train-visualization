#!/usr/bin/env python3
"""KROK 2: regresja liniowa od zera. Cechy -> czas przejazdu (sekundy).

Kolejność: podział train/test -> baseline -> cechy (one-hot + standaryzacja)
-> trening (spadek gradientu) -> ewaluacja.

Uruchomienie: python3 ml/prepare_data.py && python3 ml/train.py
"""
import csv
import random
from pathlib import Path

DATASET_PATH = Path(__file__).parent / "dataset.csv"
TEST_FRACTION = 0.2
EPOCHS = 300
LEARNING_RATE = 0.05
TOP_CATEGORIES = 8  # resztę kategorii grupujemy w jedną cechę "INNA"
SEED = 42


def load_rows():
    with DATASET_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- 1. podział

def train_test_split(rows):
    rng = random.Random(SEED)
    shuffled = rows[:]
    rng.shuffle(shuffled)
    cut = int(len(shuffled) * (1 - TEST_FRACTION))
    return shuffled[:cut], shuffled[cut:]


# --------------------------------------------------------------- 2. baseline

def category_speed_baseline(train_rows):
    """Baseline: średnia prędkość per kategoria (ten sam pomysł co animacja pociągów w appce)."""
    total_km = {}
    total_sec = {}
    for r in train_rows:
        cat = r["route"]
        total_km[cat] = total_km.get(cat, 0.0) + float(r["distance_km"])
        total_sec[cat] = total_sec.get(cat, 0.0) + float(r["duration_sec"])
    avg_speed_kmh = {cat: (total_km[cat] / total_sec[cat]) * 3600 for cat in total_km}
    overall_speed = sum(total_km.values()) / sum(total_sec.values()) * 3600

    def predict(row):
        speed = avg_speed_kmh.get(row["route"], overall_speed)
        return float(row["distance_km"]) / speed * 3600

    return predict


# ----------------------------------------------------- 3. przygotowanie cech

def build_feature_encoder(train_rows):
    """Zamienia jeden wiersz CSV na wektor liczb (cechy wejściowe modelu)."""
    counts = {}
    for r in train_rows:
        counts[r["route"]] = counts.get(r["route"], 0) + 1
    top = sorted(counts, key=counts.get, reverse=True)[:TOP_CATEGORIES]
    categories = top + ["INNA"]

    def raw_features(row):
        cat = row["route"] if row["route"] in top else "INNA"
        one_hot = [1.0 if cat == c else 0.0 for c in categories]
        return [float(row["distance_km"]), float(row["hour"])] + one_hot

    feature_names = ["distance_km", "hour"] + [f"kategoria={c}" for c in categories]
    return raw_features, feature_names


def standardize(matrix):
    """Każda kolumna -> średnia 0, odchylenie 1 (inaczej gradient descent jest niestabilny)."""
    n_cols = len(matrix[0])
    means = [sum(row[j] for row in matrix) / len(matrix) for j in range(n_cols)]
    stds = []
    for j in range(n_cols):
        variance = sum((row[j] - means[j]) ** 2 for row in matrix) / len(matrix)
        stds.append(variance ** 0.5 or 1.0)  # unikamy dzielenia przez 0 dla stałej kolumny
    scaled = [[(row[j] - means[j]) / stds[j] for j in range(n_cols)] for row in matrix]
    return scaled, means, stds


# -------------------------------------------------------------------- 4/5. model

def train_linear_regression(X, y, feature_names):
    n, d = len(X), len(X[0])
    weights = [0.0] * d
    bias = 0.0

    for epoch in range(1, EPOCHS + 1):
        # Przewidywania modelu przy obecnych wagach.
        preds = [sum(w * x for w, x in zip(weights, row)) + bias for row in X]
        errors = [p - target for p, target in zip(preds, y)]

        # Gradient funkcji straty (błędu średniokwadratowego) względem każdej wagi.
        grad_w = [sum(e * X[i][j] for i, e in enumerate(errors)) / n for j in range(d)]
        grad_b = sum(errors) / n

        # Krok w stronę mniejszego błędu — to jest dosłownie "uczenie się".
        weights = [w - LEARNING_RATE * g for w, g in zip(weights, grad_w)]
        bias -= LEARNING_RATE * grad_b

        if epoch == 1 or epoch % 50 == 0 or epoch == EPOCHS:
            mse = sum(e * e for e in errors) / n
            print(f"  epoka {epoch:>4}: MSE = {mse:,.0f}  (RMSE ~ {mse ** 0.5 / 60:.2f} min)")

    print("\nNauczone wagi (po standaryzacji cech — porównywalne między sobą):")
    for name, w in sorted(zip(feature_names, weights), key=lambda p: -abs(p[1])):
        kierunek = "wydłuża czas" if w > 0 else "skraca czas"
        print(f"  {name:<20} {w:+7.1f}  ({kierunek})")

    return weights, bias


def predict_linear(row_scaled, weights, bias):
    return sum(w * x for w, x in zip(weights, row_scaled)) + bias


def mae_minutes(predictions, actual):
    return sum(abs(p - a) for p, a in zip(predictions, actual)) / len(actual) / 60


def main():
    rows = load_rows()
    train_rows, test_rows = train_test_split(rows)
    print(f"Wiersze: {len(rows)} (trening: {len(train_rows)}, test: {len(test_rows)})\n")

    print("=== Baseline: średnia prędkość per kategoria pociągu ===")
    baseline_predict = category_speed_baseline(train_rows)
    baseline_preds = [baseline_predict(r) for r in test_rows]
    baseline_actual = [float(r["duration_sec"]) for r in test_rows]
    print(f"Baseline MAE (test): {mae_minutes(baseline_preds, baseline_actual):.2f} min\n")

    print("=== Regresja liniowa (spadek gradientu, od zera) ===")
    encode, feature_names = build_feature_encoder(train_rows)
    X_train_raw = [encode(r) for r in train_rows]
    y_train = [float(r["duration_sec"]) for r in train_rows]
    X_train, means, stds = standardize(X_train_raw)

    weights, bias = train_linear_regression(X_train, y_train, feature_names)

    X_test_raw = [encode(r) for r in test_rows]
    X_test = [[(x - m) / s for x, m, s in zip(row, means, stds)] for row in X_test_raw]
    model_preds = [predict_linear(row, weights, bias) for row in X_test]
    model_actual = [float(r["duration_sec"]) for r in test_rows]
    model_mae = mae_minutes(model_preds, model_actual)
    baseline_mae = mae_minutes(baseline_preds, baseline_actual)

    print(f"\nModel MAE (test): {model_mae:.2f} min")
    print(f"Baseline MAE (test): {baseline_mae:.2f} min")
    improvement = (1 - model_mae / baseline_mae) * 100
    print(f"-> model jest o {improvement:.0f}% dokładniejszy niż baseline" if improvement > 0
          else f"-> model jest GORSZY od baseline'u o {-improvement:.0f}% (to też ważna informacja!)")

    print("\n=== 5 przykładowych przewidywań (test) ===")
    for row, pred, actual in list(zip(test_rows, model_preds, model_actual))[:5]:
        print(f"  {row['route']:<5} {row['from']:<20} -> {row['to']:<20} "
              f"rzeczywiście: {actual/60:.1f} min, model: {pred/60:.1f} min")


if __name__ == "__main__":
    main()
