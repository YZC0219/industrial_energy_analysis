import pandas as pd
import pytest

from tools.verify_ml_artifacts import verify_shared_model_contract


def _model(name, fingerprint="same"):
    return {
        "model": name,
        "fold_metrics": [{
            "fold": 1,
            "train_rows": 10,
            "test_rows": 2,
            "train_keys_sha256": fingerprint,
        }],
    }


def _predictions():
    rows=[]
    for model in ("seasonal_naive_7d", "lightgbm", "future_tabular_model"):
        for workshop in ("W01", "W02"):
            rows.append({
                "model": model,
                "fold": 1,
                "record_date": "2025-01-02",
                "workshop_code": workshop,
                "train_end": "2025-01-01",
            })
    return pd.DataFrame(rows)


def test_shared_contract_covers_new_models_without_a_name_allowlist():
    models=[_model("seasonal_naive_7d"),_model("lightgbm"),
            _model("future_tabular_model")]
    verify_shared_model_contract(models,_predictions())


def test_shared_contract_rejects_population_mismatch_in_new_model():
    models=[_model("seasonal_naive_7d"),_model("lightgbm"),
            _model("future_tabular_model",fingerprint="different")]
    with pytest.raises(SystemExit,match="future_tabular_model.*训练/测试样本不一致"):
        verify_shared_model_contract(models,_predictions())
