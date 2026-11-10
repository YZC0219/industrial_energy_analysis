"""Read-only projections of operational evidence; never trigger a warehouse job."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parents[1]
router = APIRouter()
STATES = {'success', 'failed', 'running', 'queued', 'scheduled', 'up_for_retry',
          'upstream_failed', 'skipped', 'removed', 'deferred'}
TASKS = [('sync_current_dimensions', '同步维度'),
         ('capture_complete_cdc', '捕获数据变更'),
         ('apply_cdc_dwd_dws_ads', '更新分层数仓')]


def output_dir():
    return Path(os.getenv('ENERGY_OUTPUT_DIR', ROOT / 'output')).resolve()


def read_json(path):
    try:
        if path.stat().st_size > 2_000_000:
            return None
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def moment(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def timestamp(value):
    parsed = moment(value)
    return parsed.isoformat() if parsed else None


def seconds(start, end):
    a, b = moment(start), moment(end)
    return round((b - a).total_seconds(), 2) if a and b else None


def freshness(value, now):
    at = moment(value)
    if at is None:
        return 'unknown'
    age = (now - at).total_seconds()
    return 'fresh' if -60 <= age <= 900 else 'stale'


def state(value):
    return value if isinstance(value, str) and value in STATES else 'unknown'


def project_run(run, now):
    if not isinstance(run, dict) or not isinstance(run.get('run_id'), str):
        return None
    started, ended = timestamp(run.get('start_date')), timestamp(run.get('end_date'))
    planned = timestamp(run.get('data_interval_end')) if run.get('run_type') == 'scheduled' else None
    items = run.get('task_timeline', [])
    items = items if isinstance(items, list) else []
    by_id = {t.get('task_id'): t for t in items if isinstance(t, dict) and isinstance(t.get('task_id'), str)}
    tasks = []
    for task_id, label in TASKS:
        task = by_id.get(task_id, {})
        task_state = state(task.get('state'))
        duration = seconds(task.get('start_date'), task.get('end_date'))
        if task_state == 'running' and duration is None:
            duration = seconds(task.get('start_date'), now.isoformat())
        tasks.append({'id': task_id, 'label': label, 'state': task_state,
                      'started_at': timestamp(task.get('start_date')),
                      'finished_at': timestamp(task.get('end_date')),
                      'duration_seconds': max(0, duration) if duration is not None else None,
                      'attempt': task.get('try_number') if type(task.get('try_number')) is int else None})
    return {'run_id': run['run_id'][:200], 'state': state(run.get('state')),
            'planned_at': planned, 'started_at': started, 'finished_at': ended,
            'delay_seconds': seconds(planned, started),
            'duration_seconds': seconds(started, ended),
            'logical_at': timestamp(run.get('execution_date')), 'tasks': tasks,
            'failed_steps': [t['label'] for t in tasks if t['state'] == 'failed'],
            'reconciliation': 'unknown', 'audit_source': None, 'source_rows': None}


def same_successful_execution(current, archived):
    """A run ID survives task clears; reuse audits only for the same execution."""
    if (current['run_id'] != archived['run_id'] or
            current['state'] != 'success' or archived['state'] != 'success'):
        return False
    for field in ('logical_at', 'started_at', 'finished_at'):
        observed, recorded = moment(current.get(field)), moment(archived.get(field))
        if observed is None or recorded is None or observed != recorded:
            return False
    recorded_tasks = {task['id']: task for task in archived['tasks']}
    for task in current['tasks']:
        recorded = recorded_tasks.get(task['id'])
        if not recorded or task['state'] != 'success' or recorded['state'] != 'success':
            return False
        if (task['attempt'] is None or recorded['attempt'] is None or
                task['attempt'] != recorded['attempt']):
            return False
        for field in ('started_at', 'finished_at'):
            observed, saved = moment(task.get(field)), moment(recorded.get(field))
            if observed is None or saved is None or observed != saved:
                return False
    return True


def dashboard(now=None):
    now = now or datetime.now(timezone.utc)
    folder = output_dir()
    notices = []
    archives = {}
    for path in sorted(folder.glob('mysql_cdc_business_main_daily_runtime_*.json'), reverse=True)[:60]:
        item = read_json(path)
        if not item or not isinstance(item.get('run'), dict):
            notices.append(f'无法读取批次证据：{path.name}')
            continue
        run = project_run({**item['run'], 'run_id': item.get('run_id'),
                           'task_timeline': item.get('task_timeline')}, now)
        if run and run['run_id'] not in archives:
            archives[run['run_id']] = (run, path.name)

    audits = {}
    for path in sorted(folder.glob('mysql_cdc_business_main_cutover_audit_*.json'), reverse=True)[:60]:
        item = read_json(path)
        ref = item.get('cutover_evidence') if item else None
        if isinstance(ref, str):
            name = ref.replace('\\', '/').rsplit('/', 1)[-1]
            audits.setdefault(name, (item, path.name))
    for run, source in archives.values():
        audit, name = audits.get(source, ({}, None))
        if run['state'] == 'success' and name:
            passed = audit.get('success') is True and audit.get('all_source_fields_reconciled') is True
            run.update(reconciliation='passed' if passed else 'failed', audit_source=name)
            count = audit.get('source_rows')
            run['source_rows'] = count if type(count) is int and count >= 0 else None

    live_path = folder / 'pipeline_status.json'
    live = read_json(live_path)
    live_exists = live_path.exists()
    if live_exists and not live:
        notices.append('最近采集文件无效；当前批次状态未知，请重新采集。')
    checked_at = timestamp(live.get('checked_at')) if live else None
    live_freshness = freshness(checked_at, now)
    raw_runs = live.get('runs', []) if live else []
    raw_runs = raw_runs if isinstance(raw_runs, list) else []
    observed_at = moment(checked_at) or now
    current_runs = [r for raw in raw_runs[:20] if (r := project_run(raw, observed_at))]
    for run in current_runs:
        archived = archives.get(run['run_id'])
        if archived and same_successful_execution(run, archived[0]):
            for key in ('reconciliation', 'audit_source', 'source_rows'):
                run[key] = archived[0][key]
    combined = {key: value[0] for key, value in archives.items()}
    combined.update({run['run_id']: run for run in current_runs})
    history = sorted(combined.values(), key=lambda r: r['logical_at'] or '', reverse=True)[:14]
    current = current_runs[0] if current_runs else None
    if not live_exists and history:
        current = history[0]
    if live and live.get('available') is not True:
        current = None
        notices.append('本次无法读取调度服务；历史记录仍保留，当前状态未知。')
    last_success = max((r for r in combined.values() if r['state'] == 'success' and r['finished_at']),
                       key=lambda r: moment(r['finished_at']), default=None)
    snapshot_freshness = live_freshness if live_exists else 'unknown'

    health_paths = sorted(folder.glob('mysql_cdc_main_health_*.json'), reverse=True)
    health_path = health_paths[0] if health_paths else None
    health = read_json(health_path) if health_path else None
    # A corrupt newest health file must not be replaced with an older green one.
    health_at = timestamp(health.get('checked_at')) if health else None
    checks = health.get('checks', {}) if health else {}
    checks = checks if isinstance(checks, dict) else {}
    components = []
    for key, label in [('docker_services', '基础服务'), ('scheduler', '调度器'),
                       ('connector', '变更连接器'), ('source_history_retention', '变更历史保留'),
                       ('vm_hdfs', '虚拟机 / HDFS')]:
        check = checks.get(key, {})
        check = check if isinstance(check, dict) else {}
        components.append({'id': key, 'label': label,
                           'state': 'passed' if check.get('passed') is True else
                           'failed' if check.get('passed') is False else 'unknown'})
    scheduler = checks.get('scheduler', {})
    scheduler = scheduler if isinstance(scheduler, dict) else {}
    schedule = live.get('schedule', {}) if live else {}
    schedule = schedule if isinstance(schedule, dict) else {}

    report_path = folder / 'report.html'
    try:
        modified = datetime.fromtimestamp(report_path.stat().st_mtime, timezone.utc).isoformat()
        report = {'state': 'available', 'modified_at': modified, 'batch_link': 'unknown'}
    except OSError:
        report = {'state': 'missing', 'modified_at': None, 'batch_link': 'unknown'}
    if snapshot_freshness != 'fresh':
        notices.append('批次状态来自历史或过期证据，不能据此确认当前运行情况。')
    return {'served_at': now.isoformat(), 'timezone': 'Asia/Shanghai',
            'snapshot': {'checked_at': checked_at, 'freshness': snapshot_freshness,
                         'available': bool(live and live.get('available') is True),
                         'source': live_path.name if live_exists else '历史批次归档'},
            'schedule': {'cron': '0 2 * * *',
                         'next_at': timestamp(schedule.get('next_at')),
                         'paused': schedule.get('paused') if type(schedule.get('paused')) is bool else None,
                         'old_writer_paused': schedule.get('old_writer_paused') if type(schedule.get('old_writer_paused')) is bool else None},
            'current': current, 'last_success': last_success, 'history': history,
            'health': {'checked_at': health_at, 'freshness': freshness(health_at, now),
                       'source': health_path.name if health_path else None, 'components': components,
                       'observed_batch_state': state(scheduler.get('latest_batch_state'))},
            'report': report, 'notices': notices[:10]}


@router.get('/api/v1/pipeline', tags=['运行状态'])
def pipeline_status():
    return dashboard()


@router.get('/pipeline', include_in_schema=False)
def pipeline_page():
    return FileResponse(ROOT / 'src/pipeline.html', headers={'Cache-Control': 'no-store'})


@router.get('/pipeline.js', include_in_schema=False)
def pipeline_script():
    return FileResponse(ROOT / 'src/pipeline.js', media_type='text/javascript')


@router.get('/pipeline.css', include_in_schema=False)
def pipeline_style():
    return FileResponse(ROOT / 'src/pipeline.css', media_type='text/css')
