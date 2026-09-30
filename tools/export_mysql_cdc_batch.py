"""Freeze one complete Debezium topic and project an ordered business snapshot.

The snapshot retains deleted business keys. Source updated_at remains unchanged;
Binlog coordinates determine versions; Kafka offsets freeze the capture boundary.
No records are published to energy-events.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

FIELDS = ('id', 'record_date', 'workshop_code', 'energy_code', 'consumption',
          'unit', 'unit_price', 'cost', 'record_status', 'avg_temperature',
          'data_source', 'is_production_day', 'updated_at', 'is_deleted')


def unwrap(value):
    if value is None:
        return None
    return value.get('payload', value)


def normalize(image: dict) -> dict:
    if not isinstance(image, dict) or set(FIELDS) - image.keys():
        raise ValueError('CDC image does not contain the complete business fact schema')
    row = {field: image[field] for field in FIELDS}
    if type(row['id']) is not int or row['id'] <= 0:
        raise ValueError('Invalid source primary key')
    day = row['record_date']
    row['record_date'] = ((date(1970, 1, 1) + timedelta(days=day)).isoformat()
                          if type(day) is int else date.fromisoformat(day).isoformat())
    for field, pattern in [('workshop_code', r'W[0-9]{2}'), ('energy_code', r'E[0-9]{2}')]:
        if not isinstance(row[field], str) or not re.fullmatch(pattern, row[field]):
            raise ValueError(f'Invalid {field}')
    for field in ('consumption', 'unit_price', 'cost', 'avg_temperature'):
        if row[field] is None and field == 'avg_temperature':
            continue
        # Debezium must use decimal.handling.mode=string; binary decimals need scale.
        if not isinstance(row[field], str):
            raise ValueError('Configure decimal.handling.mode=string')
        try:
            number = Decimal(row[field])
        except InvalidOperation as exc:
            raise ValueError(f'Invalid decimal {field}') from exc
        if not number.is_finite():
            raise ValueError(f'Non-finite decimal {field}')
        row[field] = str(number)
    if Decimal(row['consumption']) < 0 or Decimal(row['unit_price']) <= 0 or Decimal(row['cost']) < 0:
        raise ValueError('Invalid business measure')
    for field in ('is_deleted', 'is_production_day'):
        if type(row[field]) is not int or row[field] not in (0, 1):
            raise ValueError(f'Invalid flag {field}')
    if not isinstance(row['unit'], str) or not row['unit'] or not row['record_status']:
        raise ValueError('Missing unit/status')
    updated = row['updated_at']
    if updated is not None:
        if type(updated) is int:
            # time.precision.mode=connect stores DATETIME as epoch milliseconds.
            updated = (datetime(1970, 1, 1) + timedelta(milliseconds=updated)).isoformat(' ')
        else:
            updated = datetime.fromisoformat(updated).isoformat(' ')
        row['updated_at'] = updated
    return row


def business_key(row: dict) -> tuple:
    return tuple(row[field] for field in ('record_date', 'workshop_code', 'energy_code'))


def project(records: list[dict], topic: str, end_offset: int) -> dict:
    if not records or [r['offset'] for r in records] != list(range(end_offset)):
        raise ValueError('Expected contiguous complete topic history from offset zero')
    state = {}
    versions = {}
    binlog_versions = {}
    binlog_prefix = None
    id_versions = {}
    id_images = {}
    source_identity = None
    streaming_seen = False
    for record in records:
        if record['topic'] != topic or record['partition'] != 0:
            raise ValueError('Only one ordered source topic partition is supported')
        key = unwrap(record['key'])
        value = unwrap(record['value'])
        if value is None:
            if not isinstance(key, dict) or type(key.get('id')) is not int:
                raise ValueError('Invalid tombstone key')
            continue
        op = value.get('op')
        if op not in ('r', 'c', 'u', 'd'):
            raise ValueError('Unsupported CDC operation')
        source = value.get('source', {})
        identity = (source.get('db'), source.get('table'))
        if not all(identity) or source_identity not in (None, identity):
            raise ValueError('Mixed or missing source identity')
        source_identity = identity
        streaming_seen |= op != 'r'
        if not source.get('file') or type(source.get('pos')) is not int:
            raise ValueError('Missing binlog position')
        file = re.fullmatch(r'(.+)\.([0-9]+)', source['file'])
        if not file or binlog_prefix not in (None, file[1]):
            raise ValueError('Unsupported binlog filename or changed binlog lineage')
        binlog_prefix = file[1]
        row_number = source.get('row')
        row_number = 0 if row_number is None else row_number
        if type(row_number) is not int or row_number < 0 or source['pos'] < 0:
            raise ValueError('Invalid binlog coordinates')
        version = (int(file[2]), source['pos'], row_number, int(op != 'r'))
        before = normalize(value['before']) if value.get('before') is not None else None
        after = normalize(value['after']) if value.get('after') is not None else None
        image = before if op == 'd' else after
        if image is None or not isinstance(key, dict) or key.get('id') != image['id']:
            raise ValueError('Missing or mismatched CDC row image/key')
        if op in ('u', 'd') and before is None:
            raise ValueError('FULL before image is required')
        if op == 'd' and after is not None:
            raise ValueError('Delete must have after=null')
        signature = (op, before, after)
        previous = id_versions.get(image['id'], (-1,))
        if version < previous:
            continue
        if version == previous:
            if id_images[image['id']] != signature:
                raise ValueError('Conflicting images at the same binlog version')
            continue
        id_versions[image['id']] = version
        id_images[image['id']] = signature
        if before is not None and (op == 'd' or business_key(before) != business_key(after)):
            deleted = {**before, 'is_deleted': 1}
            old_key = business_key(deleted)
            if version >= binlog_versions.get(old_key, (-1,)) and old_key in state and state[old_key]['id'] != deleted['id']:
                raise ValueError('Delete does not match business-key owner')
            if version > binlog_versions.get(old_key, (-1,)):
                state[old_key] = deleted
                versions[old_key] = record['offset']
                binlog_versions[old_key] = version
            elif version == binlog_versions.get(old_key) and state[old_key] != deleted:
                raise ValueError('Conflicting images at the same binlog version')
        if op != 'd':
            new_key = business_key(after)
            if version < binlog_versions.get(new_key, (-1,)):
                continue
            if version == binlog_versions.get(new_key):
                if state[new_key] != after:
                    raise ValueError('Conflicting images at the same binlog version')
                continue
            if new_key in state and not state[new_key]['is_deleted'] and state[new_key]['id'] != after['id']:
                raise ValueError('Multiple active source IDs share one business key')
            state[new_key] = after
            versions[new_key] = record['offset']
            binlog_versions[new_key] = version
    # A streaming event proves initial snapshot has ended for an initial-mode connector.
    if not streaming_seen:
        last = unwrap(records[-1]['value'])
        if not last or last.get('source', {}).get('snapshot') not in ('last', True):
            raise ValueError('Initial snapshot completion not established')
        if last.get('source', {}).get('snapshot') is True:
            raise ValueError('Snapshot is still in progress')
    return {'schema_version': 2, 'topic': topic, 'partition': 0, 'start_offset': 0,
            'end_offset': end_offset, 'source_database': source_identity[0],
            'source_table': source_identity[1], 'complete_history': True,
            'ordering': 'binlog file sequence/position/row; Kafka offset freezes capture boundary; source updated_at is not rewritten',
            'rows': [state[key] for key in sorted(state)],
            'row_offsets': [versions[key] for key in sorted(state)],
            'binlog_prefix': binlog_prefix,
            'row_binlog_versions': [list(binlog_versions[key]) for key in sorted(state)]}


def capture(topic: str, bootstrap: str, timeout: int = 120) -> dict:
    from kafka import KafkaConsumer, TopicPartition
    consumer = KafkaConsumer(bootstrap_servers=bootstrap, enable_auto_commit=False,
                             group_id=None, request_timeout_ms=30000)
    try:
        if consumer.partitions_for_topic(topic) != {0}:
            raise ValueError('CDC bridge requires exactly one topic partition')
        ref = TopicPartition(topic, 0)
        consumer.assign([ref])
        start = consumer.beginning_offsets([ref])[ref]
        end = consumer.end_offsets([ref])[ref]
        if start != 0:
            raise ValueError('Retained CDC history is incomplete')
        consumer.seek(ref, 0)
        records = []
        deadline = time.monotonic() + timeout
        while consumer.position(ref) < end:
            if time.monotonic() > deadline:
                raise TimeoutError('Frozen CDC history did not finish')
            for batch in consumer.poll(timeout_ms=1000).values():
                for record in batch:
                    if record.offset < end:
                        records.append({'topic': topic, 'partition': record.partition,
                                        'offset': record.offset, 'key': json.loads(record.key),
                                        'value': None if record.value is None else json.loads(record.value)})
        result = project(records, topic, end)
        result['events'] = records
        result['captured_at_utc'] = datetime.now(timezone.utc).isoformat()
        result['raw_events_sha256'] = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
        return result
    finally:
        consumer.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--topic', required=True)
    p.add_argument('--bootstrap', default='127.0.0.1:29092')
    p.add_argument('--connector', required=True)
    p.add_argument('--connect-url', default='http://127.0.0.1:8083')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError('Refusing to overwrite CDC snapshot')
    from urllib.parse import quote
    from urllib.request import urlopen
    with urlopen(args.connect_url.rstrip('/') + '/connectors/' + quote(args.connector, safe='') + '/config', timeout=15) as response:
        config = json.load(response)
    if (config.get('snapshot.mode') != 'initial'
            or config.get('decimal.handling.mode') != 'string'
            or config.get('time.precision.mode') != 'connect'
            or config.get('table.include.list') != args.topic.removeprefix(config.get('topic.prefix', '') + '.')
            or not args.topic.startswith(config.get('topic.prefix', '') + '.')):
        raise ValueError('Connector must use initial snapshot, string decimals, connect timestamps, and the exact source table')
    result = capture(args.topic, args.bootstrap)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8', newline='\n') as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
        out.write('\n')
    print(f'CDC_BUSINESS_SNAPSHOT PASS rows={len(result["rows"])} output={args.output}')


if __name__ == '__main__':
    main()
