"""Create or rotate the least-privilege MySQL account used by the BI profile."""
from __future__ import annotations

import os
import re

import pymysql


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_ACCOUNT_HOST = re.compile(r"^[A-Za-z0-9_.%:-]{1,255}$")


def grant_bi_reader(conn, database: str, username: str, password: str,
                    host: str = "%") -> None:
    """Grant read access only to the enriched view; never to fact/base tables."""
    if not _IDENTIFIER.fullmatch(database) or not _IDENTIFIER.fullmatch(username):
        raise ValueError("database and username must be simple SQL identifiers")
    if not _ACCOUNT_HOST.fullmatch(host):
        raise ValueError("invalid MySQL account host")
    if len(password) < 16:
        raise ValueError("BI_DB_PASSWORD must contain at least 16 characters")

    account = f"'{username}'@'{host}'"
    # PyMySQL applies %-formatting when a password parameter is present.
    # Escape wildcard account hosts only for those two parameterized queries.
    parameterized_account = account.replace("%", "%%")
    with conn.cursor() as cursor:
        cursor.execute(f"CREATE USER IF NOT EXISTS {parameterized_account} IDENTIFIED BY %s", (password,))
        cursor.execute(f"ALTER USER {parameterized_account} IDENTIFIED BY %s", (password,))
        cursor.execute(
            f"GRANT SELECT, SHOW VIEW ON `{database}`.`v_energy_enriched` TO {account}"
        )
    conn.commit()


def main() -> None:
    password = os.getenv("BI_DB_PASSWORD", "")
    if not password:
        raise SystemExit("请先设置 BI_DB_PASSWORD（至少 16 个字符）")
    database = os.getenv("MYSQL_DB", "industrial_energy")
    username = os.getenv("BI_DB_USER", "energy_bi_reader")
    host = os.getenv("BI_DB_ACCOUNT_HOST", "%")
    conn = pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3307")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PWD", ""),
        database=database,
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        grant_bi_reader(conn, database, username, password, host)
    finally:
        conn.close()
    print(f"BI reader ready: {username}@{host}; SELECT-only on {database}.v_energy_enriched")


if __name__ == "__main__":
    main()
