"""Explicitly refresh SQL hashes and reviewed artifact schemas; never runs in API."""
import csv
import hashlib
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.import_mysql import parse_analysis


def build():
    path = ROOT / "governance/metrics.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    sql = dict(parse_analysis(str(ROOT / "sql/analysis.sql")))
    config["version"] = 2
    config["source_of_truth"] = "sql/analysis.sql; pinned SQL blocks and typed table contracts; no averaging or summing derived analytical results"
    config["metrics"] = [m for m in config["metrics"] if m["aggregation"] != "sql_result"]
    for entry in config["queries"]:
        name = entry["artifact"][:-4]
        entry["sql_sha256"] = hashlib.sha256(sql[name].encode()).hexdigest()
        with (ROOT / "tests/baseline" / entry["artifact"]).open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            rows = list(reader)
            columns = []
            for column in reader.fieldnames:
                values = [row[column] for row in rows if row[column] != ""]
                try:
                    for value in values:
                        float(value)
                    numeric = bool(values)
                except ValueError:
                    numeric = False
                unit = next((unit for suffix, unit in (("_tce", "tce"), ("_kgce", "kgce"), ("_tCO2", "tCO2"),
                             ("_元", "CNY"), ("_pct", "%"), ("_kWh", "kWh")) if column.endswith(suffix)), "as_named")
                columns.append({"name": column, "type": "number" if numeric else "string",
                                "unit": unit, "null_policy": "empty_is_SQL_NULL",
                                "reaggregation": "forbidden; execute canonical SQL for a new scope"})
        entry["columns"] = columns
        entry["result_kind"] = "table"
        entry["scope"] = "canonical SQL scope; artifact filtering does not recompute window statistics"
        config["metrics"].append({"id": "analysis_" + entry["id"], "name": entry["name"],
                                  "source_query": entry["id"], "aggregation": "sql_result", "result_kind": "table",
                                  "definition": entry["description"], "unit": "per_column"})
    for ident, name, column, unit, exclude in (
        ("total_cost_yuan", "能源总费用", "能源费用_元", "CNY", False),
        ("net_cost_yuan", "剔除公用工程费用", "能源费用_元", "CNY", True),
        ("total_carbon_tco2", "碳排放总量", "碳排放_tCO2", "tCO2", False),
        ("net_carbon_tco2", "剔除公用工程碳排放", "碳排放_tCO2", "tCO2", True)):
        if not any(m["id"] == ident for m in config["metrics"]):
            metric = {"id": ident, "name": name, "source_query": "Q28", "column": column,
                      "unit": unit, "aggregation": "sum", "definition": name + "按车间日记录求和"}
            if exclude:
                metric["exclude_process"] = "公用工程"
            config["metrics"].append(metric)
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    build()
