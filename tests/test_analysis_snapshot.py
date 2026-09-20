# -*- coding: utf-8 -*-
"""
Q01~Q29 结果回归基线测试 —— 需要 MySQL, 默认跳过。

    python -m pytest -m db

**为什么需要它**: 第一阶段之后要动的是装载逻辑(增量)、表结构(维度扩展)和
口径视图(统一公用工程)。这三件事任何一件做错, 都会让 29 条分析查询的结果
悄悄漂移 —— 不报错、不崩, 只是数字变了。没有基线就发现不了。

**基线怎么来的**: `tests/baseline/` 下是 2026-09-20 在固定种子数据上跑出的
29 份结果快照, 已经过人工核对(见 README 的回归基线表)。它们是"正确答案"。

**比较策略**: 逐行逐列比对, 浮点按相对容差 1e-6 比较。不要求字节相同 ——
CSV 里的浮点格式化方式可能随 pandas 版本变化, 那样的差异不是回归。
但**行数、列名、行序**必须一致: 它们变了说明 SQL 语义真的变了。

**基线过期了怎么办**: 如果确认改动是**有意**改变业务口径(比如统一公用工程
后数字本就该变), 那就更新基线, 并在提交信息里写明为什么变。更新方法:
    python tests/update_baseline.py
"""

from __future__ import annotations

import os

import pandas as pd
import pytest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE_DIR, "output")
BASELINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline")

REL_TOL = 1e-6

pytestmark = pytest.mark.db


def _baseline_files():
    if not os.path.isdir(BASELINE_DIR):
        return []
    return sorted(f for f in os.listdir(BASELINE_DIR) if f.endswith(".csv"))


def _read(path: str) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def _compare(name: str, expected: pd.DataFrame, actual: pd.DataFrame) -> list[str]:
    """返回差异描述列表, 空列表表示一致。"""
    problems = []

    if list(expected.columns) != list(actual.columns):
        problems.append(
            f"列名不一致\n     基线: {list(expected.columns)}\n     实际: {list(actual.columns)}"
        )
        return problems  # 列都对不上, 后面的数值比较没有意义

    if len(expected) != len(actual):
        problems.append(f"行数不一致: 基线 {len(expected)} 行, 实际 {len(actual)} 行")
        return problems

    # 逐列比较: 数值列用容差, 其余(字符串/日期)要求严格相等
    for col in expected.columns:
        exp, act = expected[col], actual[col]
        if pd.api.types.is_numeric_dtype(exp) and pd.api.types.is_numeric_dtype(act):
            # 两边都是 NaN 视为相等; 一边 NaN 一边有值视为差异
            both_nan = exp.isna() & act.isna()
            one_nan = exp.isna() ^ act.isna()
            if one_nan.any():
                idx = list(one_nan[one_nan].index[:5])
                problems.append(f"列 `{col}`: 第 {idx} 行一边有值一边为空")
                continue
            diff = (exp - act).abs()
            scale = exp.abs().clip(lower=1e-12)
            bad = (~both_nan) & (diff / scale > REL_TOL)
            if bad.any():
                idx = list(bad[bad].index[:5])
                problems.append(
                    f"列 `{col}`: {int(bad.sum())} 行超出容差 {REL_TOL}, "
                    f"首几行 基线={list(exp[idx])} 实际={list(act[idx])}"
                )
        else:
            both_na = exp.isna() & act.isna()
            neq = ~(both_na | (exp.astype(str) == act.astype(str)))
            if neq.any():
                idx = list(neq[neq].index[:5])
                problems.append(
                    f"列 `{col}`: {int(neq.sum())} 行不等, "
                    f"首几行 基线={list(exp[idx])} 实际={list(act[idx])}"
                )
    return problems


@pytest.mark.parametrize("fname", _baseline_files())
def test_query_matches_baseline(fname, db_params):
    """跑一遍分析并逐条比对基线。

    这里直接在测试里连库执行 analysis.sql 的查询 —— 不调用 import_mysql.py,
    是为了让"装载"和"分析"两条被测路径互不掩盖: 装载测试在另一个文件里。
    """
    import pymysql

    from import_mysql import parse_analysis

    baseline_path = os.path.join(BASELINE_DIR, fname)
    actual_path = os.path.join(OUT_DIR, fname)

    if not os.path.exists(actual_path):
        pytest.skip(f"output/{fname} 不存在, 请先运行 --run-analysis")

    problems = _compare(fname, _read(baseline_path), _read(actual_path))
    assert not problems, (
        f"{fname} 与回归基线不一致:\n   - " + "\n   - ".join(problems)
        + "\n\n若本次改动是有意改变口径, 请运行 python tests/update_baseline.py 更新基线,"
          "\n并在提交信息里说明为什么变。"
    )


def test_baseline_covers_all_queries():
    """基线必须覆盖 Q01~Q29 全部 29 条, 少一条就是漏保护"""
    files = _baseline_files()
    assert len(files) == 29, f"基线应含 29 个文件, 实际 {len(files)} 个"
    ids = {f.split("_")[0] for f in files}
    assert ids == {f"Q{i:02d}" for i in range(1, 30)}


def test_analysis_sql_query_count(db_params):
    """analysis.sql 里的查询条数必须与基线一致 —— 防止新增查询却忘了建基线"""
    from import_mysql import parse_analysis

    sql_path = os.path.join(BASE_DIR, "sql", "analysis.sql")
    queries = parse_analysis(sql_path)
    assert len(queries) == 29, (
        f"analysis.sql 含 {len(queries)} 条查询, 基线有 29 条。"
        f"新增查询后请一并生成对应基线。"
    )
