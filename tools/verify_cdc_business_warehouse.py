"""Verify real business CDC snapshots through the unchanged warehouse SQL."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spark.apply_mysql_cdc_batch import apply, map_sql
from spark import apply_mysql_cdc_batch as warehouse
from tools.check_isolated_lakehouse_replay import seed_dimensions


def main() -> None:
    from pyspark.sql import SparkSession
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database', required=True)
    p.add_argument('--snapshots', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    map_sql('', args.database)
    if args.database == 'energy' or args.output.exists():
        raise ValueError('Verification requires a fresh isolated database/output')
    doc = json.loads(args.snapshots.read_text(encoding='utf-8'))
    spark = SparkSession.builder.appName('real-business-cdc-acceptance').enableHiveSupport().getOrCreate()
    spark.sparkContext.setLogLevel('ERROR')
    try:
        location = f'hdfs://localhost:9000/warehouse/{args.database}'
        fs = spark._jvm.org.apache.hadoop.fs.FileSystem.get(
            spark._jvm.java.net.URI(location), spark._jsc.hadoopConfiguration())
        if spark.catalog.databaseExists(args.database) or fs.exists(spark._jvm.org.apache.hadoop.fs.Path(location)):
            raise ValueError('Refusing to reuse verification database/HDFS directory')
        spark.sql(f"CREATE DATABASE {args.database} LOCATION '{location}'")
        for path in ('hive/ddl/01_ods.sql', 'hive/ddl/02_dwd.sql', 'hive/ddl/03_dws_ads.sql'):
            sql = (ROOT / path).read_text(encoding='utf-8')
            sql = map_sql(sql, args.database)
            import re
            sql = re.sub(r"LOCATION '/warehouse/energy_(?:ods|dwd|dws|ads)/([^']+)'",
                         lambda m: f"LOCATION '{location}/{m[1]}'", sql)
            sql = '\n'.join(line for line in sql.splitlines() if not line.lstrip().startswith('--'))
            for statement in sql.split(';'):
                if statement.strip():
                    spark.sql(statement.strip())
        seed_dimensions(spark, args.database)
        phases = {}
        for name, expected, active in [('insert', '30.00', 2), ('correction', '37.00', 2),
                                      ('physical_delete', '20.00', 1), ('full_replay', '20.00', 1)]:
            source = Path(doc['snapshots']['source_replay' if name == 'full_replay' else name])
            if name == 'physical_delete':
                original_execute = warehouse.execute_sql

                def fault(*positional, **keyword):
                    original_execute(*positional, **keyword)
                    if positional[1].endswith('10_dwd_energy.sql'):
                        raise RuntimeError('injected failure after DWD')

                warehouse.execute_sql = fault
                try:
                    try:
                        apply(spark, source, args.database, '2026-09-30', ROOT / 'output' / 'fault.json')
                    except RuntimeError as exc:
                        if str(exc) != 'injected failure after DWD':
                            raise
                    else:
                        raise ValueError('Expected injected DWD failure')
                finally:
                    warehouse.execute_sql = original_execute
                correction = Path(doc['snapshots']['correction'])
                try:
                    apply(spark, correction, args.database, '2026-09-30', ROOT / 'output' / 'old_after_fault.json')
                except ValueError as exc:
                    if 'watermark regressed' not in str(exc):
                        raise
                else:
                    raise ValueError('Intent did not block an old snapshot after partial failure')
                committed = spark.table(f'{args.database}.cdc_snapshot_watermark').collect()[0]
                if committed.end_offset != json.loads(correction.read_text())['end_offset']:
                    raise ValueError('Failure incorrectly advanced the committed watermark')
            result = apply(spark, source, args.database, '2026-09-30',
                           ROOT / 'output' / f'{args.database}_{name}.json')
            if result['costs']['ads_day'] != expected or result['active_rows'] != active:
                raise ValueError(f'{name}: expected business cost/active-row state differs')
            phases[name] = result
            print(f'CDC_BUSINESS_PHASE {name} PASS', flush=True)
        # An earlier frozen snapshot must fail before it writes anything.
        try:
            apply(spark, Path(doc['snapshots']['insert']), args.database, '2026-09-30',
                  ROOT / 'output' / f'{args.database}_stale.json')
        except ValueError as exc:
            if 'watermark regressed' not in str(exc):
                raise
            rejected = True
        else:
            raise ValueError('Stale pre-delete snapshot was not rejected')
        result = {'success': True, 'database': args.database, 'phases': phases,
                  'stale_snapshot_rejected': rejected,
                  'partial_failure_recovered': True,
                  'partial_failure_old_snapshot_rejected': True,
                  'scope': 'real MySQL business-shaped CDC -> project SQL DWD/DWS/ADS in isolated schemas; production source not switched'}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
        print(f'CDC_BUSINESS_WAREHOUSE_VERIFICATION PASS report={args.output}')
    finally:
        spark.stop()


if __name__ == '__main__':
    main()
