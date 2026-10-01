"""Apply the explicitly approved exact-duplicate plan with backup and locks."""
import argparse
import gzip
import hashlib
import json
import secrets
import subprocess
from datetime import datetime
from decimal import Decimal
import pymysql
from tools.verify_mysql_cdc_probe import ROOT, COMPOSE
from tools.verify_cdc_scheduler_runtime import source_rows


def command(argv):
    return subprocess.run(argv, cwd=ROOT, check=True, capture_output=True,
                          text=True, encoding='utf-8').stdout.strip()


def digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True)
    parser.add_argument('--apply', action='store_true', required=True)
    args = parser.parse_args()
    plan = json.loads((ROOT / args.plan).read_text(encoding='utf-8'))
    if plan['database'] != 'industrial_energy' or plan['table'] != 'fact_energy_consumption' or plan['conflicting_groups']:
        raise ValueError('Unsupported or ambiguous repair plan')
    # The caller must have explicit user authorization before invoking --apply.
    stamp = datetime.now().strftime('%Y%m%d%H%M%S')
    backup = 'industrial_energy_repair_backup_' + stamp
    metadata = ['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'airflow', '-d', 'airflow', '-At', '-c']
    paused = command([*metadata, "SELECT is_paused FROM dag WHERE dag_id='energy_pipeline'"])
    if paused not in ('t', 'f'):
        raise ValueError('Cannot establish main writer DAG state')
    command(['docker', 'compose', 'exec', '-T', 'airflow-scheduler', 'airflow', 'dags', 'pause', 'energy_pipeline'])
    conn = None
    try:
        if command([*metadata, "SELECT COUNT(*) FROM dag_run WHERE dag_id='energy_pipeline' AND state IN ('running','queued')"]) != '0':
            raise ValueError('Main writer has an active run; repair refused')
        password = command([*COMPOSE, 'exec', '-T', 'mysql', 'sh', '-lc', 'printf %s "$MYSQL_ROOT_PASSWORD"'])
        conn = pymysql.connect(host='127.0.0.1', port=3307, user='root', password=password,
                               charset='utf8mb4', autocommit=False)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE WHERE REFERENCED_TABLE_SCHEMA='industrial_energy' AND REFERENCED_TABLE_NAME='fact_energy_consumption'")
            if cur.fetchone()[0]:
                raise ValueError('Inbound foreign keys require a separate repair plan')
            cur.execute("SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE EVENT_OBJECT_SCHEMA='industrial_energy' AND EVENT_OBJECT_TABLE='fact_energy_consumption'")
            if cur.fetchone()[0]:
                raise ValueError('Source triggers require a separate repair plan')
            cur.execute(f'CREATE DATABASE {backup}')
            cur.execute(f'CREATE TABLE {backup}.fact_energy_consumption AS SELECT * FROM industrial_energy.fact_energy_consumption WHERE 1=0')
            # Lock existing rows and their primary-index gaps before checking the source.
            cur.execute('SELECT id FROM industrial_energy.fact_energy_consumption FORCE INDEX(PRIMARY) ORDER BY id FOR UPDATE')
            cur.fetchall()
            before = source_rows('industrial_energy')
            if digest(before) != plan['source_sha256']:
                raise ValueError('Source changed since reviewed plan; repair refused')
            byid = {row['id']: row for row in before}
            remove = []
            for item in plan['repairs']:
                keep = byid[item['keep_id']]
                for identifier in item['redundant_ids']:
                    row = byid[identifier]
                    if {k: v for k, v in row.items() if k != 'id'} != {k: v for k, v in keep.items() if k != 'id'}:
                        raise ValueError('Repair group is no longer identical')
                    remove.append(identifier)
            if len(remove) != len(set(remove)) or len(remove) != plan['redundant_rows']:
                raise ValueError('Invalid removal count')
            cur.execute(f'INSERT INTO {backup}.fact_energy_consumption SELECT * FROM industrial_energy.fact_energy_consumption FORCE INDEX(PRIMARY)')
            cur.execute(f'SELECT COUNT(*) FROM {backup}.fact_energy_consumption')
            if cur.fetchone()[0] != len(before):
                raise ValueError('Full backup row count mismatch')
            export = ROOT / f'output/mysql_cdc_business_repair_backup_{stamp}.json.gz'
            with gzip.open(export, 'xt', encoding='utf-8') as stream:
                json.dump(before, stream, ensure_ascii=False)
            deleted = 0
            for start in range(0, len(remove), 250):
                batch = remove[start:start + 250]
                cur.execute('DELETE FROM industrial_energy.fact_energy_consumption WHERE id IN (' + ','.join(['%s'] * len(batch)) + ')', batch)
                deleted += cur.rowcount
            if deleted != len(remove):
                raise ValueError('Actual deletion count differs from approved plan')
            cur.execute('SELECT COUNT(*),COUNT(DISTINCT record_date,workshop_code,energy_code) FROM industrial_energy.fact_energy_consumption FORCE INDEX(PRIMARY)')
            count, keys = cur.fetchone()
            if count != keys or count != len(before) - deleted:
                raise ValueError('Post-repair uniqueness gate failed')
            expected = [row for row in before if row['id'] not in set(remove)]
            cur.execute('SELECT id,cost,is_deleted FROM industrial_energy.fact_energy_consumption FORCE INDEX(PRIMARY) ORDER BY id')
            actual = cur.fetchall()
            expected_cost = sum((Decimal(row['cost']) for row in expected if not row['is_deleted']), Decimal(0))
            actual_cost = sum((row[1] for row in actual if not row[2]), Decimal(0))
            if [row[0] for row in actual] != [row['id'] for row in expected] or actual_cost != expected_cost:
                raise ValueError('Remaining keys or business cost differ from approved canonical rows')
        conn.commit()
        after = source_rows('industrial_energy')
        if after != expected or source_rows(backup) != before:
            raise RuntimeError('Post-commit source or full backup differs; retain backup for recovery')
        report = {'success': True, 'plan': args.plan, 'backup_database': backup,
                  'backup_archive': export.relative_to(ROOT).as_posix(),
                  'backup_archive_sha256': hashlib.sha256(export.read_bytes()).hexdigest(),
                  'before_rows': len(before), 'deleted_exact_duplicates': deleted,
                  'after_rows': len(after), 'distinct_business_keys': keys,
                  'before_sha256': digest(before), 'after_sha256': digest(after),
                  'canonical_active_cost': str(expected_cost), 'actual_active_cost': str(actual_cost),
                  'remaining_all_fields_unchanged': True, 'full_backup_reconciled': True,
                  'warehouse_cutover': False, 'previous_main_dag_paused': paused == 't'}
        path = ROOT / f'output/mysql_cdc_business_duplicate_repair_{stamp}.json'
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        print('MYSQL_DUPLICATE_REPAIR_PASS', path, flush=True)
    finally:
        if conn:
            conn.rollback()
            conn.close()
        if paused == 'f':
            command(['docker', 'compose', 'exec', '-T', 'airflow-scheduler', 'airflow', 'dags', 'unpause', 'energy_pipeline'])


if __name__ == '__main__':
    main()
