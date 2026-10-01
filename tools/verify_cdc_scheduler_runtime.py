"""Run the real CDC DAG scheduler twice against newly created isolated resources."""
from __future__ import annotations
import json
import secrets
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from tools.verify_mysql_cdc_probe import ROOT, mysql, connector_config
from tools.register_mysql_cdc import request_json
from tools.export_mysql_cdc_batch import capture, FIELDS, normalize
from datax.run_sync import TABLES


def run(argv, *, output=False):
    result = subprocess.run(argv, cwd=ROOT, check=True, text=True,
                            capture_output=output, encoding='utf-8')
    return result.stdout.strip() if output else None


def source_rows(database):
    pairs = []
    for field in FIELDS:
        value = f'`{field}`'
        if field in ('consumption', 'unit_price', 'cost', 'avg_temperature', 'record_date'):
            value = f'CAST({value} AS CHAR)'
        elif field == 'updated_at':
            value = "DATE_FORMAT(`updated_at`, '%Y-%m-%d %H:%i:%s')"
        pairs.extend([f"'{field}'", value])
    result = mysql(f"SELECT JSON_OBJECT({','.join(pairs)}) FROM {database}.fact_energy_consumption ORDER BY id")
    return [normalize(json.loads(line)) for line in result.splitlines()]


