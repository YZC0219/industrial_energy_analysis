"""Reproducible duplicate CDC experiment; optional real isolated warehouse run.

Offline mode executes the production projector, never a simulated warehouse.
--warehouse requires the project's Spark/Hive/HDFS runtime and creates a fresh
probe database. Evidence is retained on failure; existing directories are refused.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import time

from tools.export_mysql_cdc_batch import project

ROOT = Path(__file__).resolve().parents[1]
TOPIC = 'duplicate-experiment.fixture.fact_energy_consumption'
EXPECTED = {
    'rows': 2, 'active_rows': 2, 'cost': '37.00', 'consumption': '3.700',
    'keys': [['2024-03-15', 'W01', 'E01'], ['2024-03-15', 'W01', 'E02']],
}


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def fixtures():
    first = dict(id=1, record_date='2024-03-15', workshop_code='W01', energy_code='E01',
                 consumption='1.000', unit='unit', unit_price='10.0000', cost='10.00',
                 record_status='normal', avg_temperature='20.00', data_source='fixture',
                 is_production_day=1, updated_at='2025-12-30 09:00:00', is_deleted=0)
    second = {**first, 'id': 2, 'energy_code': 'E02', 'consumption': '2.000', 'cost': '20.00'}
    corrected = {**first, 'consumption': '1.700', 'cost': '17.00',
                 'updated_at': '2025-12-30 10:00:00'}

    def event(offset, op, before, after):
        return dict(topic=TOPIC, partition=0, offset=offset, key={'id': after['id']},
                    value=dict(op=op, before=before, after=after,
                               source=dict(db='fixture', table='fact_energy_consumption',
                                           file='binlog.000001', pos=100 + offset * 100, row=0)))

    original = [event(0, 'c', None, first), event(1, 'c', None, second),
                event(2, 'u', first, corrected)]
    # New Kafka offsets, SAME binlog coordinates: re-delivery, not new updates.
    repeated = deepcopy(original)
    for record in deepcopy(original):
        record['offset'] += len(original)
        repeated.append(record)
    return original, repeated


def measures(snapshot):
    rows = snapshot['rows']
    active = [row for row in rows if row['is_deleted'] == 0]
    return dict(rows=len(rows), active_rows=len(active),
                cost=f"{sum((Decimal(r['cost']) for r in active), Decimal(0)):.2f}",
                consumption=f"{sum((Decimal(r['consumption']) for r in active), Decimal(0)):.3f}",
                keys=[[r[k] for k in ('record_date', 'workshop_code', 'energy_code')] for r in rows])


def reconcile(expected, actual):
    checks = {key: {'expected': value, 'actual': actual.get(key),
                    'passed': value == actual.get(key)} for key, value in expected.items()}
    if not all(check['passed'] for check in checks.values()):
        raise ValueError(f'Reconciliation failed: {checks}')
    return checks


def warehouse_run(directory, snapshots, database):
    """Run project SQL twice at one boundary, then with re-delivered events."""
    from spark.apply_mysql_cdc_batch import apply, map_sql
    from tools.check_isolated_lakehouse_replay import seed_dimensions
    from pyspark.sql import SparkSession

    map_sql('', database)
    if database == 'energy':
        raise ValueError('Production database is forbidden')
    spark = SparkSession.builder.appName('cdc-duplicate-experiment').enableHiveSupport().getOrCreate()
    spark.sparkContext.setLogLevel('ERROR')
    try:
        location = f'hdfs://localhost:9000/warehouse/{database}'
        fs = spark._jvm.org.apache.hadoop.fs.FileSystem.get(
            spark._jvm.java.net.URI(location), spark._jsc.hadoopConfiguration())
        if spark.catalog.databaseExists(database) or fs.exists(spark._jvm.org.apache.hadoop.fs.Path(location)):
            raise ValueError('Refusing to reuse probe database/HDFS location')
        spark.sql(f"CREATE DATABASE {database} LOCATION '{location}'")
        for path in ('hive/ddl/01_ods.sql', 'hive/ddl/02_dwd.sql', 'hive/ddl/03_dws_ads.sql'):
            sql = map_sql((ROOT / path).read_text(encoding='utf-8'), database)
            sql = re.sub(r"LOCATION '/warehouse/energy_(?:ods|dwd|dws|ads)/([^']+)'",
                         lambda m: f"LOCATION '{location}/{m[1]}'", sql)
            sql = '\n'.join(line for line in sql.splitlines() if not line.lstrip().startswith('--'))
            for statement in sql.split(';'):
                if statement.strip():
                    spark.sql(statement)
        seed_dimensions(spark, database)
        tables = ('dwd_energy_consumption_detail', 'dws_workshop_energy_day',
                  'dws_workshop_energy_month', 'ads_factory_energy_day')
        baseline = None
        results = {}
        for phase, source in [('first', 'original'), ('same_snapshot', 'original'),
                              ('redelivered_events', 'duplicate')]:
            started = now()
            clock = time.perf_counter()
            applied = apply(spark, directory / f'{source}.json', database, '2026-10-20',
                            directory / f'warehouse_{phase}.json')
            observed = {}
            for table in tables:
                # ETL time changes on re-run; business columns must stay identical.
                frame = spark.table(f'{database}.{table}').drop('etl_time')
                rows = sorted([r.asDict() for r in frame.collect()],
                              key=lambda r: json.dumps(r, sort_keys=True, default=str))
                observed[table] = {'count': len(rows), 'business_sha256': digest(rows), 'rows': rows}
            write(directory / f'business_{phase}.json', json.loads(json.dumps(observed, default=str)))
            detail = observed['dwd_energy_consumption_detail']['rows']
            actual_detail = sorted([(r['energy_code'], str(r['consumption']), str(r['cost']),
                                     int(r['is_deleted'])) for r in detail])
            reconcile({'detail': [('E01', '1.700', '17.00', 0), ('E02', '2.000', '20.00', 0)]},
                      {'detail': actual_detail})
            for table in tables[1:]:
                reconcile({'count': 1}, observed[table])
                row = observed[table]['rows'][0]
                reconcile({'cost_yuan': Decimal('37.00'), 'tce': Decimal('0.0057'),
                           'co2_t': Decimal('0.009')}, row)
            if baseline is None:
                baseline = observed
            reconcile(baseline, observed)
            checks = reconcile({'dwd_rows': 2, 'active_rows': 2}, applied)
            for layer, cost in applied['costs'].items():
                reconcile({'cost': '37.00'}, {'cost': f'{Decimal(cost):.2f}'})
            watermark = spark.table(f'{database}.cdc_snapshot_watermark').collect()[0].asDict()
            intent = spark.table(f'{database}.cdc_snapshot_intent').collect()[0].asDict()
            reconcile({'topic': TOPIC, 'end_offset': snapshots[source]['end_offset']}, watermark)
            reconcile(watermark, intent)
            results[phase] = dict(started_at_utc=started, ended_at_utc=now(),
                                  elapsed_seconds=time.perf_counter() - clock,
                                  checks=checks, watermark=watermark, business_equal=True)
            write(directory / f'warehouse_checks_{phase}.json', results[phase])
        return dict(status='passed', database=database, phases=results,
                    scope='project Spark DWD/DWS/ADS SQL, synthetic CDC input; no live Kafka capture')
    finally:
        spark.stop()


def run(directory: Path, *, warehouse=False, database=None):
    if warehouse and (database is None or not re.fullmatch(r'energy_cdc_business_probe_[0-9]{14}', database)):
        raise ValueError('Warehouse mode requires a fresh energy_cdc_business_probe_YYYYmmddHHMMSS database')
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    report = dict(started_at_utc=now(), status='running', input_kind='synthetic Debezium-shaped events',
                  warehouse={'status': 'not_run', 'reason': 'offline mode'}, live_cdc={'status': 'not_run'})
    # Save independent oracle BEFORE the projector executes.
    write(directory / 'expected.json', EXPECTED)
    original, repeated = fixtures()
    write(directory / 'input.json', {'original': original, 'duplicate': repeated})
    report['fault'] = dict(kind='redelivery', injected_at_utc=now(),
                           description='append all 3 events with new Kafka offsets and original binlog positions')
    report['source_sha256'] = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in
        ('tools/export_mysql_cdc_batch.py', 'spark/apply_mysql_cdc_batch.py',
         'tools/experiment_cdc_duplicate.py', 'spark/sql/10_dwd_energy.sql',
         'spark/sql/20_dws.sql', 'spark/sql/30_ads.sql')}
    try:
        snapshots = {}
        phases = {}
        for name, records in [('original', original), ('duplicate', repeated)]:
            clock = time.perf_counter()
            snapshot = {**project(records, TOPIC, len(records)), 'events': records}
            snapshots[name] = snapshot
            write(directory / f'{name}.json', snapshot)
            phases[name] = dict(checks=reconcile(EXPECTED, measures(snapshot)),
                                elapsed_seconds=time.perf_counter() - clock,
                                end_offset=snapshot['end_offset'])
        reconcile({'rows': snapshots['original']['rows'],
                   'versions': snapshots['original']['row_binlog_versions']},
                  {'rows': snapshots['duplicate']['rows'],
                   'versions': snapshots['duplicate']['row_binlog_versions']})
        # Negative control: naive summation of all after-images must be caught.
        naive = sum((Decimal(r['value']['after']['cost']) for r in repeated), Decimal(0))
        if naive == Decimal(EXPECTED['cost']):
            raise ValueError('Negative control did not expose double counting')
        report['projection'] = dict(status='passed', phases=phases, rows_and_versions_equal=True,
                                    negative_control={'naive_cost': str(naive), 'expected_cost': '37.00',
                                                      'incorrect_result_detected': True})
        if warehouse:
            report['warehouse'] = {'status': 'running', 'database': database}
            report['warehouse'] = warehouse_run(directory, snapshots, database)
        report['status'] = 'passed' if warehouse else 'projection_passed_warehouse_not_run'
        return report
    except Exception as exc:
        report['status'] = 'failed'
        if report['warehouse']['status'] == 'running':
            report['warehouse']['status'] = 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        raise
    finally:
        report['ended_at_utc'] = now()
        report['artifacts_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in sorted(directory.glob('*.json'))}
        write(directory / 'report.json', report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--warehouse', action='store_true')
    parser.add_argument('--database')
    args = parser.parse_args()
    result = run(args.output_dir, warehouse=args.warehouse, database=args.database)
    print(f"CDC_DUPLICATE {result['status']} report={args.output_dir.resolve() / 'report.json'}")


if __name__ == '__main__':
    main()
