"""Inspect or register the optional MySQL CDC source in local Kafka Connect.

Registration writes only to the independent energy-cdc topic namespace. No
password is sent to Kafka Connect's REST API or stored in its config topic.
"""
from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CONNECTOR_NAME = "energy-mysql-cdc"
CONNECTOR_CONFIG = {
    "connector.class": "io.debezium.connector.mysql.MySqlConnector",
    "tasks.max": "1",
    "database.hostname": "mysql",
    "database.port": "3306",
    "database.user": "energy_cdc",
    "database.password": "${file:/opt/connect-secrets.properties:db-password}",
    "database.server.id": "184054",
    "topic.prefix": "energy-cdc",
    "database.include.list": "industrial_energy",
    "table.include.list": "industrial_energy.fact_energy_consumption",
    "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
    "schema.history.internal.kafka.topic": "energy-cdc-schema-history",
    "snapshot.mode": "initial",
    "tombstones.on.delete": "true",
    "include.schema.changes": "false",
}


def request_json(url: str, method: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method=method,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect-url", default="http://127.0.0.1:8083")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--print-config", action="store_true",
                      help="Print connector config; no REST request")
    mode.add_argument("--apply", action="store_true",
                      help="Create or update connector through Kafka Connect REST")
    mode.add_argument("--status", action="store_true",
                      help="Read connector status; no changes")
    args = parser.parse_args(argv)
    if args.print_config:
        print(json.dumps(CONNECTOR_CONFIG, ensure_ascii=False, indent=2))
        return 0
    url = args.connect_url.rstrip("/") + "/connectors/" + CONNECTOR_NAME
    try:
        if args.apply:
            result = request_json(url + "/config", "PUT", CONNECTOR_CONFIG)
        else:
            result = request_json(url + "/status", "GET")
    except HTTPError as exc:
        print(f"Kafka Connect HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}",
              file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"Kafka Connect unavailable: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
