"""验证深度模型与基线使用相同测试键，并从明细复算指标。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--baseline-predictions",required=True)
    parser.add_argument("--deep-predictions",required=True)
    parser.add_argument("--metrics",required=True)
    args=parser.parse_args()
    baseline=pd.read_csv(args.baseline_predictions)
    deep=pd.read_csv(args.deep_predictions)
    report=json.loads(Path(args.metrics).read_text(encoding="utf-8"))
    keys=["fold","record_date","workshop_code","train_end"]
    expected=(baseline[baseline["model"]=="seasonal_naive_7d"][keys]
              .astype(str).reset_index(drop=True))
    for metrics in report["models"]:
        predictions=deep[deep["model"]==metrics["model"]].reset_index(drop=True)
        if not expected.equals(predictions[keys].astype(str)):
            raise SystemExit(f"{metrics['model']} 未使用与基线相同的滚动测试键")
        if len(predictions)!=metrics["samples"]:
            raise SystemExit(f"{metrics['model']} 样本数不一致")
        error=predictions["actual"].astype(float)-predictions["prediction"].astype(float)
        mae=float(error.abs().mean()); rmse=float(np.sqrt((error**2).mean()))
        if not np.isclose(mae,metrics["mae"]) or not np.isclose(rmse,metrics["rmse"]):
            raise SystemExit(f"{metrics['model']} 指标不能由预测明细复算")
        if not (pd.to_datetime(predictions["train_end"])<pd.to_datetime(predictions["record_date"])).all():
            raise SystemExit(f"{metrics['model']} 存在时间穿越")
        if metrics.get("alert_lead_time_days") is not None:
            raise SystemExit("没有确认事件标签时不得报告提前量")
    print(f"DEEP_ARTIFACTS PASS models={len(report['models'])} predictions={len(deep)}")


if __name__=="__main__":
    main()
