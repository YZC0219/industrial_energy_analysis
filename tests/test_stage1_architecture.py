"""阶段一离线契约测试：解析配置并实际执行 DAG 定义，不要求 Hadoop/Airflow。"""
from __future__ import annotations
import csv
from pathlib import Path
import importlib.util
import json
import re
import sys
import types

ROOT = Path(__file__).resolve().parents[1]

def text(path): return (ROOT/path).read_text(encoding="utf-8")

def load_module(name, path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def test_all_four_layers_have_declared_grain_and_partitions():
    ddl="\n".join(text(p) for p in ["hive/ddl/00_create_databases.sql","hive/ddl/01_ods.sql","hive/ddl/02_dwd.sql","hive/ddl/03_dws_ads.sql"])
    expected={"energy_ods":5,"energy_dwd":3,"energy_dws":2,"energy_ads":1}
    for db,count in expected.items():
        pattern=rf"CREATE\s+(?:EXTERNAL\s+)?TABLE\s+IF\s+NOT\s+EXISTS\s+{re.escape(db)}\."
        assert len(re.findall(pattern,ddl,re.I))==count
    assert ddl.count("PARTITIONED BY") == sum(expected.values())
    assert ddl.count("LOCATION '/warehouse/") == sum(expected.values()) + len(expected)
    assert "source_updated_at TIMESTAMP" in ddl
    ods=text("hive/ddl/01_ods.sql")
    assert ods.count("CREATE EXTERNAL TABLE") == ods.count("LOCATION '/warehouse/energy_ods/") == 5
    assert ods.count("STORED AS ORC") == 5

def test_datax_schema_preserves_timestamp_and_increment_is_half_open():
    mod=load_module("datax_run_sync", "datax/run_sync.py")
    _,mode,columns=mod.TABLES["fact_energy_consumption"]
    assert mode=="incremental"
    assert dict(columns)["updated_at"]=="timestamp"
    assert dict(columns)["consumption"]=="string"          # DataX ORC writer 无 DECIMAL
    job=text("datax/jobs/mysql_to_hive_incremental.json")
    assert "updated_at >= '${WINDOW_START}' AND updated_at < '${WINDOW_END}'" in job
    assert '"password": "${MYSQL_PASSWORD}"' in job
    parsed=json.loads(job.replace('${MYSQL_USER}','u').replace('${MYSQL_PASSWORD}','p')
                      .replace('${SOURCE_COLUMN_SQL}','`id`').replace('${SOURCE_TABLE}','t')
                      .replace('${WINDOW_START}','s').replace('${WINDOW_END}','e')
                      .replace('${MYSQL_JDBC_URL}','jdbc:x').replace('${HDFS_DEFAULT_FS}','hdfs://x')
                      .replace('${HIVE_STAGE_PATH}','/w').replace('${TARGET_TABLE}','t')
                      .replace('${TARGET_PARTITION}','d').replace('${TARGET_COLUMNS}','[]'))
    reader=parsed['job']['content'][0]['reader']['parameter']
    assert 'querySql' not in reader and 'querySql' in reader['connection'][0]
    runner=text("datax/run_sync.py")
    assert runner.index('"-mkdir", "-p", location') < runner.index('os.getenv("DATAX_PYTHON"')
    assert '"-fs", vals["HDFS_DEFAULT_FS"]' in runner
    dwd=text("spark/sql/10_dwd_energy.sql")
    assert "cast(f.consumption AS decimal(16,3))" in dwd
    assert "cast(f.unit_price AS decimal(12,4))" in dwd

def test_late_correction_is_merged_into_business_date_partition():
    dwd=text("spark/sql/10_dwd_energy.sql")
    assert "OR f.dt='${biz_date}'" in dwd                    # 增量读同步批次，全量读完整 ODS
    assert "batch_id='${biz_date}'" in dwd
    assert "f.record_date='${biz_date}'" not in dwd         # 不丢历史业务日期
    assert "JOIN impacted i ON d.dt=cast(i.record_date AS string) AND d.record_date=i.record_date" in dwd
    assert "UNION ALL" in dwd                               # 旧快照 + 新版本
    assert "ORDER BY source_updated_at DESC,source_priority DESC" in dwd
    cleanup="DROP IF EXISTS PARTITION (run_dt='${biz_date}',batch_id='${biz_date}')"
    assert cleanup in dwd
    assert dwd.index(cleanup) < dwd.index("WITH incoming AS") < dwd.index("INSERT OVERWRITE TABLE")
    assert "FROM (\nWITH incoming AS" not in dwd             # Hive 不支持子查询块内 WITH
    assert "PARTITION (dt)" in dwd and "target_dt" in dwd  # 动态覆盖业务分区
    for path in ("spark/sql/20_dws.sql","spark/sql/30_ads.sql"):
        sql=text(path)
        assert "dwd_energy_consumption_merge_stage" in sql  # 受影响日期继续向下传播
        assert "PARTITION(dt)" in sql
        assert "WHERE batch_id='${biz_date}'" in sql

def test_each_fact_transform_is_overwrite_and_partition_pruned():
    dwd=text("spark/sql/10_dwd_energy.sql")
    assert dwd.count("INSERT OVERWRITE TABLE") == 3
    assert "batch_id='${biz_date}'" in dwd
    dws=text("spark/sql/20_dws.sql")
    assert dws.count("INSERT OVERWRITE TABLE") == 2
    assert "PARTITION(year_month)" in dws
    assert "FROM energy_dws.dws_workshop_energy_day d" in dws
    assert "GROUP BY d.year_month,d.workshop_code" in dws
    assert "WHERE '${load_mode}'='full'" in dws
    ads=text("spark/sql/30_ads.sql")
    assert ads.count("INSERT OVERWRITE TABLE") == 1
    impacted=r"d\.dt\s+IN\s*\(\s*SELECT\s+DISTINCT\s+target_dt\s+FROM\s+energy_dwd\.dwd_energy_consumption_merge_stage\s+WHERE\s+batch_id='\$\{biz_date\}'\s*\)"
    assert re.search(impacted,ads,re.I)
    assert "GROUP BY d.record_date" in ads
    assert ads.count("FROM energy_dws.dws_workshop_energy_day d") == 1
    assert "WHERE '${load_mode}'='full'" in ads

def test_full_load_reads_all_ods_batches_but_tracks_this_run():
    dwd=text("spark/sql/10_dwd_energy.sql")
    assert "('${load_mode}'='full' OR f.dt='${biz_date}')" in dwd
    assert "cast('${biz_date}' AS string) batch_id" in dwd
    assert "WHERE batch_id='${biz_date}'" in dwd
    assert "PARTITION (run_dt,batch_id)" in dwd
    runner=text("spark/run_sql.py")
    assert "--load-mode" in runner
    assert "cwd=ROOT" in runner
    quality=text("tools/check_hive_quality.py")
    assert "cwd=ROOT" in quality
    assert "SELECT DISTINCT record_date,workshop_code,energy_code" in quality
    assert "production_unique" in quality
    assert "dws_partition_coverage" in quality

def test_datax_templates_render_to_valid_json():
    mod=load_module("datax_render", "datax/run_sync.py")
    common={"MYSQL_USER":"u","MYSQL_PASSWORD":"p","MYSQL_JDBC_URL":"jdbc:mysql://x/db",
            "HDFS_DEFAULT_FS":"hdfs://x","HIVE_STAGE_PATH":"/w","SOURCE_TABLE":"t",
            "TARGET_TABLE":"t","BIZ_DATE":"2025-01-01","TARGET_PARTITION":"2025-01-01","WINDOW_START":"2025-01-01 00:00:00",
            "WINDOW_END":"2025-01-02 00:00:00","TARGET_COLUMNS":"[]"}
    common["SOURCE_COLUMNS"]="[]"; common["SOURCE_COLUMN_SQL"]="id"
    for name in ("full","incremental"):
        rendered=mod.render(text(f"datax/jobs/mysql_to_hive_{name}.json"),common)
        config=json.loads(rendered)
        assert config["job"]["content"]
        writer=config["job"]["content"][0]["writer"]["parameter"]
        assert writer["fileType"]=="orc"
        assert len(writer["fieldDelimiter"])==1

def test_datax_quotes_mysql_identifiers_including_reserved_words():
    runner=text("datax/run_sync.py")
    assert 'json.dumps([f"`{n}`" for n, _ in columns])' in runner
    assert '",".join(f"`{n}`" for n, _ in columns)' in runner

def test_airflow_dependency_graph_is_complete(monkeypatch):
    registry={}
    class Node:
        def __init__(self,task_id,**kwargs): self.task_id=task_id; self.downstream=set(); registry[task_id]=self
        def link(self,other):
            targets=other if isinstance(other,list) else [other]
            for target in targets: self.downstream.add(target.task_id)
            return other
        def __rshift__(self,other): return self.link(other)
        def __rrshift__(self,other):
            for source in (other if isinstance(other,list) else [other]): source.link(self)
            return self
    class FakeDAG:
        def __init__(self,*args,**kwargs): self.kwargs=kwargs
        def __enter__(self): return self
        def __exit__(self,*args): return False
    airflow=types.ModuleType("airflow"); airflow.DAG=FakeDAG
    operators=types.ModuleType("airflow.operators")
    bash=types.ModuleType("airflow.operators.bash"); bash.BashOperator=Node
    monkeypatch.setitem(sys.modules,"airflow",airflow)
    monkeypatch.setitem(sys.modules,"airflow.operators",operators)
    monkeypatch.setitem(sys.modules,"airflow.operators.bash",bash)
    load_module("energy_pipeline_contract", "dags/energy_pipeline_dag.py")
    expected={
      "generate_raw_data":{"clean_data"},
      "clean_data":{"load_warehouse","sync_ods_dimensions","sync_ods_energy_incremental","run_phase2"},
      "load_warehouse":{"run_analysis"}, "run_analysis":{"build_report"},
      "sync_ods_dimensions":{"build_dwd"}, "sync_ods_energy_incremental":{"build_dwd"},
      "build_dwd":{"quality_dwd"}, "quality_dwd":{"build_dws"},
      "build_dws":{"quality_dws"}, "quality_dws":{"build_ads"},
      "run_phase2":{"run_deep_validation"},
    }
    assert {k:v.downstream for k,v in registry.items() if v.downstream}==expected
    assert "ENERGY_ALERT_WEBHOOK" in text("dags/energy_pipeline_dag.py")

def test_full_snapshots_use_one_stable_partition():
    runner=text("datax/run_sync.py")
    assert 'args.biz_date if args.table == "fact_energy_consumption" else "current"' in runner
    dwd=text("spark/sql/10_dwd_energy.sql")
    assert dwd.count("dt='current'") == 5


def test_checked_in_cluster_evidence_is_internally_consistent():
    comparison=json.loads(text("output/engine_comparison.json"))
    assert comparison["rows"] == {"pandas":5848,"mysql":5848,"spark":5848}
    assert all(item["key_equal"] and item["passed"]
               for item in comparison["comparisons"].values())
    assert max(max(item["max_abs_diff"].values())
               for item in comparison["comparisons"].values()) <= comparison["atol"]

    for name in ("pandas_daily.csv","mysql_daily.csv","spark_daily.csv"):
        with (ROOT/"output"/name).open(encoding="utf-8",newline="") as handle:
            rows=list(csv.DictReader(handle))
        assert len(rows)==5848
        assert set(rows[0]) == {"record_date","workshop_code","tce","co2_t","cost_yuan","unit_energy_kgce"}

    validation=json.loads(text("output/lakehouse_validation.json"))
    late=validation["late_correction"]
    assert late["before"]["dwd_consumption"] != late["corrected"]["dwd_consumption"]
    assert late["restored"]["dwd_consumption"] == late["before"]["dwd_consumption"]
    assert late["restored"]["dws_tce"] == late["before"]["dws_tce"]
    assert late["restored"]["ads_tce"] == late["before"]["ads_tce"]
    assert late["quality_gates_passed_after_correction"]
    assert late["quality_gates_passed_after_restore"]

    performance=[json.loads(line) for line in text("output/spark_performance.jsonl").splitlines()]
    successful_full={row["job"] for row in performance
                     if row["biz_date"]=="2025-12-31" and row["exit_code"]==0}
    assert successful_full == {"spark/sql/10_dwd_energy.sql","spark/sql/20_dws.sql","spark/sql/30_ads.sql"}
