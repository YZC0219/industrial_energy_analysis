from copy import deepcopy
import pytest
from governance.metabase import compile_card, binding


def card():
    return {'lib/type': 'mbql/query', 'database': 2, 'stages': [{
        'lib/type': 'mbql.stage/mbql', 'source-table': 9,
        'aggregation': [['sum', {'lib/uuid': 'a'}, ['field', {'lib/uuid': 'b'}, 90]]],
        'breakout': [['field', {'lib/uuid': 'c'}, 80]],
        'filters': [['=', {}, ['field', {}, 80], 'W04']]}]}


def test_compiler_keeps_drill_dimensions_scope_and_input():
    source = card()
    original = deepcopy(source)
    compiled = compile_card(source, 9, 90)
    assert source == original
    assert compiled['stages'][0]['breakout'] == source['stages'][0]['breakout']
    assert compiled['stages'][0]['filters'] == source['stages'][0]['filters']
    assert compiled['stages'][0]['aggregation'][0][1]['display-name'] == 'total_cost_yuan (CNY)'
    assert len(binding()['sql_sha256']) == 64


@pytest.mark.parametrize('change', ['source', 'field', 'ratio', 'stage'])
def test_unreviewed_sql_shape_is_rejected(change):
    query = card()
    if change == 'source':
        query['stages'][0]['source-table'] = 10
    elif change == 'field':
        query['stages'][0]['aggregation'][0][2][2] = 94
    elif change == 'ratio':
        query['stages'][0]['aggregation'][0][0] = 'avg'
    else:
        query['stages'].append(deepcopy(query['stages'][0]))
    with pytest.raises(ValueError):
        compile_card(query, 9, 90)
