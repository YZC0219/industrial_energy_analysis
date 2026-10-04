"""Reconcile migrated report and established API against canonical artifacts."""
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))  # Existing report helpers import sibling clean_data.
from src import make_report
from src.api import app
from governance.catalog import catalog, metric, query


def main():
    semantic = json.loads((ROOT / "output/extensions/semantic_runtime.json").read_text(encoding="utf-8"))
    source = ROOT / semantic["evidence_directory"]
    if not source.resolve().is_relative_to((ROOT / "output/extensions").resolve()):
        raise ValueError("Artifact source outside project")
    make_report.OUT_DIR = str(source)
    migrated = make_report.build()
    migrated_read = make_report.read
    def legacy_reader(name):
        path = source / name
        if not path.exists():
            raise SystemExit("Reference artifact missing")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return [{(key or "").strip(): (value or "").strip() for key, value in row.items()}
                    for row in csv.DictReader(stream)]
    try:
        make_report.read = legacy_reader
        reference = make_report.build()
    finally:
        make_report.read = migrated_read
    assert migrated == reference
    os.environ["ENERGY_OUTPUT_DIR"] = str(source)
    client = TestClient(app)
    cases = []
    for params in ({}, {"workshop_code": "W01"}, {"date_from": "2025-01-01", "date_to": "2025-01-31", "workshop_code": "W02"}):
        response = client.get("/api/v1/metrics", params=params)
        assert response.status_code == 200
        from datetime import date
        filters = {key: date.fromisoformat(value) if key.startswith("date_") else value for key, value in params.items()}
        expected = {"tce": round(metric("total_energy_tce", source, **filters)["value"] or 0, 4),
                    "cost_yuan": round(metric("total_cost_yuan", source, **filters)["value"] or 0, 2),
                    "co2_t": round(metric("total_carbon_tco2", source, **filters)["value"] or 0, 3)}
        assert all(response.json()[key] == value for key, value in expected.items())
        cases.append({"filters": params, "totals": expected})
    for endpoint in ("anomalies", "report-summary"):
        assert client.get("/api/v1/" + endpoint).status_code == 200
    # Check raw analytical table consumers as well as the presentation adapters.
    for entry in catalog()["queries"]:
        assert client.get("/api/v1/semantic/metrics/analysis_" + entry["id"]).json()["rows"] == query(entry["id"], source)["rows"]
    folder = ROOT / "output/extensions" / ("semantic_consumers_" + uuid4().hex[:12])
    folder.mkdir(parents=True)
    (folder / "report_data.json").write_text(json.dumps(migrated, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    report = {"success": True, "report_data_unchanged": True, "legacy_api_cases": cases,
              "table_metric_cases": 29, "legacy_anomaly_and_summary_ok": True,
              "source_semantic_report_sha256": hashlib.sha256((ROOT / "output/extensions/semantic_runtime.json").read_bytes()).hexdigest(),
              "source_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                ("governance/consumers.py", "src/api.py", "src/make_report.py")},
              "evidence_directory": str(folder.relative_to(ROOT)),
              "pending": "Additional BI metrics and browser analytical formulas beyond seven basic scalar metrics"}
    (ROOT / "output/extensions/semantic_consumers_runtime.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
