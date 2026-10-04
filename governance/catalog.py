"""Allowlisted query artifacts; never executes YAML as SQL or Python."""
from pathlib import Path
import csv
import math
import hashlib
import yaml

ROOT = Path(__file__).resolve().parents[1]


class UnsupportedFilter(ValueError):
    pass


def canonical_sql():
    from src.import_mysql import parse_analysis
    return dict(parse_analysis(str(ROOT / "sql/analysis.sql")))


def catalog():
    config = yaml.safe_load((ROOT / "governance/metrics.yaml").read_text(encoding="utf-8"))
    entries = config["queries"]
    sql = canonical_sql()
    if len({q["id"] for q in entries}) != len(entries):
        raise ValueError("Duplicate query ids")
    for entry in entries:
        filename = entry["artifact"]
        if Path(filename).name != filename or not filename.endswith(".csv"):
            raise ValueError("Unsafe artifact name")
        if entry.get("sql_sha256") != hashlib.sha256(sql[filename[:-4]].encode()).hexdigest():
            raise ValueError("Semantic SQL definition is stale; review and refresh contract")
        columns = entry.get("columns", [])
        if not columns or len({c["name"] for c in columns}) != len(columns):
            raise ValueError("Invalid semantic output schema")
    if len({m["id"] for m in config["metrics"]}) != len(config["metrics"]):
        raise ValueError("Duplicate metric IDs")
    return config


def query(query_id, directory, strict_contract=True):
    entry = next((q for q in catalog()["queries"] if q["id"] == query_id), None)
    if entry is None:
        raise KeyError(query_id)
    with (Path(directory) / entry["artifact"]).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("Invalid artifact header")
        if strict_contract and reader.fieldnames != [c["name"] for c in entry["columns"]]:
            raise ValueError("Artifact schema differs from semantic definition")
        rows = list(reader)
        if any(None in row or any(v is None for v in row.values()) for row in rows):
            raise ValueError("Invalid artifact row")
    if strict_contract:
        for column in entry["columns"]:
            if column["type"] == "number":
                for row in rows:
                    if row[column["name"]] and not math.isfinite(float(row[column["name"]])):
                        raise ValueError("Nonfinite analytical output")
    return {"definition": entry, "rows": rows,
            "semantic_version": catalog()["version"]}


def metric(metric_id, directory, date_from=None, date_to=None, workshop_code=None):
    config = catalog()
    definition = next((m for m in config["metrics"] if m["id"] == metric_id), None)
    if definition is None:
        raise KeyError(metric_id)
    if definition["aggregation"] == "sql_result":
        if date_from or date_to or workshop_code:
            raise UnsupportedFilter("Analytical tables use canonical SQL scope; filters require recomputation")
        result = query(definition["source_query"], directory)
        return {"definition": definition, "rows": result["rows"], "row_count": len(result["rows"]),
                "columns": result["definition"]["columns"], "semantic_version": config["version"]}
    rows = query(definition["source_query"], directory, strict_contract=False)["rows"]
    from datetime import date
    selected = []
    for row in rows:
        day = date.fromisoformat(row["日期"])
        if day.isoformat() != row["日期"]:
            raise ValueError("Noncanonical business date")
        if date_from and day < date_from or date_to and day > date_to:
            continue
        if workshop_code and row["车间编码"] != workshop_code:
            continue
        if definition.get("exclude_process") and row["工序"] == definition["exclude_process"]:
            continue
        selected.append(row)
    def total(column):
        values = [float(r[column]) for r in selected]
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("Invalid metric values")
        return math.fsum(values)
    value = total(definition["column"]) if selected else None
    if definition["aggregation"] == "ratio_of_sums" and selected:
        if len({row["车间编码"] for row in selected}) > 1:
            raise UnsupportedFilter("Product intensity requires one workshop; cross-workshop product units are not interchangeable")
        denominator = total(definition["denominator"])
        value = value * definition["scale"] / denominator if denominator else None
    return {"definition": definition, "value": value, "row_count": len(selected),
            "semantic_version": config["version"]}
