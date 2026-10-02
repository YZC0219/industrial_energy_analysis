"""Execute the approved exclusive CDC cutover with warehouse backups."""
import argparse
import json
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from decimal import Decimal
from tools.verify_mysql_cdc_probe import ROOT
from tools.verify_cdc_scheduler_runtime import source_rows
from tools.export_mysql_cdc_batch import capture


def run(args, output=False):
    result = subprocess.run(args, cwd=ROOT, check=True, text=True,
                            capture_output=output, encoding='utf-8')
    return result.stdout.strip() if output else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True)
    parser.add_argument('--activate', action='store_true', required=True)
    args = parser.parse_args()
    plan = json.loads((ROOT / args.plan).read_text(encoding='utf-8'))
    deployment = Path(plan['deployment_directory'])
    if deployment.resolve().parent != (ROOT / 'tmp').resolve():
        raise ValueError('Deployment must be under project tmp')
    envfile = deployment / 'deployment.env'
    project = deployment / 'project'
    compose = ['docker', 'compose', '--env-file', str(envfile), '-f', str(ROOT / 'docker-compose.yml'),
               '-f', str(project / 'docker-compose.cdc-main.yml'), '--profile', 'cdc-main']
    key = 'C:/Users/32074/.ssh/id_ed25519_github_yzc0219'
    ssh = ['ssh', '-i', key, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', 'yzc@192.168.21.131']
    metadata = ['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'airflow', '-d']
    def sql(database, query):
        return run([*metadata, database, '-At', '-c', query], True)
    stamp = datetime.now().strftime('%Y%m%d%H%M%S')
    backup = '/warehouse/energy_cdc_main_backup_' + stamp
    local_backup = '/home/yzc/industrial_energy_cdc_main_backup_' + stamp
    evidence = {'success': False, 'plan': args.plan, 'git_sha': plan['git_sha'],
                'hdfs_backup': backup, 'vm_backup': local_backup, 'steps': []}
    evidence_path = ROOT / f'output/mysql_cdc_business_main_cutover_{stamp}.json'
    def save():
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    was_paused = sql('airflow', "SELECT is_paused FROM dag WHERE dag_id='energy_pipeline'")
    if was_paused not in ('t', 'f'):
        raise ValueError('Cannot establish old writer state')
    if 'Safe mode is OFF' not in run([*ssh, '/usr/local/hadoop/bin/hdfs dfsadmin -safemode get'], True):
        raise ValueError('HDFS must be available and out of safe mode before pausing the writer')
    backups_ready = False
    scheduler_started = False
    writes_started = False
    try:
        run(['docker', 'compose', 'exec', '-T', 'airflow-scheduler', 'airflow', 'dags', 'pause', 'energy_pipeline'])
        if sql('airflow', "SELECT COUNT(*) FROM dag_run WHERE dag_id='energy_pipeline' AND state IN ('running','queued')") != '0':
            raise ValueError('Old writer has active runs; cutover refused')
        evidence['steps'].append('old_writer_paused_and_idle')
        before = source_rows('industrial_energy')
        if len(before) != plan['source_rows']:
            raise ValueError('Source row count changed since preparation')
        if run(['git', '-C', str(project), 'rev-parse', 'HEAD'], True) != plan['git_sha']:
            raise ValueError('Prepared local revision changed')
        remote = plan['remote_project']
        if run([*ssh, f'cd {remote} && git rev-parse HEAD'], True) != plan['git_sha']:
            raise ValueError('Prepared remote revision changed')
        run([*ssh, f"set -e; test ! -e {local_backup}; mkdir {local_backup}; "
                   f"tar -czf {local_backup}/metastore.tar.gz -C /home/yzc/industrial_energy_analysis metastore_db; "
                   f"/usr/local/hadoop/bin/hdfs dfs -mkdir {backup}; "
                   f"for layer in energy_ods energy_dwd energy_dws energy_ads; do "
                   f"/usr/local/hadoop/bin/hdfs dfs -cp -p -t 8 /warehouse/$layer {backup}/$layer; done; "
                   f"tar -tzf {local_backup}/metastore.tar.gz >/dev/null; "
                   f"/usr/local/hadoop/bin/hdfs dfs -count {backup}/*"])
        backups_ready = True
        evidence['steps'].append('warehouse_and_metastore_backed_up')
        save()
        with urlopen(Request('http://127.0.0.1:8083/connectors/' + plan['verified_connector'] + '/resume', method='PUT'), timeout=15):
            pass
        # This bridge replays from offset zero, so both deletes and initial rows
        # must remain in the source topic. Never compact its event history.
        run(['docker', 'compose', 'exec', '-T', 'kafka', '/opt/kafka/bin/kafka-configs.sh',
             '--bootstrap-server', 'kafka:9092', '--entity-type', 'topics',
             '--entity-name', plan['verified_topic'], '--alter', '--add-config',
             'cleanup.policy=delete,retention.ms=-1,retention.bytes=-1'])
        doc = capture(plan['verified_topic'], '127.0.0.1:29092')
        if sorted(doc['rows'], key=lambda row: row['id']) != before:
            raise ValueError('Resumed CDC projection differs from current source')
        if sql('postgres', "SELECT COUNT(*) FROM pg_database WHERE datname='airflow_cdc_main'") != '0':
            raise ValueError('Dedicated CDC metadata already exists; inspect previous cutover before retry')
        sql('postgres', 'CREATE DATABASE airflow_cdc_main')
        text = envfile.read_text(encoding='utf-8')
        text = text.replace('CDC_MAIN_ENABLED=0\n', 'CDC_MAIN_ENABLED=1\n')
        text = text.replace('CDC_MAIN_EXCLUSIVE_CONFIRMED=0\n', 'CDC_MAIN_EXCLUSIVE_CONFIRMED=1\n')
        envfile.write_text(text, encoding='utf-8')
        run([*compose, 'run', '--rm', '--no-deps', 'cdc-main-scheduler', 'airflow', 'db', 'migrate'])
        run([*compose, 'up', '-d', '--no-deps', 'cdc-main-scheduler'])
        scheduler_started = True
        deadline = time.monotonic() + 150
        while not sql('airflow_cdc_main', "SELECT dag_id FROM dag WHERE dag_id='energy_cdc_warehouse'"):
            if time.monotonic() > deadline:
                raise TimeoutError('Main CDC DAG was not parsed')
            time.sleep(3)
        run([*compose, 'exec', '-T', 'cdc-main-scheduler', 'airflow', 'dags', 'unpause', 'energy_cdc_warehouse'])
        runid = 'main_cutover_' + stamp
        writes_started = True
        run([*compose, 'exec', '-T', 'cdc-main-scheduler', 'airflow', 'dags', 'trigger', '--run-id', runid, 'energy_cdc_warehouse'])
        deadline = time.monotonic() + 2400
        while True:
            state = sql('airflow_cdc_main', f"SELECT state FROM dag_run WHERE run_id='{runid}'")
            if state in ('success', 'failed'):
                break
            if time.monotonic() > deadline:
                raise TimeoutError('Main cutover run timed out')
            time.sleep(10)
        tasks = sql('airflow_cdc_main', f"SELECT task_id||':'||state FROM task_instance WHERE run_id='{runid}' ORDER BY task_id").splitlines()
        if state != 'success' or len(tasks) != 3 or any(not item.endswith(':success') for item in tasks):
            raise RuntimeError('Main DAG failed: ' + str(tasks))
        tag = sql('airflow_cdc_main', f"SELECT to_char(execution_date AT TIME ZONE 'UTC','YYYYMMDD\"T\"HH24MISS') FROM dag_run WHERE run_id='{runid}'")
        snapshot = f'output/mysql_cdc_snapshot_{tag}.json'
        report = f'output/mysql_cdc_business_{tag}.json'
        shutil.copy2(project / snapshot, ROOT / snapshot)
        run(['scp', *ssh[1:-1], ssh[-1] + ':' + remote + '/' + report, str(ROOT / report)])
        result = json.loads((ROOT / report).read_text(encoding='utf-8'))
        expected = sum((Decimal(row['cost']) for row in before if not row['is_deleted']), Decimal(0))
        if not result['success'] or result['active_rows'] != len(before) or any(Decimal(value) != expected for value in result['costs'].values()):
            raise ValueError('Main warehouse report differs from source')
        if source_rows('industrial_energy') != before:
            raise ValueError('Source changed during cutover')
        evidence.update(success=True, run_id=runid, tasks=tasks, source_rows=len(before),
                        warehouse_report=report, snapshot=snapshot, costs=result['costs'],
                        old_writer_paused=True, cdc_schedule=None)
        evidence['steps'].append('main_dag_and_source_reconciliation_passed')
        save()
        print('MAIN_CDC_CUTOVER_PASS', evidence_path, flush=True)
    except Exception as exc:
        evidence['error'] = str(exc)
        save()
        if scheduler_started:
            run([*compose, 'stop', 'cdc-main-scheduler'])
        if writes_started and backups_ready:
            # Do not race an orphan Spark driver: refuse restoration while its cwd is active.
            rollback = (f"set -e; python3 -c 'import os; root=\"{remote}\"; "
                        "active=[p for p in os.listdir(\"/proc\") if p.isdigit() and "
                        "os.path.exists(\"/proc/\"+p+\"/cwd\") and "
                        "os.path.realpath(\"/proc/\"+p+\"/cwd\")==root]; assert not active, active'; "
                        f"mkdir {local_backup}/failed; "
                        f"/usr/local/hadoop/bin/hdfs dfs -mkdir {backup}/failed; "
                        f"for layer in energy_ods energy_dwd energy_dws energy_ads; do "
                        f"/usr/local/hadoop/bin/hdfs dfs -mv /warehouse/$layer {backup}/failed/$layer; "
                        f"/usr/local/hadoop/bin/hdfs dfs -cp {backup}/$layer /warehouse/$layer; done; "
                        f"mv /home/yzc/industrial_energy_analysis/metastore_db {local_backup}/failed/metastore_db; "
                        f"tar -xzf {local_backup}/metastore.tar.gz -C /home/yzc/industrial_energy_analysis")
            run([*ssh, rollback])
            evidence['steps'].append('warehouse_and_metastore_restored')
        if was_paused == 'f':
            run(['docker', 'compose', 'exec', '-T', 'airflow-scheduler', 'airflow', 'dags', 'unpause', 'energy_pipeline'])
        save()
        raise


if __name__ == '__main__':
    main()
