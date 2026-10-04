"""Check/apply the two existing BI cost cards with backup and guarded rollback.

No new database grants, tables or dashboards. Credentials and sessions stay in
memory. Run --apply to update, --restore PATH to restore the saved card payloads.
"""
import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import uuid
import pymysql
from governance.catalog import canonical_sql, catalog
from governance.metabase import binding, compile_card, description
from tools.verify_bi_runtime import api, credentials, expect, verify

ROOT = Path(__file__).resolve().parents[1]


def money(value):
    return Decimal(str(value)).quantize(Decimal('0.01'))


def run(base, apply=False, restore=None):
    cfg = credentials(ROOT / '.env')
    status, session = api(base, 'session', method='POST', payload={
        'username': cfg['METABASE_ADMIN_EMAIL'], 'password': cfg['METABASE_ADMIN_PASSWORD']})
    expect(status, 200, 'admin login')
    token = session['id']
    def call(path, method='GET', payload=None):
        status, result = api(base, path, token=token, method=method, payload=payload)
        if not (method == 'POST' and (path == 'dataset' or path.endswith('/query')) and status == 202):
            expect(status, 200, path)
        return result
    try:
        databases = call('database')['data']
        database = next(d for d in databases if d['name'] == 'Industrial Energy (read-only)')
        metadata = call(f"database/{database['id']}/metadata")
        table = next(t for t in metadata['tables'] if t['name'] == 'v_energy_enriched')
        fields = {f['name']: f['id'] for f in table['fields']}
        dashboard = next(d for d in call('dashboard') if d['name'] == '工业能耗分析')
        dashboard = call(f"dashboard/{dashboard['id']}")
        cards = [call(f"card/{d['card_id']}") for d in dashboard['dashcards'] if d.get('card_id')]
        if len(cards) != 2:
            raise ValueError('Expected exactly two existing energy cards')
        if restore:
            backup = json.loads(Path(restore).read_text(encoding='utf-8'))
            if backup['database_id'] != database['id'] or backup['dashboard_id'] != dashboard['id']:
                raise ValueError('Backup belongs to another dashboard/database')
            if {c['id'] for c in backup['cards']} != {c['id'] for c in cards}:
                raise ValueError('Backup card IDs differ')
            for old in backup['cards']:
                call(f"card/{old['id']}", 'PUT', old['payload'])
            return {'restored': True, 'cards': len(cards)}
        contract = binding()
        plans = []
        dimensions = set()
        for card in cards:
            query = compile_card(card['dataset_query'], table['id'], fields['cost'])
            breakout = query['stages'][0].get('breakout', [])
            if len(breakout) != 1 or breakout[0][0] != 'field':
                raise ValueError('Unexpected chart dimension')
            field_id = breakout[0][2]
            dimension = next((n for n in ('workshop_code', 'record_date') if fields[n] == field_id), None)
            if not dimension or query['stages'][0].get('filters'):
                raise ValueError('Unsupported dimension or fixed scope filter')
            dimensions.add(dimension)
            plans.append((card, {'dataset_query': query, 'description': description()}, dimension))
        if dimensions != {'workshop_code', 'record_date'}:
            raise ValueError('Expected workshop ranking and daily trend')
        # Verify every workshop/day at one read-only consistent snapshot before writes.
        connection = pymysql.connect(host='127.0.0.1', port=3307, user='root',
            password=cfg['MYSQL_ROOT_PASSWORD'], database='industrial_energy',
            cursorclass=pymysql.cursors.DictCursor)
        try:
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                cursor.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
                query = next(q for q in catalog()['queries'] if q['id'] == 'Q28')
                cursor.execute(canonical_sql()[query['artifact'][:-4]])
                daily = cursor.fetchall()
                cursor.execute('SELECT record_date, workshop_code, SUM(cost) cost FROM v_energy_enriched GROUP BY record_date, workshop_code')
                raw = {(str(r['record_date']), r['workshop_code']): money(r['cost']) for r in cursor.fetchall()}
                canonical = {(str(r['日期']), r['车间编码']): money(r['能源费用_元']) for r in daily}
                if not canonical or canonical != raw:
                    raise AssertionError('Raw cost binding differs from canonical Q28 daily totals')
        finally:
            connection.rollback()
            connection.close()
        folder = ROOT / 'output/extensions' / ('metabase_semantic_' + uuid.uuid4().hex[:12])
        folder.mkdir(parents=True)
        backup = {'database_id': database['id'], 'dashboard_id': dashboard['id'],
            'cards': [{'id': c['id'], 'payload': {'dataset_query': c['dataset_query'],
                       'description': c.get('description')}} for c in cards]}
        (folder / 'backup.json').write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding='utf-8')
        (folder / 'plan.json').write_text(json.dumps([{'id': c['id'], 'payload': p} for c,p,_ in plans], ensure_ascii=False, indent=2), encoding='utf-8')
        checks = []
        attempted = []
        try:
            if apply:
                for card, payload, _ in plans:
                    current = call(f"card/{card['id']}")
                    if current['dataset_query'] != card['dataset_query'] or current.get('description') != card.get('description'):
                        raise ValueError('Card changed during preflight; rerun to avoid overwriting concurrent edits')
                    attempted.append(card['id'])
                    call(f"card/{card['id']}", 'PUT', payload)
            for card, payload, dimension in plans:
                saved = call(f"card/{card['id']}")
                if apply and (saved['description'] != payload['description'] or
                              compile_card(saved['dataset_query'], table['id'], fields['cost']) != saved['dataset_query']):
                    raise AssertionError('Saved semantic contract did not persist')
                actual = call(f"card/{card['id']}/query", 'POST', {})
                if actual.get('status') != 'completed':
                    raise AssertionError('Saved card execution failed')
                for scope in (None, 'W04', 'W01'):
                    data_query = deepcopy(saved['dataset_query'] if apply else payload['dataset_query'])
                    selected = daily
                    if scope:
                        selected = [r for r in daily if r['车间编码'] == scope and '2025-01-01' <= str(r['日期']) <= '2025-01-31']
                        stage = data_query['stages'][0]
                        stage['filters'] = [
                            ['=', {'lib/uuid': str(uuid.uuid4())}, ['field', {'lib/uuid': str(uuid.uuid4())}, fields['workshop_code']], scope],
                            ['between', {'lib/uuid': str(uuid.uuid4())}, ['field', {'lib/uuid': str(uuid.uuid4())}, fields['record_date']], '2025-01-01', '2025-01-31']]
                    result = call('dataset', 'POST', data_query) if scope or not apply else actual
                    if result.get('status') != 'completed':
                        raise AssertionError('Card query failed')
                    expected = defaultdict(Decimal)
                    for row in selected:
                        key = str(row['日期']) if dimension == 'record_date' else row['车间编码']
                        expected[key] += money(row['能源费用_元'])
                    observed = {str(r[0])[:10] if dimension == 'record_date' else str(r[0]): money(r[1]) for r in result['data']['rows']}
                    if dict(expected) != observed or len(observed) != len(result['data']['rows']):
                        raise AssertionError('Metabase chart differs from canonical semantic cost')
                    checks.append({'card_id': card['id'], 'dimension': dimension, 'workshop': scope,
                                   'groups': len(observed), 'cost_yuan': str(sum(expected.values())), 'passed': True})
            permissions = verify(base, ROOT / '.env')
        except Exception:
            # Include the currently attempted PUT: a lost HTTP response may follow a successful write.
            for old in backup['cards']:
                if old['id'] in attempted:
                    call(f"card/{old['id']}", 'PUT', old['payload'])
            raise
        report = {'success': True, 'applied': apply, 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
            'contract': contract, 'canonical_daily_groups': len(canonical), 'checks': checks,
            'permissions': permissions, 'backup': str(folder / 'backup.json'),
            'source_hashes': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in
                ('governance/metabase.py', 'governance/metrics.yaml', 'tools/migrate_metabase_semantics.py')}}
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        (ROOT / 'output/extensions/metabase_semantic_runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'success': True, 'applied': apply, 'daily_groups': len(canonical), 'chart_checks': len(checks), 'report': str(folder / 'report.json')}
    finally:
        api(base, 'session', token=token, method='DELETE')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:3000')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--apply', action='store_true')
    actions.add_argument('--restore', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.base_url, args.apply, args.restore), ensure_ascii=False))
