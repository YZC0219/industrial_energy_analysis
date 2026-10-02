"""Inspect or trigger the deployed main CDC writer while enforcing exclusivity."""
import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from tools.verify_mysql_cdc_probe import ROOT


def run(args):
    return subprocess.run(args, cwd=ROOT, check=True, capture_output=True,
                          text=True, encoding='utf-8').stdout.strip()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['status', 'trigger'])
    p.add_argument('--deployment', type=Path, required=True)
    args = p.parse_args()
    deployment = args.deployment.resolve()
    if deployment.parent != (ROOT / 'tmp').resolve():
        raise ValueError('Unsupported deployment directory')
    metadata = ['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'airflow', '-At', '-d']
    old_paused = run([*metadata, 'airflow', '-c', "SELECT is_paused FROM dag WHERE dag_id='energy_pipeline'"])
    active = run([*metadata, 'airflow_cdc_main', '-c', "SELECT COUNT(*) FROM dag_run WHERE dag_id='energy_cdc_warehouse' AND state IN ('queued','running')"])
    recent = run([*metadata, 'airflow_cdc_main', '-c', "SELECT run_id||':'||state FROM dag_run WHERE dag_id='energy_cdc_warehouse' ORDER BY execution_date DESC LIMIT 3"])
    if args.action == 'status':
        compose = ['docker', 'compose', '--env-file', str(deployment / 'deployment.env'),
                   '-f', str(ROOT / 'docker-compose.yml'), '-f', str(deployment / 'project/docker-compose.cdc-main.yml'),
                   '--profile', 'cdc-main']
        schedule = run([*compose, 'exec', '-T', 'cdc-main-scheduler', 'python', '-c',
                        "import os; print(os.getenv('CDC_SCHEDULE') or 'manual batch')"])
        print(json.dumps({'old_writer_paused': old_paused == 't', 'active_cdc_runs': int(active),
                          'recent_runs': recent.splitlines(), 'schedule': schedule,
                          'timezone': 'Asia/Shanghai'}, ensure_ascii=False, indent=2))
        return
    if old_paused != 't' or active != '0':
        raise ValueError('Old writer must be paused and current CDC batch must be finished')
    compose = ['docker', 'compose', '--env-file', str(deployment / 'deployment.env'),
               '-f', str(ROOT / 'docker-compose.yml'), '-f', str(deployment / 'project/docker-compose.cdc-main.yml'),
               '--profile', 'cdc-main']
    identifier = 'main_batch_' + datetime.now().strftime('%Y%m%d%H%M%S')
    print(run([*compose, 'exec', '-T', 'cdc-main-scheduler', 'airflow', 'dags', 'trigger',
               '--run-id', identifier, 'energy_cdc_warehouse']))


if __name__ == '__main__':
    main()
