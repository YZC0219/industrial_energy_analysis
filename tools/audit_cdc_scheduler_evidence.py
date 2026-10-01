"""Reconcile archived scheduled runs, immutable inputs and current source rows."""
import argparse
import hashlib
import json
from pathlib import Path
from tools.verify_cdc_scheduler_runtime import source_rows, ROOT
from tools.export_mysql_cdc_batch import project


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    evidence = json.loads(args.evidence.read_text(encoding='utf-8'))
    if not evidence['success'] or len(evidence['runs']) != 2:
        raise ValueError('Two successful scheduler runs are required')
    hashes = {}
    for item in evidence['runs']:
        snapshot = ROOT / item['snapshot']
        doc = json.loads(snapshot.read_text(encoding='utf-8'))
        rebuilt = project(doc['events'], doc['topic'], doc['end_offset'])
        if any(doc.get(k) != value for k, value in rebuilt.items()):
            raise ValueError('Archived snapshot is inconsistent with source events')
        report = json.loads((ROOT / item['report']).read_text(encoding='utf-8'))
        digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        if report['source_snapshot_sha256'] != digest or not report['success']:
            raise ValueError('Warehouse input checksum does not match archived snapshot')
        hashes[item['snapshot']] = digest
    actual = source_rows(evidence['source_database'])
    if [row for row in doc['rows'] if not row['is_deleted']] != actual:
        raise ValueError('Final scheduled snapshot differs from complete MySQL source rows')
    if len(actual) != 1 or actual[0]['id'] != 2:
        raise ValueError('Physical deletion was not preserved in the source')
    result = {'success': True, 'scheduler_evidence': str(args.evidence),
              'runtime_git_sha': evidence['git_sha'], 'snapshot_sha256': hashes,
              'final_source_rows': actual, 'final_source_reconciliation': True,
              'scope': 'isolated scheduled DAG; initial populated snapshot and final source reconciliation'}
    with args.output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print('CDC_SCHEDULER_EVIDENCE_AUDIT_PASS')


if __name__ == '__main__':
    main()
