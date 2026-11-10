"""Inspect or trigger the Linux three-node CDC pipeline without Windows Docker."""
import argparse
import json
import shlex
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.configure_vm_cluster import BASE, client, run


def require_idle(status):
    if not status['old_writer_paused'] or status['old_active_runs'] or status['old_active_tasks'] or status['active_runs']:
        raise ValueError('A writer is active or the legacy writer is not paused')


def status(connection):
    prefix = 'docker exec -u postgres energy-linux-airflow-db-1 psql -U airflow -At -d '
    old_sql = "SELECT json_build_object('old_writer_paused',COALESCE((SELECT is_paused FROM dag WHERE dag_id='energy_pipeline'),false),'old_active_runs',(SELECT count(*) FROM dag_run WHERE state IN ('queued','running')),'old_active_tasks',(SELECT count(*) FROM task_instance WHERE state IN ('queued','running','restarting')))"
    new_sql = "SELECT json_build_object('active_runs',(SELECT count(*) FROM dag_run WHERE state IN ('queued','running')),'is_paused',(SELECT is_paused FROM dag WHERE dag_id='energy_cdc_warehouse'),'schedule_interval',(SELECT schedule_interval FROM dag WHERE dag_id='energy_cdc_warehouse'),'next_run_local',(SELECT to_char(next_dagrun_create_after AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD HH24:MI:SS') FROM dag WHERE dag_id='energy_cdc_warehouse'),'recent_runs',(SELECT json_agg(r) FROM (SELECT run_id,state FROM dag_run ORDER BY execution_date DESC LIMIT 3) r))"
    result = json.loads(run(connection, prefix+'airflow -c '+shlex.quote(old_sql)))
    result.update(json.loads(run(connection, prefix+'airflow_cdc_main -c '+shlex.quote(new_sql))))
    result.update({'hdfs':'hdfs://192.168.21.131:19000','spark':'spark://192.168.21.131:7077','runtime':BASE+'/pipeline'})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['status','trigger'])
    args = parser.parse_args()
    connection = client('192.168.21.131')
    try:
        current = status(connection)
        if args.action == 'trigger':
            require_idle(current)
            if current['is_paused']:
                raise ValueError('CDC DAG is paused; investigate before triggering')
            identifier = 'cluster_manual_'+datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d%H%M%S')
            print(run(connection, 'cd '+shlex.quote(BASE+'/pipeline')+' && docker compose exec -T scheduler airflow dags trigger --run-id '+identifier+' energy_cdc_warehouse',120))
        else:
            print(json.dumps(current,ensure_ascii=False,indent=2))
    finally:
        connection.close()


if __name__ == '__main__':
    main()
