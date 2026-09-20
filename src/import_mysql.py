# -*- coding: utf-8 -*-
"""
import_mysql.py — 建库建表、装载清洗后数据、执行分析 SQL

步骤:
  1. --init      执行 sql/create_table.sql (建库/建表/建视图/灌主数据)
  2. 装载        output/clean_energy.csv -> fact_energy_consumption
                 output/clean_production.csv -> fact_production
                 output/dim_calendar.csv -> dim_calendar
     (使用 LOAD DATA LOCAL INFILE 批量装载, 比逐行 INSERT 快 1~2 个数量级)
  3. --run-analysis  逐条执行 sql/analysis.sql, 结果导出到 output/qNN_*.csv

用法:
  python src/import_mysql.py --init --run-analysis
  python src/import_mysql.py --user root --password 你的密码
  MYSQL_PWD=你的密码 python src/import_mysql.py --init

连接参数优先级: 命令行参数 > 环境变量 > 默认值
  环境变量: MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PWD / MYSQL_DB
"""

import argparse
import csv
import os
import re
import sys

import pymysql

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL_DIR = os.path.join(BASE_DIR, "sql")
OUT_DIR = os.path.join(BASE_DIR, "output")

# CSV 列顺序必须与建表顺序一致 (dim_calendar 的列名与表头一致)
ENERGY_COLS = ["record_date", "workshop_code", "energy_code", "consumption", "unit",
               "unit_price", "cost", "record_status", "avg_temperature",
               "data_source", "is_production_day"]
PROD_COLS = ["record_date", "workshop_code", "output_qty", "output_unit"]
CAL_COLS = ["calendar_date", "year", "quarter", "month", "year_month",
            "day_of_week", "weekday_name", "is_weekend", "holiday_name", "is_holiday"]


def log(msg=""):
    print(msg)


def connect(args):
    return pymysql.connect(
        host=args.host, port=args.port, user=args.user, password=args.password,
        charset="utf8mb4", autocommit=False,
        local_infile=True,
    )


def split_sql(script: str):
    """按分号切分 SQL 脚本, 跳过注释与空语句"""
    lines = []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        lines.append(line)
    body = "\n".join(lines)
    return [s.strip() for s in body.split(";") if s.strip()]


def run_script(conn, path: str, use_db: str | None) -> None:
    with open(path, encoding="utf-8") as f:
        script = f.read()
    statements = split_sql(script)
    with conn.cursor() as cur:
        if use_db:
            cur.execute(f"USE `{use_db}`")
        for stmt in statements:
            cur.execute(stmt)
    conn.commit()


