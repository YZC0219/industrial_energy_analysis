# -*- coding: utf-8 -*-
"""
import_mysql.py — 建库建表、装载清洗后数据、执行分析 SQL

步骤:
  1. --init      执行 sql/create_table.sql (建库/建表/建视图/灌主数据)
  2. 装载        output/clean_energy.csv -> fact_energy_consumption (按 updated_at upsert)
                 output/clean_production.csv -> fact_production
                 output/dim_calendar.csv -> dim_calendar
     (使用 LOAD DATA LOCAL INFILE 批量装载, 比逐行 INSERT 快 1~2 个数量级)
  3. --run-analysis  逐条执行 sql/analysis.sql, 结果导出到 output/qNN_*.csv

装载的三种模式:
  增量(默认)   读 etl_watermark -> 只装 clean_batch_*.csv -> **同事务**推进水位线。
               水位线为空时自动退化为全量(冷启动), 所以首次部署不需要特殊操作。
  --full       忽略水位线, 装完整 clean_energy.csv, 并把水位线重置为全量最大值。
               用于"我确认要以当前完整数据为准重算"。
  --init --full 重建表结构 + 全量装载 + 重置水位线。**首次部署用这个**。
  --init 单独用会**被拒绝** —— 它清空事实表却不重置水位线, 会让水位线领先于数据,
  之后的数据被永久跳过。详见 main() 里的说明。

用法:
  python src/import_mysql.py --init --full --run-analysis     # 首次部署
  python src/import_mysql.py --run-analysis                   # 日常: 增量装载 + 分析
  MYSQL_PWD=你的密码 python src/import_mysql.py

连接参数优先级: 命令行参数 > 环境变量 > 默认值
  环境变量: MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PWD / MYSQL_DB
"""

import argparse
import csv
import os
import re
import sys
from datetime import datetime

import pymysql

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL_DIR = os.path.join(BASE_DIR, "sql")
OUT_DIR = os.path.join(BASE_DIR, "output")

# CSV 列顺序必须与建表顺序一致 (dim_calendar 的列名与表头一致)
ENERGY_COLS = ["record_date", "workshop_code", "energy_code", "consumption", "unit",
               "unit_price", "cost", "record_status", "avg_temperature",
               "data_source", "is_production_day", "updated_at", "is_deleted"]
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
    """按分号切分 SQL 脚本, 跳过注释与空语句

    注意: 这里是**朴素按 `;` 切分**, 不识别引号 —— 单引号里的分号(例如
    `COMMENT '...; ...'`)会把一条语句切成两半, 两半都语法错误。
    `sql/create_table.sql` 里现存的 COMMENT 恰好都没有分号, 所以一直没暴露。
    要写带分号的字符串字面量, 得么避开分号, 要么把本函数改成逐字符扫描引号状态。
    """
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


def ensure_soft_delete_column(conn, db: str) -> None:
    """Idempotently upgrade pre-existing MySQL fact tables for soft-delete CDC."""
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", db):
        raise ValueError("database must be a simple SQL identifier")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='fact_energy_consumption' "
            "AND COLUMN_NAME='is_deleted'", (db,)
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                f"ALTER TABLE `{db}`.`fact_energy_consumption` "
                "ADD COLUMN `is_deleted` TINYINT(1) NOT NULL DEFAULT 0 "
                "COMMENT '软删除标记; 增量 CDC tombstone' AFTER `updated_at`"
            )
    conn.commit()
    run_script(conn, os.path.join(SQL_DIR, "migrations", "001_soft_delete_view.sql"), db)


