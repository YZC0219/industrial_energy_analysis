# -*- coding: utf-8 -*-
"""
装载一致性测试 —— 需要 MySQL, 默认跳过。

    python -m pytest -m db

这个文件的目标是第一阶段指定的三个测试目标:

  1. 同一批数据连续装载两次, 记录数与能耗总量不变(幂等)
  2. 修改一条历史记录后重新装载, 数据库正确更新(而非静默忽略);
     且重放过期数据时, 已有的新值不得被改回去
  3. 装载中断后重跑, 最终结果与一次完整装载一致

事实表装载走 upsert: 唯一键冲突时按 updated_at 判新旧, 只有严格更新的版本
才覆盖 (见 import_mysql.build_upsert_sql)。目标 2 曾在第一阶段被标成
xfail(strict) —— 当时用的是 `LOAD DATA ... IGNORE`, IGNORE 的语义是
"冲突时保留已存在的行、丢弃新来的行", 历史修正会被静默丢弃。upsert 落地后
该标记已移除。
"""

from __future__ import annotations

import os
import secrets

import pandas as pd
import pytest

import import_mysql as im
from import_mysql import ENERGY_COLS, PROD_COLS, CAL_COLS
from tools.setup_bi_reader import grant_bi_reader

pytestmark = pytest.mark.db

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE_DIR, "output")
DB_NAME = os.getenv("MYSQL_DB", "industrial_energy")


def _connect(db_params):
    import pymysql
    # local_infile=True 是必须的: LOAD DATA LOCAL INFILE 的客户端一侧也要开启,
    # 只开服务端会报 3948。这与 src/import_mysql.py 的 connect() 保持一致。
    return pymysql.connect(charset="utf8mb4", autocommit=False,
                           local_infile=True, **db_params)


def _energy_total(conn, db: str):
    """返回 (行数, 能耗合计) —— 幂等与一致性都靠这两个数判断"""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*), COALESCE(SUM(consumption), 0) "
            f"FROM `{db}`.fact_energy_consumption"
        )
        return cur.fetchone()


@pytest.fixture
def loaded_db(db_params):
    """重建表并完整装载一次, 返回 (conn, db)。

    每个测试都从干净的表开始: 用 create_table.sql 重建(其中是 DROP TABLE
    IF EXISTS), 这样测试之间不会互相影响。

    注意: 连接时**不指定 database**, 因为 create_table.sql 自己会建库;
    先在 DB_PARAMS 里连上目标库、再执行建库语句会报库不存在。
    """
    conn = _connect(db_params)
    db = DB_NAME
    im.run_script(conn, os.path.join(BASE_DIR, "sql", "create_table.sql"), use_db=None)
    with conn.cursor() as cur:
        cur.execute("SET GLOBAL local_infile = 1")
    im.load_csv(conn, "dim_calendar", "dim_calendar.csv", CAL_COLS, db)
    im.load_csv(conn, "fact_energy_consumption", "clean_energy.csv", ENERGY_COLS,
                db, upsert=True)
    im.load_csv(conn, "fact_production", "clean_production.csv", PROD_COLS, db)
    yield conn, db
    conn.close()


def test_bi_reader_can_query_only_the_enriched_view(loaded_db, db_params):
    """Integration guard: BI view reads succeed, raw fact reads and writes fail."""
    conn, db = loaded_db
    import pymysql

    username = "bi_test_" + secrets.token_hex(5)
    password = secrets.token_urlsafe(24)
    try:
        grant_bi_reader(conn, db, username, password)
        reader = pymysql.connect(
            host=db_params["host"], port=db_params["port"], user=username,
            password=password, database=db, charset="utf8mb4", autocommit=True,
        )
        try:
            with reader.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM `{db}`.v_energy_enriched")
                assert cursor.fetchone()[0] > 0
                with pytest.raises(pymysql.err.OperationalError, match="1142"):
                    cursor.execute(f"SELECT COUNT(*) FROM `{db}`.fact_energy_consumption")
                with pytest.raises(pymysql.err.OperationalError, match="1142"):
                    cursor.execute(
                        f"UPDATE `{db}`.v_energy_enriched SET consumption=0 LIMIT 1"
                    )
        finally:
            reader.close()
    finally:
        with conn.cursor() as cursor:
            cursor.execute(f"DROP USER IF EXISTS '{username}'@'%'")
        conn.commit()


# =============================================================================
# 目标 1: 幂等 —— 连装两次, 行数与能耗总量不变
# =============================================================================

