"""将现有完整 MySQL 快照装入 ODS，再从该批次构建 Hive 分层。"""
from __future__ import annotations
import argparse
import subprocess
import sys

def run(*args: str) -> None:
    subprocess.run([sys.executable, *args], check=True)

def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument("--biz-date", required=True, help="ODS 全量快照分区标识 YYYY-MM-DD")
    a=p.parse_args()
    # 当前 DataX 全量 reader 直接读 MySQL，事实表全量带齐 2024–2025 历史。
    # 源事实和清洗 CSV 都必须已由 README 全量流程生成并装入 MySQL。
    for table in ("dim_workshop","dim_energy_type","dim_calendar","fact_production"):
        run("datax/run_sync.py","--table",table,"--biz-date",a.biz_date)
    run("datax/run_sync.py","--table","fact_energy_consumption","--biz-date",a.biz_date,"--full")
    for sql in ("spark/sql/10_dwd_energy.sql","spark/sql/20_dws.sql","spark/sql/30_ads.sql"):
        run("spark/run_sql.py",sql,"--biz-date",a.biz_date,"--load-mode","full")
        if sql.endswith("10_dwd_energy.sql"):
            run("tools/check_hive_quality.py","--stage","dwd","--biz-date",a.biz_date,"--load-mode","full")
        elif sql.endswith("20_dws.sql"):
            run("tools/check_hive_quality.py","--stage","dws","--biz-date",a.biz_date,"--load-mode","full")

if __name__ == "__main__": main()
