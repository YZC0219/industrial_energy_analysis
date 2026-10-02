"""Archive and audit the successful main cutover without changing warehouse data."""
import argparse
import gzip
import hashlib
import json
import subprocess
from tools.verify_mysql_cdc_probe import ROOT
from tools.verify_cdc_scheduler_runtime import source_rows
from tools.export_mysql_cdc_batch import project


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--evidence', required=True)
    p.add_argument('--stamp', required=True)
    args = p.parse_args()
    import re
    if not re.fullmatch(r'[0-9]{14}', args.stamp):
        raise ValueError('Invalid archive timestamp')
    evidence = json.loads((ROOT / args.evidence).read_text(encoding='utf-8'))
    if not evidence['success']:
        raise ValueError('Only a successful cutover can be archived')
    snapshot = ROOT / evidence['snapshot']
    raw = snapshot.read_bytes()
    doc = json.loads(raw)
    rebuilt = project(doc['events'], doc['topic'], doc['end_offset'])
    if any(doc[k] != value for k, value in rebuilt.items()):
        raise ValueError('Snapshot differs from complete binlog history')
    if sorted(doc['rows'], key=lambda row: row['id']) != source_rows('industrial_energy'):
        raise ValueError('Current main source differs from scheduled snapshot')
    report = json.loads((ROOT / evidence['warehouse_report']).read_text(encoding='utf-8'))
    digest = hashlib.sha256(raw).hexdigest()
    if report['source_snapshot_sha256'] != digest:
        raise ValueError('Warehouse input checksum differs')
    archive = ROOT / f'output/mysql_cdc_business_main_input_{args.stamp}.json.gz'
    with gzip.open(archive, 'xb') as stream:
        stream.write(raw)
    for path, expected in report['sql_sha256'].items():
        blob = subprocess.check_output(['git', 'show', evidence['git_sha'] + ':' + path], cwd=ROOT)
        if hashlib.sha256(blob).hexdigest() != expected:
            raise ValueError('Runtime SQL differs from pinned Git revision')
    result = {'success': True, 'cutover_evidence': args.evidence, 'runtime_git_sha': evidence['git_sha'],
              'snapshot_archive': archive.relative_to(ROOT).as_posix(),
              'snapshot_archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
              'uncompressed_input_sha256': digest, 'uncompressed_path': evidence['snapshot'],
              'complete_history_rebuilt': True, 'all_source_fields_reconciled': True,
              'production_sql_git_hashes_verified': True, 'source_rows': len(doc['rows'])}
    path = ROOT / f'output/mysql_cdc_business_main_cutover_audit_{args.stamp}.json'
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print('MAIN_CDC_CUTOVER_ARCHIVE_AUDIT_PASS', path)


if __name__ == '__main__':
    main()
