"""Create only fresh probe resources, capture real deletes, verify warehouse SQL."""
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError

from tools.export_mysql_cdc_batch import capture
from tools.register_mysql_cdc import request_json
from tools.verify_mysql_cdc_probe import ROOT, connector_config, mysql


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def command(args: list[str]) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ssh-key', type=Path, required=True)
    p.add_argument('--ssh-host', default='yzc@192.168.21.131')
    args = p.parse_args()
    stamp = datetime.now().strftime('%Y%m%d%H%M%S')
    database = 'industrial_energy_cdc_business_' + stamp
    prefix = 'energy-cdc-business-' + stamp
    connector = prefix
    topic = f'{prefix}.{database}.fact_energy_consumption'
    if mysql(f"SHOW DATABASES LIKE '{database}';"):
        raise ValueError('Source probe database already exists')
    mysql(f"""CREATE DATABASE {database};
        CREATE TABLE {database}.fact_energy_consumption (
            id BIGINT PRIMARY KEY, record_date DATE NOT NULL,
            workshop_code VARCHAR(8) NOT NULL,energy_code VARCHAR(8) NOT NULL,
            consumption DECIMAL(16,3) NOT NULL,unit VARCHAR(16) NOT NULL,
            unit_price DECIMAL(12,4) NOT NULL,cost DECIMAL(16,2) NOT NULL,
            record_status VARCHAR(16) NOT NULL,avg_temperature DECIMAL(6,2),
            data_source VARCHAR(16),is_production_day TINYINT NOT NULL,
            updated_at DATETIME(6),is_deleted TINYINT NOT NULL,
            UNIQUE KEY business_key(record_date,workshop_code,energy_code));
        GRANT SELECT ON {database}.* TO 'energy_cdc_probe'@'%';""")
    config = {**connector_config(), 'database.server.id': str(1000000000 + secrets.randbelow(1000000000)),
              'topic.prefix': prefix, 'database.include.list': database,
              'table.include.list': database + '.fact_energy_consumption',
              'schema.history.internal.kafka.topic': prefix + '-schema-history',
              'decimal.handling.mode': 'string', 'time.precision.mode': 'connect'}
    # Explicitly retain all schema history; never compact it.
    command(['docker', 'compose', 'exec', '-T', 'kafka', '/opt/kafka/bin/kafka-topics.sh',
             '--bootstrap-server', 'kafka:9092', '--create', '--topic', prefix + '-schema-history',
             '--partitions', '1', '--replication-factor', '1', '--config', 'cleanup.policy=delete',
             '--config', 'retention.ms=-1', '--config', 'retention.bytes=-1'])
    request_json('http://127.0.0.1:8083/connectors/' + connector + '/config', 'PUT', config)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            status = request_json('http://127.0.0.1:8083/connectors/' + connector + '/status', 'GET')
        except HTTPError as exc:
            if exc.code not in (404, 409, 503):
                raise
            time.sleep(2)
            continue
        tasks = status.get('tasks', [])
        if any(task.get('state') == 'FAILED' for task in tasks):
            raise RuntimeError('Business probe connector task failed')
        if tasks and all(task.get('state') == 'RUNNING' for task in tasks):
            break
        time.sleep(2)
    else:
        raise TimeoutError('Business connector did not start')
    snapshots = {}

    def snapshot(name: str, condition) -> None:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                doc = capture(topic, '127.0.0.1:29092', timeout=20)
            except ValueError:
                time.sleep(2)
                continue
            if condition(doc['rows']):
                path = Path(f'output/mysql_cdc_snapshot_{stamp}_{name}.json')
                write(ROOT / path, doc)
                snapshots[name] = path.as_posix()
                print(f'CDC_BUSINESS_CAPTURE {name} PASS', flush=True)
                return
            time.sleep(2)
        raise TimeoutError(f'Business capture {name} did not reach expected state')

    mysql(f"""INSERT INTO {database}.fact_energy_consumption VALUES
        (1,'2024-03-15','W01','E01',1.000,'unit',10.0000,10.00,'normal',20.00,'cdc-probe',1,'2025-12-30 09:00:00',0),
        (2,'2024-03-15','W01','E02',2.000,'unit',10.0000,20.00,'normal',20.00,'cdc-probe',1,'2025-12-30 09:00:00',0);""")
    snapshot('insert', lambda rows: len(rows) == 2 and rows[0]['cost'] == '10.00')
    mysql(f"UPDATE {database}.fact_energy_consumption SET consumption=1.700,cost=17.00 WHERE id=1;")
    snapshot('correction', lambda rows: len(rows) == 2 and rows[0]['cost'] == '17.00')
    mysql(f'DELETE FROM {database}.fact_energy_consumption WHERE id=1;')
    snapshot('physical_delete', lambda rows: len(rows) == 2 and rows[0]['is_deleted'] == 1)
    if mysql(f'SELECT COUNT(*) FROM {database}.fact_energy_consumption WHERE id=1;') != '0':
        raise ValueError('Physical source deletion did not finish')
    final = json.loads((ROOT / snapshots['physical_delete']).read_text(encoding='utf-8'))
    deletion = [r for r in final['events'] if r['value'] is not None and r['value'].get('op') == 'd']
    if not deletion or deletion[-1]['value']['before']['updated_at'] != 1767085200000:
        raise ValueError('Delete must preserve the old source updated_at')
    # Model an at-least-once connector replay: append the original old insert
    # after the physical delete. Binlog ordering must prevent resurrection.
    from kafka import KafkaProducer
    original = json.loads((ROOT / snapshots['insert']).read_text(encoding='utf-8'))['events'][0]
    producer = KafkaProducer(bootstrap_servers='127.0.0.1:29092')
    try:
        replay_offset = producer.send(topic, key=json.dumps(original['key']).encode(),
                                      value=json.dumps(original['value']).encode()).get(timeout=30).offset
        producer.flush(timeout=30)
    finally:
        producer.close()
    snapshot('source_replay', lambda rows: len(rows) == 2 and rows[0]['is_deleted'] == 1)
    probe_db = 'energy_cdc_business_probe_' + stamp
    manifest = Path(f'output/mysql_cdc_business_source_{stamp}.json')
    write(ROOT / manifest, {'success': True, 'source_database': database, 'source_topic': topic,
                           'source_rows_after_delete': 0, 'connector': connector,
                           'snapshots': snapshots, 'physical_delete_preserves_updated_at': True,
                           'old_insert_appended_after_delete_offset': replay_offset,
                           'replay_injection_scope': 'only this new isolated source topic'})
    # Archive only the files required for this isolated runtime; no existing VM checkout is changed.
    archive = ROOT / 'tmp' / f'cdc_business_runtime_{stamp}.tar.gz'
    paths = ['spark/apply_mysql_cdc_batch.py', 'spark/sql', 'hive/ddl',
             'tools/export_mysql_cdc_batch.py', 'tools/check_isolated_lakehouse_replay.py',
             'tools/verify_cdc_business_warehouse.py', manifest.as_posix(), *snapshots.values()]
    command(['tar', '-czf', str(archive), *paths])
    ssh = ['-i', str(args.ssh_key), '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes']
    remote_dir = '/home/yzc/industrial_energy_cdc_verify_' + stamp
    command(['ssh', *ssh, args.ssh_host, f'test ! -e {remote_dir} && mkdir {remote_dir}'])
    command(['scp', *ssh, str(archive), args.ssh_host + ':' + remote_dir + '/runtime.tar.gz'])
    report = f'output/mysql_cdc_business_warehouse_{stamp}.json'
    python = '/home/yzc/.local/share/uv/python/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11'
    remote_command = (f'cd {remote_dir} && tar -xzf runtime.tar.gz && '
        f'PYSPARK_PYTHON={python} PYSPARK_DRIVER_PYTHON={python} '
        f"/usr/local/spark/bin/spark-submit --master 'local[2]' tools/verify_cdc_business_warehouse.py "
        f'--database {probe_db} --snapshots {manifest.as_posix()} --output {report}')
    command(['ssh', *ssh, args.ssh_host, remote_command])
    command(['scp', *ssh, args.ssh_host + ':' + remote_dir + '/' + report, str(ROOT / report)])
    print(f'CDC_BUSINESS_RUNTIME PASS source={manifest} warehouse={report}')


if __name__ == '__main__':
    main()