def build_upsert_sql(table: str, columns, db: str, key: str = "updated_at") -> str:
    """
    生成"按 updated_at 判新旧"的 upsert 语句(源为临时表 _stage)。

    为什么是 upsert 而不是 INSERT IGNORE: 唯一键 (record_date, workshop_code,
    energy_code) 只标识"哪条业务记录", 不标识"哪个版本"。上游修正一条历史
    记录后重新装载, IGNORE 会把新版本当冲突丢掉, 库里永远停在旧值上。

    为什么每列都套 IF(new.updated_at > t.updated_at, ...): 只靠 ODKU 会无条件
    覆盖 —— 那样重放一批更旧的数据(如补跑历史区间)会把已有的新值改回旧的。
    加上这个条件后, 只有严格更新的版本才写得进去, 过期数据被正确忽略。

    为什么 updated_at 用 GREATEST 而不是直接赋值: 被忽略的旧行不该把它自己
    那个更早的时间戳盖上去, 否则该行的"最后修改时刻"会随重放倒退。
    """
    cols = list(columns)
    new_vals = ", ".join(f"new.`{c}`" for c in cols)
    # 判新旧的条件: 显式把 NULL 当"最旧"。若某行 updated_at 为空,
    # `new.updated_at > t.updated_at` 恒为 NULL(falsy), 该行永远无法覆盖 ——
    # 这里用 COALESCE 让"空时间戳"退化为"可覆盖", 语义更接近"缺元数据不代表更新"。
    newer = (f"COALESCE(new.`{key}`, '1970-01-01') "
             f"> COALESCE(`{table}`.`{key}`, '1970-01-01')")
    assigns = [f"`{c}` = IF({newer}, new.`{c}`, `{table}`.`{c}`)"
               for c in cols if c != key]
    assigns.append(
        f"`{key}` = GREATEST(COALESCE(`{table}`.`{key}`, '1970-01-01'), "
        f"COALESCE(new.`{key}`, '1970-01-01'))"
    )
    return (
        f"INSERT INTO `{db}`.`{table}` "
        f"({', '.join(f'`{c}`' for c in cols)}) "
        f"SELECT {new_vals} FROM `{db}`._stage AS new "
        f"ON DUPLICATE KEY UPDATE " + ", ".join(assigns)
    )


def _file_max_date(path: str, col: str) -> str:
    """
    读取 CSV 里某一日期列的最大值(按 ISO 格式字符串比较即可, 字典序 == 时序)。

    为何不引 pandas: 这个模块刻意只依赖 pymysql + 标准库, 装载侧不需要 DataFrame。
    为一次边界校验引入 pandas 会让"装载"这一层的依赖面无故变宽。
    """
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        vals = [r[col] for r in reader if r.get(col)]
    return max(vals) if vals else ""


