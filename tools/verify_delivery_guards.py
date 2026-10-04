"""Local HTTP acknowledgement and restarted worker retry-budget acceptance."""
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
from threading import Thread
from uuid import uuid4
from streaming.alert_outbox import Outbox

ROOT = Path(__file__).resolve().parents[1]


def run():
    folder = ROOT / 'output/extensions' / ('delivery_guards_' + uuid4().hex[:12])
    folder.mkdir(parents=True)
    received = []
    responses = {}
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            received.append(['POST', self.path])
            self.send_response(responses[self.path])
            self.send_header('Location', '/landing')
            self.end_headers()
        def do_GET(self):
            received.append(['GET', self.path])
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    def work(path, url, maximum=5):
        subprocess.run([sys.executable, '-m', 'streaming.alert_outbox', 'work', '--once',
                        '--db', str(path), '--webhook-url', url, '--max-attempts', str(maximum),
                        '--base-delay', '0'], cwd=ROOT, check=True, capture_output=True, timeout=20)
    cases = []
    try:
        for status in (200, 204, 301, 302, 303, 307, 308, 400, 503):
            endpoint = f'/status/{status}'
            responses[endpoint] = status
            url = base + endpoint
            path = folder / f'status_{status}.sqlite'
            queue = Outbox(path, url)
            queue.enqueue({'case': status})
            queue.db.close()
            work(path, url)
            queue = Outbox(path, url)
            try:
                state = 'sent' if status < 300 else 'pending' if status == 503 else 'dead'
                assert queue.stats() == {state: 1}
                assert sum(r == ['POST', endpoint] for r in received) == 1
                assert not any(r[0] == 'GET' for r in received)
                cases.append({'http_status': status, 'queue_state': state, 'post_attempts': 1})
            finally:
                queue.db.close()
        responses['/budget'] = 503
        url = base + '/budget'
        path = folder / 'retry_budget.sqlite'
        queue = Outbox(path, url)
        ident = queue.enqueue({'case': 'retry_budget'})
        queue.db.close()
        work(path, url, 5)
        work(path, url, 1)  # A fresh process must retire the already-used budget.
        queue = Outbox(path, url)
        try:
            assert queue.stats() == {'dead': 1}
            assert queue.db.execute('SELECT attempts FROM messages').fetchone()[0] == 1
            assert received.count(['POST', '/budget']) == 1
            assert queue.requeue(ident)
        finally:
            queue.db.close()
        responses['/budget'] = 200
        work(path, url, 1)
        queue = Outbox(path, url)
        try:
            assert queue.stats() == {'sent': 1}
            assert queue.db.execute('SELECT attempts FROM messages').fetchone()[0] == 1
            assert received.count(['POST', '/budget']) == 2
        finally:
            queue.db.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    report = {'success': True, 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'http_cases': cases, 'redirects_not_followed': True,
        'lowered_budget_after_process_restart_blocks_delivery': True,
        'manual_requeue_resets_budget': True, 'scope': '127.0.0.1 receiver only',
        'source_hashes': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in
            ('streaming/alert_outbox.py', 'streaming/dispatch.py', 'tools/verify_delivery_guards.py')},
        'evidence_directory': str(folder.relative_to(ROOT))}
    (folder / 'http_requests.json').write_text(json.dumps(received, indent=2), encoding='utf-8')
    (folder / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (ROOT / 'output/extensions/delivery_guards_runtime.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    run()
