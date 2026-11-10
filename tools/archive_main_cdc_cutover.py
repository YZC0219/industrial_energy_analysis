"""Archive and audit the successful main cutover without changing warehouse data."""
import argparse
import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path
import subprocess
from tools.verify_mysql_cdc_probe import ROOT
from tools.verify_cdc_scheduler_runtime import source_rows
from tools.export_mysql_cdc_batch import project


def publish_archives(files):
    """Publish complete files without replacing existing evidence; roll back our links."""
    published = []
    try:
        for staged, destination in files:
            # Same-volume hard links provide atomic, exclusive publication on Windows/Linux.
            os.link(staged, destination)
            published.append((staged, destination))
    except BaseException:
        for staged, destination in reversed(published):
            # Never remove a file another process has replaced since publication.
            if destination.exists() and os.path.samefile(staged, destination):
                destination.unlink()
        raise


def prepare_archive(evidence, evidence_path, stamp, staging):
    """Complete every audit before staging the immutable archive and success receipt."""
    import re
    if not re.fullmatch(r'[0-9]{14}', stamp):
        raise ValueError('Invalid archive timestamp')
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
    archive = ROOT / f'output/mysql_cdc_business_main_input_{stamp}.json.gz'
    for path, expected in report['sql_sha256'].items():
        blob = subprocess.check_output(['git', 'show', evidence['git_sha'] + ':' + path], cwd=ROOT)
        if hashlib.sha256(blob).hexdigest() != expected:
            raise ValueError('Runtime SQL differs from pinned Git revision')
    staged_archive = staging / archive.name
    with gzip.open(staged_archive, 'xb') as stream:
        stream.write(raw)
    result = {'success': True, 'cutover_evidence': evidence_path, 'runtime_git_sha': evidence['git_sha'],
              'snapshot_archive': archive.relative_to(ROOT).as_posix(),
              'snapshot_archive_sha256': hashlib.sha256(staged_archive.read_bytes()).hexdigest(),
              'uncompressed_input_sha256': digest, 'uncompressed_path': evidence['snapshot'],
              'complete_history_rebuilt': True, 'all_source_fields_reconciled': True,
              'production_sql_git_hashes_verified': True, 'source_rows': len(doc['rows'])}
    path = ROOT / f'output/mysql_cdc_business_main_cutover_audit_{stamp}.json'
    staged_audit = staging / path.name
    with staged_audit.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return [(staged_archive, archive), (staged_audit, path)]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--evidence', required=True)
    p.add_argument('--stamp', required=True)
    args = p.parse_args()
    evidence = json.loads((ROOT / args.evidence).read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='.cdc-archive-', dir=ROOT / 'output') as directory:
        files = prepare_archive(evidence, args.evidence, args.stamp, Path(directory))
        publish_archives(files)
    print('MAIN_CDC_CUTOVER_ARCHIVE_AUDIT_PASS', files[-1][1])


if __name__ == '__main__':
    main()
