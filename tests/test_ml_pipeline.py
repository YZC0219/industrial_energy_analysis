import pandas as pd
import json
from pathlib import Path
import pytest
from ml.feature_pipeline import build_features
from ml.rolling_validation import evaluate_seasonal_naive
from ml.model_benchmark import evaluate_lightgbm, evaluate_models
from ml.attribution_assistant import (Evidence, GroundingError, SUMMARY_GROUNDED,
                                      analyze, retrieve, validate_grounding)
from ml.deep_benchmark import build_sequences, evaluate_deep_model

def sample(days=400):
    dates=pd.date_range("2024-01-01",periods=days)
    energy=pd.DataFrame({"record_date":dates,"workshop_code":"W01","energy_code":"E01","consumption":range(1,days+1),"cost":range(1,days+1),"avg_temperature":20.0,"record_status":"正常"})
    production=pd.DataFrame({"record_date":dates,"workshop_code":"W01","output_qty":100.0})
    return energy,production

def test_target_history_features_are_shifted_without_same_day_leakage():
    energy,production=sample(40); f=build_features(energy,production)
    assert pd.isna(f.loc[0,"tce_lag_1"])
    assert f.loc[1,"tce_lag_1"]==f.loc[0,"tce"]
    assert pd.isna(f.loc[0,"output_qty_lag_1"])
    assert f.loc[1,"output_qty_lag_1"]==f.loc[0,"output_qty"]
    assert f.loc[1,"avg_temperature_lag_1"]==f.loc[0,"avg_temperature"]
    assert f.loc[7,"tce_rolling_mean_7"]==pytest.approx(f.loc[:6,"tce"].mean())

def test_rolling_validation_is_chronological_and_reports_metrics():
    energy,production=sample(); f=build_features(energy,production)
    pred,metrics=evaluate_seasonal_naive(f,train_days=365,test_days=30,step_days=30)
    assert len(pred)>0 and (pred["train_end"]<pred["record_date"]).all()
    assert metrics["samples"]==len(pred) and metrics["mae"]>=0 and metrics["rmse"]>=metrics["mae"]
    assert metrics["alert_lead_time_days"] is None
    assert metrics["lead_time_status"]=="unavailable_no_verified_incident_labels"

def test_attribution_contract_requires_citations_for_every_claim():
    schema=json.loads((Path(__file__).resolve().parents[1]/"ml/schemas/attribution_result.schema.json").read_text(encoding="utf-8"))
    claim=schema["properties"]["claims"]["items"]
    assert {"statement","citations"} <= set(claim["required"])
    citations=claim["properties"]["citations"]
    assert citations["minItems"]==1
    allowed=set(citations["items"]["properties"]["source_type"]["enum"])
    assert allowed=={"metric_dictionary","sql_result","anomaly_evidence"}

def test_feature_pipeline_rejects_missing_calendar_day():
    energy,production=sample(10)
    energy=energy.drop(index=energy.index[4]); production=production.drop(index=production.index[4])
    with pytest.raises(ValueError,match="缺日"): build_features(energy,production)

def test_feature_pipeline_rejects_unknown_energy_code():
    energy,production=sample(10); energy.loc[0,"energy_code"]="E99"
    with pytest.raises(ValueError,match="未知能源编码"): build_features(energy,production)

def test_rolling_split_rejects_calendar_gap_even_without_feature_builder():
    energy,production=sample(400); f=build_features(energy,production)
    f=f[f["record_date"]!=pd.Timestamp("2024-06-01")]
    with pytest.raises(ValueError,match="连续自然日"): evaluate_seasonal_naive(f)

def test_lead_time_label_contract_excludes_detector_outputs_as_ground_truth():
    schema=json.loads((Path(__file__).resolve().parents[1]/"ml/schemas/incident_label.schema.json").read_text(encoding="utf-8"))
    allowed=set(schema["properties"]["source_type"]["enum"])
    assert allowed=={"maintenance_log","operator_confirmed","simulation_ground_truth"}


def test_lightgbm_uses_same_chronological_folds_as_seasonal_baseline():
    energy,production=sample(); features=build_features(energy,production)
    seasonal,_=evaluate_seasonal_naive(features)
    tree,metrics=evaluate_lightgbm(features)
    keys=["fold","record_date","workshop_code","train_end"]
    pd.testing.assert_frame_equal(seasonal[keys].reset_index(drop=True),tree[keys].reset_index(drop=True))
    assert metrics["model"]=="lightgbm" and metrics["samples"]==len(tree)
    assert (tree["train_end"]<tree["record_date"]).all()


