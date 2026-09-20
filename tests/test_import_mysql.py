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

import pandas as pd
import pytest

import import_mysql as im
from import_mysql import ENERGY_COLS, PROD_COLS, CAL_COLS

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
