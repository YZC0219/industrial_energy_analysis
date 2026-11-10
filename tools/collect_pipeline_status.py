"""Collect read-only Airflow metadata for the dashboard, once or periodically."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = """SELECT row_to_json(s) FROM (
  SELECT COALESCE((SELECT json_agg(r ORDER BY r.execution_date DESC) FROM (
    SELECT d.run_id,d.state,d.run_type,d.execution_date,d.data_interval_end,d.start_date,d.end_date,
      (SELECT json_agg(t ORDER BY t.task_id) FROM (
        SELECT task_id,state,start_date,end_date,try_number FROM task_instance
        WHERE dag_id=d.dag_id AND run_id=d.run_id) t) AS task_timeline
    FROM dag_run d WHERE d.dag_id='energy_cdc_warehouse'
    ORDER BY d.execution_date DESC LIMIT 14) r), '[]'::json) AS runs,
    (SELECT row_to_json(p) FROM (
      SELECT is_paused AS paused,next_dagrun_create_after AS next_at
      FROM dag WHERE dag_id='energy_cdc_warehouse') p) AS schedule
) s"""


def query(database, sql):
    result = subprocess.run(['docker', 'compose', 'exec', '-T', 'postgres', 'psql',
                             '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'airflow', '-At',
                             '-d', database, '-c', sql], cwd=ROOT, capture_output=True,
                            text=True, encoding='utf-8', timeout=20)
    if result.returncode:
        raise RuntimeError('metadata_unavailable')
    return result.stdout.strip()


def collect(folder=None):
    folder = Path(folder or os.getenv('ENERGY_OUTPUT_DIR', ROOT / 'output')).resolve()
    snapshot = {'checked_at': datetime.now(timezone.utc).isoformat(),
                'available': False, 'runs': [], 'schedule': {}}
    try:
        result = json.loads(query('airflow_cdc_main', SQL))
        if not isinstance(result, dict) or not isinstance(result.get('runs'), list):
            raise ValueError('invalid_metadata')
        snapshot.update(result, available=True)
        snapshot['schedule'] = snapshot.get('schedule') or {}
        old = query('airflow', "SELECT is_paused FROM dag WHERE dag_id='energy_pipeline'")
        snapshot['schedule']['old_writer_paused'] = True if old == 't' else False if old == 'f' else None
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # No raw command output, environment, SQL errors or credentials are published.
        snapshot.update(available=False, runs=[], schedule={}, error='metadata_unavailable')
    folder.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='pipeline-', suffix='.tmp', dir=folder)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(snapshot, stream, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(temporary, folder / 'pipeline_status.json')
    finally:
        Path(temporary).unlink(missing_ok=True)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interval', type=int, default=0, help='0: collect once; otherwise at least 30 seconds')
    parser.add_argument('--serve', action='store_true', help='also serve the local dashboard')
    parser.add_argument('--port', type=int, default=8010)
    args = parser.parse_args()
    if args.interval and args.interval < 30:
        parser.error('interval must be at least 30 seconds')
    if args.serve and not args.interval:
        args.interval = 60
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            try:
                snapshot = collect()
                print('运行状态已采集' if snapshot['available'] else '调度服务不可达，已记录未知状态', flush=True)
            except OSError:
                print('运行状态文件写入失败', flush=True)
            if not args.interval or stop.wait(args.interval):
                break

    if args.serve:
        import uvicorn
        worker = threading.Thread(target=poll, daemon=True)
        worker.start()
        print(f'运行看板：http://127.0.0.1:{args.port}/pipeline', flush=True)
        try:
            uvicorn.run('src.api:app', host='127.0.0.1', port=args.port)
        finally:
            stop.set()
            worker.join(timeout=45)
    else:
        try:
            poll()
        except KeyboardInterrupt:
            stop.set()


if __name__ == '__main__':
    main()
