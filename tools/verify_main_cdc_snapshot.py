"""Read the populated business source and reconcile its complete CDC snapshot.

No source rows, warehouse tables or active scheduler settings are changed.
Only a new connector/topic and its SELECT grant are created; the connector is
paused on exit. Source reads bracket capture and must be identical.
"""
import hashlib
import gzip
import json
import secrets
import subprocess
import time
from datetime import datetime
from urllib.request import Request, urlopen
from tools.verify_mysql_cdc_probe import ROOT, mysql, connector_config
from tools.register_mysql_cdc import request_json
from tools.verify_cdc_scheduler_runtime import source_rows
from tools.export_mysql_cdc_batch import capture_history, normalize, unwrap, project
from tools.check_mysql_cdc_readiness import inspect


def digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False).encode()).hexdigest()


def main():
    stamp = datetime.now().strftime('%Y%m%d%H%M%S')
    name = 'energy-cdc-main-readonly-' + stamp
    database = 'industrial_energy'
    topic = f'{name}.{database}.fact_energy_consumption'
    readiness = inspect()
    if not readiness['binlog_capture_prerequisites_met']:
        raise ValueError('Business source failed CDC preflight')
    before = source_rows(database)
    if not before:
        raise ValueError('Business source must be populated')
    mysql("GRANT SELECT ON industrial_energy.* TO 'energy_cdc_probe'@'%';")
    subprocess.run(['docker', 'compose', 'exec', '-T', 'kafka', '/opt/kafka/bin/kafka-topics.sh',
                    '--bootstrap-server', 'kafka:9092', '--create', '--topic', name + '-history',
                    '--partitions', '1', '--replication-factor', '1', '--config', 'cleanup.policy=delete',
                    '--config', 'retention.ms=-1', '--config', 'retention.bytes=-1'], cwd=ROOT, check=True)
    config = {**connector_config(), 'database.server.id': str(1000000000 + secrets.randbelow(1000000000)),
              'topic.prefix': name, 'database.include.list': database,
              'table.include.list': database + '.fact_energy_consumption',
              'schema.history.internal.kafka.topic': name + '-history',
              'decimal.handling.mode': 'string', 'time.precision.mode': 'connect',
              'snapshot.locking.mode': 'none'}
    request_json('http://127.0.0.1:8083/connectors/' + name + '/config', 'PUT', config)
    try:
        deadline = time.monotonic() + 300
        while True:
            try:
                events, end = capture_history(topic, '127.0.0.1:29092', timeout=60)
                values = [unwrap(event['value']) for event in events if event['value']]
                if (len(values) == len(before) and values[-1]['source'].get('snapshot') == 'last'):
                    break
            except ValueError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError('Business initial snapshot did not complete')
            time.sleep(3)
        after = source_rows(database)
        rows = sorted([normalize(value['after']) for value in values], key=lambda row: row['id'])
        if before != after or rows != after:
            raise ValueError('Source changed during verification or full snapshot differs')
        ops = [value['op'] for value in values]
        if len(ops) != len(before) or set(ops) != {'r'}:
            raise ValueError('Expected a complete populated initial snapshot without concurrent writes')
        projection_error = None
        try:
            project(events, topic, end)
        except ValueError as exc:
            projection_error = str(exc)
        snapshot = {'topic': topic, 'end_offset': end, 'events': events,
                    'source_rows_by_id': rows, 'scope': 'raw complete initial snapshot; not a warehouse input'}
        snapshot_path = ROOT / f'output/mysql_cdc_business_main_raw_{stamp}.json.gz'
        with gzip.open(snapshot_path, 'xt', encoding='utf-8') as stream:
            json.dump(snapshot, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        report = {'success': True, 'scope': 'current populated business source, read-only reconciliation; no warehouse cutover',
                  'source_database': database, 'connector': name, 'topic': topic,
                  'source_rows': len(after), 'snapshot_rows': len(rows), 'snapshot_op_r_count': len(ops),
                  'all_fields_reconciled': True, 'source_reads_before_after_equal': True,
                  'source_sha256': digest(after), 'projected_rows_sha256': digest(rows),
                  'snapshot': snapshot_path.relative_to(ROOT).as_posix(),
                  'snapshot_sha256': hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
                  'preflight': readiness, 'warehouse_cutover': False,
                  'warehouse_projection_eligible': projection_error is None,
                  'warehouse_projection_error': projection_error,
                  'distinct_business_keys': len({(r['record_date'], r['workshop_code'], r['energy_code']) for r in rows}),
                  'source_data_provenance': 'existing project business table; this verification does not establish external industrial provenance',
                  'consistency_limit': 'identical bracketing source reads; no locking and no proof under concurrent source mutation'}
        output = ROOT / f'output/mysql_cdc_business_main_reconciliation_{stamp}.json'
        with output.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        print('MAIN_CDC_FULL_SNAPSHOT_PASS', len(rows), output, flush=True)
    finally:
        with urlopen(Request('http://127.0.0.1:8083/connectors/' + name + '/pause', method='PUT'), timeout=15):
            pass


if __name__ == '__main__':
    main()
