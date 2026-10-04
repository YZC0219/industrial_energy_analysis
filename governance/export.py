"""Export all semantic analyses in one read-only consistent MySQL snapshot."""
import csv
import hashlib
import json
from pathlib import Path
from governance.catalog import catalog, canonical_sql, query


def export_all(connection, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError("Semantic export requires a new empty directory")
    config = catalog()
    sql = canonical_sql()
    connection.rollback()
    evidence = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            for entry in config["queries"]:
                cursor.execute(sql[entry["artifact"][:-4]])
                columns = [description[0] for description in cursor.description]
                if columns != [column["name"] for column in entry["columns"]]:
                    raise ValueError("Live SQL schema differs from semantic contract")
                path = directory / entry["artifact"]
                with path.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(columns)
                    writer.writerows(cursor.fetchall())
                data = query(entry["id"], directory)
                evidence[entry["id"]] = {"rows": len(data["rows"]), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                         "sql_sha256": entry["sql_sha256"]}
        connection.rollback()  # Never commits a write or changes the business schema.
    except BaseException:
        connection.rollback()
        raise
    manifest = {"success": True, "semantic_version": config["version"], "queries": evidence,
                "consistency": "MySQL REPEATABLE READ consistent snapshot READ ONLY",
                "filters": "canonical SQL scopes; not arbitrary ad-hoc SQL"}
    (directory / "semantic_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
