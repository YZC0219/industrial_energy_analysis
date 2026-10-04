"""Explicit opt-in Redis/Webhook alert dispatch; default writes a local preview.

Delivery is at-least-once when the caller retries. Redis caching is not an
atomic exactly-once boundary for an external HTTP recipient.
Webhook URLs must acknowledge POST directly with 2xx; redirects are rejected.
"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _RejectRedirects(HTTPRedirectHandler):
    """Require a direct acknowledgement from the configured destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def prepare(alert):
    payload = json.dumps(alert, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest(), payload


def deliver(alert, redis_client=None, webhook_url=None):
    key, payload = prepare(alert)
    if redis_client is not None:
        redis_client.set(f"energy:alert:{key}", payload, ex=86400)
    if webhook_url:
        request = Request(webhook_url, data=payload.encode(),
                          headers={"Content-Type": "application/json", "Idempotency-Key": key},
                          method="POST")
        with build_opener(_RejectRedirects()).open(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise HTTPError(webhook_url, response.status, "Webhook failed",
                                response.headers, None)
    return {"alert_id": key, "payload": alert}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True, help="JSONL alerts")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--redis-url")
    parser.add_argument("--webhook-url")
    args = parser.parse_args()
    client = None
    if args.redis_url:
        import redis
        client = redis.Redis.from_url(args.redis_url, socket_timeout=10)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.input.open(encoding="utf-8") as source, args.output.open("w", encoding="utf-8") as sink:
        for line in source:
            if line.strip():
                result = deliver(json.loads(line), client, args.webhook_url)
                sink.write(json.dumps(result, ensure_ascii=False) + "\n")
