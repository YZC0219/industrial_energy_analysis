"""Verify the local Metabase demo against the MySQL read-only view.

This is an opt-in runtime check, not part of offline CI. It writes no secrets
to its JSON report and leaves the database and dashboard unchanged.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pymysql


ROOT = Path(__file__).resolve().parents[1]


def credentials(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    for key in ("BI_DB_PASSWORD", "METABASE_VIEWER_EMAIL", "METABASE_VIEWER_PASSWORD"):
        if not values.get(key):
            raise ValueError(f"missing {key} in {path}")
    return values


def api(base: str, path: str, *, token: str | None = None,
        method: str = "GET", payload: dict | None = None) -> tuple[int, object]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Metabase-Session"] = token
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(f"{base.rstrip('/')}/api/{path.lstrip('/')}", data=data,
                      headers=headers, method=method)
    try:
        response = urlopen(request, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        body = response.read()
        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = None
        return response.status, parsed


def expect(status: int, wanted: int, context: str) -> None:
    if status != wanted:
        raise AssertionError(f"{context}: HTTP {status}, expected {wanted}")


def verify(base: str, env_file: Path) -> dict:
    cfg = credentials(env_file)
    status, session = api(base, "session", method="POST", payload={
        "username": cfg["METABASE_VIEWER_EMAIL"],
        "password": cfg["METABASE_VIEWER_PASSWORD"],
    })
    expect(status, 200, "viewer login")
    token = session["id"]

    status, databases = api(base, "database", token=token)
    expect(status, 200, "database listing")
    visible = databases["data"]
    if len(visible) != 1 or visible[0]["name"] != "Industrial Energy (read-only)":
        raise AssertionError("viewer must see only the industrial energy database")
    database_id = visible[0]["id"]
    status, metadata = api(base, f"database/{database_id}/metadata", token=token)
    expect(status, 200, "view metadata")
    tables = metadata["tables"]
    if len(tables) != 1 or tables[0]["name"] != "v_energy_enriched":
        raise AssertionError("viewer must see only the enriched view")
    table = tables[0]
    fields = {item["name"]: item["id"] for item in table["fields"]}
    for key in ("record_date", "workshop_code", "cost"):
        if key not in fields:
            raise AssertionError(f"missing {key} in Metabase view metadata")

    status, dashboards = api(base, "dashboard", token=token)
    expect(status, 200, "dashboard listing")
    if [item["name"] for item in dashboards] != ["工业能耗分析"]:
        raise AssertionError("viewer should only see the industrial energy dashboard")
    dashboard_id = dashboards[0]["id"]
    status, dashboard = api(base, f"dashboard/{dashboard_id}", token=token)
    expect(status, 200, "dashboard")
    if dashboard.get("can_write") or dashboard.get("can_delete"):
        raise AssertionError("viewer can modify the dashboard")
    parameter_ids = {item["id"] for item in dashboard["parameters"]}
    if {item["slug"] for item in dashboard["parameters"]} != {
        "record_date", "workshop_code"
    }:
        raise AssertionError("dashboard date/workshop filters are missing")
    cards = dashboard["dashcards"]
    if len(cards) != 2 or any(
        {item["parameter_id"] for item in card["parameter_mappings"]} != parameter_ids
        for card in cards
    ):
        raise AssertionError("both dashboard cards must map both filters")

    query = {
        "database": database_id,
        "type": "query",
        "query": {
            "source-table": table["id"],
            "aggregation": [["sum", ["field", fields["cost"], None]]],
            "filter": ["and",
                       ["=", ["field", fields["workshop_code"], None], "W04"],
                       ["between", ["field", fields["record_date"], None],
                        "2025-01-01", "2025-01-31"]],
        },
    }
    status, result = api(base, "dataset", token=token, method="POST", payload=query)
    if status not in (200, 202):
        raise AssertionError(f"filtered query-builder query: HTTP {status}")
    if result.get("status") != "completed" or len(result.get("data", {}).get("rows", [])) != 1:
        raise AssertionError("filtered query-builder query did not complete with one total")
    mb_total = Decimal(str(result["data"]["rows"][0][0])).quantize(Decimal("0.01"))

    native = {"database": database_id, "type": "native",
              "native": {"query": "SELECT 1"}}
    native_status, _ = api(base, "dataset", token=token, method="POST", payload=native)
    expect(native_status, 403, "native SQL denial")
    edit_status, _ = api(base, f"dashboard/{dashboard_id}", token=token,
                         method="PUT", payload={"name": dashboard["name"]})
    expect(edit_status, 403, "dashboard edit denial")

    connection = pymysql.connect(
        host="127.0.0.1", port=3307, user="energy_bi_reader",
        password=cfg["BI_DB_PASSWORD"], database="industrial_energy",
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM v_energy_enriched")
            view_rows = cursor.fetchone()[0]
            cursor.execute("""SELECT COUNT(*), ROUND(SUM(cost), 2)
                              FROM v_energy_enriched
                              WHERE workshop_code = %s
                                AND record_date BETWEEN %s AND %s""",
                           ("W04", "2025-01-01", "2025-01-31"))
            sample_rows, mysql_total = cursor.fetchone()
            cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
            grants = "\n".join(row[0] for row in cursor.fetchall())
            if "v_energy_enriched" not in grants or "fact_energy_consumption" in grants:
                raise AssertionError("reader grants are wider than the enriched view")
            try:
                cursor.execute("SELECT id FROM fact_energy_consumption LIMIT 1")
            except pymysql.err.OperationalError as error:
                if error.args[0] != 1142:
                    raise
            else:
                raise AssertionError("reader unexpectedly accessed the fact table")
            try:
                cursor.execute("UPDATE v_energy_enriched SET consumption=consumption WHERE 1=0")
            except pymysql.err.OperationalError as error:
                if error.args[0] != 1142:
                    raise
            else:
                raise AssertionError("reader unexpectedly has write access")
    finally:
        connection.close()
    if mb_total != mysql_total:
        raise AssertionError(f"Metabase total {mb_total} differs from MySQL {mysql_total}")

    return {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "metabase_base": base,
        "database_id": database_id,
        "dashboard_id": dashboard_id,
        "visible_tables": [table["name"]],
        "view_rows": view_rows,
        "sample_filter": {"workshop_code": "W04", "date_from": "2025-01-01",
                          "date_to": "2025-01-31", "rows": sample_rows,
                          "cost_yuan": str(mysql_total)},
        "metabase_cost_yuan": str(mb_total),
        "dashboard_cards_with_both_filters": len(cards),
        "native_sql_http_status": native_status,
        "dashboard_edit_http_status": edit_status,
        "fact_table_read_denied": True,
        "mysql_write_denied": True,
        "result": "pass",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "output" / "metabase_runtime.json")
    args = parser.parse_args()
    report = verify(args.base_url, args.env_file)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"Metabase runtime checks passed; evidence: {args.output}")


if __name__ == "__main__":
    main()
