"""生成车间×日预测特征；目标历史特征一律滞后，避免时间泄漏。"""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

STD_COAL={"E01":0.1229,"E02":1.3300,"E03":95.7000,"E04":0.2429,"E05":0.0400,"E06":1.4571}
REFERENCE_PRICE={"E01":0.68,"E02":3.45,"E03":220.0,"E04":4.10,"E05":0.28,"E06":7.85}

def build_features(energy: pd.DataFrame, production: pd.DataFrame) -> pd.DataFrame:
    """Build workshop-day features.

    ``low_load_share`` and ``shutdown_share`` are proportions of energy-detail rows
    carrying each retrospective status, not proportions of calendar time/workshop-days.
    They are exposed only with a one-day lag in model inputs.
    """
    e=energy.copy(); p=production.copy()
    e["record_date"]=pd.to_datetime(e["record_date"]); p["record_date"]=pd.to_datetime(p["record_date"])
    unknown=sorted(set(e["energy_code"].dropna())-set(STD_COAL))
    if unknown: raise ValueError(f"未知能源编码，无法计算折标煤: {unknown}")
    if e.duplicated(["record_date","workshop_code","energy_code"]).any():
        raise ValueError("能耗明细业务键重复，请先按 updated_at 去重")
    if p.duplicated(["record_date","workshop_code"]).any():
        raise ValueError("产量业务键重复，不能静默选择其中一条")
    e["std_coal_kgce"]=e["consumption"]*e["energy_code"].map(STD_COAL)
    e["reference_cost"]=e["consumption"]*e["energy_code"].map(REFERENCE_PRICE)
    e["is_low_load"]=(e["record_status"]=="低负荷").astype(float)
    e["is_shutdown"]=(e["record_status"]=="停产").astype(float)
    daily=(e.groupby(["record_date","workshop_code"],as_index=False)
             .agg(tce=("std_coal_kgce",lambda x:x.sum()/1000),cost_yuan=("cost","sum"),
                  reference_cost=("reference_cost","sum"),avg_temperature=("avg_temperature","mean"),
                  low_load_share=("is_low_load","mean"),shutdown_share=("is_shutdown","mean")))
    daily["energy_price_index"]=daily["cost_yuan"]/daily["reference_cost"].replace(0,np.nan)
    p=p[["record_date","workshop_code","output_qty"]]
    out=daily.merge(p,on=["record_date","workshop_code"],how="left",validate="one_to_one")
    out["day_of_week"]=out["record_date"].dt.dayofweek
    out["is_weekend"]=(out["day_of_week"]>=5).astype(int)
    day=out["record_date"].dt.dayofyear
    out["year_sin"]=np.sin(2*np.pi*day/365.25); out["year_cos"]=np.cos(2*np.pi*day/365.25)
    out=out.sort_values(["workshop_code","record_date"]).reset_index(drop=True)
    gaps=(out.groupby("workshop_code")["record_date"].diff().dropna()!=pd.Timedelta(days=1))
    if gaps.any(): raise ValueError("车间日序列存在缺日，不能把行滞后误当作自然日滞后")
    # 当前数据只有事后实绩，没有生产计划/天气预报/设备遥测快照。
    # 为保证 T 日预测只依赖 T 日开始前已知信息，动态外生变量统一使用 T-1 日值；
    # 日型和年度周期是事先可知的日历特征，可以保留 T 日值。
    dynamic=["output_qty","avg_temperature","low_load_share","shutdown_share","energy_price_index"]
    for column in dynamic:
        out[f"{column}_lag_1"]=out.groupby("workshop_code",sort=False)[column].shift(1)
    groups=out.groupby("workshop_code",sort=False)["tce"]
    for lag in (1,7,14): out[f"tce_lag_{lag}"]=groups.shift(lag)
    for window in (7,28): out[f"tce_rolling_mean_{window}"]=groups.transform(lambda s:s.shift(1).rolling(window,min_periods=window).mean())
    return out

def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--energy",default="output/clean_energy.csv"); p.add_argument("--production",default="output/clean_production.csv"); p.add_argument("--output",default="output/ml_features.csv"); a=p.parse_args()
    result=build_features(pd.read_csv(a.energy),pd.read_csv(a.production)); path=Path(a.output); path.parent.mkdir(parents=True,exist_ok=True); result.to_csv(path,index=False,encoding="utf-8-sig"); print(f"features={len(result)} output={path}")
if __name__=="__main__": main()
