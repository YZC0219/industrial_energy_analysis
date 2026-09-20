# -*- coding: utf-8 -*-
"""临时探针: 只跑指定的几条查询并导出, 不重跑全量分析(用于开发期快速迭代)。"""
import csv
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "src")
import import_mysql as im  # noqa: E402


class A:
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = int(os.getenv("MYSQL_PORT", "3307"))
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PWD", "energy_root_pwd")
    db = os.getenv("MYSQL_DB", "industrial_energy")


def main():
    conn = im.connect(A)
    qs = dict(im.parse_analysis("sql/analysis.sql"))
    print(f"queries: {len(qs)}")
    for name in sys.argv[1:]:
        with conn.cursor() as cur:
            cur.execute(f"USE `{A.db}`")
            cur.execute(qs[name])
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        safe = re.sub(r"[^\w\-]", "_", name)
        out = os.path.join("output", safe + ".csv")
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
        print(f"  {name}  {len(rows)} rows -> {out}")
    conn.close()


if __name__ == "__main__":
    main()
