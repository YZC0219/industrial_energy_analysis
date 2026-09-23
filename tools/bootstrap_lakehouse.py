"""首次全量初始化：避免先全量写 ODS、再被日常增量任务 truncate 成空批次。"""
from __future__ import annotations
import argparse
import subprocess
import sys

def run(*args: str) -> None:
    subprocess.run([sys.executable, *args], check=True)

def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument("--biz-date", required=True, help="本次初始化的同步批次日期 YYYY-MM-DD")
    a=p.parse_args()
    for table in ("dim_workshop","dim_energy_type","dim_calendar","fact_production"):
        run("datax/run_sync.py","--table",table,"--biz-date",a.biz_date)
    run("datax/run_sync.py","--table","fact_energy_consumption","--biz-date",a.biz_date,"--full")
    for sql in ("spark/sql/10_dwd_energy.sql","spark/sql/20_dws.sql","spark/sql/30_ads.sql"):
        run("spark/run_sql.py",sql,"--biz-date",a.biz_date)
        if sql.endswith("10_dwd_energy.sql"):
            run("tools/check_hive_quality.py","--stage","dwd","--biz-date",a.biz_date)
        elif sql.endswith("20_dws.sql"):
            run("tools/check_hive_quality.py","--stage","dws","--biz-date",a.biz_date)

if __name__ == "__main__": main()
