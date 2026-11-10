"""Operational evidence must not turn historical success into current health."""
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.pipeline_dashboard import dashboard
from tools import collect_pipeline_status as collector

NOW = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)


def write(folder, name, value):
    (folder / name).write_text(json.dumps(value), encoding='utf-8')


def run(identifier='scheduled__2026-10-12T18:00:00+00:00', state='success'):
    # 成功批次要对齐三步任务与结束时间，审计才能复用；失败/运行中仍只暴露第一步，
    # 方便断言「失败步骤名」与「卡住时长」。
    finished = '2026-10-14T00:30:00+00:00' if state == 'success' else None
    if state == 'success':
        timeline = [
            {'task_id': 'sync_current_dimensions', 'state': 'success',
             'start_date': '2026-10-14T00:00:00+00:00', 'end_date': '2026-10-14T00:10:00+00:00',
             'try_number': 3},
            {'task_id': 'capture_complete_cdc', 'state': 'success',
             'start_date': '2026-10-14T00:10:00+00:00', 'end_date': '2026-10-14T00:20:00+00:00',
             'try_number': 3},
            {'task_id': 'apply_cdc_dwd_dws_ads', 'state': 'success',
             'start_date': '2026-10-14T00:20:00+00:00', 'end_date': finished, 'try_number': 3},
        ]
    else:
        timeline = [{'task_id': 'sync_current_dimensions', 'state': state,
                     'start_date': '2026-10-14T00:00:00+00:00', 'end_date': None,
                     'try_number': 3}]
    return {'run_id': identifier, 'state': state, 'run_type': 'scheduled',
            'execution_date': '2026-10-12T18:00:00+00:00',
            'data_interval_end': '2026-10-13T18:00:00+00:00',
            'start_date': '2026-10-14T00:00:00+00:00',
            'end_date': finished,
            'task_timeline': timeline}


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv('ENERGY_OUTPUT_DIR', str(tmp_path))
    return tmp_path


def snapshot(folder, rows, **extra):
    write(folder, 'pipeline_status.json', {'available': True,
          'checked_at': '2026-10-14T01:59:00+00:00', 'runs': rows, **extra})


def archive(folder, row, stamp='20261014083000'):
    name = f'mysql_cdc_business_main_daily_runtime_{stamp}.json'
    write(folder, name, {'success': True, 'run_id': row['run_id'], 'run': row,
                        'task_timeline': row['task_timeline']})
    return name


def test_empty_outputs_and_page_assets(folder):
    data = dashboard(NOW)
    assert data['current'] is None and data['report']['state'] == 'missing'
    assert data['snapshot']['freshness'] == 'unknown'
    client = TestClient(app)
    for path in ['/pipeline', '/pipeline.js', '/pipeline.css', '/api/v1/pipeline']:
        assert client.get(path).status_code == 200


def test_success_delay_is_not_punctual_and_report_is_independent(folder):
    snapshot(folder, [run()])
    (folder / 'report.html').write_text('report', encoding='utf-8')
    data = dashboard(NOW)
    assert data['snapshot']['freshness'] == 'fresh'
    assert data['current']['delay_seconds'] == 21600
    assert data['current']['duration_seconds'] == 1800
    assert data['current']['reconciliation'] == 'unknown'
    assert data['report']['state'] == 'available' and data['report']['batch_link'] == 'unknown'


def test_audit_must_match_exact_current_batch_archive(folder):
    row = run()
    source = archive(folder, row)
    write(folder, 'mysql_cdc_business_main_cutover_audit_20261014083000.json',
          {'success': True, 'all_source_fields_reconciled': True,
           'cutover_evidence': 'output/' + source, 'source_rows': 19006, 'secret': 'never-export'})
    snapshot(folder, [row])
    data = dashboard(NOW)
    assert data['current']['reconciliation'] == 'passed'
    assert data['current']['source_rows'] == 19006
    assert 'never-export' not in json.dumps(data)
    snapshot(folder, [run(identifier='different-run', state='running')])
    assert dashboard(NOW)['current']['reconciliation'] == 'unknown'


