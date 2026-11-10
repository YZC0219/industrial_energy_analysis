"""Archive project data files to MySQL without changing business fact tables.
Default is inventory only; --apply writes immutable, chunked file versions.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
import pymysql

CHUNK = 1024 * 1024
SUFFIXES = {'.zip', '.avro', '.csv', '.json', '.jsonl', '.xml', '.parquet', '.xlsx', '.xls', '.db', '.sqlite', '.sqlite3', '.html', '.pdf', '.png', '.svg', '.pkl', '.joblib', '.txt', '.md', '.yaml', '.yml'}
DDL = [
'''CREATE TABLE IF NOT EXISTS project_data_versions (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 source_path VARCHAR(768) NOT NULL, path_hash CHAR(64) NOT NULL,
 content_hash CHAR(64) NOT NULL, byte_size BIGINT UNSIGNED NOT NULL,
 source_format VARCHAR(24) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 UNIQUE KEY version_identity(path_hash,content_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS project_data_chunks (
 version_id BIGINT UNSIGNED NOT NULL, chunk_no INT UNSIGNED NOT NULL,
 content MEDIUMBLOB NOT NULL, PRIMARY KEY(version_id,chunk_no),
 FOREIGN KEY(version_id) REFERENCES project_data_versions(id)
) ENGINE=InnoDB''']


def inventory():
    files, skipped = [], []
    for directory in ('data', 'output'):
        for path in sorted((ROOT / directory).rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT).as_posix()
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()) or path.suffix.lower() not in SUFFIXES:
                skipped.append(relative)
                continue
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(CHUNK), b''):
                    digest.update(block)
            files.append({'path': relative, 'size': path.stat().st_size,
                          'sha256': digest.hexdigest(), 'format': path.suffix[1:].lower()})
    return files, skipped


def archive(conn, item):
    path = ROOT / item['path']
    path_hash = hashlib.sha256(item['path'].encode()).hexdigest()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM project_data_versions WHERE path_hash=%s AND content_hash=%s',
                        (path_hash, item['sha256']))
            if cur.fetchone():
                conn.rollback()
                return 'existing'
            cur.execute('INSERT INTO project_data_versions(source_path,path_hash,content_hash,byte_size,source_format) VALUES (%s,%s,%s,%s,%s)',
                        (item['path'], path_hash, item['sha256'], item['size'], item['format']))
            ident = cur.lastrowid
            digest, size = hashlib.sha256(), 0
            with path.open('rb') as stream:
                for number, block in enumerate(iter(lambda: stream.read(CHUNK), b'')):
                    digest.update(block)
                    size += len(block)
                    cur.execute('INSERT INTO project_data_chunks VALUES (%s,%s,%s)', (ident, number, block))
            if digest.hexdigest() != item['sha256'] or size != item['size']:
                raise ValueError('File changed during import: ' + item['path'])
            cur.execute('SELECT content FROM project_data_chunks WHERE version_id=%s ORDER BY chunk_no', (ident,))
            restored = hashlib.sha256()
            while True:
                row = cur.fetchone()
                if row is None:
                    break
                restored.update(row[0])
            if restored.hexdigest() != item['sha256']:
                raise ValueError('Database checksum mismatch: ' + item['path'])
        conn.commit()
        return 'inserted'
    except Exception:
        conn.rollback()
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--manifest', type=Path, default=ROOT / 'output' / 'database_archive_manifest.json')
    args = ap.parse_args()
    load_dotenv(ROOT / '.env')
    files, skipped = inventory()
    # The manifest is operational evidence, not an input to its own archive.
    files = [x for x in files if (ROOT / x['path']).resolve() != args.manifest.resolve()]
    report = {'mode': 'apply' if args.apply else 'inventory', 'files': files, 'skipped': skipped,
              'file_count': len(files), 'bytes': sum(x['size'] for x in files), 'inserted': 0, 'existing': 0}
    failure = False
    if args.apply:
        conn = None
        try:
            conn = pymysql.connect(host=os.getenv('MYSQL_HOST', '127.0.0.1'),
                port=int(os.getenv('MYSQL_PORT', '3307')), user=os.getenv('MYSQL_USER', 'root'),
                password=os.getenv('MYSQL_PWD', 'energy_root_pwd'), database=os.getenv('MYSQL_DB', 'industrial_energy'),
                charset='utf8mb4', connect_timeout=5, read_timeout=60, write_timeout=60)
            with conn.cursor() as cur:
                for statement in DDL:
                    cur.execute(statement)
            for item in files:
                result = archive(conn, item)
                report[result] += 1
            report['status'] = 'complete'
        except pymysql.MySQLError as exc:
            report['status'] = 'failed'
            report['mysql_error_code'] = exc.args[0]
            failure = True
        finally:
            if conn:
                conn.close()
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('files', 'skipped')}, ensure_ascii=False))
    return int(failure)

if __name__ == '__main__':
    raise SystemExit(main())
