"""Persist Kafka alerts before committing the next offset; stop on bad input.

Use a dedicated consumer group. A malformed message blocks that partition until
the operator fixes/replays the input; it is never silently acknowledged.
"""
import argparse
import json
from streaming.alert_outbox import Outbox


def persist_and_commit(queue, consumer, message, partition_factory, offset_factory):
    alert = json.loads(message.value.decode("utf-8"))
    ident = queue.enqueue(alert)
    consumer.commit({partition_factory(message.topic, message.partition):
                     offset_factory(message.offset + 1, "", -1)})
    return ident


def main():
    from kafka import KafkaConsumer, TopicPartition
    from kafka.structs import OffsetAndMetadata
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap-servers", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--webhook-url", required=True)
    parser.add_argument("--redis-url")
    parser.add_argument("--quarantine-db", help="Opt-in durable poison quarantine; default stops on bad input")
    args = parser.parse_args()
    queue = Outbox(args.db, args.webhook_url, redis_url=args.redis_url)
    quarantine = None
    if args.quarantine_db:
        from streaming.alert_quarantine import Quarantine
        quarantine = Quarantine(args.quarantine_db, queue.db.execute("SELECT fingerprint FROM route").fetchone()[0])
    consumer = KafkaConsumer(args.topic, bootstrap_servers=args.bootstrap_servers,
                             group_id=args.group_id, enable_auto_commit=False,
                             auto_offset_reset="earliest", max_poll_records=1)
    try:
        for message in consumer:
            if quarantine is None:
                persist_and_commit(queue, consumer, message, TopicPartition, OffsetAndMetadata)
            else:
                from streaming.alert_quarantine import persist_or_quarantine
                persist_or_quarantine(queue, quarantine, args.bootstrap_servers + ":" + args.group_id,
                                      consumer, message, TopicPartition, OffsetAndMetadata)
    finally:
        consumer.close(autocommit=False)
        queue.db.close()
        if quarantine is not None:
            quarantine.db.close()


if __name__ == "__main__":
    main()
