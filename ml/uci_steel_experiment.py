"""Real-data benchmark for UCI Steel Industry Energy Consumption (CC BY 4.0)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20240918
BLOCK_LENGTHS = (7, 14, 30)
FEATURES = ["lag_1", "lag_4", "lag_7", "lag_28", "mean_7", "mean_28", "day_sin", "day_cos", "dow_sin", "dow_cos", "weekend"]
GROUPS = {
    "recent_history": ["lag_1", "lag_4", "mean_7"],
    "monthly_history": ["lag_28", "mean_28"],
    "calendar": ["day_sin", "day_cos", "dow_sin", "dow_cos", "weekend"],
}


def _moving_block_ci(values, block_length, rng, replicates=20000):
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


def load_daily(path: Path):
    raw = pd.read_csv(path)
    ts = pd.to_datetime(raw["date"], format="%d/%m/%Y %H:%M", errors="raise")
    data = pd.DataFrame({"timestamp": ts, "usage_kwh": pd.to_numeric(raw["Usage_kWh"], errors="raise")}).sort_values("timestamp")
    duplicates = int(data.timestamp.duplicated().sum())
    delta = data.timestamp.diff().dropna()
    gaps = int((delta != pd.Timedelta(minutes=15)).sum())
    if duplicates or gaps:
        raise ValueError(f"Timestamp integrity failure: duplicates={duplicates}, non-15m gaps={gaps}")
    day = data.assign(record_date=data.timestamp.dt.floor("D")).groupby("record_date", as_index=False).agg(
        usage_kwh=("usage_kwh", "sum"), intervals=("usage_kwh", "size"))
    if not (day.intervals == 96).all():
        raise ValueError("Daily aggregation expected exactly 96 quarter-hour samples per day")
    day["day_of_year"] = day.record_date.dt.dayofyear
    day["day_of_week"] = day.record_date.dt.dayofweek
    day["day_sin"] = np.sin(2*np.pi*day.day_of_year/365.25)
    day["day_cos"] = np.cos(2*np.pi*day.day_of_year/365.25)
    day["dow_sin"] = np.sin(2*np.pi*day.day_of_week/7)
    day["dow_cos"] = np.cos(2*np.pi*day.day_of_week/7)
    day["weekend"] = (day.day_of_week >= 5).astype(int)
    for lag in (1, 4, 7, 28):
        day[f"lag_{lag}"] = day.usage_kwh.shift(lag)
    day["mean_7"] = day.usage_kwh.shift(1).rolling(7, min_periods=7).mean()
    day["mean_28"] = day.usage_kwh.shift(1).rolling(28, min_periods=28).mean()
    return raw, day, {"raw_rows": len(raw), "raw_columns": list(raw.columns), "first_timestamp": str(data.timestamp.min()),
                      "last_timestamp": str(data.timestamp.max()), "timestamp_duplicates": duplicates,
                      "non_15m_intervals": gaps, "daily_rows": len(day), "daily_interval_min": int(day.intervals.min()),
                      "daily_interval_max": int(day.intervals.max()), "missing_values": int(raw.isna().sum().sum())}


def _fit(train, test, features):
    from lightgbm import LGBMRegressor
    model = LGBMRegressor(objective="regression_l2", n_estimators=100, learning_rate=.04,
                          num_leaves=7, max_depth=3, min_child_samples=5,
                          subsample=.9, colsample_bytree=.9, reg_lambda=.1,
                          random_state=SEED, n_jobs=1, verbosity=-1)
    model.fit(train[features], train.usage_kwh)
    return model, model.predict(test[features])


def run(input_path: Path, out: Path):
    raw, daily, integrity = load_daily(input_path)
    eligible = daily.dropna(subset=["usage_kwh", *FEATURES]).copy()
    # 28-day warm-up, 120-day initial train, 30-day test and 30-day rolling step.
    dates = pd.DatetimeIndex(pd.to_datetime(daily.record_date))
    eligible["record_date"] = pd.to_datetime(eligible.record_date)
    start = dates.min() + pd.Timedelta(days=28+120)
    end = dates.max()
    folds = []
    predictions, ablations, ablation_predictions = [], [], []
    fold = 0
    while start + pd.Timedelta(days=29) <= end:
        fold += 1
        test_end = start + pd.Timedelta(days=29)
        train_dates = dates[(dates >= dates.min()+pd.Timedelta(days=28)) & (dates < start)]
        test_dates = dates[(dates >= start) & (dates <= test_end)]
        train = eligible[eligible.record_date.isin(train_dates)]
        test = eligible[eligible.record_date.isin(test_dates)]
        if len(test) != 30 or train.empty:
            raise ValueError(f"Incomplete fold {fold}: train={len(train)} test={len(test)}")
        full_model, full_pred = _fit(train, test, FEATURES)
        baseline = test.lag_7.to_numpy(float)
        for model_name, yhat in (("lightgbm", full_pred), ("seasonal_naive_7d", baseline)):
            err = test.usage_kwh.to_numpy(float)-np.asarray(yhat)
            predictions.append(pd.DataFrame({"fold": fold, "record_date": test.record_date,
                "actual": test.usage_kwh, "model": model_name, "prediction": yhat,
                "absolute_error": np.abs(err), "squared_error": err**2}))
        for name, pred in [("full", full_pred)]:
            err = test.usage_kwh.to_numpy(float)-pred
            ablations.append({"fold":fold,"ablation":name,"mae":float(np.mean(np.abs(err))),"rmse":float(np.sqrt(np.mean(err**2)))})
        for group, removed in GROUPS.items():
            _, yhat = _fit(train, test, [x for x in FEATURES if x not in removed])
            err = test.usage_kwh.to_numpy(float)-yhat
            ablations.append({"fold":fold,"ablation":f"without_{group}","mae":float(np.mean(np.abs(err))),"rmse":float(np.sqrt(np.mean(err**2)))})
            ablation_predictions.append(pd.DataFrame({"fold":fold,"record_date":test.record_date,
                "ablation":f"without_{group}","absolute_error":np.abs(err)}))
        if fold == 1:
            shap_model, shap_test = full_model, test.copy()
        folds.append({"fold":fold,"train_start":str(train.record_date.min().date()),"train_end":str(train.record_date.max().date()),
                      "test_start":str(test.record_date.min().date()),"test_end":str(test.record_date.max().date()),
                      "train_rows":len(train),"test_rows":len(test)})
        start += pd.Timedelta(days=30)

    pred = pd.concat(predictions, ignore_index=True)
    abl = pd.DataFrame(ablations)
    ablation_pred = pd.concat(ablation_predictions, ignore_index=True)
    out.mkdir(parents=True, exist_ok=True)
    daily.to_csv(out/"uci_steel_daily.csv", index=False, encoding="utf-8-sig")
    pred.to_csv(out/"uci_steel_predictions.csv", index=False, encoding="utf-8-sig")
    abl.to_csv(out/"uci_steel_ablation_by_fold.csv", index=False, encoding="utf-8-sig")
    ablation_pred.to_csv(out/"uci_steel_ablation_daily_errors.csv", index=False, encoding="utf-8-sig")

    summary = pred.groupby("model").agg(samples=("actual","size"),mae=("absolute_error","mean"),rmse=("squared_error",lambda x:float(np.sqrt(x.mean())))).reset_index()
    fold_metrics = pred.groupby(["model","fold"]).agg(mae=("absolute_error","mean"),rmse=("squared_error",lambda x:float(np.sqrt(x.mean())))).reset_index()
    by_ab = abl.groupby("ablation").agg(mae_mean=("mae","mean"),mae_sd=("mae","std"),rmse_mean=("rmse","mean"),rmse_sd=("rmse","std")).reset_index()
    full_daily = pred[pred.model=="lightgbm"].groupby("record_date",as_index=False).absolute_error.mean().rename(columns={"absolute_error":"full_mae"})
    tests=[]; rng=np.random.default_rng(SEED)
    for name, g in abl[abl.ablation!="full"].groupby("ablation",sort=False):
        daily = ablation_pred[ablation_pred.ablation==name].merge(full_daily,on="record_date",validate="one_to_one").sort_values("record_date")
        d=(daily.absolute_error-daily.full_mae).to_numpy()
        tests.append({"comparison":name,"paired_dates":len(d),"mean_daily_mae_delta":float(d.mean()),
                      "moving_block_bootstrap_95ci_by_days":{str(length):_moving_block_ci(d,length,rng) for length in BLOCK_LENGTHS}})

    import shap
    shap_sample=shap_test.sample(n=min(60,len(shap_test)),random_state=SEED)
    x=shap_sample[FEATURES]
    sv=np.asarray(shap.TreeExplainer(shap_model).shap_values(x))
    shap_rows=[]
    for i,(_, row) in enumerate(x.iterrows()):
        r={"sample_index":i,"date":str(shap_sample.iloc[i].record_date.date()),"actual":float(shap_sample.iloc[i].usage_kwh),
           "prediction":float(shap_model.predict(row.to_frame().T)[0])}
        for j,name in enumerate(FEATURES): r[f"value::{name}"]=float(row[name]); r[f"shap::{name}"]=float(sv[i,j])
        shap_rows.append(r)
    pd.DataFrame(shap_rows).to_csv(out/"uci_steel_shap.csv",index=False,encoding="utf-8-sig")
    shap_imp=sorted([{"feature":f,"mean_abs_shap":float(np.mean(np.abs(sv[:,j]))),"mean_shap":float(np.mean(sv[:,j]))} for j,f in enumerate(FEATURES)],key=lambda x:-x["mean_abs_shap"])

    digest=hashlib.sha256(input_path.read_bytes()).hexdigest()
    result={"dataset":{"name":"Steel Industry Energy Consumption","source":"UCI Machine Learning Repository",
              "doi":"10.24432/C52G8C","license":"CC BY 4.0","sha256":digest,"aggregation":"sum of 96 quarter-hour Usage_kWh observations per calendar day",
              "limitations":["single steel facility","one calendar year","no measured production quantity","15-minute records are not independent plants"]},
            "integrity":integrity,"protocol":{"warmup_days":28,"initial_train_days":120,"test_days":30,"step_days":30,
              "folds":fold,"test_samples_per_model":int(summary.samples.min()),"forecast":"next-day daily energy; rolling single step",
              "seasonal_naive":"7-day lag baseline","synthetic_protocol_directly_reused":False,
              "inference":"daily paired loss differences; non-circular moving-block bootstrap; 20,000 replicates; exploratory",
              "block_lengths_days":list(BLOCK_LENGTHS)},
            "metrics":summary.to_dict(orient="records"),"fold_metrics":fold_metrics.to_dict(orient="records"),
            "ablation_summary":by_ab.to_dict(orient="records"),"paired_daily_effects":tests,
            "shap":{"method":"TreeSHAP","first_test_fold_sample_n":len(shap_rows),"global_importance":shap_imp}}
    (out/"uci_steel_experiment_summary.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"integrity":integrity,"protocol":result["protocol"],"metrics":result["metrics"],"ablation":result["ablation_summary"],"tests":tests,"shap_top":shap_imp[:5]},ensure_ascii=False,indent=2))
    return result


def main():
    p=argparse.ArgumentParser(); p.add_argument("--input",default="data/real/uci_steel_energy/Steel_industry_data.csv"); p.add_argument("--out",default="output/paper/uci_steel"); a=p.parse_args()
    run(Path(a.input),Path(a.out))

if __name__=="__main__": main()
