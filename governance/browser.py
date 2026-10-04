"""Compile reviewed scalar definitions into an offline browser contract."""
import hashlib
import json
from governance.catalog import catalog

FIELDS = {'综合能耗_tce': 'tce', '能源费用_元': 'cost', '碳排放_tCO2': 'co2', '产量': 'qty'}


def contract():
    config = catalog()
    metrics = []
    for metric in config['metrics']:
        if metric['aggregation'] == 'sql_result':
            continue
        if metric['source_query'] != 'Q28' or metric['aggregation'] not in ('sum', 'ratio_of_sums'):
            raise ValueError('Browser metric requires reviewed additive daily scope')
        value = {k: v for k, v in metric.items() if k in
                 ('id', 'unit', 'aggregation', 'scale', 'exclude_process', 'scope', 'definition')}
        value['field'] = FIELDS[metric['column']]
        if metric.get('denominator'):
            value['denominator_field'] = FIELDS[metric['denominator']]
        metrics.append(value)
    query = next(q for q in config['queries'] if q['id'] == 'Q28')
    result = {'version': config['version'], 'source_sql_sha256': query['sql_sha256'], 'metrics': metrics}
    result['sha256'] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result
