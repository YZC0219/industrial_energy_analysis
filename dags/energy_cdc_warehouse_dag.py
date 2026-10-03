"""Opt-in CDC warehouse source mode; exclusive with the energy DataX writer.

Uses the complete source topic, frozen offsets, immutable evidence upload, and
the same warehouse SQL. No simulated source data is generated or reinserted.
Before activation pause the old warehouse writer, configure an initial-mode
business connector, deploy the same Git revision to the VM, and bootstrap dimensions.
"""
from __future__ import annotations

import os
import re
import shlex
from datetime import datetime, timedelta
import pendulum

from airflow import DAG
from airflow.operators.bash import BashOperator

PROJECT = os.getenv('PROJECT_DIR', '/opt/airflow/project')
DATABASE = os.getenv('CDC_TARGET_DATABASE', 'energy')
DAG_ID = os.getenv('CDC_DAG_ID', 'energy_cdc_warehouse')
SCHEDULE = os.getenv('CDC_SCHEDULE', '').strip() or None
if SCHEDULE not in (None, '0 2 * * *'):
    raise ValueError('CDC schedule must be manual or daily at 02:00 Asia/Shanghai')
START = pendulum.parse(os.getenv('CDC_SCHEDULE_START', '2026-09-30T00:00:00+08:00')).in_timezone('Asia/Shanghai')
if DATABASE != 'energy' and not re.fullmatch(r'energy_cdc_business_probe_[0-9]{14}', DATABASE):
    raise ValueError('Unsupported CDC target database')
if DAG_ID != 'energy_cdc_warehouse' and not re.fullmatch(r'energy_cdc_warehouse_probe_[0-9]{14}', DAG_ID):
    raise ValueError('Unsupported CDC DAG id')
SNAPSHOT = 'output/mysql_cdc_snapshot_{{ ts_nodash }}.json'
REPORT = 'output/mysql_cdc_business_{{ ts_nodash }}.json'
BIZ_DATE = '{{ logical_date.in_timezone("Asia/Shanghai").to_date_string() }}'
GUARD = ('test "${CDC_WAREHOUSE_ENABLED:-0}" = "1" && '
         'test "${CDC_EXCLUSIVE_SOURCE_CONFIRMED:-0}" = "1" && ')
DIMENSIONS = ('for table in dim_workshop dim_energy_type dim_calendar fact_production; do '
              'python3 datax/run_sync.py --table "$table" --biz-date ' + BIZ_DATE + ' '
              + (f'--probe-database {DATABASE} ' if DATABASE != 'energy' else '')
              + '|| exit $?; done')
APPLY = ('PYSPARK_PYTHON="${CDC_PYSPARK_PYTHON:?set CDC_PYSPARK_PYTHON}" '
         'PYSPARK_DRIVER_PYTHON="${CDC_PYSPARK_PYTHON}" '
         '"${SPARK_SUBMIT:-spark-submit}" --master "${CDC_SPARK_MASTER:-local[2]}" '
         f'spark/apply_mysql_cdc_batch.py --database {DATABASE} '
         '--biz-date ' + BIZ_DATE + ' --snapshot ' + SNAPSHOT + ' --output ' + REPORT)

with DAG(DAG_ID, start_date=START, schedule=SCHEDULE,
         catchup=False, max_active_runs=1, is_paused_upon_creation=True,
         default_args={'owner': 'data-engineering', 'retries': 0,
                       'execution_timeout': timedelta(minutes=30)},
         doc_md=__doc__, tags=['energy', 'cdc', 'opt-in']) as dag:
    dimensions = BashOperator(
        task_id='sync_current_dimensions',
        retries=2, retry_delay=timedelta(minutes=2),
        bash_command=(f'cd {shlex.quote(PROJECT)} && ' + GUARD +
            'if [ "${LAKEHOUSE_EXECUTION_MODE:-local}" = "ssh" ]; then '
            'python -m tools.run_remote_lakehouse --command ' + shlex.quote(DIMENSIONS) +
            '; else ' + DIMENSIONS + '; fi'),
    )
    capture = BashOperator(
        task_id='capture_complete_cdc',
        bash_command=(f'cd {shlex.quote(PROJECT)} && '
            + GUARD +
            'python -m tools.export_mysql_cdc_batch '
            '--connector "${CDC_CONNECTOR:?set CDC_CONNECTOR}" '
            '--topic "${CDC_SOURCE_TOPIC:?set CDC_SOURCE_TOPIC}" '
            '--bootstrap "${CDC_BOOTSTRAP:-kafka:9092}" '
            '--connect-url "${CDC_CONNECT_URL:-http://debezium-connect:8083}" '
            '--output ' + SNAPSHOT),
    )
    apply = BashOperator(
        task_id='apply_cdc_dwd_dws_ads',
        bash_command=(f'cd {shlex.quote(PROJECT)} && '
            'if [ "${LAKEHOUSE_EXECUTION_MODE:-local}" = "ssh" ]; then '
            'python -m tools.run_remote_lakehouse --input ' + SNAPSHOT +
            ' --command ' + shlex.quote(APPLY) + '; else ' + APPLY + '; fi'),
    )
    dimensions >> capture >> apply
