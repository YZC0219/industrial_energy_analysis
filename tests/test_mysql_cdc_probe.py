"""Contract checks for the isolated CDC projection; live proof needs Docker."""
import json
import sqlite3
from types import SimpleNamespace

from tools.verify_mysql_cdc_probe import consume_until, decode


class Consumer:
    def __init__(self, records):
        self.records = iter(records)

    def poll(self, **_):
        try:
            return {0: [next(self.records)]}
        except StopIteration:
            return {}


def event(offset, key, value):
    return SimpleNamespace(partition=0, offset=offset,
                           key=json.dumps(key).encode(),
                           value=None if value is None else json.dumps(value).encode())


def test_insert_delete_and_tombstone_clear_downstream_state():
    row_id = 42
    marker = "cdc-probe-sample"
    source = {"file": "mysql-bin.000001", "pos": 100, "row": 0}
    records = [
        event(10, {"id": row_id}, {"op": "c", "before": None,
                                   "after": {"id": row_id, "marker": marker},
                                   "source": source}),
        event(11, {"id": row_id}, {"op": "d", "before": {"id": row_id, "marker": marker},
                                   "after": None, "source": {**source, "pos": 200}}),
        event(12, {"id": row_id}, None),
    ]
    state = sqlite3.connect(":memory:")
    state.execute("CREATE TABLE projection (id INTEGER PRIMARY KEY, marker TEXT NOT NULL)")
    seen = []
    consumer = Consumer(records)
    consume_until(consumer, row_id, marker, state, seen, {"insert"}, 2)
    assert state.execute("SELECT marker FROM projection WHERE id=42").fetchone() == (marker,)
    consume_until(consumer, row_id, marker, state, seen,
                  {"insert", "delete", "tombstone"}, 2)
    assert state.execute("SELECT COUNT(*) FROM projection").fetchone() == (0,)
    assert [item["offset"] for item in seen] == [10, 11, 12]
    assert decode(b'{"payload":{"id":42}}') == {"id": 42}
    state.close()


def test_connector_wait_retries_connection_closed_during_startup(monkeypatch):
    from http.client import RemoteDisconnected
    from tools import verify_mysql_cdc_probe as probe

    running = {"connector": {"state": "RUNNING"},
               "tasks": [{"state": "RUNNING"}]}
    responses = iter([RemoteDisconnected("starting"), {},
                      RemoteDisconnected("rebalancing"), running])

    def request(*_):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(probe, "request_json", request)
    monkeypatch.setattr(probe.time, "sleep", lambda _: None)
    assert probe.wait_connector("http://localhost:8083", 2) == running
