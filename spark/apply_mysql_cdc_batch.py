"""Apply a complete ordered CDC snapshot through the project's warehouse SQL.

The regular DataX/updated_at path is unchanged. This entry point owns the whole
energy fact scope; it must never run concurrently with the DataX source mode.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.export_mysql_cdc_batch import FIELDS, business_key, normalize, project


def map_sql(sql: str, database: str) -> str:
    if database == 'energy':
        return sql
    if not re.fullmatch(r'energy_cdc_business_probe_[0-9]{14}', database):
        raise ValueError('Use energy or a fresh dated CDC business probe database')
    for name in ('energy_ods', 'energy_dwd', 'energy_dws', 'energy_ads'):
        sql = sql.replace(name + '.', database + '.')
    return sql


def execute_sql(spark, path: str, database: str, batch: str, *, cdc_input=False) -> None:
    sql = (ROOT / path).read_text(encoding='utf-8')
    if cdc_input:
        needle = 'FROM energy_ods.ods_energy_consumption f'
        if sql.count(needle) != 1:
            raise ValueError('Warehouse SQL source changed; review CDC integration')
        sql = sql.replace(needle, 'FROM _energy_cdc_snapshot f')
    sql = map_sql(sql, database).replace('${biz_date}', batch).replace('${load_mode}', 'full')
    sql = '\n'.join(line for line in sql.splitlines() if not line.lstrip().startswith('--'))
    for statement in sql.split(';'):
        if statement.strip():
            spark.sql(statement.strip())


def apply(spark, snapshot: Path, database: str, batch: str, output: Path) -> dict:
    from pyspark.sql import types as T
    date.fromisoformat(batch)
    map_sql('', database)
    if output.exists():
        raise FileExistsError('Refusing to overwrite warehouse evidence')
    raw = snapshot.read_bytes()
    doc = json.loads(raw)
    if (doc.get('schema_version') != 2 or doc.get('partition') != 0
            or doc.get('start_offset') != 0 or doc.get('complete_history') is not True
            or type(doc.get('end_offset')) is not int or doc['end_offset'] <= 0):
        raise ValueError('Expected complete ordered CDC snapshot v2')
    rows = [normalize(row) for row in doc['rows']]
    rebuilt = project(doc['events'], doc['topic'], doc['end_offset'])
    if any(rebuilt[key] != doc[key] for key in rebuilt):
        raise ValueError('Snapshot differs from its raw CDC event history')
    keys = [business_key(row) for row in rows]
    offsets = doc['row_offsets']
    if (not rows or len(set(keys)) != len(keys) or len(offsets) != len(rows)
            or any(type(offset) is not int or not 0 <= offset < doc['end_offset'] for offset in offsets)):
        raise ValueError('Invalid snapshot rows/versions')
    ods = 'energy_ods' if database == 'energy' else database
    dwd = 'energy_dwd' if database == 'energy' else database
    dws = 'energy_dws' if database == 'energy' else database
    ads = 'energy_ads' if database == 'energy' else database
    manifest = f'{ods}.cdc_snapshot_watermark'
    intent = f'{ods}.cdc_snapshot_intent'
    content_hash = hashlib.sha256(json.dumps({key: doc[key] for key in
        ('topic', 'partition', 'end_offset', 'rows', 'row_offsets')}, sort_keys=True).encode()).hexdigest()
    def check_boundary(table):
        if not spark.catalog.tableExists(table):
            return
        prior = spark.table(table).collect()
        if len(prior) != 1:
            raise ValueError('Invalid CDC ownership/watermark')
        prior = prior[0]
        if prior.topic != doc['topic'] or prior.end_offset > doc['end_offset']:
            raise ValueError('CDC source changed or snapshot watermark regressed')
        if prior.end_offset == doc['end_offset'] and prior.content_hash != content_hash:
            raise ValueError('Conflicting payload at the same Kafka boundary')
    check_boundary(manifest)
    check_boundary(intent)
    existing = spark.table(f'{dwd}.dwd_energy_consumption_detail').select(
        'record_date', 'workshop_code', 'energy_code').collect()
    if not {tuple(str(value) for value in row) for row in existing}.issubset(set(keys)):
        raise ValueError('CDC snapshot would omit existing warehouse keys; bootstrap is incomplete')
    schema = T.StructType([T.StructField(name,
        T.LongType() if name == 'id' else T.DateType() if name == 'record_date' else
        T.TimestampType() if name == 'updated_at' else T.ByteType() if name in
        ('is_deleted', 'is_production_day') else T.StringType(), True) for name in FIELDS]
        + [T.StructField('dt', T.StringType(), False)])
    tuples = []
    for row in rows:
        values = dict(row)
        values['record_date'] = date.fromisoformat(values['record_date'])
        values['updated_at'] = (datetime.fromisoformat(values['updated_at'])
                                if values['updated_at'] else None)
        tuples.append(tuple(values[field] for field in FIELDS) + (batch,))
    frame = spark.createDataFrame(tuples, schema)
    frame.createOrReplaceTempView('_energy_cdc_snapshot')
    # Fail before publishing if inner joins would silently remove source records.
    joined = spark.sql(f"""SELECT f.id FROM _energy_cdc_snapshot f
        JOIN {ods}.ods_workshop w ON f.workshop_code=w.workshop_code AND w.dt='current'
        JOIN {ods}.ods_energy_type e ON f.energy_code=e.energy_code AND e.dt='current'
        JOIN {ods}.ods_calendar c ON f.record_date=c.calendar_date AND c.dt='current'""")
    if joined.count() != len(rows) or joined.distinct().count() != len({row['id'] for row in rows}):
        raise ValueError('Missing or duplicate dimension matches')
    def write_boundary(table):
        spark.sql(f'CREATE TABLE IF NOT EXISTS {table} (topic STRING,end_offset BIGINT,content_hash STRING) STORED AS ORC')
        spark.createDataFrame([(doc['topic'], doc['end_offset'], content_hash)],
                             'topic string,end_offset long,content_hash string').write.mode('overwrite').insertInto(table)
    # This intent blocks older snapshots even if a downstream write fails before commit.
    write_boundary(intent)
    for path in ('spark/sql/10_dwd_energy.sql', 'spark/sql/20_dws.sql', 'spark/sql/30_ads.sql'):
        execute_sql(spark, path, database, batch, cdc_input=path.endswith('10_dwd_energy.sql'))
    expected = sum((Decimal(row['cost']) for row in rows if row['is_deleted'] == 0), Decimal(0))
    costs = {}
    for name, table in [('dws_day', f'{dws}.dws_workshop_energy_day'),
                        ('dws_month', f'{dws}.dws_workshop_energy_month'),
                        ('ads_day', f'{ads}.ads_factory_energy_day')]:
        actual = spark.sql(f'SELECT sum(cost_yuan) AS cost FROM {table}').collect()[0].cost
        if actual is None or Decimal(str(actual)) != expected:
            raise ValueError(f'{name} did not propagate CDC cost: {actual} != {expected}')
        costs[name] = str(actual)
    state = spark.table(f'{dwd}.dwd_energy_consumption_detail')
    actual_state = {(str(row.record_date), row.workshop_code, row.energy_code): int(row.is_deleted)
                    for row in state.select('record_date', 'workshop_code', 'energy_code', 'is_deleted').collect()}
    if actual_state != {business_key(row): row['is_deleted'] for row in rows} or state.count() != len(rows):
        raise ValueError('DWD deletion/key state differs from CDC snapshot')
    # Publish the checkpoint only after every downstream layer and quality check succeeds.
    write_boundary(manifest)
    result = {'success': True, 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'database': database, 'source_topic': doc['topic'], 'end_offset': doc['end_offset'],
              'source_snapshot_sha256': hashlib.sha256(raw).hexdigest(),
              'dwd_rows': len(rows), 'active_rows': sum(row['is_deleted'] == 0 for row in rows),
              'costs': costs, 'sql_sha256': {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                  for path in ('spark/sql/10_dwd_energy.sql', 'spark/sql/20_dws.sql', 'spark/sql/30_ads.sql')},
              'scope': 'CDC business snapshot through project DWD/DWS/ADS SQL; full snapshot source mode'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    return result


def main() -> None:
    from pyspark.sql import SparkSession
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot', type=Path, required=True)
    p.add_argument('--database', required=True)
    p.add_argument('--biz-date', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    spark = SparkSession.builder.appName('mysql-cdc-business-warehouse').enableHiveSupport().getOrCreate()
    spark.sparkContext.setLogLevel('ERROR')
    try:
        apply(spark, args.snapshot, args.database, args.biz_date, args.output)
        print(f'CDC_BUSINESS_WAREHOUSE PASS report={args.output}')
    finally:
        spark.stop()


if __name__ == '__main__':
    main()
