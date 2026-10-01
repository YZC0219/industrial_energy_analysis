"""Verify importer replay in a fresh database; existing source rows stay untouched."""
import csv
import gzip
import json
import secrets
from collections import defaultdict
from datetime import datetime
import pymysql
from src import import_mysql as importer
from tools.verify_mysql_cdc_probe import ROOT, mysql


def main():
    stamp = datetime.now().strftime('%Y%m%d%H%M%S')
    db = 'industrial_energy_import_probe_' + stamp
    user = 'import_probe_' + stamp
    password = secrets.token_hex(24)
    source = ROOT / 'output/mysql_cdc_business_main_raw_20261001225051.json.gz'
    with gzip.open(source, 'rt', encoding='utf-8') as stream:
        document = json.load(stream)
    groups = defaultdict(list)
    for row in document['source_rows_by_id']:
        groups[tuple(row[k] for k in ('record_date', 'workshop_code', 'energy_code'))].append(row)
    duplicates = [rows for rows in groups.values() if len(rows) > 1]
    identical = sum(len({json.dumps({k: v for k, v in row.items() if k != 'id'},
                                   sort_keys=True, ensure_ascii=False) for row in rows}) == 1
                    for rows in duplicates)
    work = ROOT / 'tmp' / ('import_replay_' + stamp)
    work.mkdir()
    rows = [values[0] for values in list(groups.values())[:2]]
    with (work / 'facts.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=importer.ENERGY_COLS, extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    mysql(f"CREATE DATABASE {db}; CREATE TABLE {db}.fact_energy_consumption LIKE industrial_energy.fact_energy_consumption; "
          f"CREATE USER '{user}'@'%' IDENTIFIED BY '{password}'; GRANT ALL ON {db}.* TO '{user}'@'%';")
    conn = pymysql.connect(host='127.0.0.1', port=3307, user=user, password=password,
                           database=db, charset='utf8mb4', local_infile=True)
    original = importer.OUT_DIR
    counts = []
    try:
        importer.OUT_DIR = str(work)
        for upsert in (False, False, True, True):
            counts.append(importer.load_csv(conn, 'fact_energy_consumption', 'facts.csv',
                                           importer.ENERGY_COLS, db, upsert=upsert))
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*),COUNT(DISTINCT record_date,workshop_code,energy_code) FROM fact_energy_consumption')
            total, keys = cur.fetchone()
        if counts != [2, 2, 2, 2] or total != keys:
            raise ValueError('Replayed import produced duplicate business rows')
    finally:
        importer.OUT_DIR = original
        conn.close()
    report = {'success': True, 'isolated_database': db, 'load_counts': counts,
              'scope': 'real importer LOAD DATA / upsert replay; fresh database only',
              'source_archive': source.relative_to(ROOT).as_posix(),
              'source_rows': len(document['source_rows_by_id']), 'distinct_business_keys': len(groups),
              'duplicate_groups': len(duplicates), 'groups_identical_except_id': identical,
              'original_source_mutated': False,
              'cause_limit': 'importer disabled UNIQUE_CHECKS; historical cause cannot be proven from current rows alone'}
    output = ROOT / f'output/mysql_cdc_business_import_replay_{stamp}.json'
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print('MYSQL_IMPORT_REPLAY_PASS', output)


if __name__ == '__main__':
    main()
