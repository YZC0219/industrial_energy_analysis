"""Reproducible ablation, paired inference, and SHAP artifacts for the paper."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ml.model_benchmark import NUMERIC_FEATURES, _design_matrix
from ml.rolling_validation import eligible_rows, rolling_splits

GROUPS = {
    "production": ["output_qty_lag_1"],
    "weather_calendar": ["avg_temperature_lag_1", "day_of_week", "is_weekend", "year_sin", "year_cos"],
    "price_status": ["low_load_share_lag_1", "shutdown_share_lag_1", "energy_price_index_lag_1"],
    "target_history": ["tce_lag_1", "tce_lag_7", "tce_lag_14", "tce_rolling_mean_7", "tce_rolling_mean_28"],
}
SEED = 20240918
BLOCK_LENGTHS = (7, 14, 30)


def _moving_block_ci(values, block_length, rng, replicates=20000):
    """Percentile CI from non-circular moving-block resamples of daily deltas."""
    values = np.asarray(values, dtype=float)
    n = len(values)
    length = min(int(block_length), n)
    starts = np.arange(n - length + 1)
    draws = np.empty(replicates, dtype=float)
    blocks_needed = int(np.ceil(n / length))
    for i in range(replicates):
        chosen = rng.choice(starts, size=blocks_needed, replace=True)
        sample = np.concatenate([values[s:s + length] for s in chosen])[:n]
        draws[i] = sample.mean()
    return [float(np.quantile(draws, .025)), float(np.quantile(draws, .975))]


def _metric(actual, predicted):
    error = np.asarray(actual, dtype=float) - np.asarray(predicted, dtype=float)
    return float(np.mean(np.abs(error))), float(np.sqrt(np.mean(error**2)))


def _fit_predict(train, test, features, workshops):
    from lightgbm import LGBMRegressor

    matrix_train = _design_matrix(train, workshops)[features]
    matrix_test = _design_matrix(test, workshops)[features]
    model = LGBMRegressor(
        objective="regression_l2", n_estimators=180, learning_rate=0.04,
        num_leaves=31, max_depth=7, min_child_samples=20,
        subsample=0.9, colsample_bytree=0.9, reg_lambda=0.1,
        random_state=SEED, n_jobs=1, verbosity=-1,
    )
    model.fit(matrix_train, train["tce"].astype(float))
    return model, matrix_train, matrix_test, model.predict(matrix_test)


def run(features: pd.DataFrame, out: Path, shap_rows_per_fold: int = 60):
    frame = features.copy()
    frame["record_date"] = pd.to_datetime(frame["record_date"])
    common = eligible_rows(frame, NUMERIC_FEATURES)
    workshops = sorted(frame["workshop_code"].dropna().astype(str).unique())
    all_features = list(NUMERIC_FEATURES) + [f"workshop_{shop}" for shop in workshops]
    ablation_rows, pred_rows, ablation_pred_rows, shap_rows = [], [], [], []
    first_model = first_train = first_test = None

    for fold, (train_dates, test_dates) in enumerate(rolling_splits(frame.record_date), 1):
        train = common[common.record_date.isin(train_dates)]
        test = common[common.record_date.isin(test_dates)]
        if train.empty or test.empty:
            continue
        model, x_train, x_test, pred = _fit_predict(train, test, all_features, workshops)
        actual = test["tce"].to_numpy(float)
        mae, rmse = _metric(actual, pred)
        ablation_rows.append({"fold": fold, "ablation": "full", "removed_group": "none", "features": len(all_features), "mae": mae, "rmse": rmse, "samples": len(test)})
        for item, prediction in zip(test.itertuples(), pred):
            pred_rows.append({"fold": fold, "record_date": item.record_date, "workshop_code": item.workshop_code,
                              "actual": float(item.tce), "prediction": float(prediction), "absolute_error": abs(float(item.tce)-float(prediction))})

        for group, removed in GROUPS.items():
            remaining = [name for name in all_features if name not in removed]
            _, _, _, ablated_pred = _fit_predict(train, test, remaining, workshops)
            g_mae, g_rmse = _metric(actual, ablated_pred)
            ablation_rows.append({"fold": fold, "ablation": f"without_{group}", "removed_group": group,
                                  "features": len(remaining), "mae": g_mae, "rmse": g_rmse, "samples": len(test)})
            for item, prediction in zip(test.itertuples(), ablated_pred):
                ablation_pred_rows.append({"fold": fold, "record_date": item.record_date,
                    "workshop_code": item.workshop_code, "ablation": f"without_{group}",
                    "absolute_error": abs(float(item.tce) - float(prediction))})

        if fold == 1:
            first_model, first_train, first_test = model, x_train, x_test

    if first_model is None:
        raise RuntimeError("No complete rolling folds were evaluated")

    # TreeSHAP on a deterministic sample from the first held-out fold, using its fold model.
    import shap
    sample_n = min(shap_rows_per_fold, len(first_test))
    sample = first_test.sample(n=sample_n, random_state=SEED)
    values = shap.TreeExplainer(first_model).shap_values(sample)
    if isinstance(values, list):
        values = values[0]
    base = float(np.asarray(shap.TreeExplainer(first_model).expected_value).reshape(-1)[0])
    shap_values = np.asarray(values)
    if shap_values.ndim == 3:
        shap_values = shap_values[..., 0]
    for i, (_, row) in enumerate(sample.iterrows()):
        record = {"sample_index": i, "base_value": base, "prediction": float(first_model.predict(row.to_frame().T)[0])}
        for j, name in enumerate(sample.columns):
            record[f"value::{name}"] = float(row[name])
            record[f"shap::{name}"] = float(shap_values[i, j])
        shap_rows.append(record)

    ablation = pd.DataFrame(ablation_rows)
    preds = pd.DataFrame(pred_rows)
    ablation_preds = pd.DataFrame(ablation_pred_rows)
    shap_frame = pd.DataFrame(shap_rows)
    out.mkdir(parents=True, exist_ok=True)
    ablation.to_csv(out / "paper_ablation_by_fold.csv", index=False, encoding="utf-8-sig")
    preds.to_csv(out / "paper_lightgbm_fold1_predictions.csv", index=False, encoding="utf-8-sig")
    ablation_preds.to_csv(out / "paper_ablation_daily_errors.csv", index=False, encoding="utf-8-sig")
    shap_frame.to_csv(out / "paper_shap_fold1.csv", index=False, encoding="utf-8-sig")

    # Inference pairs daily losses within date, averaging workshops before resampling.
    summary = ablation.groupby("ablation", sort=False).agg(mae_mean=("mae", "mean"), mae_sd=("mae", "std"),
                                                              rmse_mean=("rmse", "mean"), rmse_sd=("rmse", "std")).reset_index()
    full_daily = preds.groupby("record_date", as_index=False).absolute_error.mean().rename(columns={"absolute_error":"full_mae"})
    abl_daily = ablation_preds.groupby(["ablation", "record_date"], as_index=False).absolute_error.mean()
    comparisons = []
    rng = np.random.default_rng(SEED)
    for name, group in abl_daily.groupby("ablation", sort=False):
        daily = group.merge(full_daily, on="record_date", validate="one_to_one").sort_values("record_date")
        d = (daily.absolute_error - daily.full_mae).to_numpy()
        comparisons.append({"comparison": name, "n_paired_dates": len(d), "mean_daily_mae_delta": float(d.mean()),
            "moving_block_bootstrap_95ci_by_days": {str(length): _moving_block_ci(d, length, rng)
                                                       for length in BLOCK_LENGTHS}})

    shap_importance = []
    for name in NUMERIC_FEATURES + [f"workshop_{shop}" for shop in workshops]:
        col = f"shap::{name}"
        vals = shap_frame[col]
        shap_importance.append({"feature": name, "mean_abs_shap": float(vals.abs().mean()),
                                "mean_shap": float(vals.mean())})
    shap_importance.sort(key=lambda x: x["mean_abs_shap"], reverse=True)
    report = {
        "data_scope": "fixed-seed synthetic dataset; conclusions are methodological and not field validation",
        "protocol": {"train_days": 365, "test_days": 30, "step_days": 30, "warmup_days": 28,
                     "complete_folds": int(ablation.fold.nunique()), "eligible_workshop_days": int(len(common)), "paired_inference_unit": "daily mean absolute error; workshop errors averaged by date",
                     "ablation_groups": GROUPS, "inference": "non-circular moving-block bootstrap; 20,000 replicates; exploratory",
                     "block_lengths_days": list(BLOCK_LENGTHS), "bootstrap_replicates": 20000, "seed": SEED},
        "full_model": summary[summary.ablation == "full"].iloc[0].to_dict(),
        "ablation_summary": summary.to_dict(orient="records"),
        "paired_daily_effects": comparisons,
        "shap": {"method": "TreeSHAP", "model_fold": 1, "test_sample_n": sample_n,
                 "global_importance": shap_importance[:20]},
    }
    (out / "paper_experiment_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", default="output/ml_features.csv")
    parser.add_argument("--out", default="output/paper")
    parser.add_argument("--shap-rows-per-fold", type=int, default=60)
    args = parser.parse_args()
    report = run(pd.read_csv(args.features), Path(args.out), args.shap_rows_per_fold)
    print(json.dumps({"folds": report["protocol"]["complete_folds"], "shap_n": report["shap"]["test_sample_n"],
                      "top_shap": report["shap"]["global_importance"][:5]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
