"""Real child-process supervisor acceptance, isolated process fixtures on D."""
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from streaming.outbox_inspect import inspect_queue
from streaming.outbox_supervisor import supervise


def main():
    folder = ROOT / "output/extensions" / ("outbox_operations_" + uuid4().hex[:12])
    folder.mkdir(parents=True)
    counter = folder / "attempt-count.txt"
    code = "from pathlib import Path; import sys; p=Path(sys.argv[1]); n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n)); sys.exit(17 if n<3 else 0)"
    recovered = supervise([sys.executable, "-c", code, str(counter)], folder / "recovery", base_delay=0.05)
    assert recovered["state"] == "completed"
    assert [a["exit_code"] for a in recovered["attempts"]] == [17, 17, 0]
    bounded = supervise([sys.executable, "-c", "import sys; sys.exit(18)"], folder / "bounded",
                        restart_limit=2, base_delay=0.05)
    assert bounded["state"] == "restart_limit_reached" and len(bounded["attempts"]) == 3
    stop = threading.Event()
    result = []
    ready = folder / "child-ready.txt"
    code = "from pathlib import Path; import sys,time; Path(sys.argv[1]).write_text('ready'); time.sleep(120)"
    thread = threading.Thread(target=lambda: result.append(supervise(
        [sys.executable, "-c", code, str(ready)], folder / "cancel", stop=stop)))
    thread.start()
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        assert ready.exists()
    finally:
        stop.set()
        thread.join(timeout=15)
    assert not thread.is_alive() and result[0]["state"] == "stopped"
    assert len(result[0]["attempts"]) == 1
    redis_report = json.loads((ROOT / "output/extensions/redis_outbox_runtime.json").read_text(encoding="utf-8"))
    path = ROOT / redis_report["evidence_directory"] / "queue.sqlite"
    if not path.resolve().is_relative_to((ROOT / "output/extensions").resolve()):
        raise ValueError("Queue evidence is outside project")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    inspection = inspect_queue(path, "sent", limit=3)
    assert inspection["counts"] == {"sent": 20} and inspection["matched_count"] == 20
    assert len(inspection["messages"]) == 3
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    report = {"success": True, "source_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                  for name in ("streaming/outbox_inspect.py", "streaming/outbox_supervisor.py")},
              "automatic_restart_exit_codes": [17, 17, 0], "bounded_failure_attempts": 3,
              "stop_terminated_child_without_restart": True, "read_only_inspection": True,
              "inspected_sent_count": 20, "queue_database_sha256": before,
              "source_redis_report_sha256": hashlib.sha256((ROOT / "output/extensions/redis_outbox_runtime.json").read_bytes()).hexdigest(),
              "evidence_directory": str(folder.relative_to(ROOT)),
              "scope": "real isolated child processes; OS service registration not performed"}
    (folder / "inspection.json").write_text(json.dumps(inspection, indent=2), encoding="utf-8")
    (folder / "process_acceptance.json").write_text(json.dumps({"recovered": recovered, "bounded": bounded,
                                                               "cancelled": result[0]}, indent=2), encoding="utf-8")
    (ROOT / "output/extensions/outbox_operations_runtime.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
