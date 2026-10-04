"""Shared semantic adapter for established report/API output contracts."""
from datetime import date
import csv
from pathlib import Path
import math
from governance.catalog import catalog, query


def read_artifact(filename, directory):
    entry = next((q for q in catalog()["queries"] if q["artifact"] == filename), None)
    if entry is None:
        raise KeyError("Unknown semantic artifact")
    # Established consumers use documented subsets of a canonical table.
    # Validate every supplied field while keeping older subset CSVs compatible.
    allowed = {c["name"]: c for c in entry["columns"]}
    with (Path(directory) / filename).open(encoding="utf-8-sig", newline="") as stream:
        headers = next(csv.reader(stream, strict=True), [])
    normalized = [header.strip() for header in headers]
    if not normalized or len(set(normalized)) != len(normalized) or any(key not in allowed for key in normalized):
        raise ValueError("Unknown or duplicate semantic artifact column")
    rows = query(entry["id"], directory, strict_contract=False)["rows"]
    rows = [{key.strip(): value.strip() for key, value in row.items()} for row in rows]
    for row in rows:
        if any(key not in allowed for key in row):
            raise ValueError("Unknown semantic artifact column")
        for key, value in row.items():
            if value.strip() and allowed[key]["type"] == "number" and not math.isfinite(float(value)):
                raise ValueError("Nonfinite semantic artifact value")
    return [{key.strip(): value.strip() for key, value in row.items()} for row in rows]


def daily_totals(rows, date_from=None, date_to=None, workshop_code=None):
    selected = []
    for row in rows:
        day = date.fromisoformat(row["日期"])
        if day.isoformat() != row["日期"]:
            raise ValueError("Noncanonical business date")
        if date_from and day < date_from or date_to and day > date_to:
            continue
        if workshop_code and row.get("车间编码") != workshop_code:
            continue
        selected.append(row)
    result = {"rows": selected}
    for ident in ("total_energy_tce", "total_cost_yuan", "total_carbon_tco2"):
        definition = next(m for m in catalog()["metrics"] if m["id"] == ident)
        if definition["aggregation"] != "sum" or definition.get("exclude_process"):
            raise ValueError("Legacy dashboard requires additive whole-factory metrics")
        values = [float(row[definition["column"]]) for row in selected]
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("Invalid additive metric value")
        result[ident] = math.fsum(values)
    return result