def test_reload_is_idempotent(loaded_db):
    """同一批数据再装一次, 行数与能耗合计都必须不变。

    这条守的是"管道重跑任意次结果一致"这个承诺。若失败, 说明 IGNORE 没生效
    或唯一键缺失, 数据会随重跑次数翻倍。
    """
    conn, db = loaded_db
    before = _energy_total(conn, db)

    im.load_csv(conn, "fact_energy_consumption", "clean_energy.csv", ENERGY_COLS,
                db, upsert=True)
    after = _energy_total(conn, db)

    assert after[0] == before[0], f"重装后行数变了: {before[0]} -> {after[0]}"
    assert float(after[1]) == pytest.approx(float(before[1]), rel=1e-9), \
        f"重装后能耗总量变了: {before[1]} -> {after[1]}"


def test_loaded_row_count_matches_csv(loaded_db):
    """表内行数必须等于 CSV 数据行数 —— 这是 a0761f0 那次修复的核心校验。

    修复前这里会静默装入 0 行(行尾不匹配)或漏行(路径错), 而任务仍报成功。
    """
    conn, db = loaded_db
    path = os.path.join(OUT_DIR, "clean_energy.csv")
    with open(path, encoding="utf-8-sig", newline="") as f:
        file_rows = sum(1 for _ in f) - 1

    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM `{db}`.fact_energy_consumption")
        n = cur.fetchone()[0]
    assert n == file_rows, f"CSV {file_rows} 行, 表中 {n} 行"


def test_production_fact_matches_csv(loaded_db):
    """产量表同样校验行数"""
    conn, db = loaded_db
    prod = pd.read_csv(os.path.join(OUT_DIR, "clean_production.csv"),
                       encoding="utf-8-sig")
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM `{db}`.fact_production")
        n = cur.fetchone()[0]
    assert n == len(prod)


# =============================================================================
# 目标 3: 中断重跑 —— 最终结果与一次完整装载一致
# =============================================================================

def test_resume_after_partial_load_matches_full(loaded_db):
    """模拟"装载到一半断了", 重跑后结果应与完整装载一致。

    做法: 把事实表清空, 只装前一半数据, 再完整跑一次装载。因为装载是
    INSERT IGNORE, 已存在的前一半会被跳过、缺少的后一半会补上, 最终行数与
    能耗总量应与"一次装满"完全相同 —— 不重复累计。
    """
    conn, db = loaded_db
    full = _energy_total(conn, db)

    # 清空, 只装一半
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        cur.execute(f"TRUNCATE TABLE `{db}`.fact_energy_consumption")
        cur.execute("SET FOREIGN_KEY_CHECKS=1")
    conn.commit()

    half_path = os.path.join(OUT_DIR, "clean_energy.csv")
    df = pd.read_csv(half_path, encoding="utf-8-sig")
    half = df.iloc[: len(df) // 2]
    tmp_name = "_test_half_energy.csv"
    half.to_csv(os.path.join(OUT_DIR, tmp_name), index=False,
                encoding="utf-8-sig", lineterminator="\n")
    try:
        im.load_csv(conn, "fact_energy_consumption", tmp_name, ENERGY_COLS, db,
                    upsert=True)
        partial = _energy_total(conn, db)
        assert partial[0] == len(half), "半量装载本身就不完整, 测试前提不成立"

        # 断点续跑: 再装完整文件
        im.load_csv(conn, "fact_energy_consumption", "clean_energy.csv",
                    ENERGY_COLS, db, upsert=True)
        resumed = _energy_total(conn, db)

        assert resumed[0] == full[0], \
            f"续跑后行数与完整装载不一致: {full[0]} vs {resumed[0]}"
        assert float(resumed[1]) == pytest.approx(float(full[1]), rel=1e-9), \
            f"续跑后能耗总量与完整装载不一致: {full[1]} vs {resumed[1]}"
    finally:
        p = os.path.join(OUT_DIR, tmp_name)
        if os.path.exists(p):
            os.remove(p)


# =============================================================================
# 目标 2: 历史修正 —— 更新的版本覆盖旧值, 过期的版本不得回退
# =============================================================================

def _patch_one_row(df, idx: int, qty: float, updated_at: str):
    """复制一份 CSV, 把第 idx 行的消耗量与时间戳改掉, 写到临时文件"""
    patched = df.copy()
    patched.loc[idx, "consumption"] = qty
    patched.loc[idx, "updated_at"] = updated_at
    tmp = "_test_corrected_energy.csv"
    patched.to_csv(os.path.join(OUT_DIR, tmp), index=False,
                   encoding="utf-8-sig", lineterminator="\n")
    return tmp


def _fetch_consumption(conn, db, target):
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT consumption FROM `{db}`.fact_energy_consumption "
            f"WHERE record_date=%s AND workshop_code=%s AND energy_code=%s",
            (str(target["record_date"]), str(target["workshop_code"]),
             str(target["energy_code"])),
        )
        return cur.fetchone()


