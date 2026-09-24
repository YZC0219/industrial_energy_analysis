"""统一滚动折上的季节基线与 LightGBM 对照实验。"""
from __future__ import annotations

import argparse
import json
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd

from ml.rolling_validation import (eligible_rows, evaluate_seasonal_naive,
                                  fold_summary, key_fingerprint, rolling_splits)


NUMERIC_FEATURES = [
    "output_qty_lag_1", "avg_temperature_lag_1", "low_load_share_lag_1",
    "shutdown_share_lag_1", "energy_price_index_lag_1",
    "day_of_week", "is_weekend", "year_sin", "year_cos",
    "tce_lag_1", "tce_lag_7", "tce_lag_14", "tce_rolling_mean_7",
    "tce_rolling_mean_28",
]


def _design_matrix(frame: pd.DataFrame, workshops: list[str]) -> pd.DataFrame:
    numeric=frame[NUMERIC_FEATURES].astype(float)
    categorical=pd.get_dummies(
        pd.Categorical(frame["workshop_code"],categories=workshops),
        prefix="workshop",dtype=float,
    )
    categorical.index=frame.index
    return pd.concat([numeric,categorical],axis=1)


def evaluate_lightgbm(features: pd.DataFrame, train_days: int=365,
                      test_days: int=30, step_days: int=30,
                      random_state: int=20240918, warmup_days: int=28):
    try:
        from lightgbm import LGBMRegressor
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("LightGBM 未安装，请执行 pip install -r requirements.txt") from exc

    df=features.copy(); df["record_date"]=pd.to_datetime(df["record_date"])
    workshops=sorted(df["workshop_code"].dropna().astype(str).unique())
    required=["tce","workshop_code",*NUMERIC_FEATURES]
    common=eligible_rows(df,NUMERIC_FEATURES)
    rows=[]
    for fold,(train_dates,test_dates) in enumerate(
        rolling_splits(df["record_date"],train_days,test_days,step_days,warmup_days),1
    ):
        if not len(test_dates):
            continue
        train=common[common["record_date"].isin(train_dates)]
        test=common[common["record_date"].isin(test_dates)]
        if train.empty or test.empty:
            continue
        model=LGBMRegressor(
            objective="regression_l2",n_estimators=180,learning_rate=0.04,
            num_leaves=31,max_depth=7,min_child_samples=20,
            subsample=0.9,colsample_bytree=0.9,reg_lambda=0.1,
            random_state=random_state,n_jobs=1,verbosity=-1,
        )
        train_matrix=_design_matrix(train,workshops)
        test_matrix=_design_matrix(test,workshops)
        if list(train_matrix.columns)!=list(test_matrix.columns):
            raise ValueError("训练集与测试集设计矩阵列不一致")
        model.fit(train_matrix,train["tce"].astype(float))
        predictions=model.predict(test_matrix)
        metadata={"train_start":train_dates.min(),"train_end":train_dates.max(),
                  "train_rows":len(train),"test_rows":len(test),
                  "train_keys_sha256":key_fingerprint(train)}
        for row,prediction in zip(test.itertuples(),predictions):
            rows.append({
                "model":"lightgbm","fold":fold,"record_date":row.record_date,
                "workshop_code":row.workshop_code,"actual":float(row.tce),
                "prediction":float(prediction),**metadata,
            })
    pred=pd.DataFrame(rows)
    if pred.empty:
        return pred,{"model":"lightgbm","samples":0,"mae":None,"rmse":None,"fold_metrics":[],
                    "alert_lead_time_days":None,
                    "lead_time_status":"unavailable_no_verified_incident_labels"}
    error=pred["actual"]-pred["prediction"]
    return pred,{"model":"lightgbm","samples":len(pred),
                 "mae":float(error.abs().mean()),
                 "rmse":float(np.sqrt((error**2).mean())),
                 "fold_metrics":fold_summary(pred),"alert_lead_time_days":None,
                 "lead_time_status":"unavailable_no_verified_incident_labels"}


def evaluate_models(features: pd.DataFrame, train_days: int=365,
                    test_days: int=30, step_days: int=30, warmup_days: int=28):
    seasonal_pred,seasonal_metrics=evaluate_seasonal_naive(
        features,train_days,test_days,step_days,warmup_days
    )
    seasonal_pred=seasonal_pred.copy()
    seasonal_pred.insert(0,"model","seasonal_naive_7d")
    tree_pred,tree_metrics=evaluate_lightgbm(features,train_days,test_days,step_days,
                                             warmup_days=warmup_days)
    seasonal_folds={item["fold"]:item for item in seasonal_metrics["fold_metrics"]}
    tree_folds={item["fold"]:item for item in tree_metrics["fold_metrics"]}
    if seasonal_folds.keys()!=tree_folds.keys():
        raise ValueError("季节基线与 LightGBM 滚动折不一致")
    for fold in seasonal_folds:
        for field in ("train_rows","test_rows","train_keys_sha256"):
            if seasonal_folds[fold][field]!=tree_folds[fold][field]:
                raise ValueError(f"第 {fold} 折模型训练/测试样本契约不一致: {field}")
    predictions=pd.concat([seasonal_pred,tree_pred],ignore_index=True)
    metrics={
        "split_contract":{"train_days":train_days,"test_days":test_days,
                          "step_days":step_days,"calendar_days":True,
                          "warmup_days":warmup_days,"partial_test_fold":False,
                          "expanding_training_window":True},
        "feature_availability":{"measured_actuals":"t-1",
                                 "retrospective_energy_row_status_share":"t-1",
                                 "calendar":"t"},
        "runtime":{"lightgbm":version("lightgbm"),"random_state":20240918},
        "models":[seasonal_metrics,tree_metrics],
    }
    return predictions,metrics


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--features",default="output/ml_features.csv")
    parser.add_argument("--predictions",default="output/ml_model_predictions.csv")
    parser.add_argument("--metrics",default="output/ml_model_metrics.json")
    args=parser.parse_args()
    predictions,metrics=evaluate_models(pd.read_csv(args.features))
    output=Path(args.predictions); output.parent.mkdir(parents=True,exist_ok=True)
    predictions.to_csv(output,index=False,encoding="utf-8-sig")
    Path(args.metrics).write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(metrics,ensure_ascii=False))


if __name__=="__main__":
    main()