def test_model_benchmark_reports_both_models_without_claiming_lead_time():
    energy,production=sample(); predictions,report=evaluate_models(build_features(energy,production))
    assert set(predictions["model"])=={"seasonal_naive_7d","lightgbm"}
    assert {item["model"] for item in report["models"]}=={"seasonal_naive_7d","lightgbm"}
    assert all(item["alert_lead_time_days"] is None for item in report["models"])
    assert report["split_contract"]["calendar_days"] is True


def test_attribution_refuses_to_invent_root_cause_from_anomaly_signal():
    evidence=[Evidence("e1","anomaly_evidence","output/Q16.csv","W02|2025-01-01",
                       {"Z值":"3.2"},"W02 2025-01-01 单耗异常 Z值=3.2")]
    result,audit=analyze("W02 这天为什么设备故障？",evidence,analysis_id="case-causal")
    assert result["insufficient_evidence"] is True and result["claims"]==[]
    assert audit["provider"]=="offline_guarded"


def test_attribution_rejects_citation_not_returned_by_retrieval():
    evidence=[Evidence("e1","sql_result","output/Q13.csv","车间=W04",{"待机浪费_元":"196015.33"},
                       "W04 待机浪费_元=196015.33")]
    fabricated={"analysis_id":"x","summary":SUMMARY_GROUNDED,"insufficient_evidence":False,
                "claims":[{"statement":"x","citations":[{"source_type":"sql_result",
                "source_path":"output/Q99.csv","record_key":"x","evidence_value":999}]}]}
    with pytest.raises(GroundingError,match="不在本次检索证据"):
        validate_grounding(fabricated,evidence)


def test_attribution_rejects_invented_claim_with_real_citation():
    item=Evidence("e1","sql_result","output/Q13.csv","车间=W04",
                  {"待机浪费_元":"196015.33"},"Q13 W04 待机浪费_元=196015.33")
    fabricated={"analysis_id":"x",
                "summary":SUMMARY_GROUNDED,
                "insufficient_evidence":False,
                "claims":[{"statement":"W04 因设备故障造成费用上升", "citations":[item.citation()]}]}
    with pytest.raises(GroundingError,match="直接摘自"):
        validate_grounding(fabricated,[item])


def test_attribution_offline_answer_keeps_exact_source_citation():
    item=Evidence("e1","sql_result","output/Q13.csv","车间=W04",{"待机浪费_元":"196015.33"},
                  "W04 停产 待机浪费_元=196015.33")
    result,_=analyze("W04 停产待机费用",[item],analysis_id="case-grounded")
    assert result["insufficient_evidence"] is False
    assert result["claims"][0]["citations"]==[item.citation()]


def test_attribution_retrieval_resolves_workshop_code_and_intent():
    evidence=[
        Evidence("map","sql_result","output/Q03.csv","车间编码=W04",
                 {"车间编码":"W04","车间":"机加工车间"},"Q03 车间编码=W04 车间=机加工车间 能源费用"),
        Evidence("idle","sql_result","output/Q13.csv","车间=机加工车间",
                 {"车间":"机加工车间","待机浪费_元":"196015.33"},"Q13 车间=机加工车间 停产 待机浪费_元=196015.33"),
    ]
    assert retrieve("W04 停产日待机费用",evidence)[0].evidence_id=="idle"


def test_attribution_rejects_unmatched_explicit_entity_even_for_noncausal_question():
    evidence=[Evidence("e1","sql_result","output/Q03.csv","车间编码=W04",
                       {"车间编码":"W04","综合能耗_tce":"1"},"W04 综合能耗_tce=1")]
    result,_=analyze("W99 综合能耗是多少",evidence,analysis_id="missing-entity")
    assert result["insufficient_evidence"] is True and result["claims"]==[]


def test_deep_sequences_use_only_days_before_target():
    energy,production=sample(70); features=build_features(energy,production)
    sequences=build_sequences(features,sequence_days=28)
    first=sequences[0]
    source=features[features["record_date"]<=first["record_date"]].tail(28)
    assert first["sequence"].shape==(28,15)  # 单车间样本：14 数值特征 + 1 个身份位
    assert first["sequence"][-1,0]==pytest.approx(source.iloc[-1]["output_qty_lag_1"])


def test_lstm_smoke_uses_chronological_fold():
    pytest.importorskip("torch")
    energy,production=sample(90); features=build_features(energy,production)
    predictions,metrics=evaluate_deep_model(features,"lstm",train_days=60,test_days=15,
                                             step_days=15,sequence_days=14,epochs=1)
    assert len(predictions)==metrics["samples"]>0
    assert (predictions["train_end"]<predictions["record_date"]).all()