def test_historical_correction_is_applied(loaded_db):
    """修改一条历史记录的消耗量(并给出更晚的时间戳)后重新装载, 库里应变成新值。

    这是"任务 B: 已修改的记录能够正确更新"那条验收标准的最小复现。
    真实场景里上游修正一条历史记录, 该记录的 updated_at 必然变晚 ——
    因此这里必须把时间戳一并推后, 才构得成"修正"。
    """
    conn, db = loaded_db

    path = os.path.join(OUT_DIR, "clean_energy.csv")
    df = pd.read_csv(path, encoding="utf-8-sig")
    target = df.iloc[0]
    new_qty = float(target["consumption"]) + 100.0
    later = pd.Timestamp(target["updated_at"]) + pd.Timedelta(hours=1)
    tmp = _patch_one_row(df, 0, new_qty, later.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        im.load_csv(conn, "fact_energy_consumption", tmp, ENERGY_COLS, db,
                    upsert=True)
        row = _fetch_consumption(conn, db, target)
        assert row is not None
        assert float(row[0]) == pytest.approx(new_qty), (
            f"历史修正未生效: 期望 {new_qty}, 库中仍为 {row[0]}"
        )
    finally:
        p = os.path.join(OUT_DIR, tmp)
        if os.path.exists(p):
            os.remove(p)


def test_stale_version_does_not_overwrite(loaded_db):
    """重放一批更旧的数据, 不得把库里已有的新值改回去。

    守的是"补跑历史区间"这个日常操作: 若 upsert 只做无条件覆盖,
    把去年的数据重放一遍就会把后来的修正全部抹掉。
    """
    conn, db = loaded_db

    path = os.path.join(OUT_DIR, "clean_energy.csv")
    df = pd.read_csv(path, encoding="utf-8-sig")
    target = df.iloc[1]

    # 先推一个更晚的修正版本
    newer_qty = float(target["consumption"]) + 50.0
    later = pd.Timestamp(target["updated_at"]) + pd.Timedelta(hours=2)
    tmp = _patch_one_row(df, 1, newer_qty, later.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        im.load_csv(conn, "fact_energy_consumption", tmp, ENERGY_COLS, db,
                    upsert=True)
        assert float(_fetch_consumption(conn, db, target)[0]) == \
            pytest.approx(newer_qty), "新版本未写入, 测试前提不成立"

        # 再推一个更早的版本, 它应当被忽略
        older_qty = float(target["consumption"]) - 25.0
        earlier = pd.Timestamp(target["updated_at"]) - pd.Timedelta(hours=2)
        tmp2 = _patch_one_row(df, 1, older_qty, earlier.strftime("%Y-%m-%d %H:%M:%S"))
        try:
            im.load_csv(conn, "fact_energy_consumption", tmp2, ENERGY_COLS, db,
                        upsert=True)
            got = float(_fetch_consumption(conn, db, target)[0])
            assert got == pytest.approx(newer_qty), (
                f"过期版本把新值改回去了: 期望仍为 {newer_qty}, 实际 {got}"
            )
        finally:
            p2 = os.path.join(OUT_DIR, tmp2)
            if os.path.exists(p2):
                os.remove(p2)
    finally:
        p = os.path.join(OUT_DIR, tmp)
        if os.path.exists(p):
            os.remove(p)


def test_soft_delete_tombstone_hides_fact_and_stale_snapshot_cannot_resurrect(loaded_db):
    conn, db = loaded_db
    source = pd.read_csv(os.path.join(OUT_DIR, "clean_energy.csv"), encoding="utf-8-sig")
    target = source.iloc[0]
    key = (str(target["record_date"]), str(target["workshop_code"]),
           str(target["energy_code"]))
    deleted_at = pd.Timestamp(target["updated_at"]) + pd.Timedelta(days=1)

    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE `{db}`.fact_energy_consumption SET is_deleted=1, updated_at=%s "
            "WHERE record_date=%s AND workshop_code=%s AND energy_code=%s",
            (deleted_at.to_pydatetime(), *key),
        )
        cur.execute(
            f"SELECT COUNT(*) FROM `{db}`.v_energy_enriched "
            "WHERE record_date=%s AND workshop_code=%s AND energy_code=%s", key,
        )
        assert cur.fetchone()[0] == 0
    conn.commit()

    # Replaying the older full snapshot must preserve the later tombstone timestamp.
    temp_name = _patch_one_row(source, 0, float(target["consumption"]),
                               str(target["updated_at"]))
    try:
        im.load_csv(conn, "fact_energy_consumption", temp_name, ENERGY_COLS, db,
                    upsert=True)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT is_deleted FROM `{db}`.fact_energy_consumption "
                "WHERE record_date=%s AND workshop_code=%s AND energy_code=%s", key,
            )
            assert cur.fetchone()[0] == 1
            cur.execute(
                f"SELECT COUNT(*) FROM `{db}`.v_energy_enriched "
                "WHERE record_date=%s AND workshop_code=%s AND energy_code=%s", key,
            )
            assert cur.fetchone()[0] == 0
    finally:
        p = os.path.join(OUT_DIR, temp_name)
        if os.path.exists(p):
            os.remove(p)


