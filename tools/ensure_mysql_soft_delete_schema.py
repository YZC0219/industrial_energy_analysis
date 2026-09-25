"""Idempotently migrate the MySQL energy fact table and analytical view."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import import_mysql  # noqa: E402


def main() -> None:
    db = os.getenv("MYSQL_DB", "industrial_energy")
    conn = pymysql.connect(
        host=os.getenv("MYSQL_HOST", "mysql"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PWD", ""),
        database=db,
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        import_mysql.ensure_soft_delete_column(conn, db)
    finally:
        conn.close()
    print("MYSQL_SOFT_DELETE_SCHEMA up-to-date")


if __name__ == "__main__":
    main()
