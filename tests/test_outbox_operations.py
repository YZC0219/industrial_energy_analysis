import json
import pytest
from streaming.alert_outbox import Outbox
from streaming.outbox_inspect import inspect_queue
from streaming.outbox_supervisor import command_from_config, supervise


def test_inspect_is_read_only_and_filters_failures(tmp_path):
    path = tmp_path / "queue.sqlite"
    queue = Outbox(path, "http://localhost")
    ident = queue.enqueue({"private_payload": "must not appear in diagnostics"})
    claim = queue.claim()
    queue.finish(claim, "HTTP 400", retryable=False)
    queue.db.close()
    before = path.read_bytes()
    report = inspect_queue(path, "dead", ident, limit=2)
    assert path.read_bytes() == before
    assert report["counts"] == {"dead": 1} and report["matched_count"] == 1
    assert report["messages"][0]["error"] == "HTTP 400"
    assert report["audit"][0]["event"] == "dead"
    assert len(report["audit"]) == 2
    assert "private_payload" not in json.dumps(report)
    assert inspect_queue(path, "pending")["matched_count"] == 0


def test_inspect_missing_file_does_not_create_database(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(FileNotFoundError):
        inspect_queue(path)
    assert not path.exists()
    with pytest.raises(ValueError):
        inspect_queue(path, state="unknown")


def test_supervisor_only_builds_known_roles_with_complete_routes(tmp_path):
    config = {"role": "worker", "db": str(tmp_path / "queue.sqlite"),
              "webhook_env": "TEST_HOOK", "redis_env": "TEST_REDIS"}
    env = {"TEST_HOOK": "http://localhost", "TEST_REDIS": "redis://localhost"}
    command = command_from_config(config, env)
    assert command[1:4] == ["-m", "streaming.alert_outbox", "work"]
    assert command[-2:] == ["--redis-url", "redis://localhost"]
    with pytest.raises(ValueError, match="Missing"):
        command_from_config(config, {})
    with pytest.raises(ValueError, match="Role"):
        command_from_config({**config, "role": "shell"}, env)
    with pytest.raises(ValueError, match="absolute"):
        command_from_config({**config, "db": "relative.sqlite"}, env)


def test_supervisor_invalid_policy_does_not_spawn(tmp_path):
    with pytest.raises(ValueError):
        supervise(["not-a-program"], tmp_path, restart_limit=-1)


def test_operations_evidence_rejects_unbounded_and_stale_results():
    from tools.summarize_extension_runtime import validate_outbox_operations
    report = {"success": True, "source_hashes": {"source": "hash"},
              "source_redis_report_sha256": "redis", "automatic_restart_exit_codes": [17, 17, 0],
              "bounded_failure_attempts": 3, "stop_terminated_child_without_restart": True,
              "read_only_inspection": True, "inspected_sent_count": 20}
    validate_outbox_operations(report, {"source": "hash"}, "redis")
    for key, value in (("bounded_failure_attempts", 4), ("read_only_inspection", False),
                       ("source_redis_report_sha256", "stale")):
        with pytest.raises(ValueError):
            validate_outbox_operations({**report, key: value}, {"source": "hash"}, "redis")