def load_csv(conn, table: str, csv_name: str, columns, db: str) -> int:
    """
    把一个 CSV 装载进指定表, 返回装载后该表的总行数。

    装载是幂等的: SQL 里带 IGNORE, 唯一键冲突的行会被跳过而不是报错,
    因此重复执行本脚本不会产生重复数据(与已经装载过的数据保持一致)。
    装完后会用表内实际行数校验, 该写进去的行一行没少才算成功。
    """
    path = os.path.join(OUT_DIR, csv_name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到 {csv_name} (期望位置: {path})")
    # 先数一遍 CSV 的数据行数(总行数减去表头), 装完用它校验
    with open(path, encoding="utf-8-sig", newline="") as f:
        file_rows = max(sum(1 for _ in f) - 1, 0)

    # MySQL 需要正斜杠路径
    win_path = path.replace("\\", "/")
    cols = ", ".join(f"@{c}" for c in columns)
    assigns = ", ".join(f"`{c}` = NULLIF(@{c}, '')" for c in columns)
    # 先导入用户变量 @col 再 SET 到真实列: 空串经 NULLIF 变成 NULL,
    # 否则空的 avg_temperature 会被当成 0 而不是"未知"
    # IGNORE: 唯一键重复的行直接丢弃, 使整个装载过程可重复执行
    # 行终止符用 '\n' 而非 '\r\n': 上游 clean_data.py 已固定写 LF。
    # 若换成 '\r\n', 在 LF 文件上整个文件会被当成一行, 静默装入 0 行。
    sql = (
        f"LOAD DATA LOCAL INFILE '{win_path}' INTO TABLE `{db}`.`{table}` "
        f"CHARACTER SET utf8mb4 FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '\"' "
        f"LINES TERMINATED BY '\\n' IGNORE 1 LINES ({cols}) SET {assigns}"
    )
    with conn.cursor() as cur:
        # 装载期间关掉唯一性与外键校验以提升速度(数据本身已通过清洗保证一致性)
        cur.execute("SET UNIQUE_CHECKS=0")
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        try:
            cur.execute(sql)
            loaded = "LOAD DATA"
        except pymysql.err.OperationalError as exc:
            # 只有"服务端不允许 LOCAL INFILE"这一种情况才值得降级重试。
            # 其它 OperationalError(比如服务端读不到文件)必须原样抛出 ——
            # 早先这里无差别吞掉所有 pymysql 错误, 导致装载静默失败
            # 而任务仍报 success, 后面分析查询全查空表。
            if "local_infile" not in str(exc) and "not allowed" not in str(exc):
                raise
            log("      (服务端未开启 local_infile, 退化为批量 INSERT)")
            loaded = "批量 INSERT"
            insert_sql = (
                f"INSERT IGNORE INTO `{db}`.`{table}` "
                f"({', '.join(f'`{c}`' for c in columns)}) "
                f"VALUES ({', '.join(['%s'] * len(columns))})"
            )
            rows = []
            with open(path, encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                next(reader)
                for r in reader:
                    if len(r) != len(columns):
                        continue
                    rows.append(tuple(v if v != "" else None for v in r))
            for i in range(0, len(rows), 2000):
                cur.executemany(insert_sql, rows[i:i + 2000])
        cur.execute("SET FOREIGN_KEY_CHECKS=1")
        cur.execute("SET UNIQUE_CHECKS=1")
        cur.execute(f"SELECT COUNT(*) FROM `{db}`.`{table}`")
        n = cur.fetchone()[0]
    conn.commit()

    # 校验: 表里至少要有 CSV 那么多行。少了说明有行没进去(路径错、编码错、
    # 列数不匹配……), 必须让任务失败而不是把空表交给下游查询。
    if n < file_rows:
        raise RuntimeError(
            f"{table} 装载不完整: CSV {file_rows:,} 行, 表中仅 {n:,} 行 "
            f"(经 {loaded})"
        )
    log(f"      ({csv_name} 经 {loaded} 装载, {table} 共 {n:,} 行)")
    return n


def parse_analysis(path: str):
    """解析 analysis.sql: 以 '-- @@name X' 标记切分查询块, 忽略注释行"""
    with open(path, encoding="utf-8") as f:
        content = f.read()

    blocks = re.split(r"^--\s*@@name\s+", content, flags=re.MULTILINE)[1:]
    queries = []
    for blk in blocks:
        lines = blk.splitlines()
        name = lines[0].strip()
        rest = "\n".join(lines[1:])
        sql_lines = [ln for ln in rest.splitlines()
                     if not ln.strip().startswith("--") and ln.strip()]
        sql = "\n".join(sql_lines).strip().rstrip(";")
        if sql:
            queries.append((name, sql))
    return queries


def main() -> None:
    ap = argparse.ArgumentParser(description="装载能耗数据到 MySQL 并执行分析")
    ap.add_argument("--host", default=os.getenv("MYSQL_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("MYSQL_PORT", "3306")))
    ap.add_argument("--user", default=os.getenv("MYSQL_USER", "root"))
    ap.add_argument("--password", default=os.getenv("MYSQL_PWD", ""))
    ap.add_argument("--db", default=os.getenv("MYSQL_DB", "industrial_energy"))
    ap.add_argument("--init", action="store_true", help="执行 create_table.sql 重建表结构")
    ap.add_argument("--run-analysis", action="store_true", help="执行 analysis.sql 并导出结果")
    ap.add_argument("--truncate", action="store_true", help="装载前清空事实表")
    args = ap.parse_args()

    try:
        conn = connect(args)
    except pymysql.err.OperationalError as e:
        log(f"[错误] 无法连接 MySQL {args.host}:{args.port} — {e}")
        log("       请确认服务已启动, 并用 --user/--password 或环境变量 MYSQL_PWD 提供口令")
        raise SystemExit(1)

    with conn.cursor() as cur:
        cur.execute("SELECT VERSION()")
        log(f"[连接] MySQL {cur.fetchone()[0]} @ {args.host}:{args.port} (user={args.user})")

    # ---- 1. 建库建表 -----------------------------------------------------
    if args.init:
        log("\n[1/3] 执行 sql/create_table.sql ...")
        run_script(conn, os.path.join(SQL_DIR, "create_table.sql"), use_db=None)
        log("      建库/建表/视图/主数据 完成")
    else:
        log("\n[1/3] 跳过建表 (未指定 --init)")
        with conn.cursor() as cur:
            cur.execute(f"USE `{args.db}`")
        if args.truncate:
            with conn.cursor() as cur:
                cur.execute("SET FOREIGN_KEY_CHECKS=0")
                cur.execute("TRUNCATE TABLE fact_energy_consumption")
                cur.execute("TRUNCATE TABLE fact_production")
                cur.execute("TRUNCATE TABLE dim_calendar")
                cur.execute("SET FOREIGN_KEY_CHECKS=1")
            conn.commit()
            log("      已清空事实表")

    # ---- 2. 装载 ---------------------------------------------------------
    log("\n[2/3] 装载 CSV 数据 ...")
    with conn.cursor() as cur:
        try:
            cur.execute("SET GLOBAL local_infile = 1")
            log("      已开启 local_infile")
        except pymysql.err.Error:
            log("      无法开启 local_infile (权限不足), 将使用批量 INSERT")
    # dim_calendar 必须最先装载 (事实表有指向它的外键)
    n_cal = load_csv(conn, "dim_calendar", "dim_calendar.csv", CAL_COLS, args.db)
    n_eng = load_csv(conn, "fact_energy_consumption", "clean_energy.csv",
                     ENERGY_COLS, args.db)
    n_prd = load_csv(conn, "fact_production", "clean_production.csv",
                     PROD_COLS, args.db)
    log(f"      dim_calendar               {n_cal:>8,} 行")
    log(f"      fact_energy_consumption    {n_eng:>8,} 行")
    log(f"      fact_production            {n_prd:>8,} 行")

    # ---- 3. 分析 ---------------------------------------------------------
    if args.run_analysis:
        log("\n[3/3] 执行 sql/analysis.sql ...")
        queries = parse_analysis(os.path.join(SQL_DIR, "analysis.sql"))
        ok = 0
        for i, (name, sql) in enumerate(queries, 1):
            try:
                with conn.cursor() as cur:
                    cur.execute(f"USE `{args.db}`")
                    cur.execute(sql)
                    cols = [d[0] for d in cur.description]
                    rows = cur.fetchall()
                safe = re.sub(r"[^\w\-]", "_", name)
                out = os.path.join(OUT_DIR, f"{safe}.csv")
                with open(out, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f)
                    w.writerow(cols)
                    w.writerows(rows)
                log(f"  [{i:>2}/{len(queries)}] {name:<46} {len(rows):>6,} 行 -> {os.path.basename(out)}")
                ok += 1
            except pymysql.err.Error as e:
                log(f"  [{i:>2}/{len(queries)}] {name:<46} 失败: {e}")
        log(f"      完成 {ok}/{len(queries)} 条查询, 结果已导出到 output/")
    else:
        log("\n[3/3] 跳过分析 (未指定 --run-analysis)")

    conn.close()
    log("\n[完成]")


if __name__ == "__main__":
    main()