def test_latest_failure_overrides_older_success_and_identifies_step(folder):
    archive(folder, run(identifier='older-run'))
    failed = run(state='failed')
    snapshot(folder, [failed])
    data = dashboard(NOW)
    assert data['current']['state'] == 'failed'
    assert data['current']['failed_steps'] == ['同步维度']
    assert data['last_success']['run_id'] == 'older-run'
    assert data['current']['reconciliation'] == 'unknown'


def test_recovered_duplicate_run_uses_newest_archive(folder):
    row = run()
    archive(folder, row, '20261014080000')
    later = {**row, 'start_date': '2026-10-14T01:00:00+00:00',
             'end_date': '2026-10-14T01:30:00+00:00'}
    archive(folder, later, '20261014083000')
    data = dashboard(NOW)
    assert len(data['history']) == 1
    assert data['current']['started_at'] == later['start_date']
    assert data['snapshot']['freshness'] == 'unknown'


@pytest.mark.parametrize('contents', ['{broken', json.dumps({'available': False, 'runs': []})])
def test_invalid_or_offline_snapshot_does_not_fallback_to_green(folder, contents):
    archive(folder, run())
    (folder / 'pipeline_status.json').write_text(contents, encoding='utf-8')
    data = dashboard(NOW)
    assert data['current'] is None
    assert data['last_success']['state'] == 'success'
    assert data['notices']


def test_stale_running_duration_stops_at_observation_time(folder):
    snapshot(folder, [run(state='running')], checked_at='2026-10-14T00:10:00+00:00')
    data = dashboard(NOW)
    assert data['snapshot']['freshness'] == 'stale'
    assert data['current']['tasks'][0]['duration_seconds'] == 600


def test_health_corruption_and_expiry_are_not_live_health(folder):
    write(folder, 'mysql_cdc_main_health_20261014080000.json',
          {'checked_at': '2026-10-14T00:00:00+00:00',
           'checks': {'docker_services': {'passed': True}}})
    assert dashboard(NOW)['health']['freshness'] == 'stale'
    (folder / 'mysql_cdc_main_health_20261014090000.json').write_text('{broken', encoding='utf-8')
    data = dashboard(NOW)
    assert data['health']['components'][0]['state'] == 'unknown'
    assert data['health']['freshness'] == 'unknown'


def test_manual_or_naive_timestamps_do_not_invent_delay(folder):
    snapshot(folder, [{**run(), 'run_type': 'manual', 'start_date': '2026-10-14T01:00:00'}])
    data = dashboard(NOW)
    assert data['current']['planned_at'] is None
    assert data['current']['started_at'] is None
    assert data['current']['delay_seconds'] is None


def test_collector_failure_publishes_unavailable_without_raw_errors(folder, monkeypatch):
    def fail(*args):
        raise RuntimeError('private-password')
    monkeypatch.setattr(collector, 'query', fail)
    result = collector.collect(folder)
    assert result['available'] is False and result['runs'] == []
    assert 'private-password' not in (folder / 'pipeline_status.json').read_text(encoding='utf-8')


def test_collector_projects_read_only_metadata(folder, monkeypatch):
    calls = []
    def query(database, sql):
        calls.append((database, sql))
        return 't' if database == 'airflow' else json.dumps({'runs': [run()], 'schedule': {'paused': False}})
    monkeypatch.setattr(collector, 'query', query)
    result = collector.collect(folder)
    assert result['available'] is True
    assert result['schedule']['old_writer_paused'] is True
    assert len(calls) == 2 and all(sql.lstrip().startswith('SELECT') for _, sql in calls)
    assert read_snapshot(folder)['runs'][0]['run_id'] == run()['run_id']


def read_snapshot(folder):
    return json.loads((folder / 'pipeline_status.json').read_text(encoding='utf-8'))
