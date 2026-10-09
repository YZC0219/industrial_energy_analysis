"""Prepare, inspect, activate or replay standalone report versions; no deployments."""
import argparse
import json
from pathlib import Path
import sys

from governance import report_delivery as delivery
from governance.analysis_formulas import contract
from governance.catalog import catalog
from tools.build_analysis_report import ROOT, hashes
from src import make_report

DEFAULT = ROOT / 'output/extensions/report_delivery'
OPTIONAL = ('clean_energy.csv', 'clean_production.csv', 'dim_calendar.csv',
            'clean_rejects.csv', 'clean_fixed.csv', 'pandas_daily.csv',
            'ml_model_metrics.json', 'ml_deep_metrics.json', 'engine_comparison.json',
            'lakehouse_validation.json', 'attribution_eval.json')


def source_bytes(source):
    required = {entry['artifact'] for entry in catalog()['queries']}
    result = {}
    for name in sorted(required | set(OPTIONAL)):
        path = source / name
        if path.exists():
            result['inputs/' + name] = path.read_bytes()
        elif name in required:
            raise ValueError('Missing report input: ' + name)
    raw = ROOT / 'data/raw_energy_data.csv'
    if raw.exists():
        result['inputs/raw/raw_energy_data.csv'] = raw.read_bytes()
    return result


def build(root=DEFAULT):
    root = Path(root)
    source_hashes = {**hashes(), **{name: delivery.digest((ROOT/name).read_bytes()) for name in
                                  ('tools/report_delivery.py', 'governance/report_delivery.py')}}
    metadata_path = ROOT / 'output/extensions/semantic_runtime.json'
    metadata_bytes = metadata_path.read_bytes()
    accepted = json.loads(metadata_bytes)
    source = (ROOT / accepted['evidence_directory']).resolve()
    if not accepted.get('success') or not source.is_relative_to((ROOT/'output/extensions').resolve()):
        raise ValueError('Unverified report input directory')
    inputs = source_bytes(source)
    release = delivery.reserve_version(root)
    folder = delivery.version_folder(root, release)
    for name, value in inputs.items():
        delivery.write_new(delivery.safe_file(folder, name), value)
    # Render exactly the captured bytes, including optional evidence, through the original validated CSV readers.
    old_read = make_report.read
    raw_path = str(ROOT / 'data/raw_energy_data.csv')
    def snapshot_read(name):
        return old_read(str(folder/'inputs/raw/raw_energy_data.csv') if str(name) == raw_path else name)
    prior = make_report.OUT_DIR
    try:
        make_report.OUT_DIR = str(folder/'inputs')
        make_report.read = snapshot_read
        data = make_report.build()
        html = make_report.render(data)
    finally:
        make_report.OUT_DIR = prior
        make_report.read = old_read
    if source_bytes(source) != inputs or metadata_path.read_bytes() != metadata_bytes:
        raise ValueError('Report inputs changed during preparation; no version published')
    current_hashes = {**hashes(), **{name: delivery.digest((ROOT/name).read_bytes()) for name in
                                   ('tools/report_delivery.py', 'governance/report_delivery.py')}}
    if current_hashes != source_hashes:
        raise ValueError('Report source changed during preparation; no version published')
    artifacts = {**inputs, 'report.html': html.encode('utf-8'),
                 # Preserve insertion order: canonical rendering embeds JSON in this same order.
                 'report_data.json': json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8'),
                 'analysis_contract.json': delivery.encode(contract()), 'source_record.json': metadata_bytes}
    return delivery.complete_version(root, release, artifacts, {'source_hashes': source_hashes,
        'source_input_directory': str(source), 'source_record_sha256': delivery.digest(metadata_bytes),
        'formula_definitions': len(contract()['formulas']),
        'scope': 'standalone report versions only; existing deployment, root report and API snapshot unchanged'})


def replay(root, release, output):
    if Path(output).resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Replay output must be outside the report version store')
    version = delivery.verify_version(root, release)
    folder = Path(version['report']).parent
    recorded = version['manifest']['metadata']['source_hashes']
    if any(delivery.digest((ROOT/name).read_bytes()) != digest for name,digest in recorded.items()):
        raise ValueError('Replay requires the recorded source version; archived HTML remains usable')
    html = make_report.render(json.loads((folder/'report_data.json').read_bytes()))
    if delivery.digest(html.encode('utf-8')) != version['manifest']['files']['report.html']:
        raise ValueError('Replayed HTML differs from the archived report')
    # New output only: a replay must never overwrite a version or an already accepted report.
    delivery.write_new(Path(output), html.encode('utf-8'))
    return str(output)


def main():
    parser = argparse.ArgumentParser(description='报告版本归档、核验和独立指针切换')
    parser.add_argument('--root', type=Path, default=DEFAULT)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('build')
    commands.add_parser('current')
    for command in ('verify', 'activate'):
        commands.add_parser(command).add_argument('release')
    replay_parser = commands.add_parser('replay')
    replay_parser.add_argument('release'); replay_parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'build':
        release = build(args.root)
        result = delivery.verify_version(args.root, release)
    elif args.command == 'current':
        result = delivery.current(args.root)
    elif args.command == 'verify':
        result = delivery.verify_version(args.root, args.release)
    elif args.command == 'activate':
        prior = delivery.current(args.root)
        result = delivery.activate(args.root, args.release, prior['publication'] if prior else None)
    else:
        result = {'report': replay(args.root, args.release, args.output)}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
