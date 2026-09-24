"""Config-driven Great Expectations quality gate for the offline energy batch."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES = ROOT / "quality" / "rules" / "energy_batch.json"
DEFAULT_OUTPUT = ROOT / "output" / "data_quality_report.json"


def _expectation_result(name: str, result) -> dict:
    details = result.result or {}
    return {
        "rule_id": name,
        "engine": "Great Expectations",
        "success": bool(result.success),
        "observed": {
            key: details[key]
            for key in ("element_count", "unexpected_count", "unexpected_percent")
            if key in details
        },
    }


def _validate_dataset(context, gx, dataset_name: str, frame: pd.DataFrame,
                      config: dict) -> list[dict]:
    required = config.get("required_columns", [])
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        return [{"rule_id": f"{dataset_name}.required_columns", "engine": "contract",
                 "success": False, "missing_columns": missing}]

    source = context.data_sources.add_pandas(name=f"{dataset_name}_source")
    asset = source.add_dataframe_asset(name=f"{dataset_name}_asset")
    batch_definition = asset.add_batch_definition_whole_dataframe("whole_batch")
    suite = context.suites.add(gx.ExpectationSuite(name=f"{dataset_name}_suite"))

    for item in config.get("expectations", []):
        expectation_type = item["type"]
        expectation_class = getattr(gx.expectations, expectation_type, None)
        if expectation_class is None:
            raise ValueError(f"Unsupported Great Expectations rule type: {expectation_type}")
        suite.add_expectation(expectation_class(
            **item.get("kwargs", {}), meta={"rule_id": item["rule_id"]}
        ))

    validation = gx.ValidationDefinition(
        name=f"{dataset_name}_validation", data=batch_definition, suite=suite
    )
    context.validation_definitions.add(validation)
    result = validation.run(
        batch_parameters={"dataframe": frame}, result_format="SUMMARY"
    )
    return [
        _expectation_result(
            (item.expectation_config.meta or {}).get("rule_id", item.expectation_config.type),
            item,
        )
        for item in result.results
    ]


def validate_frames(energy: pd.DataFrame, production: pd.DataFrame,
                    rules: dict | None = None) -> dict:
    """Run configured table expectations and relational key checks on DataFrames."""
    try:
        import great_expectations as gx
    except ImportError as exc:
        raise RuntimeError(
            "Great Expectations is required; install project requirements.txt"
        ) from exc

    if rules is None:
        rules = json.loads(DEFAULT_RULES.read_text(encoding="utf-8"))
    if rules.get("version") != 1:
        raise ValueError(f"Unsupported quality rules version: {rules.get('version')!r}")
    configured_ids = [
        expectation.get("rule_id")
        for dataset in rules.get("datasets", {}).values()
        for expectation in dataset.get("expectations", [])
    ] + [check.get("rule_id") for check in rules.get("cross_table", [])]
    if any(not rule_id for rule_id in configured_ids) or len(set(configured_ids)) != len(configured_ids):
        raise ValueError("Every configured quality rule needs a unique rule_id")

    frames = {"energy": energy, "production": production}
    context = gx.get_context(mode="ephemeral")
    context.variables.progress_bars = {"globally": False}
    results = []
    for dataset_name, dataset_config in rules["datasets"].items():
        if dataset_name not in frames:
            raise ValueError(f"No input DataFrame provided for dataset {dataset_name!r}")
        results.extend(_validate_dataset(context, gx, dataset_name, frames[dataset_name],
                                         dataset_config))

    for check in rules.get("cross_table", []):
        source_name = check["source_dataset"]
        target_name = check["target_dataset"]
        source, target = frames[source_name], frames[target_name]
        source_cols, target_cols = check["source_columns"], check["target_columns"]
        absent = (set(source_cols) - set(source.columns)) | (set(target_cols) - set(target.columns))
        if absent:
            results.append({"rule_id": check["rule_id"], "engine": "pandas_relational",
                            "success": False, "missing_columns": sorted(absent)})
            continue
        target_keys = target[target_cols].drop_duplicates()
        joined = source[source_cols].drop_duplicates().merge(
            target_keys, how="left", left_on=source_cols, right_on=target_cols,
            indicator=True,
        )
        missing_keys = joined.loc[joined["_merge"] == "left_only", source_cols]
        results.append({
            "rule_id": check["rule_id"], "engine": "pandas_relational",
            "success": missing_keys.empty,
            "source_distinct_keys": int(len(joined)),
            "orphan_key_count": int(len(missing_keys)),
            "orphan_key_samples": missing_keys.head(5).astype(str).to_dict("records"),
        })

    return {
        "rules_version": rules["version"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": "Great Expectations 1.23.1 + pandas relational checks",
        "datasets": {name: int(len(frame)) for name, frame in frames.items()},
        "success": all(item["success"] for item in results),
        "passed": sum(item["success"] for item in results),
        "failed": sum(not item["success"] for item in results),
        "checks": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--energy", type=Path, default=ROOT / "output" / "clean_batch_energy.csv")
    parser.add_argument("--production", type=Path,
                        default=ROOT / "output" / "clean_batch_production.csv")
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    rules = json.loads(args.rules.read_text(encoding="utf-8"))
    report = validate_frames(pd.read_csv(args.energy), pd.read_csv(args.production), rules)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"DATA_QUALITY {'PASS' if report['success'] else 'FAIL'} "
          f"passed={report['passed']} failed={report['failed']} report={args.output}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