def _file_min_date(path: str, col: str) -> str:
    """读取 ISO 日期列的最小值, 与 _file_max_date 配对做完整范围校验。"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        vals = [r[col] for r in reader if r.get(col)]
    return min(vals) if vals else ""


def _date_range_is_covered(calendar_min: str, calendar_max: str,
                           batch_min: str, batch_max: str) -> bool:
    """判断非空批次日期区间是否完整落在日期维覆盖范围内。"""
    return not batch_min or calendar_min <= batch_min <= batch_max <= calendar_max


def load_csv(conn, table: str, csv_name: str, columns, db: str, upsert: bool = False,
             commit: bool = True) -> int:
    """
    把一个 CSV 装载进指定表, 返回装载后该表的总行数。

    装载是幂等的: 重复执行本脚本不会产生重复数据(与已经装载过的数据保持一致)。
    upsert=False 时靠 INSERT IGNORE 丢弃冲突行; upsert=True 时按 updated_at
    判新旧, 更新的版本覆盖旧值 —— 见 build_upsert_sql 的说明。
    装完后会用表内实际行数校验, 该写进去的行一行没少才算成功。

    commit=False 时**不提交**, 把事务留给调用方 —— 增量装载要把"写事实表"与
    "推进水位线"放进同一个事务, 所以它传 False, 由 main() 在两步都做完后统一提交。
    默认 True 保持原有调用方的行为不变(--full 路径与 tests/ 里的调用都不受影响)。
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
    # LOAD DATA 的目标表: upsert 时先进临时表, 避免冲突行在灌入阶段就被丢掉
    stage = "`%s`.`_stage`" % db if upsert else f"`{db}`.`{table}`"
    # 先导入用户变量 @col 再 SET 到真实列: 空串经 NULLIF 变成 NULL,
    # 否则空的 avg_temperature 会被当成 0 而不是"未知"
    # 行终止符用 '\n' 而非 '\r\n': 上游 clean_data.py 已固定写 LF。
    # 若换成 '\r\n', 在 LF 文件上整个文件会被当成一行, 静默装入 0 行。
    sql = (
        f"LOAD DATA LOCAL INFILE '{win_path}' INTO TABLE {stage} "
        f"CHARACTER SET utf8mb4 FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '\"' "
        f"LINES TERMINATED BY '\\n' IGNORE 1 LINES ({cols}) SET {assigns}"
    )
    with conn.cursor() as cur:
        # 装载期间关掉唯一性与外键校验以提升速度(数据本身已通过清洗保证一致性)
        cur.execute("SET UNIQUE_CHECKS=0")
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        if upsert:
            # 临时表结构与事实表一致, 但它没有唯一键, 因此同业务键的多版本
            # 都能先落进来, 留待下一步按 updated_at 挑出胜者。
            cur.execute(f"DROP TEMPORARY TABLE IF EXISTS `{db}`.`_stage`")
            cur.execute(f"CREATE TEMPORARY TABLE `{db}`.`_stage` "
                        f"LIKE `{db}`.`{table}`")
        try:
            cur.execute(sql)
            if upsert:
                cur.execute(build_upsert_sql(table, columns, db))
            loaded = "LOAD DATA + UPSERT" if upsert else "LOAD DATA"
        except pymysql.err.OperationalError as exc:
            # 只有"服务端不允许 LOCAL INFILE"这一种情况才值得降级重试。
            # 其它 OperationalError(比如服务端读不到文件)必须原样抛出 ——
            # 早先这里无差别吞掉所有 pymysql 错误, 导致装载静默失败
            # 而任务仍报 success, 后面分析查询全查空表。
            if "local_infile" not in str(exc) and "not allowed" not in str(exc):
                raise
            log("      (服务端未开启 local_infile, 退化为批量 INSERT)")
            rows = []
            with open(path, encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                next(reader)
                for r in reader:
                    if len(r) != len(columns):
                        continue
                    rows.append(tuple(v if v != "" else None for v in r))
            if upsert:
                # 降级路径同样要 upsert: 复用 LOAD DATA 分支用的那条 ODKU,
                # 只是把源表从临时表换成批量 INSERT 建出来的临时表。
                loaded = "批量 INSERT + UPSERT"
                cur.execute(f"DROP TEMPORARY TABLE IF EXISTS `{db}`.`_stage`")
                cur.execute(f"CREATE TEMPORARY TABLE `{db}`.`_stage` "
                            f"LIKE `{db}`.`{table}`")
                stage_insert = (
                    f"INSERT INTO `{db}`.`_stage` "
                    f"({', '.join(f'`{c}`' for c in columns)}) "
                    f"VALUES ({', '.join(['%s'] * len(columns))})"
                )
                for i in range(0, len(rows), 2000):
                    cur.executemany(stage_insert, rows[i:i + 2000])
                cur.execute(build_upsert_sql(table, columns, db))
            else:
                loaded = "批量 INSERT"
                insert_sql = (
                    f"INSERT IGNORE INTO `{db}`.`{table}` "
                    f"({', '.join(f'`{c}`' for c in columns)}) "
                    f"VALUES ({', '.join(['%s'] * len(columns))})"
                )
                for i in range(0, len(rows), 2000):
                    cur.executemany(insert_sql, rows[i:i + 2000])
        if upsert:
            cur.execute(f"DROP TEMPORARY TABLE IF EXISTS `{db}`.`_stage`")
        cur.execute("SET FOREIGN_KEY_CHECKS=1")
        cur.execute("SET UNIQUE_CHECKS=1")
        cur.execute(f"SELECT COUNT(*) FROM `{db}`.`{table}`")
        n = cur.fetchone()[0]
    if commit:
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


def read_watermark(conn, db: str, table: str) -> tuple:
    """
    读取指定目标表的增量水位线, 返回 (watermark_val, batch_id)。

    watermark_val 为 None 表示"从未装载过" —— 这是**冷启动**信号, 调用方应当
    退化成全量装载。注意不要把它当成"取 updated_at > NULL"(SQL 里恒为 NULL,
    会静默装 0 行, 是这类实现最容易踩的坑)。

    表不存在时同样返回 (None, ""): 首次部署可能还没跑过 --init, 由调用方决定
    是报错还是按冷启动处理。
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT watermark_val, last_batch_id FROM `{db}`.`etl_watermark` "
                f"WHERE target_table = %s", (table,))
            row = cur.fetchone()
    except pymysql.err.ProgrammingError:
        return None, ""
    if not row:
        return None, ""
    val, batch = row
    # DATETIME 经 pymysql 回来是 datetime 对象; 统一转成字符串便于拼进 SQL 与打日志
    return (val.strftime("%Y-%m-%d %H:%M:%S") if val else None), (batch or "")


def advance_watermark(cur, db: str, table: str, wm_col: str,
                      old_val, batch_id: str, rows: int) -> str:
    """
    用**表内实际最大值**推进水位线, 返回推进后的值(字符串; 未推进则返回原值)。

    只接收 cursor 而不接收 conn, 是刻意的: 它**不得**自己 commit。
    装载与水位线推进必须是同一个事务 —— 若分开提交, 会出现"水位线领先于数据":
    装载只写了一半就崩了, 水位线却已经推到最后一天, 下次从最后一天之后取数,
    **那半批记录永久丢失且不报错**, 管道每次还报 success。这类缺陷最难查。
    反过来"数据领先于水位线"(装载成功、推进失败)是可自愈的: 下批重放同一区间,
    upsert 幂等, 不产生重复。设计目标就是让所有不一致都退化成后一种。
    签名里没有 conn 就没有 commit 的能力, 用类型表达这条约束。

    为什么从**已落库**的数据取 MAX 而不是从 CSV 取: CSV 里的最大值是"我打算装的",
    表里的是"实际装进去的"。若批内有行被 upsert 忽略(如一条更旧的修正记录),
    两者会不同。水位线描述"表里有什么"而非"我尝试过什么", 语义才对。

    为什么用 GREATEST 而不是直接赋值: 重放一个更旧的批次不该让水位线**倒退**
    —— 那会让下一批变成全量大重装。与 build_upsert_sql 里 updated_at 的处理同一理由。
    """
    # 单调性不能只靠 old_val 参数: old_val 由调用方读出来再传进来, 它可能是过期的,
    # 也可能是调用方搞错了。这里**以库里已记录的行为准**再取一次, 让 GREATEST 真正
    # 兜住"倒退"这件事, 而不是依赖调用方老老实实传对了值。
    cur.execute(f"SELECT watermark_val FROM `{db}`.`etl_watermark` "
                f"WHERE target_table = %s", (table,))
    _row = cur.fetchone()
    stored_val = _row[0].strftime("%Y-%m-%d %H:%M:%S") if _row and _row[0] else None
    lower = max(old_val or "", stored_val or "") or "1970-01-01 00:00:00"

    cur.execute(f"SELECT MAX(`{wm_col}`) FROM `{db}`.`{table}` "
                f"WHERE `{wm_col}` > %s", (lower,))
    new_val = cur.fetchone()[0]
    new_str = new_val.strftime("%Y-%m-%d %H:%M:%S") if new_val else None

    # 空批次(无新行)时 new_str 为 None。此时靠 GREATEST 的 COALESCE 兜底让水位线
    # 保持不变, last_rows 记 0 作为可观测凭据。直接赋 NULL 会把水位线打回 1970,
    # 下一批变成全量大重装 —— 那是静默的性能事故, 不是正确性事故, 但同样要避免。
    cur.execute(
        f"INSERT INTO `{db}`.`etl_watermark` "
        f"(target_table, watermark_col, watermark_val, last_batch_id, last_rows) "
        f"VALUES (%s, %s, %s, %s, %s) AS new "
        f"ON DUPLICATE KEY UPDATE "
        f"watermark_val = GREATEST("
        f"    COALESCE(etl_watermark.watermark_val, '1970-01-01'), "
        f"    COALESCE(new.watermark_val, '1970-01-01')), "
        f"last_batch_id = new.last_batch_id, "
        f"last_rows     = new.last_rows",
        (table, wm_col, new_str, batch_id, rows),
    )
    return new_str or lower or ""


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
    ap.add_argument("--full", action="store_true",
                    help="以全量为准: 忽略水位线, 装完整 clean_energy.csv, 并把水位线重置为全量最大值")
    ap.add_argument("--incremental", action="store_true",
                    help="增量装载: 读水位线 -> 装批次 -> 同事务推进水位线(默认行为)")
    ap.add_argument("--batch-dir", default=OUT_DIR,
                    help=f"读取 clean_batch_*.csv 的目录 (默认 {OUT_DIR})")
    args = ap.parse_args()

    # --init 会 DROP 并重建事实表, 而水位线是**故意的**不随之重置。这两个凑在一起
    # 会产生最坏的一种错位: 水位线说"已装到 2025-12-31", 表里却一行没有, 下一批
    # 取 updated_at > 2025-12-31 得空集 —— **数据永久丢失且不报错**。
    # 所以这里运行期直接拒绝, 而不是靠文档提醒别这么用。
    if args.init and not args.full:
        log("[错误] --init 会清空事实表, 但不会重置水位线 —— 单独使用会让水位线"
            "领先于数据, 之后的数据会被永久跳过。")
        log("       首次部署请用: --init --full")
        log("       只想重置表结构: --init --full (它会一并把水位线重置为全量最大值)")
        raise SystemExit(2)

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

    if args.init:
        # --init --full: 表刚重建, 水位线里可能还留着旧值(它是故意不随 --init 重置的),
        # 必须清掉, 否则新的事实表配一个旧水位线, 下一批又会取空集。
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM `{args.db}`.`etl_watermark`")
        conn.commit()
        log("      --init 已重置水位线")

    ensure_soft_delete_column(conn, args.db)

    wm_table = "fact_energy_consumption"
    wm_col = "updated_at"
    wm_val, wm_batch = read_watermark(conn, args.db, wm_table)

    # 增量还是全量。--full 显式要求全量; 否则看水位线: 有值就走增量, 无值等价于冷启动。
    use_incremental = (not args.full) and (wm_val is not None)

    # dim_calendar 必须最先装载 (事实表有指向它的外键); 它是静态维表, 两种模式都全量灌
    n_cal = load_csv(conn, "dim_calendar", "dim_calendar.csv", CAL_COLS, args.db)

    if use_incremental:
        log(f"      水位线: {wm_table}.{wm_col} > {wm_val} "
            f"(上批 {wm_batch or '未知'}) -> 增量装载")
        # 只装增量文件。CSV 本身已由 generate_data.py --since-updated-at 或
        # clean_data.py --batch-since 切成窗口, 装载侧不再二次过滤 —— 二次过滤
        # 会让"CSV 里有什么"与"装了什么"两处逻辑各说各话, 出问题时无法判断
        # 该信哪一边。批次文件的生成范围就是装载范围。
        batch_energy = "clean_batch_energy.csv"
        batch_prod = "clean_batch_production.csv"
        for name in (batch_energy, batch_prod):
            p = os.path.join(args.batch_dir, name)
            if not os.path.exists(p):
                log(f"[错误] 增量模式需要批次文件 {name}, 但 {p} 不存在。")
                log("       先跑: python src/clean_data.py --batch-since <日期>")
                raise SystemExit(3)
        # 事实表的 record_date 必须被 dim_calendar 覆盖, 否则会**静默丢数据**:
        # v_energy_enriched 用 INNER JOIN dim_calendar, 而 29 条查询全部经该视图.
        # 日期落在维表范围外的行装得进事实表, 但在视图里被 JOIN 滤掉 ——
        # 事实表行数正常、水位线正常推进、管道报 success, 而所有分析结果里
        # 这批数据**一行都看不见**。这是最坏的一类失败: 无声、且伪装成成功。
        #
        # clean_data.build_calendar() 已按全量源事实的 min/max 日期扩展维表; 装载端
        # 仍必须**校验而不是假设**, 同时检查下界和上界, 捕捉批文件/维表不一致。
        cal_p = os.path.join(args.batch_dir, "dim_calendar.csv")
        if not os.path.exists(cal_p):
            log(f"[错误] 增量模式需要日期维表 {cal_p} 来做覆盖校验, 但它不存在。")
            log(f"       这道校验不能省: 批次日期若超出维表范围, 那些行会装进事实表")
            log(f"       却在 v_energy_enriched 的 JOIN 里被静默滤掉。")
            log(f"       先跑: python src/clean_data.py --batch-all")
            raise SystemExit(3)
        cal_min = _file_min_date(cal_p, "calendar_date")
        cal_max = _file_max_date(cal_p, "calendar_date")
        batch_dates = [
            (_file_min_date(os.path.join(args.batch_dir, name), "record_date"),
             _file_max_date(os.path.join(args.batch_dir, name), "record_date"))
            for name in (batch_energy, batch_prod)
        ]
        nonempty_ranges = [(lo, hi) for lo, hi in batch_dates if lo and hi]
        bat_min = min((lo for lo, _ in nonempty_ranges), default="")
        bat_max = max((hi for _, hi in nonempty_ranges), default="")
        if not _date_range_is_covered(cal_min, cal_max, bat_min, bat_max):
            log(f"[错误] 批次日期范围 [{bat_min}, {bat_max}] 超出 dim_calendar "
                f"覆盖范围 [{cal_min}, {cal_max}]。")
            log(f"       这些行能装进事实表, 但会在 v_energy_enriched 的 JOIN 里被")
            log(f"       静默滤掉 —— 分析结果看不见它们, 而管道不会报错。")
            log(f"       请检查全量清洗生成的日期维表 (src/clean_data.py build_calendar)。")
            raise SystemExit(4)
        # commit=False: 装载与水位线推进要落在同一个事务里。见 advance_watermark 的说明。
        n_eng = load_csv(conn, wm_table, batch_energy, ENERGY_COLS, args.db,
                         upsert=True, commit=False)
        n_prd = load_csv(conn, "fact_production", batch_prod, PROD_COLS, args.db,
                         commit=False)
        batch_id = f"inc:{datetime.now().strftime('%Y%m%dT%H%M%S')}"
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM `{args.db}`.`{wm_table}` "
                        f"WHERE `{wm_col}` > %s", (wm_val,))
            new_rows = cur.fetchone()[0]
            new_wm = advance_watermark(cur, args.db, wm_table, wm_col,
                                       wm_val, batch_id, new_rows)
        conn.commit()
        log(f"      本批新增 {new_rows:,} 行, 水位线推进 {wm_val} -> {new_wm}")
    else:
        reason = "--full 指定" if args.full else "水位线为空(冷启动)"
        log(f"      水位线: 无({reason}) -> 全量装载")
        n_eng = load_csv(conn, wm_table, "clean_energy.csv",
                         ENERGY_COLS, args.db, upsert=True, commit=False)
        n_prd = load_csv(conn, "fact_production", "clean_production.csv",
                         PROD_COLS, args.db, commit=False)
        # 全量装载后把水位线**重置**为表内最大值(用旧值 '1970-01-01' 作下界,
        # 于是 GREATEST 取到的就是全量最大值, 等价于重置)。
        batch_id = f"full:{datetime.now().strftime('%Y%m%dT%H%M%S')}"
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM `{args.db}`.`{wm_table}`")
            new_rows = cur.fetchone()[0]
            new_wm = advance_watermark(cur, args.db, wm_table, wm_col,
                                       "1970-01-01 00:00:00", batch_id, new_rows)
        conn.commit()
        log(f"      水位线重置为 {new_wm} (表内 {new_rows:,} 行)")

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
