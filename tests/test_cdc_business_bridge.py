"""Business CDC ordering checks; live MySQL/Spark evidence is archived separately."""
from copy import deepcopy

import pytest

from tools.export_mysql_cdc_batch import normalize, project

TOPIC = 'probe.db.fact_energy_consumption'
ROW = dict(id=1, record_date='2024-03-15', workshop_code='W01', energy_code='E01',
           consumption='1.000', unit='unit', unit_price='10.0000', cost='10.00',
           record_status='normal', avg_temperature='20.00', data_source='probe',
           is_production_day=1, updated_at=1767085200000, is_deleted=0)


def event(offset, op, before=None, after=None, *, pos=None, file='binlog.000001'):
    return dict(topic=TOPIC, partition=0, offset=offset, key={'id': 1},
                value=None if op is None else dict(op=op, before=before, after=after,
                    source=dict(db='db', table='fact_energy_consumption', file=file,
                                pos=pos if pos is not None else offset * 100 + 100, row=0)))


def deletion_history():
    updated = {**ROW, 'cost': '17.00', 'consumption': '1.700'}
    return [event(0, 'c', after=ROW), event(1, 'u', before=ROW, after=updated),
            event(2, 'd', before=updated), event(3, None)]


def test_physical_delete_preserves_timestamp_and_uses_binlog_order():
    records = deletion_history()
    # A restarted connector re-emits an earlier insert at a later Kafka offset.
    records.append(event(4, 'c', after=ROW, pos=100))
    snapshot = project(records, TOPIC, 5)
    assert snapshot['rows'][0]['is_deleted'] == 1
    assert snapshot['rows'][0]['cost'] == '17.00'
    assert snapshot['rows'][0]['updated_at'] == '2025-12-30 09:00:00'
    assert snapshot['row_offsets'] == [2]


def test_business_key_move_retains_old_key_tombstone():
    moved = {**ROW, 'record_date': '2024-03-16'}
    records = [event(0, 'c', after=ROW), event(1, 'u', before=ROW, after=moved),
               event(2, 'c', after=ROW, pos=100)]
    rows = project(records, TOPIC, 3)['rows']
    assert [row['is_deleted'] for row in rows] == [1, 0]


def test_binlog_rotation_uses_numeric_file_sequence():
    records = deletion_history()[:2]
    records.append(event(2, 'd', before=records[1]['value']['after'], pos=4, file='binlog.000002'))
    assert project(records, TOPIC, 3)['rows'][0]['is_deleted'] == 1


def test_incomplete_history_or_conflicting_source_version_is_rejected():
    with pytest.raises(ValueError, match='complete topic history'):
        project(deletion_history()[1:], TOPIC, 4)
    records = [event(0, 'c', after=ROW), event(1, 'c', after={**ROW, 'cost': '99.00'}, pos=100)]
    with pytest.raises(ValueError, match='Conflicting images'):
        project(records, TOPIC, 2)


def test_binary_decimal_and_missing_before_image_fail_closed():
    with pytest.raises(ValueError):
        normalize({**ROW, 'cost': 'aGVsbG8='})
    with pytest.raises(ValueError, match='row image'):
        project([event(0, 'd')], TOPIC, 1)
    records = deepcopy(deletion_history())
    records[1]['value']['before'] = None
    with pytest.raises(ValueError, match='FULL before'):
        project(records, TOPIC, 4)
