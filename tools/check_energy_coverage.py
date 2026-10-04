"""Gate completeness against approved master data and production workshop-days."""
import argparse
import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def validate(energy, production, manifest):
    if manifest.get("version") != 1:
        raise ValueError("Unknown manifest version")
    expected = manifest["workshops"]
    required_energy = {"record_date", "workshop_code", "energy_code"}
    required_production = {"record_date", "workshop_code"}
    if not required_energy <= set(energy.columns) or not required_production <= set(production.columns):
        return {"success": False, "reason": "missing_columns"}
    for frame, columns in ((energy, required_energy), (production, required_production)):
        for column in columns:
            if frame[column].isna().any() or frame[column].map(
                lambda value: isinstance(value, str) and not value.strip()).any():
                return {"success": False, "reason": "invalid_business_keys"}
    actual = energy.groupby(["record_date", "workshop_code"])["energy_code"].agg(set).to_dict()
    failures = []
    for day, workshop in production[["record_date", "workshop_code"]].drop_duplicates().itertuples(index=False, name=None):
        seen = actual.get((day, workshop), set())
        configured = set(expected.get(workshop, []))
        if not configured or seen != configured:
            failures.append({"record_date": str(day), "workshop_code": str(workshop),
                             "missing": sorted(configured - seen), "unexpected": sorted(seen - configured),
                             "unknown_workshop": workshop not in expected})
    orphan = set(actual) - set(production[["record_date", "workshop_code"]].itertuples(index=False, name=None))
    return {"success": not failures and not orphan and not production.empty,
            "failed_workshop_days": len(failures), "samples": failures[:10],
            "orphan_workshop_days": len(orphan), "manifest_version": manifest["version"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--energy", type=Path, default=ROOT / "output/clean_batch_energy.csv")
    parser.add_argument("--production", type=Path, default=ROOT / "output/clean_batch_production.csv")
    parser.add_argument("--manifest", type=Path, default=ROOT / "quality/rules/workshop_energy_coverage.json")
    parser.add_argument("--output", type=Path, default=ROOT / "output/extensions/coverage_report.json")
    args = parser.parse_args()
    result = validate(pd.read_csv(args.energy), pd.read_csv(args.production),
                      json.loads(args.manifest.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Coverage {'PASS' if result['success'] else 'FAIL'}: {args.output}")
    raise SystemExit(0 if result["success"] else 1)
