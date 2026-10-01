"""Prepare pinned main CDC deployment without stopping or changing main writers."""
import json
import secrets
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from tools.verify_mysql_cdc_probe import ROOT, mysql


def run(args, output=False):
    result = subprocess.run(args, cwd=ROOT, check=True, capture_output=output,
                            text=True, encoding='utf-8')
    return result.stdout.strip() if output else None


def main():
    stamp = datetime.now().strftime('%Y%m%d%H%M%S')
    root = ROOT / 'tmp' / ('cdc_main_deployment_' + stamp)
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
    key = Path('C:/Users/32074/.ssh/id_ed25519_github_yzc0219')
    known = key.parent / 'known_hosts'
    ssh = ['-i', str(key), '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes']
    host = 'yzc@192.168.21.131'
    remote = '/home/yzc/industrial_energy_cdc_main_' + stamp
    run(['ssh', *ssh, host, f'test ! -e {remote} && mkdir -p {remote}/conf'])
    run(['scp', *ssh, str(bundle), host + ':' + remote + '/runtime.bundle'])
    run(['ssh', *ssh, host, f'git clone {remote}/runtime.bundle {remote}/project'])
    conf = root / 'spark-defaults.conf'
    conf.write_text('spark.hadoop.javax.jdo.option.ConnectionURL jdbc:derby:/home/yzc/industrial_energy_analysis/metastore_db;create=false\n'
                    'spark.sql.warehouse.dir hdfs://localhost:9000/user/hive/warehouse\n', encoding='utf-8')
    run(['scp', *ssh, str(conf), host + ':' + remote + '/conf/spark-defaults.conf'])
    report = json.loads((ROOT / 'output/mysql_cdc_business_main_reconciliation_20261002012039.json').read_text(encoding='utf-8'))
    if not report['warehouse_projection_eligible']:
        raise ValueError('Source reconciliation did not pass projection gate')
    reader = 'cdc_main_reader_' + stamp
    password = secrets.token_hex(24)
    mysql(f"CREATE USER '{reader}'@'%' IDENTIFIED WITH mysql_native_password BY '{password}'; GRANT SELECT ON industrial_energy.* TO '{reader}'@'%';")
    env = {'CDC_MAIN_ENABLED': '0', 'CDC_MAIN_EXCLUSIVE_CONFIRMED': '0',
           'CDC_MAIN_CONNECTOR': report['connector'], 'CDC_MAIN_SOURCE_TOPIC': report['topic'],
           'CDC_MAIN_MYSQL_USER': reader, 'CDC_MAIN_MYSQL_PASSWORD': password,
           'CDC_MAIN_PROJECT_DIR': project.as_posix(), 'CDC_MAIN_DAGS_DIR': dags.as_posix(),
           'CDC_MAIN_LOG_DIR': logs.as_posix(), 'CDC_MAIN_REMOTE_PROJECT': remote + '/project',
           'CDC_MAIN_REMOTE_SPARK_CONF_DIR': remote + '/conf',
           'CDC_MAIN_REMOTE_MYSQL_HOST': '192.168.21.1', 'CDC_MAIN_REMOTE_MYSQL_PORT': '3307',
           'LAKEHOUSE_SSH_KEY_HOST': key.as_posix(), 'LAKEHOUSE_KNOWN_HOSTS_HOST': known.as_posix()}
    envfile = root / 'deployment.env'
    envfile.write_text(''.join(f'{k}={v}\n' for k, v in env.items()), encoding='utf-8')
    run(['docker', 'compose', '--env-file', str(envfile), '-f', 'docker-compose.yml',
         '-f', str(project / 'docker-compose.cdc-main.yml'), '--profile', 'cdc-main', 'config', '--quiet'])
    plan = {'status': 'prepared_not_activated', 'git_sha': sha, 'deployment_directory': str(root),
            'remote_project': remote + '/project', 'remote_spark_conf': remote + '/conf',
            'main_metastore': '/home/yzc/industrial_energy_analysis/metastore_db',
            'verified_connector': report['connector'], 'verified_topic': report['topic'],
            'source_rows': report['source_rows'], 'compose_configuration_valid': True,
            'new_scheduler_metadata_database': 'airflow_cdc_main',
            'initial_target_databases': ['energy_ods', 'energy_dwd', 'energy_dws', 'energy_ads'],
            'source_writer_change': 'pause energy_pipeline; leave paused while CDC owns source consumption',
            'source_account': reader, 'source_account_privileges': 'SELECT on industrial_energy only',
            'activation_flags': {'CDC_MAIN_ENABLED': 0, 'CDC_MAIN_EXCLUSIVE_CONFIRMED': 0},
            'steps_after_approval': ['pause old main DAG and reject active runs',
                                     'back up all four HDFS warehouse directories and existing Derby metastore',
                                     'verify pinned checkouts, source, dimensions and metastore schema',
                                     'create dedicated Airflow metadata database; migrate it',
                                     'resume verified connector; enable exclusive CDC scheduler',
                                     'trigger main CDC DAG, verify 19006 active rows and source-matching totals',
                                     'keep old simulation/import DAG paused to prevent physical deletes being reinserted'],
            'rollback': 'stop new writer; restore warehouse/metastore from maintenance backups before resuming old writer',
            'requires_approval': 'switch modifies existing main warehouse and pauses previous daily pipeline',
            'secret_handling': 'password only in ignored D drive deployment.env; never archived in this plan'}
    path = ROOT / f'output/mysql_cdc_business_main_cutover_plan_{stamp}.json'
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print('MAIN_CDC_CUTOVER_PREPARED', path, flush=True)


if __name__ == '__main__':
    main()
