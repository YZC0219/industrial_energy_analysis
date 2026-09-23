"""Execute parameterized Spark SQL files and record wall-clock performance."""
from __future__ import annotations
import argparse, json, os, subprocess, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("sql_file")
    p.add_argument("--biz-date", required=True)
    p.add_argument("--load-mode", choices=("incremental", "full"), default="incremental")
    p.add_argument("--metrics", default="output/spark_performance.jsonl")
    args = p.parse_args()
    sql_path = ROOT / args.sql_file
    year_month = args.biz_date[:7]
    cmd = [os.getenv("SPARK_SQL", "spark-sql"), "--hiveconf", f"biz_date={args.biz_date}",
           "--hiveconf", f"year_month={year_month}", "--hiveconf", f"load_mode={args.load_mode}",
           "-f", str(sql_path)]
    started = time.perf_counter()
    # Embedded Hive metastore paths may be relative; keep every invocation on one catalog.
    proc = subprocess.run(cmd, check=False, cwd=ROOT)
    elapsed = time.perf_counter() - started
    out = ROOT / args.metrics; out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"engine":"spark_sql","job":str(sql_path.relative_to(ROOT)),
                            "biz_date":args.biz_date,"elapsed_seconds":round(elapsed,3),
                            "exit_code":proc.returncode}, ensure_ascii=False)+"\n")
    raise SystemExit(proc.returncode)

if __name__ == "__main__": main()