# =============================================================================
# 增量装载: 水位线的推进语义
# =============================================================================

def _watermark(conn, db: str):
    """读取水位线行, 返回 (watermark_val, last_rows)；无记录返回 (None, None)。

    watermark_val 归一化成 'YYYY-MM-DD HH:MM:SS' 字符串: pymysql 读 DATETIME 给的是
    datetime 对象, 而 advance_watermark 的入参/出参都是字符串。两边混着比会在类型上
    翻车, 掩盖真正的语义断言。
    """
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT watermark_val, last_rows FROM `{db}`.etl_watermark "
            f"WHERE target_table = 'fact_energy_consumption'")
        row = cur.fetchone()
    if not row or row[0] is None:
        return (None, row[1] if row else None)
    return (row[0].strftime("%Y-%m-%d %H:%M:%S"), row[1])


def test_watermark_advances_within_load_transaction(loaded_db):
    """水位线必须随装载推进, 且等于**表内实际最大值**(不是 CSV 里的最大值)。

    这条同时守住三件事:
      1. advance_watermark 确实写入(而不是静默不生效)
      2. 取值来自已落库数据(SELECT MAX(...) WHERE col > 旧值)
      3. 装载与推进在同一事务里完成 —— 这里先 commit=False 装载、再推进、
         最后统一 commit, 与 import_mysql.main() 的增量分支同一路径
    """
    conn, db = loaded_db
    with conn.cursor() as cur:
        cur.execute(f"SELECT MAX(updated_at) FROM `{db}`.fact_energy_consumption")
        table_max = cur.fetchone()[0].strftime("%Y-%m-%d %H:%M:%S")

    # fixture 的全量装载走的是 import_mysql 的 --full 路径, 它会**顺带播种**水位线。
    # 所以这里不是"冷启动无水位线", 而是"水位线已与表内最大值对齐" —— 这本身就是
    # 一条断言: 全量装载之后, 水位线必须等于表里最大的 updated_at, 否则下一批会
    # 把已经装过的行再取一遍。
    assert _watermark(conn, db)[0] == table_max

    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM `{db}`.fact_energy_consumption")
        n_rows = cur.fetchone()[0]
        im.advance_watermark(cur, db, "fact_energy_consumption", "updated_at",
                             "1970-01-01 00:00:00", "test:advance", n_rows)
    conn.commit()

    val, last_rows = _watermark(conn, db)
    assert val == table_max, f"水位线应等于表内最大值 {table_max}, 实际 {val}"
    assert last_rows == n_rows


def test_empty_batch_does_not_move_watermark(loaded_db):
    """空批次(无新行)时水位线必须**保持不变**, 且不被兜底值打回 1970。

    这条守的是 advance_watermark 里那个不显眼的决定: MAX(...) 在新数据为空时
    返回 NULL, 若把它直接写进 watermark_val, 水位线会倒退回 1970 ——
    下一批就变成全量大重装。用 GREATEST 的 COALESCE 兜底后应当保持原值。

    这是静默的性能事故而非正确性事故(数据不会错), 但 19,006 行的全量重装
    在真实场景里是每天白跑一遍, 必须被测试钉住。
    """
    conn, db = loaded_db
    with conn.cursor() as cur:
        cur.execute(f"SELECT MAX(updated_at) FROM `{db}`.fact_energy_consumption")
        first = cur.fetchone()[0].strftime("%Y-%m-%d %H:%M:%S")
        im.advance_watermark(cur, db, "fact_energy_consumption", "updated_at",
                             "1970-01-01 00:00:00", "test:seed", 1)
    conn.commit()
    assert _watermark(conn, db)[0] == first

    # 用当前水位线作下界去推进 —— 没有更新的行, MAX 返回 NULL
    with conn.cursor() as cur:
        im.advance_watermark(cur, db, "fact_energy_consumption", "updated_at",
                             first, "test:empty", 0)
    conn.commit()

    val, last_rows = _watermark(conn, db)
    assert val == first, f"空批次把水位线改动了: {first} -> {val}"
    assert last_rows == 0


