"""Read-only operational checks for the deployed main CDC chain."""
import argparse
import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from tools.verify_mysql_cdc_probe import ROOT


def command(argv, timeout=35):
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                            encoding='utf-8', timeout=timeout)
    if result.returncode:
        # Avoid exposing remote environment values or connector credentials.
        raise RuntimeError('Command failed with exit code ' + str(result.returncode))
    return result.stdout.strip()


def schedule_freshness(status, scheduled, now):
    """Require a recent daily interval; heartbeat alone does not prove scheduling."""
    local_now = now.astimezone(ZoneInfo('Asia/Shanghai'))
    due = local_now.replace(hour=2, minute=0, second=0, microsecond=0)
    # Allow the same 45-minute recovery window as the active-batch check.
    expected = due if local_now >= due + timedelta(minutes=45) else due - timedelta(days=1)
    next_local = status.get('next_run_local')
    next_time = (datetime.fromisoformat(next_local).replace(tzinfo=ZoneInfo('Asia/Shanghai'))
                 if next_local else None)
    overdue = next_time is not None and local_now > next_time + timedelta(minutes=45)
    planned = datetime.fromisoformat(scheduled['data_interval_end']) if scheduled and scheduled.get('data_interval_end') else None
    recent = planned is not None and planned.astimezone(ZoneInfo('Asia/Shanghai')) >= expected
    return {'schedule_fresh': recent and not overdue,
            'next_plan_overdue': overdue,
            'expected_scheduled_interval_end_local': expected.isoformat(),
            'latest_scheduled_interval_end': planned.isoformat() if planned else None,
            'latest_scheduled_state': scheduled.get('state') if scheduled else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, required=True)
    args = parser.parse_args()
    deployment = args.deployment.resolve()
    if deployment.parent != (ROOT / 'tmp').resolve():
        raise ValueError('Unsupported deployment directory')
    env = dict(line.split('=', 1) for line in (deployment / 'deployment.env').read_text().splitlines() if '=' in line)
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    report = {'checked_at': now.isoformat(), 'deployment': str(deployment),
              'scope': 'Read-only point-in-time availability; no warehouse reconciliation or repairs', 'checks': {}}

    def check(name, action):
        try:
            detail = action()
            passed = bool(detail.pop('passed'))
            report['checks'][name] = {'passed': passed, **detail}
        except Exception as exc:
            report['checks'][name] = {'passed': False, 'error_type': type(exc).__name__}

    compose = ['docker', 'compose', '--env-file', str(deployment / 'deployment.env'),
               '-f', 'docker-compose.yml', '-f', str(deployment / 'project/docker-compose.cdc-main.yml'), '--profile', 'cdc-main']
    sql = ['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'airflow', '-At', '-d']

    def services():
        rows = [json.loads(line) for line in command([*compose, 'ps', '--format', 'json']).splitlines() if line.strip()]
        required = {'mysql', 'kafka', 'debezium-connect', 'postgres', 'cdc-main-scheduler'}
        states = {r['Service']: {'state': r.get('State'), 'health': r.get('Health')} for r in rows if r['Service'] in required}
        return {'passed': required <= states.keys() and all(r['state'] == 'running' and r['health'] in ('', None, 'healthy') for r in states.values()), 'services': states}

    def scheduler():
        status = json.loads(command(['python', '-m', 'tools.main_cdc_operator', 'status', '--deployment', str(deployment)]))
        paused = command([*sql, 'airflow_cdc_main', '-c', "SELECT is_paused FROM dag WHERE dag_id='energy_cdc_warehouse'"])
        latest = command([*sql, 'airflow_cdc_main', '-c', "SELECT state FROM dag_run WHERE dag_id='energy_cdc_warehouse' ORDER BY execution_date DESC LIMIT 1"])
        heartbeat = command([*sql, 'airflow_cdc_main', '-c', "SELECT COUNT(*) FROM job WHERE job_type='SchedulerJob' AND state='running' AND latest_heartbeat > NOW()-INTERVAL '120 seconds'"])
        stale = int(command([*sql, 'airflow_cdc_main', '-c', "SELECT COUNT(*) FROM dag_run WHERE dag_id='energy_cdc_warehouse' AND state IN ('running','queued') AND COALESCE(start_date,queued_at,execution_date)<NOW()-INTERVAL '45 minutes'"]))
        scheduled_raw = command([*sql, 'airflow_cdc_main', '-c', "SELECT row_to_json(r) FROM (SELECT state,data_interval_end FROM dag_run WHERE dag_id='energy_cdc_warehouse' AND run_type='scheduled' ORDER BY data_interval_end DESC LIMIT 1) r"])
        freshness = schedule_freshness(status, json.loads(scheduled_raw) if scheduled_raw else None,
                                       datetime.now(ZoneInfo('Asia/Shanghai')))
        plan_visible = bool(status['next_run_local']) or status['active_cdc_runs'] == 1
        return {'passed': status['old_writer_paused'] and status.get('old_writer_active_runs') == 0 and status.get('old_writer_active_tasks') == 0 and paused == 'f' and int(heartbeat) > 0 and latest in ('success', 'running', 'queued') and status['schedule'] == '0 2 * * *' and plan_visible and stale == 0 and freshness['schedule_fresh'] and freshness['latest_scheduled_state'] in ('success', 'running', 'queued'),
                **freshness,
                'stale_active_batches': stale,
                **status, 'cdc_paused': paused != 'f', 'latest_batch_state': latest, 'recent_scheduler_heartbeat': int(heartbeat) > 0}

    def connector():
        name = env['CDC_MAIN_CONNECTOR']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            raise ValueError('Invalid connector name')
        code = "import json,urllib.request; d=json.load(urllib.request.urlopen('http://debezium-connect:8083/connectors/" + name + "/status',timeout=10)); print(json.dumps({'connector':d['connector']['state'],'tasks':[t['state'] for t in d['tasks']]}))"
        states = json.loads(command([*compose, 'exec', '-T', 'cdc-main-scheduler', 'python', '-c', code]))
        return {'passed': states['connector'] == 'RUNNING' and bool(states['tasks']) and all(t == 'RUNNING' for t in states['tasks']), **states}

    def topic():
        name = env['CDC_MAIN_SOURCE_TOPIC']
        output = command(['docker', 'compose', 'exec', '-T', 'kafka', '/opt/kafka/bin/kafka-configs.sh', '--bootstrap-server', 'kafka:9092', '--entity-type', 'topics', '--entity-name', name, '--describe'])
        values = {k: re.search(r'\b' + re.escape(k) + r'=([^,\s]+)', output) for k in ('cleanup.policy', 'retention.ms', 'retention.bytes')}
        values = {k: v.group(1) if v else None for k, v in values.items()}
        return {'passed': values == {'cleanup.policy': 'delete', 'retention.ms': '-1', 'retention.bytes': '-1'}, 'topic': name, 'retention': values}

    def vm():
        output = command(['ssh', '-i', env['LAKEHOUSE_SSH_KEY_HOST'], '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8', 'yzc@192.168.21.131', '/usr/local/hadoop/bin/hdfs dfsadmin -safemode get && /usr/local/hadoop/bin/hdfs dfs -test -d /user/hive/warehouse'], timeout=25)
        return {'passed': 'Safe mode is OFF' in output, 'hdfs_safe_mode_off': 'Safe mode is OFF' in output, 'warehouse_directory_reachable': True}

    for name, action in [('docker_services', services), ('scheduler', scheduler), ('connector', connector), ('source_history_retention', topic), ('vm_hdfs', vm)]:
        check(name, action)
    report['success'] = all(c['passed'] for c in report['checks'].values())
    path = ROOT / 'output' / ('mysql_cdc_main_health_' + now.strftime('%Y%m%d%H%M%S%f') + '.json')
    with path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('HEALTH_REPORT', path)
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
