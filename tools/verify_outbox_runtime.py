"""Real local HTTP and process-crash/restart acceptance; no external notification."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from streaming.alert_outbox import Outbox


def main():
    folder = ROOT / "output/extensions"
    run = folder / ("outbox_" + str(time.time_ns()))
    run.mkdir(parents=True)
    attempts, received = [], {}
    failures = {"transient": 1, "permanent": 100, "exhaust": 100}
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            ident = self.headers["Idempotency-Key"]
            kind = payload.get("case", "normal")
            code = 200
            if failures.get(kind, 0):
                failures[kind] -= 1
                code = 400 if kind == "permanent" else 503
            attempts.append({"id": ident, "case": kind, "status": code})
            if code == 200:
                received.setdefault(ident, payload)
            self.send_response(code)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/alerts"
    path = run / "queue.sqlite"
    queue = Outbox(path, url)
    crash_id = queue.enqueue({"case": "crash_after_remote_ack"})
    # Abrupt exit after successful remote delivery but before the local ACK.
    script = "from streaming.alert_outbox import Outbox; from streaming.dispatch import deliver; import json,os,sys; q=Outbox(sys.argv[1],sys.argv[2]); r=q.claim(lease_seconds=0.2); deliver(json.loads(r['payload']),webhook_url=sys.argv[2]); os._exit(87)"
    child = subprocess.run([sys.executable, "-c", script, str(path), url], cwd=ROOT, capture_output=True, timeout=20)
    assert child.returncode == 87, child.stderr.decode(errors="replace")
    assert queue.stats() == {"inflight": 1}
    assert crash_id in received
    ids = {kind: queue.enqueue({"case": kind}) for kind in ("transient", "permanent", "exhaust")}
    queue.db.close()
    worker = subprocess.Popen([sys.executable, "-m", "streaming.alert_outbox", "work", "--db", str(path),
                               "--webhook-url", url, "--max-attempts", "3", "--base-delay", "0.1"], cwd=ROOT,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            q = Outbox(path, url)
            states = q.stats()
            q.db.close()
            if states == {"dead": 2, "sent": 2}:
                break
            if worker.poll() is not None:
                raise RuntimeError(worker.communicate()[1].decode(errors="replace"))
            time.sleep(0.1)
        assert states == {"dead": 2, "sent": 2}, states
    finally:
        worker.terminate()
        worker.communicate(timeout=10)
    q = Outbox(path, url)
    before = [dict(row) for row in q.db.execute("SELECT id,state,attempts,error FROM messages ORDER BY id")]
    assert next(row for row in before if row["id"] == ids["exhaust"])["attempts"] == 3
    assert next(row for row in before if row["id"] == ids["permanent"])["attempts"] == 1
    failures["exhaust"] = 0
    assert q.requeue(ids["exhaust"])
    q.db.close()
    subprocess.run([sys.executable, "-m", "streaming.alert_outbox", "work", "--db", str(path),
                    "--webhook-url", url, "--once"], cwd=ROOT, check=True, capture_output=True, timeout=20)
    q = Outbox(path, url)
    assert q.stats() == {"dead": 1, "sent": 3}
    audit = [dict(row) for row in q.db.execute("SELECT * FROM audit ORDER BY seq")]
    q.db.close()
    server.shutdown()
    assert len([a for a in attempts if a["id"] == crash_id]) == 2
    assert len(received) == 3
    report = {"success": True, "source_sha256": hashlib.sha256((ROOT / "streaming/alert_outbox.py").read_bytes()).hexdigest(),
              "process_crash_after_remote_ack_recovered": True, "receiver_deduplicated": True,
              "automatic_retry_recovered": True, "retry_exhaustion_attempts": 3,
              "permanent_error_attempts": 1, "manual_requeue_recovered": True,
              "final_states": {"dead": 1, "sent": 3}, "http_attempts": len(attempts),
              "unique_received": len(received), "evidence_directory": str(run.relative_to(ROOT))}
    (run / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    (run / "http_attempts.json").write_text(json.dumps(attempts, indent=2), encoding="utf-8")
    (run / "before_manual_requeue.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    (folder / "outbox_runtime.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