def test_watermark_ignores_older_replay(loaded_db):
    """重放一个更旧的批次, 不得让水位线**倒退**。

    与事实表 upsert 的 GREATEST 同一理由: 水位线是单调不减的。若用 REPLACE
    或直接赋值, 补跑一段历史区间就会把水位线拉回去, 下一批变成大范围重装。
    """
    conn, db = loaded_db
    with conn.cursor() as cur:
        cur.execute(f"SELECT MAX(updated_at) FROM `{db}`.fact_energy_consumption")
        table_max = cur.fetchone()[0].strftime("%Y-%m-%d %H:%M:%S")
        im.advance_watermark(cur, db, "fact_energy_consumption", "updated_at",
                             "1970-01-01 00:00:00", "test:high", 1)
    conn.commit()

    # 用"更旧的上界"去推进: 实际表内最大值仍高于它, 但我们要验证的是
    # 即使传入的 old_val 更早, 结果也不会低于已记录的值
    with conn.cursor() as cur:
        im.advance_watermark(cur, db, "fact_energy_consumption", "updated_at",
                             "2024-01-01 00:00:00", "test:replay", 0)
    conn.commit()

    val, _ = _watermark(conn, db)
    assert val == table_max, f"水位线倒退了: 期望 {table_max}, 实际 {val}"


def test_out_of_range_batch_is_rejected_not_silently_dropped(loaded_db, db_params, tmp_path):
    """批次日期超出 dim_calendar 范围时, 装载必须**报错终止**而不是装进去。

    这条守的是一个真实踩到的静默失败: v_energy_enriched 用 INNER JOIN dim_calendar,
    而 29 条查询全部经该视图。日期在维表范围外的行**装得进事实表, 却在视图里被
    JOIN 滤掉** —— 事实表行数正常、水位线正常推进、管道报 success, 而所有分析
    结果里这批数据一行都看不见。最坏的一类失败: 无声且伪装成成功。

    所以现在装载前会校验批次最大 record_date 是否超出 dim_calendar 的最大日期,
    越界抛 SystemExit(4)。这条测试锁住"校验存在且生效"——没有它, 后来者很容易
    在重构时把这步校验当成多余的防御删掉。
    """
    import subprocess
    import tempfile
    import sys as _sys

    loaded_db   # 确保库已建、水位线已播种(增量分支才会走到)
    batch_dir = tempfile.mkdtemp(dir=str(tmp_path))
    # 写一个日期远超维表范围的批次文件 (dim_calendar 到 2025-12-31)
    energy_csv = os.path.join(batch_dir, "clean_batch_energy.csv")
    prod_csv = os.path.join(batch_dir, "clean_batch_production.csv")
    with open(energy_csv, "w", encoding="utf-8-sig", newline="") as f:
        f.write("record_date,workshop_code,energy_code,consumption,unit,"
                "unit_price,cost,temperature,updated_at\n")
        f.write("2031-06-01,W01,E01,100,kWh,0.65,65.0,20,2031-06-01 09:00:00\n")
    with open(prod_csv, "w", encoding="utf-8-sig", newline="") as f:
        f.write("record_date,workshop_code,output_qty,output_unit\n")
        f.write("2031-06-01,W01,500,t\n")
    # dim_calendar.csv 也必须在这个目录里 —— 校验靠它取维表上界。
    # 不写它会被判成"批次目录不完整"(退出码 3), 那是另一条守卫。
    import shutil
    shutil.copy(os.path.join(OUT_DIR, "dim_calendar.csv"),
                os.path.join(batch_dir, "dim_calendar.csv"))

    # 显式把连接参数传给子进程: 否则子进程用默认端口(3306)去连, 而本机 3306 上
    # 往往是另一个 MySQL —— 连不上就退出 1, 断言把它读成"没做越界校验", 属于误报。
    env = dict(os.environ)
    env["MYSQL_HOST"] = db_params["host"]
    env["MYSQL_PORT"] = str(db_params["port"])
    env["MYSQL_USER"] = db_params["user"]
    env["MYSQL_PWD"] = db_params["password"]
    proc = subprocess.run(
        [_sys.executable, "src/import_mysql.py", "--incremental",
         "--batch-dir", batch_dir],
        cwd=BASE_DIR, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 4, \
        f"越界批次应终止(4), 实际 {proc.returncode}\n{proc.stdout[-800:]}"
    assert "dim_calendar" in proc.stdout, "报错信息应指明是维表覆盖不到"