def main():
    stamp = datetime.now().strftime('%Y%m%d%H%M%S')
    work = ROOT / 'tmp' / ('cdc_scheduler_' + stamp)
    work.mkdir(parents=True)
    source = 'industrial_energy_cdc_business_' + stamp
    target = 'energy_cdc_business_probe_' + stamp
    dag = 'energy_cdc_warehouse_probe_' + stamp
    container = 'energy-cdc-scheduler-' + stamp
    metadata = 'airflow_cdc_probe_' + stamp
    connector = 'energy-cdc-scheduler-' + stamp
    topic = f'{connector}.{source}.fact_energy_consumption'
    key = Path('C:/Users/32074/.ssh/id_ed25519_github_yzc0219')
    known = key.parent / 'known_hosts'
    ssh = ['-i', str(key), '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
           '-o', 'StrictHostKeyChecking=yes']
    host = 'yzc@192.168.21.131'
    remote = '/home/yzc/industrial_energy_cdc_scheduler_' + stamp
    password = secrets.token_hex(24)
    reader = 'cdc_reader_' + stamp
    sha = run(['git', 'rev-parse', 'HEAD'], output=True)
    bundle = work / 'runtime.bundle'
    run(['git', 'bundle', 'create', str(bundle), 'HEAD'])
    project = work / 'project'
    run(['git', 'clone', str(bundle), str(project)])
    run(['ssh', *ssh, host, f'test ! -e {remote} && mkdir {remote}'])
    run(['scp', *ssh, str(bundle), host + ':' + remote + '/runtime.bundle'])
    run(['ssh', *ssh, host, f'git clone {remote}/runtime.bundle {remote}/project'])
    # A populated initial snapshot, followed later by an actual physical DELETE.
    mysql(f'''CREATE DATABASE {source};
      CREATE TABLE {source}.fact_energy_consumption (
      id BIGINT PRIMARY KEY, record_date DATE NOT NULL, workshop_code VARCHAR(8),
      energy_code VARCHAR(8), consumption DECIMAL(16,3), unit VARCHAR(16),
      unit_price DECIMAL(12,4), cost DECIMAL(16,2), record_status VARCHAR(16),
      avg_temperature DECIMAL(6,2), data_source VARCHAR(16), is_production_day TINYINT,
      updated_at DATETIME(6), is_deleted TINYINT,
      UNIQUE KEY business_key(record_date,workshop_code,energy_code));
      INSERT INTO {source}.fact_energy_consumption VALUES
      (1,'2024-03-15','W01','E01',1,'unit',10,10,'normal',20,'cdc-probe',1,'2025-12-30 09:00:00',0),
      (2,'2024-03-15','W01','E02',2,'unit',10,20,'normal',20,'cdc-probe',1,'2025-12-30 09:00:00',0);
      GRANT SELECT ON {source}.* TO 'energy_cdc_probe'@'%';
      CREATE USER '{reader}'@'%' IDENTIFIED WITH mysql_native_password BY '{password}';
      GRANT SELECT ON {source}.* TO '{reader}'@'%';''')
    for table in ('dim_workshop', 'dim_energy_type', 'dim_calendar', 'fact_production'):
        columns = ','.join(f'`{name}` ' + ('DATE' if kind == 'date' else
                           'INT' if kind in ('tinyint', 'smallint') else 'VARCHAR(100)')
                           for name, kind in TABLES[table][2])
        mysql(f'CREATE TABLE {source}.{table} ({columns});')
    mysql(f'''INSERT INTO {source}.dim_workshop VALUES ('W01','Probe workshop','batch',0,'unit','100','probe');
      INSERT INTO {source}.dim_energy_type VALUES ('E01','Energy 1','unit','1','2','10'),('E02','Energy 2','unit','2','3','10');
      INSERT INTO {source}.dim_calendar VALUES ('2024-03-15',2024,1,3,'2024-03',5,'Friday',0,NULL,0);
      INSERT INTO {source}.fact_production VALUES ('2024-03-15','W01','100','unit');''')
    run(['docker', 'compose', 'exec', '-T', 'kafka', '/opt/kafka/bin/kafka-topics.sh',
         '--bootstrap-server', 'kafka:9092', '--create', '--topic', connector + '-history',
         '--partitions', '1', '--replication-factor', '1', '--config', 'cleanup.policy=delete',
         '--config', 'retention.ms=-1', '--config', 'retention.bytes=-1'])
    config = {**connector_config(), 'database.server.id': str(1000000000 + secrets.randbelow(1000000000)),
              'topic.prefix': connector, 'database.include.list': source,
              'table.include.list': source + '.fact_energy_consumption',
              'schema.history.internal.kafka.topic': connector + '-history',
              'decimal.handling.mode': 'string', 'time.precision.mode': 'connect'}
    request_json('http://127.0.0.1:8083/connectors/' + connector + '/config', 'PUT', config)
    tunnel = subprocess.Popen(['ssh', *ssh, '-o', 'ExitOnForwardFailure=yes', '-N',
                               '-R', '127.0.0.1:13307:127.0.0.1:3307', host])
    scheduler_started = False
    evidence = {'git_sha': sha, 'database': target, 'source_database': source,
                'dag_id': dag, 'connector': connector, 'runs': [], 'success': False}
    (work / 'scope.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    try:
        deadline = time.monotonic() + 150
        while True:
            try:
                initial = capture(topic, '127.0.0.1:29092', timeout=10)
                if len(initial['rows']) == 2 and all(row['is_deleted'] == 0 for row in initial['rows']):
                    break
            except (ValueError, RuntimeError):
                pass
            if time.monotonic() > deadline:
                raise TimeoutError('Populated initial snapshot did not complete')
            time.sleep(2)
        operations = [e['value']['op'] for e in initial['events'] if e['value']]
        if operations != ['r', 'r']:
            raise ValueError('Expected two genuine initial snapshot records')
        evidence['initial_snapshot_ops'] = operations
        if initial['rows'] != source_rows(source):
            raise ValueError('Initial snapshot differs from complete MySQL source rows')
        evidence['initial_source_reconciliation'] = True
        python = '/home/yzc/.local/share/uv/python/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11'
        run(['ssh', *ssh, host, f'cd {remote}/project && PYSPARK_PYTHON={python} PYSPARK_DRIVER_PYTHON={python} /usr/local/spark/bin/spark-submit --master "local[2]" spark/init_cdc_probe_schema.py --database {target}'])
        dags = work / 'dags'
        dags.mkdir()
        shutil.copy2(project / 'dags/energy_cdc_warehouse_dag.py', dags)
        env = {'AIRFLOW__CORE__EXECUTOR': 'LocalExecutor', 'AIRFLOW__CORE__LOAD_EXAMPLES': 'false',
               'AIRFLOW__DATABASE__SQL_ALCHEMY_CONN': f'postgresql+psycopg2://airflow:airflow@postgres/{metadata}',
               'AIRFLOW__CORE__DAGS_FOLDER': '/opt/airflow/probe-dags', 'AIRFLOW__CORE__PARALLELISM': '2',
               'PROJECT_DIR': '/opt/airflow/project', 'CDC_TARGET_DATABASE': target, 'CDC_DAG_ID': dag,
               'CDC_WAREHOUSE_ENABLED': '1', 'CDC_EXCLUSIVE_SOURCE_CONFIRMED': '1',
               'CDC_CONNECTOR': connector, 'CDC_SOURCE_TOPIC': topic,
               'LAKEHOUSE_EXECUTION_MODE': 'ssh', 'LAKEHOUSE_SSH_HOST': '192.168.21.131',
               'LAKEHOUSE_SSH_USER': 'yzc', 'LAKEHOUSE_SSH_KEY': '/opt/airflow/ssh/key',
               'LAKEHOUSE_SSH_KNOWN_HOSTS': '/opt/airflow/ssh/known_hosts',
               'LAKEHOUSE_REMOTE_PROJECT': remote + '/project', 'LAKEHOUSE_REMOTE_MYSQL_HOST': '127.0.0.1',
               'LAKEHOUSE_REMOTE_MYSQL_PORT': '13307', 'MYSQL_DB': source, 'MYSQL_USER': reader,
               'MYSQL_PASSWORD': password, 'CDC_PYSPARK_PYTHON': python,
               'SPARK_SUBMIT': '/usr/local/spark/bin/spark-submit'}
        envfile = work / 'runtime.env'
        envfile.write_text(''.join(f'{k}={v}\n' for k, v in env.items()), encoding='utf-8')
        logs = work / 'logs'
        logs.mkdir()
        mounts = ['--mount', f'type=bind,source={logs},target=/opt/airflow/logs']
        for local, destination in [(project, '/opt/airflow/project'), (dags, '/opt/airflow/probe-dags'),
                                   (key, '/opt/airflow/ssh/key'), (known, '/opt/airflow/ssh/known_hosts')]:
            mounts += ['--mount', f'type=bind,source={local},target={destination}' + (',readonly' if local != project else '')]
        base = ['docker', 'run', '--network', 'industrial_energy_analysis_default', '--env-file', str(envfile), *mounts]
        run(['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'airflow', '-d', 'postgres', '-c', f'CREATE DATABASE {metadata}'])
        run([*base, '--rm', 'industrial-energy-airflow:2.10.5', 'airflow', 'db', 'migrate'])
        run([*base, '-d', '--name', container, 'industrial-energy-airflow:2.10.5', 'airflow', 'scheduler'])
        scheduler_started = True
        def sql(query):
            return run(['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'airflow', '-d', metadata,
                        '-At', '-c', query], output=True)
        deadline = time.monotonic() + 120
        while not sql(f"SELECT dag_id FROM dag WHERE dag_id='{dag}'"):
            if time.monotonic() > deadline:
                raise TimeoutError('Isolated DAG was not parsed')
            time.sleep(3)
        run(['docker', 'exec', container, 'airflow', 'dags', 'unpause', dag])
        for phase, expected in [('initial', 30), ('physical_delete', 20)]:
            if phase == 'physical_delete':
                mysql(f'DELETE FROM {source}.fact_energy_consumption WHERE id=1;')
                assert mysql(f'SELECT COUNT(*) FROM {source}.fact_energy_consumption WHERE id=1') == '0'
                time.sleep(5)
            runid = 'probe_' + phase + '_' + stamp
            run(['docker', 'exec', container, 'airflow', 'dags', 'trigger', '--run-id', runid, dag])
            deadline = time.monotonic() + 1800
            while True:
                state = sql(f"SELECT state FROM dag_run WHERE dag_id='{dag}' AND run_id='{runid}'")
                if state in ('success', 'failed'):
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError('Scheduler run exceeded time limit')
                time.sleep(10)
            tasks = sql(f"SELECT task_id||':'||state FROM task_instance WHERE dag_id='{dag}' AND run_id='{runid}' ORDER BY task_id").splitlines()
            if state != 'success' or len(tasks) != 3 or any(not t.endswith(':success') for t in tasks):
                raise RuntimeError(f'Scheduler run failed: {tasks}; inspect isolated container logs')
            tag = sql(f"SELECT to_char(execution_date AT TIME ZONE 'UTC','YYYYMMDD\"T\"HH24MISS') FROM dag_run WHERE dag_id='{dag}' AND run_id='{runid}'")
            filename = f'output/mysql_cdc_business_{tag}.json'
            run(['scp', *ssh, host + ':' + remote + '/project/' + filename, str(ROOT / filename)])
            report = json.loads((ROOT / filename).read_text(encoding='utf-8'))
            from decimal import Decimal
            if report['active_rows'] != (2 if phase == 'initial' else 1) or any(Decimal(v) != expected for v in report['costs'].values()):
                raise ValueError('Scheduled warehouse totals differ from source')
            snapshot = f'output/mysql_cdc_snapshot_{tag}.json'
            shutil.copy2(project / snapshot, ROOT / snapshot)
            doc = json.loads((ROOT / snapshot).read_text(encoding='utf-8'))
            if [row for row in doc['rows'] if not row['is_deleted']] != source_rows(source):
                raise ValueError('Scheduled snapshot differs from current MySQL source rows')
            evidence['runs'].append({'phase': phase, 'state': state, 'tasks': tasks, 'report': filename, 'snapshot': snapshot})
            print('CDC_SCHEDULER_PHASE_PASS', phase, flush=True)
        evidence['success'] = True
        output = ROOT / f'output/mysql_cdc_business_scheduler_{stamp}.json'
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('CDC_SCHEDULER_PASS', output, flush=True)
    finally:
        if scheduler_started:
            run(['docker', 'stop', container])
        tunnel.terminate()
        tunnel.wait(timeout=15)
        with urlopen(Request('http://127.0.0.1:8083/connectors/' + connector + '/pause', method='PUT'), timeout=15):
            pass


if __name__ == '__main__':
    main()
