"""Generate deterministic minute kW samples; Kafka publishing is explicitly opt-in."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path


def samples(start, minutes=45):
    if minutes < 1 or start.tzinfo is None:
        raise ValueError("Expected aware start and positive minutes")
    for i in range(minutes):
        ts = start + timedelta(minutes=i)
        yield {"event_id": f"W01-{ts.isoformat()}", "event_time": ts.isoformat(),
               "workshop_code": "W01", "power_kw": 100 if i < 20 else 160}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minutes", type=int, default=45)
    parser.add_argument("--bootstrap-servers")
    args = parser.parse_args()
    producer = None
    if args.bootstrap_servers:
        from kafka import KafkaProducer
        producer = KafkaProducer(bootstrap_servers=args.bootstrap_servers, acks="all")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open("w", encoding="utf-8") as sink:
            for point in samples(datetime(2026, 9, 27, tzinfo=timezone.utc), args.minutes):
                payload = json.dumps(point)
                sink.write(payload + "\n")
                if producer:
                    producer.send("energy-power-samples", key=b"W01", value=payload.encode()).get(timeout=30)
        if producer:
            producer.flush(timeout=30)
    finally:
        if producer:
            producer.close(timeout=30)
