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
    metric_models=[item["model"] for item in report["models"]]
    if len(metric_models)!=len(set(metric_models)):
        raise SystemExit("深度 metrics 中存在重复模型名")
    prediction_models=set(deep["model"].astype(str).unique()) if len(deep) else set()
    if prediction_models!=set(metric_models):
        raise SystemExit("深度 metrics 与预测明细中的模型集合不一致")
    keys=["fold","record_date","workshop_code","train_end"]
    expected=(baseline[baseline["model"]=="seasonal_naive_7d"][keys]
              .astype(str).reset_index(drop=True))
    for metrics in report["models"]:
        predictions=deep[deep["model"]==metrics["model"]].reset_index(drop=True)
        if not expected.equals(predictions[keys].astype(str)):
            raise SystemExit(f"{metrics['model']} 未使用与基线相同的滚动测试键")
        population_fields=["train_rows","test_rows","train_keys_sha256"]
        expected_population=(baseline[baseline["model"]=="seasonal_naive_7d"]
                             .groupby("fold",sort=True).first()[population_fields])
        actual_population=(predictions.groupby("fold",sort=True).first()[population_fields])
        if not expected_population.astype(str).equals(actual_population.astype(str)):
            raise SystemExit(f"{metrics['model']} 每折训练/测试样本与基线不一致")
        metric_population=(pd.DataFrame(metrics.get("fold_metrics",[]))
                           .set_index("fold")[population_fields])
        if not expected_population.astype(str).equals(metric_population.astype(str)):
            raise SystemExit(f"{metrics['model']} metrics 中的每折样本数/训练键摘要不一致")
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
    if report.get("split_contract",{}).get("partial_test_fold") is not False:
        raise SystemExit("滚动验证必须排除不足完整测试窗口的末折")
    print(f"DEEP_ARTIFACTS PASS models={len(report['models'])} predictions={len(deep)}")


if __name__=="__main__":
    main()
