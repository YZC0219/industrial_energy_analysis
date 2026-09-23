"""统一滚动验证；首版实现季节性朴素基线，后续模型复用同一折定义。"""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

def rolling_splits(dates, train_days=365, test_days=30, step_days=30):
    values=pd.DatetimeIndex(sorted(pd.to_datetime(pd.Series(dates).dropna().unique())))
    if not len(values): return
    # train_days/test_days 是“日历天”而不是“观测行数”；缺日会让折的含义改变，
    # 因此即使调用者绕过 feature_pipeline，也在这里再次守住连续日契约。
    if len(values)>1 and not (values[1:]-values[:-1]==pd.Timedelta(days=1)).all():
        raise ValueError("滚动验证要求连续自然日，发现日期缺口")
    start=values.min()+pd.Timedelta(days=train_days)
    end=values.max()
    while start<=end:
        test_end=min(start+pd.Timedelta(days=test_days-1),end)
        yield values[values<start],values[(values>=start)&(values<=test_end)]
        start+=pd.Timedelta(days=step_days)

def evaluate_seasonal_naive(features, train_days=365, test_days=30, step_days=30):
    df=features.copy(); df["record_date"]=pd.to_datetime(df["record_date"])
    rows=[]
    for fold,(train_dates,test_dates) in enumerate(rolling_splits(df["record_date"],train_days,test_days,step_days),1):
        if not len(test_dates): continue
        test=df[df["record_date"].isin(test_dates)].dropna(subset=["tce","tce_lag_7"]).copy()
        for r in test.itertuples(): rows.append({"fold":fold,"record_date":r.record_date,"workshop_code":r.workshop_code,"actual":r.tce,"prediction":r.tce_lag_7,"train_end":train_dates.max()})
    pred=pd.DataFrame(rows)
    if pred.empty: return pred,{"model":"seasonal_naive_7d","samples":0,"mae":None,"rmse":None,"alert_lead_time_days":None,"lead_time_status":"unavailable_no_verified_incident_labels"}
    err=pred["actual"]-pred["prediction"]
    return pred,{"model":"seasonal_naive_7d","samples":len(pred),"mae":float(err.abs().mean()),"rmse":float(np.sqrt((err**2).mean())),"alert_lead_time_days":None,"lead_time_status":"unavailable_no_verified_incident_labels"}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--features",default="output/ml_features.csv"); p.add_argument("--predictions",default="output/ml_seasonal_predictions.csv"); p.add_argument("--metrics",default="output/ml_model_metrics.json"); a=p.parse_args()
    pred,metrics=evaluate_seasonal_naive(pd.read_csv(a.features)); Path(a.predictions).parent.mkdir(parents=True,exist_ok=True); pred.to_csv(a.predictions,index=False,encoding="utf-8-sig"); Path(a.metrics).write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(metrics,ensure_ascii=False))
if __name__=="__main__": main()
