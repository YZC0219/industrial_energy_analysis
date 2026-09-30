"""Opt-in CDC warehouse source mode; exclusive with the energy DataX writer.

Uses the complete source topic, frozen offsets, immutable evidence upload, and
the same warehouse SQL. No simulated source data is generated or reinserted.
Before activation pause the old warehouse writer, configure an initial-mode
business connector, deploy the same Git revision to the VM, and bootstrap dimensions.
"""
from __future__ import annotations

import os
import shlex
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

PROJECT = os.getenv('PROJECT_DIR', '/opt/airflow/project')
SNAPSHOT = 'output/mysql_cdc_snapshot_{{ ts_nodash }}.json'
REPORT = 'output/mysql_cdc_business_{{ ts_nodash }}.json'
GUARD = ('test "${CDC_WAREHOUSE_ENABLED:-0}" = "1" && '
         'test "${CDC_EXCLUSIVE_SOURCE_CONFIRMED:-0}" = "1" && ')
DIMENSIONS = ('for table in dim_workshop dim_energy_type dim_calendar fact_production; do '
              'python datax/run_sync.py --table "$table" --biz-date {{ ds }} || exit $?; done')
APPLY = ('PYSPARK_PYTHON="${CDC_PYSPARK_PYTHON:?set CDC_PYSPARK_PYTHON}" '
         'PYSPARK_DRIVER_PYTHON="${CDC_PYSPARK_PYTHON}" '
         '"${SPARK_SUBMIT:-spark-submit}" --master "${CDC_SPARK_MASTER:-local[2]}" '
         'spark/apply_mysql_cdc_batch.py --database energy '
         '--biz-date {{ ds }} --snapshot ' + SNAPSHOT + ' --output ' + REPORT)

with DAG('energy_cdc_warehouse', start_date=datetime(2026, 9, 30), schedule=None,
         catchup=False, max_active_runs=1, is_paused_upon_creation=True,
         default_args={'owner': 'data-engineering', 'retries': 0,
                       'execution_timeout': timedelta(minutes=30)},
         doc_md=__doc__, tags=['energy', 'cdc', 'opt-in']) as dag:
    dimensions = BashOperator(
        task_id='sync_current_dimensions',
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
