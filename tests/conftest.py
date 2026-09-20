# -*- coding: utf-8 -*-
"""
conftest.py — 测试共用夹具

两条设计原则:

1. **测试不得污染被测产物**。管道脚本把结果写到固定的 data/ 与 output/ 路径
   (没有可配置的输出目录), 所以任何会重跑管道的测试都必须先把这两个目录
   整体备份、跑完再还原。`preserve_outputs` 夹具做的就是这件事。

2. **需要 MySQL 的测试默认跳过**。本项目的净化/分析逻辑大多可以离线验证,
   真正依赖数据库的只有装载一致性与 SQL 结果回归。把后者标记为 `db`,
   默认不跑 —— 这样"克隆仓库就能跑测试"成立, 不要求先起一个数据库。
   跑它们用: python -m pytest -m db
"""

from __future__ import annotations

import os
import shutil
import sys

import pytest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
OUT_DIR = os.path.join(BASE_DIR, "output")
DATA_DIR = os.path.join(BASE_DIR, "data")

# 让测试能 import src/ 下的脚本(它们不是包, 直接按路径导入)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# 数据库连接参数: 优先取环境变量, 默认指向 docker-compose 映射出来的 3307 端口。
# 不用 3306, 因为那通常是宿主机自己装的 MySQL, 与容器里的业务库不是一回事。
DB_PARAMS = {
    "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("MYSQL_PORT", "3307")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PWD", "energy_root_pwd"),
}


def pytest_configure(config):
    config.addinivalue_line("markers", "db: 需要 MySQL 的测试")


def _db_reachable() -> bool:
    try:
        import pymysql
    except ImportError:
        return False
    try:
        conn = pymysql.connect(connect_timeout=3, **DB_PARAMS)
        conn.close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def db_params():
    """返回一个可用的 MySQL 连接参数; 连不上就跳过而不是失败。

    跳过而非失败是刻意的: 数据库没起来属于"环境不具备", 不是"代码有问题"。
    把它报成失败会让 CI 与本地开发都收到误导性的红。
    """
    if not _db_reachable():
        pytest.skip(
            f"MySQL 不可达 ({DB_PARAMS['host']}:{DB_PARAMS['port']}), "
            f"跳过数据库测试。启动方式: docker compose up -d mysql"
        )
    return DB_PARAMS


@pytest.fixture
def preserve_outputs():
    """把 data/ 与 output/ 整体备份, 测试结束后原样还原。

    管道脚本的输出路径是写死的, 没有"输出到临时目录"这个选项, 所以想跑管道
    又不想动到工作区里已有的产物, 只能备份-还原。备份放在 tmp 目录而不是
    仓库内, 避免被 git 看见。
    """
    import tempfile

    with tempfile.TemporaryDirectory(prefix="energy_test_") as tmp:
        for name, src in (("data", DATA_DIR), ("output", OUT_DIR)):
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(tmp, name))
        try:
            yield tmp
        finally:
            for name, dst in (("data", DATA_DIR), ("output", OUT_DIR)):
                backup = os.path.join(tmp, name)
                if os.path.isdir(backup):
                    if os.path.isdir(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(backup, dst)
