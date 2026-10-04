"""Collect a successful scheduled main batch and audit its immutable input."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo
from tools.verify_mysql_cdc_probe import ROOT


def run(argv):
    return subprocess.check_output(argv, cwd=ROOT, text=True, encoding='utf-8').strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--deployment-evidence', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--stamp', required=True)
    parser.add_argument('--recovery-note', action='append', default=[],
                        help='Observed recovery detail for this specific run; repeat as needed')
    args = parser.parse_args()
    import re
    if not re.fullmatch(r'[0-9]{14}', args.stamp):
        raise ValueError('Invalid archive timestamp')
    if not re.fullmatch(r'scheduled__[0-9T:+.-]+', args.run_id):
        raise ValueError('Only a scheduled run can be archived')
    deployment = json.loads((ROOT / args.deployment_evidence).read_text(encoding='utf-8'))
    directory = Path(deployment['deployment_directory'])
    if directory.resolve().parent != (ROOT / 'tmp').resolve():
        raise ValueError('Unsupported deployment path')
    sql = ['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'airflow', '-At', '-d']
    if run([*sql, 'airflow', '-c', "SELECT is_paused FROM dag WHERE dag_id='energy_pipeline'"]) != 't':
        raise ValueError('Old writer must remain paused')
    record = json.loads(run([*sql, 'airflow_cdc_main', '-c',
        "SELECT row_to_json(r) FROM (SELECT state,run_type,execution_date,data_interval_end,start_date,end_date "
        "FROM dag_run WHERE dag_id='energy_cdc_warehouse' AND run_id='" + args.run_id + "') r"]))
    tasks = run([*sql, 'airflow_cdc_main', '-c',
        "SELECT task_id||':'||COALESCE(state,'null') FROM task_instance "
        "WHERE dag_id='energy_cdc_warehouse' AND run_id='" + args.run_id + "' ORDER BY task_id"]).splitlines()
    if record['state'] != 'success' or record['run_type'] != 'scheduled' or len(tasks) != 3 or any(not t.endswith(':success') for t in tasks):
        raise ValueError('Scheduled batch has not succeeded')
    from datetime import datetime
    logical = datetime.fromisoformat(record['execution_date'])
    planned = datetime.fromisoformat(record['data_interval_end'])
    started = datetime.fromisoformat(record['start_date'])
    tag = logical.strftime('%Y%m%dT%H%M%S')
    snapshot = f'output/mysql_cdc_snapshot_{tag}.json'
    report_path = f'output/mysql_cdc_business_{tag}.json'
    shutil.copy2(directory / 'project' / snapshot, ROOT / snapshot)
    env = dict(line.split('=', 1) for line in (directory / 'deployment.env').read_text().splitlines() if '=' in line)
    run(['scp', '-i', env['LAKEHOUSE_SSH_KEY_HOST'], '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
         '-o', 'StrictHostKeyChecking=yes', 'yzc@192.168.21.131:' + deployment['remote_project'] + '/' + report_path,
         str(ROOT / report_path)])
    report = json.loads((ROOT / report_path).read_text(encoding='utf-8'))
    if not report['success'] or report['database'] != 'energy':
        raise ValueError('Main warehouse report failed')
    evidence = {'success': True, 'deployment_evidence': args.deployment_evidence,
        'git_sha': deployment['git_sha'], 'run_id': args.run_id, 'run': record, 'tasks': tasks,
        'schedule': deployment['schedule'], 'timezone': deployment['timezone'],
        'planned_start_local': planned.astimezone(ZoneInfo('Asia/Shanghai')).isoformat(),
        'actual_start_local': started.astimezone(ZoneInfo('Asia/Shanghai')).isoformat(),
        'start_delay_seconds': (started - planned).total_seconds(),
        'business_date': logical.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat(),
        'snapshot': snapshot, 'warehouse_report': report_path, 'source_rows': report['active_rows'],
        'costs': report['costs'], 'old_writer_paused': True,
        'recovery_context': args.recovery_note}
    path = ROOT / f'output/mysql_cdc_business_main_daily_runtime_{args.stamp}.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(evidence, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    run(['python', '-m', 'tools.archive_main_cdc_cutover', '--evidence', path.relative_to(ROOT).as_posix(), '--stamp', args.stamp])
    print('MAIN_CDC_DAILY_ARCHIVED', path)


if __name__ == '__main__':
    main()
