"""LSTM/Transformer 时序对照；与基线模型复用同一滚动日期折。"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
import pandas as pd

from ml.model_benchmark import NUMERIC_FEATURES
from ml.rolling_validation import rolling_splits


def _torch():
    try:
        import torch
        from torch import nn
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("深度模型依赖未安装，请执行 pip install -r requirements-deep.txt") from exc
    return torch,nn


def build_sequences(features: pd.DataFrame, sequence_days: int=28):
    columns=["record_date","workshop_code","tce",*NUMERIC_FEATURES]
    frame=features[columns].copy(); frame["record_date"]=pd.to_datetime(frame["record_date"])
    workshops=sorted(frame["workshop_code"].dropna().astype(str).unique())
    rows=[]
    for workshop,group in frame.sort_values("record_date").groupby("workshop_code",sort=True):
        group=group.reset_index(drop=True)
        identity=np.zeros(len(workshops),dtype=np.float32)
        identity[workshops.index(str(workshop))]=1.0
        for index in range(sequence_days-1,len(group)):
            # 目标日行只包含预先可知的日历字段和已经统一滞后的实绩字段；
            # 目标 tce 本身不在 NUMERIC_FEATURES 中，因此可作为序列最后一步。
            window=group.iloc[index-sequence_days+1:index+1]
            target=group.iloc[index]
            if window[NUMERIC_FEATURES].isna().any().any() or pd.isna(target["tce"]):
                continue
            values=window[NUMERIC_FEATURES].to_numpy(dtype=np.float32)
            workshop_values=np.repeat(identity[None,:],sequence_days,axis=0)
            rows.append({"record_date":target["record_date"],"workshop_code":workshop,
                         "actual":float(target["tce"]),
                         "sequence":np.concatenate([values,workshop_values],axis=1)})
    return rows


def _model(kind: str, input_size: int, sequence_days: int):
    torch,nn=_torch()
    class LSTMRegressor(nn.Module):
        def __init__(self):
            super().__init__(); self.rnn=nn.LSTM(input_size,24,batch_first=True); self.out=nn.Linear(24,1)
        def forward(self,x):
            values,_=self.rnn(x); return self.out(values[:,-1]).squeeze(-1)
    class TransformerRegressor(nn.Module):
        def __init__(self):
            super().__init__(); self.project=nn.Linear(input_size,24)
            self.position=nn.Parameter(torch.zeros(1,sequence_days,24))
            layer=nn.TransformerEncoderLayer(d_model=24,nhead=4,dim_feedforward=48,
                                             dropout=0.0,batch_first=True)
            self.encoder=nn.TransformerEncoder(layer,num_layers=1); self.out=nn.Linear(24,1)
        def forward(self,x):
            values=self.encoder(self.project(x)+self.position[:,:x.shape[1]])
            return self.out(values[:,-1]).squeeze(-1)
    return LSTMRegressor() if kind=="lstm" else TransformerRegressor()


def evaluate_deep_model(features: pd.DataFrame, kind: str, train_days: int=365,
                        test_days: int=30, step_days: int=30, sequence_days: int=28,
                        epochs: int=12, random_state: int=20240918):
    if kind not in {"lstm","transformer"}:
        raise ValueError("kind 必须是 lstm 或 transformer")
    torch,nn=_torch(); torch.set_num_threads(1)
    random.seed(random_state); np.random.seed(random_state); torch.manual_seed(random_state)
    frame=features.copy(); frame["record_date"]=pd.to_datetime(frame["record_date"])
    sequences=build_sequences(frame,sequence_days)
    rows=[]
    for fold,(train_dates,test_dates) in enumerate(
        rolling_splits(frame["record_date"],train_days,test_days,step_days),1
    ):
        train=[row for row in sequences if row["record_date"] in train_dates]
        test=[row for row in sequences if row["record_date"] in test_dates]
        if not train or not test:
            continue
        x_train=np.stack([row["sequence"] for row in train]); y_train=np.array([row["actual"] for row in train],dtype=np.float32)
        x_test=np.stack([row["sequence"] for row in test])
        mean=x_train.mean(axis=(0,1),keepdims=True); std=x_train.std(axis=(0,1),keepdims=True); std[std<1e-6]=1
        y_mean=float(y_train.mean()); y_std=float(y_train.std()) or 1.0
        x_train=(x_train-mean)/std; x_test=(x_test-mean)/std; y_scaled=(y_train-y_mean)/y_std
        network=_model(kind,x_train.shape[2],sequence_days)
        optimizer=torch.optim.Adam(network.parameters(),lr=0.003)
        loss_fn=nn.MSELoss(); network.train()
        x_tensor=torch.from_numpy(x_train); y_tensor=torch.from_numpy(y_scaled)
        generator=torch.Generator().manual_seed(random_state+fold)
        for _ in range(epochs):
            order=torch.randperm(len(x_tensor),generator=generator)
            for start in range(0,len(order),128):
                batch=order[start:start+128]; optimizer.zero_grad()
                loss=loss_fn(network(x_tensor[batch]),y_tensor[batch]); loss.backward(); optimizer.step()
        network.eval()
        with torch.no_grad():
            predictions=network(torch.from_numpy(x_test)).numpy()*y_std+y_mean
        for source,prediction in zip(test,predictions):
            rows.append({"model":kind,"fold":fold,"record_date":source["record_date"],
                         "workshop_code":source["workshop_code"],"actual":source["actual"],
                         "prediction":float(prediction),"train_end":train_dates.max()})
    pred=pd.DataFrame(rows)
    if pred.empty:
        return pred,{"model":kind,"samples":0,"mae":None,"rmse":None,
                    "alert_lead_time_days":None,
                    "lead_time_status":"unavailable_no_verified_incident_labels"}
    error=pred["actual"]-pred["prediction"]
    return pred,{"model":kind,"samples":len(pred),"mae":float(error.abs().mean()),
                 "rmse":float(np.sqrt((error**2).mean())),"epochs":epochs,
                 "sequence_days":sequence_days,"alert_lead_time_days":None,
                 "lead_time_status":"unavailable_no_verified_incident_labels"}


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--features",default="output/ml_features.csv")
    parser.add_argument("--predictions",default="output/ml_deep_predictions.csv")
    parser.add_argument("--metrics",default="output/ml_deep_metrics.json")
    parser.add_argument("--epochs",type=int,default=12)
    args=parser.parse_args(); features=pd.read_csv(args.features)
    predictions=[]; metrics=[]
    for kind in ("lstm","transformer"):
        pred,report=evaluate_deep_model(features,kind,epochs=args.epochs)
        predictions.append(pred); metrics.append(report)
    combined=pd.concat(predictions,ignore_index=True)
    output=Path(args.predictions); output.parent.mkdir(parents=True,exist_ok=True)
    combined.to_csv(output,index=False,encoding="utf-8-sig")
    torch,_=_torch()
    report={"models":metrics,"split_contract":{"train_days":365,"test_days":30,
            "step_days":30,"calendar_days":True,"expanding_training_window":True},
            "feature_availability":{"dynamic_actuals":"t-1","calendar":"t"},
            "runtime":{"torch":torch.__version__,"device":"cpu","random_state":20240918}}
    Path(args.metrics).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))


if __name__=="__main__":
    main()
