"""Narrow, reviewed MBQL binding for the additive whole-factory cost metric.

Only cost is supported: energy/carbon daily rounding and product ratios cannot
be translated by substituting a raw field. Runtime reconciliation is required.
"""
from copy import deepcopy
import hashlib
import json
from governance.catalog import catalog


def binding():
    config = catalog()
    metric = next(m for m in config['metrics'] if m['id'] == 'total_cost_yuan')
    if (metric['source_query'], metric['column'], metric['aggregation'], metric['unit']) != (
            'Q28', '能源费用_元', 'sum', 'CNY') or metric.get('exclude_process'):
        raise ValueError('Cost binding requires review after semantic definition changes')
    query = next(q for q in config['queries'] if q['id'] == 'Q28')
    return {'metric_id': metric['id'], 'unit': metric['unit'],
            'definition': metric['definition'], 'sql_sha256': query['sql_sha256'],
            'metric_sha256': hashlib.sha256(json.dumps(metric, sort_keys=True,
                ensure_ascii=False).encode()).hexdigest(), 'raw_field': 'cost'}


def compile_card(dataset_query, table_id, cost_field_id):
    """Keep dimensions, filters and UUIDs; replace the governed aggregation."""
    contract = binding()
    result = deepcopy(dataset_query)
    stages = result.get('stages', [])
    if result.get('lib/type') != 'mbql/query' or len(stages) != 1:
        raise ValueError('Only single-stage query-builder cards are supported')
    stage = stages[0]
    if stage.get('source-table') != table_id or stage.get('lib/type') != 'mbql.stage/mbql':
        raise ValueError('Unexpected card source')
    existing = stage.get('aggregation', [])
    if len(existing) != 1 or existing[0][0] != 'sum' or len(existing[0]) != 3:
        raise ValueError('Unexpected aggregation; manual review required')
    field = existing[0][2]
    if len(field) != 3 or field[0] != 'field' or field[2] != cost_field_id:
        raise ValueError('Only the reviewed raw cost field is allowed')
    # MBQL display-name comes from the registry, not an independent formula.
    stage['aggregation'] = [['sum', {**existing[0][1],
        'display-name': 'total_cost_yuan (' + contract['unit'] + ')'}, deepcopy(field)]]
    return result


def description():
    value = binding()
    return ('Managed semantic metric: ' + value['metric_id'] + '\n' + value['definition']
            + '\nQ28 SQL SHA256: ' + value['sql_sha256']
            + '\nMetric SHA256: ' + value['metric_sha256'])
