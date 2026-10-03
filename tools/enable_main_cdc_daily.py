"""Roll out an idle, exclusive CDC writer with a daily Shanghai schedule."""
import argparse
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tools.verify_mysql_cdc_probe import ROOT


def run(args, output=False):
    result = subprocess.run(args, cwd=ROOT, check=True, text=True,
                            capture_output=output, encoding='utf-8')
    return result.stdout.strip() if output else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--previous', type=Path, required=True)
    args = p.parse_args()
    previous = args.previous.resolve()
    if previous.parent != (ROOT / 'tmp').resolve():
        raise ValueError('Invalid previous deployment')
    metadata = ['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'airflow', '-At', '-d']
    if run([*metadata, 'airflow', '-c', "SELECT is_paused FROM dag WHERE dag_id='energy_pipeline'"], True) != 't':
        raise ValueError('Old writer must remain paused')
    if run([*metadata, 'airflow_cdc_main', '-c', "SELECT COUNT(*) FROM dag_run WHERE state IN ('running','queued')"], True) != '0':
        raise ValueError('Wait for the active CDC run before changing deployment')
    now = datetime.now(timezone(timedelta(hours=8)))
    stamp = now.strftime('%Y%m%d%H%M%S')
    root = ROOT / 'tmp' / ('cdc_main_daily_' + stamp)
    root.mkdir()
    sha = run(['git', 'rev-parse', 'HEAD'], True)
    bundle = root / 'runtime.bundle'
    run(['git', 'bundle', 'create', str(bundle), 'HEAD'])
    project = root / 'project'
    run(['git', 'clone', str(bundle), str(project)])
    dags = root / 'dags'
    dags.mkdir()
    shutil.copy2(project / 'dags/energy_cdc_warehouse_dag.py', dags)
    logs = root / 'logs'
    logs.mkdir()
    env = dict(line.split('=', 1) for line in (previous / 'deployment.env').read_text(encoding='utf-8').splitlines() if '=' in line)
    if env['CDC_MAIN_ENABLED'] != '1' or env['CDC_MAIN_EXCLUSIVE_CONFIRMED'] != '1':
        raise ValueError('Previous exclusive source mode is not active')
    remote = '/home/yzc/industrial_energy_cdc_daily_' + stamp
    ssh = ['-i', env['LAKEHOUSE_SSH_KEY_HOST'], '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes']
    host = 'yzc@192.168.21.131'
    run(['ssh', *ssh, host, f'test ! -e {remote} && mkdir -p {remote}/conf'])
    run(['scp', *ssh, str(bundle), host + ':' + remote + '/runtime.bundle'])
    run(['ssh', *ssh, host, f'git clone {remote}/runtime.bundle {remote}/project'])
    conf = root / 'spark-defaults.conf'
    conf.write_text('spark.hadoop.javax.jdo.option.ConnectionURL jdbc:derby:/home/yzc/industrial_energy_analysis/metastore_db;create=false\n'
                    'spark.sql.warehouse.dir hdfs://localhost:9000/user/hive/warehouse\n', encoding='utf-8')
    run(['scp', *ssh, str(conf), host + ':' + remote + '/conf/spark-defaults.conf'])
    start = (now - timedelta(days=1)).replace(hour=2, minute=0, second=0, microsecond=0)
    env.update(CDC_MAIN_PROJECT_DIR=project.as_posix(), CDC_MAIN_DAGS_DIR=dags.as_posix(),
               CDC_MAIN_LOG_DIR=logs.as_posix(), CDC_MAIN_REMOTE_PROJECT=remote + '/project',
               CDC_MAIN_REMOTE_SPARK_CONF_DIR=remote + '/conf', CDC_MAIN_SCHEDULE='0 2 * * *',
               CDC_MAIN_SCHEDULE_START=start.isoformat())
    envfile = root / 'deployment.env'
    envfile.write_text(''.join(f'{k}={v}\n' for k, v in env.items()), encoding='utf-8')
    compose = ['docker', 'compose', '--env-file', str(envfile), '-f', str(ROOT / 'docker-compose.yml'),
               '-f', str(project / 'docker-compose.cdc-main.yml'), '--profile', 'cdc-main']
    run([*compose, 'config', '--quiet'])
    validation = ("import runpy,json,pendulum; d=runpy.run_path('dags/energy_cdc_warehouse_dag.py')['dag']; "
                  "assert str(d.timezone)=='Asia/Shanghai'; assert d.max_active_runs==1 and not d.catchup; "
                  "c={'logical_date':pendulum.parse('2026-10-01T18:00:00+00:00'),'ts_nodash':'20261001T180000'}; "
                  "[t.render_template_fields(c) for t in d.tasks]; "
                  "assert all('2026-10-02' in d.get_task(n).bash_command for n in ['sync_current_dimensions','apply_cdc_dwd_dws_ads']); "
                  "print(json.dumps({'schedule':d.schedule_interval,'timezone':str(d.timezone),'tasks':d.task_ids}))")
    parsed = json.loads(run([*compose, 'run', '--rm', '--no-deps', '--entrypoint', 'python', 'cdc-main-scheduler', '-c', validation], True).splitlines()[-1])
    if parsed['schedule'] != '0 2 * * *' or len(parsed['tasks']) != 3:
        raise ValueError('Daily DAG configuration did not parse')
    # Recheck immediately before replacing the idle scheduler.
    if run([*metadata, 'airflow_cdc_main', '-c', "SELECT COUNT(*) FROM dag_run WHERE state IN ('running','queued')"], True) != '0':
        raise ValueError('A batch started during preparation; deployment refused')
    run([*compose, 'up', '-d', '--no-deps', 'cdc-main-scheduler'])
    report = {'success': True, 'scope': 'daily schedule deployment; first automatic run still requires validation',
              'git_sha': sha, 'deployment_directory': str(root), 'previous_deployment': str(previous),
              'remote_project': remote + '/project', 'schedule': '0 2 * * *', 'timezone': 'Asia/Shanghai',
              'start_date': start.isoformat(), 'catchup': False, 'max_active_runs': 1,
              'dag_parse': parsed, 'old_writer_paused': True, 'metadata_database': 'airflow_cdc_main'}
    path = ROOT / f'output/mysql_cdc_business_main_daily_deployment_{stamp}.json'
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print('MAIN_CDC_DAILY_DEPLOYED', path, flush=True)


if __name__ == '__main__':
    main()
