"""Shared, calendar-based expanding-window validation contract."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


WARMUP_DAYS = 28


def rolling_splits(dates, train_days=365, test_days=30, step_days=30,
                   warmup_days=WARMUP_DAYS, include_partial=False):
    values = pd.DatetimeIndex(sorted(pd.to_datetime(pd.Series(dates).dropna().unique())))
    if not len(values):
        return
    if len(values) > 1 and not (values[1:] - values[:-1] == pd.Timedelta(days=1)).all():
        raise ValueError("滚动验证要求连续自然日，发现日期缺口")
    first_eligible = values.min() + pd.Timedelta(days=warmup_days)
    start = first_eligible + pd.Timedelta(days=train_days)
    end = values.max()
    while start <= end:
        test_end = start + pd.Timedelta(days=test_days - 1)
        if test_end > end:
            if not include_partial:
                break
            test_end = end
        train = values[(values >= first_eligible) & (values < start)]
        test = values[(values >= start) & (values <= test_end)]
        yield train, test
        start += pd.Timedelta(days=step_days)


def eligible_rows(frame: pd.DataFrame, required_features: list[str]) -> pd.DataFrame:
    """Common target population for every model; sequence history may contain warm-up NaNs."""
    return frame.dropna(subset=["tce", *required_features]).copy()


def key_fingerprint(frame: pd.DataFrame) -> str:
    keys = frame[["record_date", "workshop_code"]].copy()
    keys["record_date"] = pd.to_datetime(keys["record_date"]).dt.strftime("%Y-%m-%d")
    values = keys.astype(str).sort_values(["record_date", "workshop_code"])
    payload = "\n".join(f"{date}|{shop}" for date, shop in values.itertuples(index=False, name=None))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fold_summary(pred: pd.DataFrame) -> list[dict]:
    if pred.empty:
        return []
    result = []
    for fold, group in pred.groupby("fold", sort=True):
        error = group["actual"].astype(float) - group["prediction"].astype(float)
        result.append({
            "fold": int(fold),
            "train_start": str(pd.to_datetime(group["train_start"].iloc[0]).date()),
            "train_end": str(pd.to_datetime(group["train_end"].iloc[0]).date()),
            "test_start": str(pd.to_datetime(group["record_date"]).min().date()),
            "test_end": str(pd.to_datetime(group["record_date"]).max().date()),
            "train_rows": int(group["train_rows"].iloc[0]),
            "test_rows": int(group["test_rows"].iloc[0]),
            "train_keys_sha256": str(group["train_keys_sha256"].iloc[0]),
            "samples": int(len(group)),
            "mae": float(error.abs().mean()),
            "rmse": float(np.sqrt((error ** 2).mean())),
        })
    return result


def _metrics(pred: pd.DataFrame, model: str) -> dict:
    if pred.empty:
        return {"model": model, "samples": 0, "mae": None, "rmse": None,
                "fold_metrics": [], "alert_lead_time_days": None,
                "lead_time_status": "unavailable_no_verified_incident_labels"}
    error = pred["actual"] - pred["prediction"]
    return {"model": model, "samples": len(pred),
            "mae": float(error.abs().mean()),
            "rmse": float(np.sqrt((error ** 2).mean())),
            "fold_metrics": fold_summary(pred),
            "alert_lead_time_days": None,
            "lead_time_status": "unavailable_no_verified_incident_labels"}


def evaluate_seasonal_naive(features, train_days=365, test_days=30, step_days=30,
                            warmup_days=WARMUP_DAYS):
    from ml.model_benchmark import NUMERIC_FEATURES

    df = features.copy()
    df["record_date"] = pd.to_datetime(df["record_date"])
    common = eligible_rows(df, NUMERIC_FEATURES)
    rows = []
    for fold, (train_dates, test_dates) in enumerate(
        rolling_splits(df["record_date"], train_days, test_days, step_days, warmup_days), 1
    ):
        if not len(test_dates):
            continue
        train = common[common["record_date"].isin(train_dates)]
        test = common[common["record_date"].isin(test_dates)]
        if train.empty or test.empty:
            continue
        metadata = {"train_start": train_dates.min(), "train_end": train_dates.max(),
                    "train_rows": len(train), "test_rows": len(test),
                    "train_keys_sha256": key_fingerprint(train)}
        for row in test.itertuples():
            rows.append({"fold": fold, "record_date": row.record_date,
                         "workshop_code": row.workshop_code, "actual": row.tce,
                         "prediction": row.tce_lag_7, **metadata})
    pred = pd.DataFrame(rows)
    return pred, _metrics(pred, "seasonal_naive_7d")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", default="output/ml_features.csv")
    parser.add_argument("--predictions", default="output/ml_seasonal_predictions.csv")
    parser.add_argument("--metrics", default="output/ml_model_metrics.json")
    args = parser.parse_args()
    pred, metrics = evaluate_seasonal_naive(pd.read_csv(args.features))
    Path(args.predictions).parent.mkdir(parents=True, exist_ok=True)
    pred.to_csv(args.predictions, index=False, encoding="utf-8-sig")
    Path(args.metrics).write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False))


if __name__ == "__main__":
    main()
