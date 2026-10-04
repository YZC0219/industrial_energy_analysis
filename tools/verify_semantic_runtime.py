"""Read-only existing MySQL semantic export and API/artifact reconciliation."""
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4
import pymysql
from dotenv import dotenv_values
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from governance.export import export_all
from governance.catalog import catalog, metric
from src.api import app


def main():
    folder = ROOT / "output/extensions" / ("semantic_" + uuid4().hex[:12])
    secret = dotenv_values(ROOT / ".env")
    connection = pymysql.connect(host="127.0.0.1", port=3307, user="root",
                                  password=secret["MYSQL_ROOT_PASSWORD"], database="industrial_energy",
                                  charset="utf8mb4", autocommit=False)
    try:
        manifest = export_all(connection, folder)
    finally:
        connection.close()
    os.environ["ENERGY_OUTPUT_DIR"] = str(folder)
    client = TestClient(app)
    reconciled = []
    for entry in catalog()["queries"]:
        ident = "analysis_" + entry["id"]
        response = client.get("/api/v1/semantic/metrics/" + ident)
        assert response.status_code == 200
        expected = metric(ident, folder)
        assert response.json() == expected
        assert expected["row_count"] == manifest["queries"][entry["id"]]["rows"]
        reconciled.append(ident)
    assert client.get("/api/v1/semantic/metrics/analysis_Q05?workshop_code=W01").status_code == 422
    report = {"success": True, "live_read_only_queries": 29, "table_metrics_api_reconciled": len(reconciled),
              "scalar_metrics": len(catalog()["metrics"]) - len(reconciled), "unsupported_filter_rejected": True,
              "source_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                 ("governance/metrics.yaml", "governance/catalog.py", "governance/export.py", "src/api.py")},
              "evidence_directory": str(folder.relative_to(ROOT)), "manifest": manifest}
    (ROOT / "output/extensions/semantic_runtime.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "manifest"}, indent=2))


if __name__ == "__main__":
    main()
