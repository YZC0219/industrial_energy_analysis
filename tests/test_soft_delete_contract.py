"""离线契约：删除标记须贯通 MySQL → DataX/ODS → DWD → DWS/ADS。"""
from pathlib import Path
from subprocess import CompletedProcess

from tools.ensure_hive_soft_delete_schema import TABLES, ensure_column

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_hive_schema_migration_is_idempotent_and_adds_only_missing_column():
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if "SHOW COLUMNS" in args[-1]:
            columns = "record_date\nupdated_at\nis_deleted\n" if len(calls) == 1 else "record_date\n"
            return CompletedProcess(args, 0, columns, "")
        return CompletedProcess(args, 0, "", "")

    assert ensure_column(TABLES[0], run=fake_run) is False
    assert ensure_column(TABLES[1], run=fake_run) is True
    assert sum("ALTER TABLE" in " ".join(call) for call in calls) == 1


def test_soft_delete_columns_flow_through_mysql_datax_and_hive_ddl():
    mysql = read("sql/create_table.sql")
    datax = read("datax/run_sync.py")
    ods = read("hive/ddl/01_ods.sql")
    dwd = read("hive/ddl/02_dwd.sql")
    assert "is_deleted        TINYINT(1)" in mysql
    assert "WHERE f.is_deleted = 0" in mysql
    assert '("is_deleted","tinyint")' in datax
    assert "is_deleted TINYINT" in ods
    assert dwd.count("is_deleted TINYINT") == 2


def test_spark_keeps_tombstone_for_lineage_but_excludes_it_from_aggregates():
    dwd = read("spark/sql/10_dwd_energy.sql")
    dws = read("spark/sql/20_dws.sql")
    ads = read("spark/sql/30_ads.sql")
    assert "coalesce(f.is_deleted,0)=1" in dwd
    assert "source_updated_at,coalesce(d.is_deleted" in dwd
    assert "coalesce(e.is_deleted,0)=0" in dws
    assert "CASE WHEN coalesce(e.is_deleted,0)=0 THEN e.std_coal_kgce ELSE 0 END" in dws
    ads = read("spark/sql/30_ads.sql")
    assert "count(DISTINCT a.workshop_code)" in ads
    assert "WHERE coalesce(is_deleted,0)=0" in ads


def test_airflow_runs_both_schema_migrations_before_syncing_tombstones():
    dag = read("dags/energy_pipeline_dag.py")
    assert "[ensure_mysql_soft_delete_schema, ensure_hive_soft_delete_schema] >> sync_ods_energy" in dag
    assert "ensure_hive_soft_delete_schema, sync_ods_dimensions, sync_ods_energy" in dag
