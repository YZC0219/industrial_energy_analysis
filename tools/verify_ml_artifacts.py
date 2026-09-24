"""验证阶段二产物来自当前输入，且指标能由预测明细重新计算。"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from ml.feature_pipeline import build_features
from ml.model_benchmark import evaluate_models
from ml.provenance import verify_manifest

def main():
    p=argparse.ArgumentParser(); p.add_argument('--energy',required=True); p.add_argument('--production',required=True); p.add_argument('--features',required=True); p.add_argument('--predictions',required=True); p.add_argument('--metrics',required=True); p.add_argument('--provenance'); a=p.parse_args()
    if a.provenance:
        verify_manifest(a.provenance, {"energy":a.energy,"production":a.production},
                        {"features":a.features,"predictions":a.predictions,"metrics":a.metrics})
    energy=pd.read_csv(a.energy); production=pd.read_csv(a.production); features=pd.read_csv(a.features); pred=pd.read_csv(a.predictions); metrics=json.loads(Path(a.metrics).read_text(encoding='utf-8'))
    source_keys=set(map(tuple,energy[['record_date','workshop_code']].drop_duplicates().astype(str).to_numpy()))
    production_keys=set(map(tuple,production[['record_date','workshop_code']].drop_duplicates().astype(str).to_numpy()))
    feature_keys=set(map(tuple,features[['record_date','workshop_code']].drop_duplicates().astype(str).to_numpy()))
    if source_keys!=feature_keys: raise SystemExit(f'特征键与当前输入不一致: source={len(source_keys)} feature={len(feature_keys)}')
    if production_keys!=feature_keys: raise SystemExit(f'特征键与当前产量不一致: production={len(production_keys)} feature={len(feature_keys)}')
    expected_features=build_features(energy,production)
    features['record_date']=pd.to_datetime(features['record_date'])
    try: pd.testing.assert_frame_equal(expected_features.reset_index(drop=True),features.reset_index(drop=True),check_dtype=False,rtol=1e-10,atol=1e-12)
    except AssertionError as exc: raise SystemExit(f'特征内容不是由当前输入生成: {exc}')
    expected_pred,expected_metrics=evaluate_models(features)
    for frame in (pred,expected_pred):
        for col in ('record_date','train_start','train_end'):
            if col in frame: frame[col]=pd.to_datetime(frame[col])
    try: pd.testing.assert_frame_equal(expected_pred.reset_index(drop=True),pred.reset_index(drop=True),check_dtype=False,rtol=1e-10,atol=1e-12)
    except AssertionError as exc: raise SystemExit(f'预测明细不是由当前特征生成: {exc}')
    if metrics!=expected_metrics: raise SystemExit('指标 JSON 不是由当前特征和统一滚动折生成')
    expected_samples=sum(item['samples'] for item in metrics['models'])
    if len(pred)!=expected_samples: raise SystemExit('模型指标样本数之和与预测明细行数不一致')
    for item in metrics['models']:
        model_pred=pred[pred['model']==item['model']]
        if len(model_pred)!=item['samples']: raise SystemExit(f"{item['model']} 样本数不一致")
        if len(model_pred):
            err=model_pred['actual'].astype(float)-model_pred['prediction'].astype(float)
            mae=float(err.abs().mean()); rmse=float(np.sqrt((err**2).mean()))
            if not np.isclose(mae,item['mae']) or not np.isclose(rmse,item['rmse']):
                raise SystemExit(f"{item['model']} 的 MAE/RMSE 不能由预测明细复算")
        if item.get('alert_lead_time_days') is not None:
            raise SystemExit('无已确认事件标签时提前量必须为 N/A')
        fold_metrics=pd.DataFrame(item.get('fold_metrics',[]))
        if len(fold_metrics) and not (fold_metrics['test_rows']==fold_metrics['samples']).all():
            raise SystemExit(f"{item['model']} 每折 test_rows 与预测样本数不一致")
    by_model={item['model']:item for item in metrics['models']}
    seasonal={fold['fold']:fold for fold in by_model['seasonal_naive_7d']['fold_metrics']}
    lightgbm={fold['fold']:fold for fold in by_model['lightgbm']['fold_metrics']}
    if seasonal.keys()!=lightgbm.keys(): raise SystemExit('季节基线与 LightGBM 折数不一致')
    for fold in seasonal:
        for field in ('train_rows','test_rows','train_keys_sha256'):
            if seasonal[fold][field]!=lightgbm[fold][field]:
                raise SystemExit(f'第 {fold} 折模型训练/测试样本不一致: {field}')
    if metrics.get('split_contract',{}).get('partial_test_fold') is not False:
        raise SystemExit('滚动验证必须排除不足完整测试窗口的末折')
    if len(pred) and not (pd.to_datetime(pred['train_end'])<pd.to_datetime(pred['record_date'])).all():
        raise SystemExit('滚动折发生时间穿越')
    print(f"ML_ARTIFACTS PASS features={len(features)} predictions={len(pred)}")
if __name__=='__main__': main()
