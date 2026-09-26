"""Reconcile a Kafka JSONL event export against a batch energy CSV snapshot."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "output" / "stream_batch_reconciliation.json"
KEY_FIELDS = ("record_date", "workshop_code", "energy_code")
VALUE_FIELDS = ("consumption", "unit", "unit_price", "cost")


def _valid_business_key(parts: tuple[object, object, object]) -> bool:
    record_date, workshop_code, energy_code = parts
    if not isinstance(record_date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", record_date):
        return False
    try:
        if date.fromisoformat(record_date).isoformat() != record_date:
            return False
    except ValueError:
        return False
    return (isinstance(workshop_code, str)
            and re.fullmatch(r"W[0-9]{2}", workshop_code) is not None
            and isinstance(energy_code, str)
            and re.fullmatch(r"E[0-9]{2}", energy_code) is not None)


def _number(value: object) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = Decimal(str(value))
        return number.normalize() if number.is_finite() else None
    except InvalidOperation:
        return None


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _batch_timestamp(value: object, timezone_name: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            zone = ZoneInfo(timezone_name)
            first = parsed.replace(tzinfo=zone, fold=0)
            second = parsed.replace(tzinfo=zone, fold=1)
            if first.utcoffset() != second.utcoffset():
                return None  # Fall-back hour has two possible source versions.
            utc_value = first.astimezone(timezone.utc)
            if utc_value.astimezone(zone).replace(tzinfo=None) != parsed:
                return None  # Spring-forward local time never existed.
            return utc_value
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def read_events(path: Path, *, required_schema_version: int | None = None
                ) -> tuple[list[dict], list[dict]]:
    events, invalid = [], []
    byte_offset = 0
    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            offset = byte_offset
            byte_offset += len(raw_line)
            if not raw_line.strip():
                continue
            try:
                line = raw_line.decode("utf-8-sig" if line_number == 1 else "utf-8")
            except UnicodeDecodeError:
                invalid.append({
                    "line": line_number, "byte_offset": offset,
                    "raw_sha256": hashlib.sha256(raw_line).hexdigest(),
                    "reason": "invalid_utf8",
                })
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                invalid.append({
                    "line": line_number, "byte_offset": offset,
                    "raw_sha256": hashlib.sha256(raw_line).hexdigest(),
                    "reason": "invalid_json", "detail": str(exc),
                })
                continue
            if not isinstance(event, dict):
                invalid.append({"line": line_number, "reason": "event_must_be_object"})
                continue
            if required_schema_version is not None and (
                type(event.get("schema_version")) is not int
                or event["schema_version"] != required_schema_version
            ):
                invalid.append({"line": line_number, "reason": "unsupported_schema_version",
                                "expected": required_schema_version,
                                "actual": event.get("schema_version")})
                continue
            missing = [field for field in (*KEY_FIELDS, "event_id", "updated_at", "op")
                       if event.get(field) in (None, "")]
            if missing:
                invalid.append({"line": line_number, "reason": "missing_fields", "fields": missing})
                continue
            if not _valid_business_key(tuple(event[field] for field in KEY_FIELDS)):
                invalid.append({"line": line_number, "reason": "invalid_business_key"})
                continue
            if event["op"] not in {"UPSERT", "DELETE"}:
                invalid.append({"line": line_number, "reason": "invalid_op"})
                continue
            if _timestamp(event.get("updated_at")) is None or _timestamp(event.get("event_time")) is None:
                invalid.append({"line": line_number, "reason": "invalid_timestamp"})
                continue
            if event["op"] == "UPSERT" and (
                any(_number(event.get(field)) is None for field in ("consumption", "unit_price", "cost"))
                or _number(event.get("consumption")) < 0
                or _number(event.get("unit_price")) <= 0
                or _number(event.get("cost")) < 0
                or not event.get("unit")
            ):
                invalid.append({"line": line_number, "reason": "invalid_upsert_values"})
                continue
            events.append(event)
    return events, invalid


def _latest_events(events: Iterable[dict]) -> tuple[dict, list[dict], list[dict]]:
    by_id: dict[str, str] = {}
    by_version: dict[tuple[tuple[str, str, str], datetime], tuple] = {}
    latest: dict[tuple[str, str, str], dict] = {}
    id_conflicts: list[dict] = []
    version_conflicts: list[dict] = []
    for event in events:
        event_id = str(event["event_id"])
        serialized = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if event_id in by_id:
            if by_id[event_id] != serialized:
                id_conflicts.append({"event_id": event_id, "reason": "event_id_payload_conflict"})
            continue
        by_id[event_id] = serialized
        key = tuple(str(event[field]) for field in KEY_FIELDS)
        updated_at = _timestamp(event["updated_at"])
        assert updated_at is not None  # read_events has already validated timestamps.
        state = (event["op"],)
        if event["op"] == "UPSERT":
            state += tuple(str(_number(event[field])) if field != "unit" else event[field]
                           for field in VALUE_FIELDS)
            state += (event.get("record_status"), event.get("is_production_day"))
        version_key = (key, updated_at)
        previous = by_version.setdefault(version_key, state)
        if previous != state:
            version_conflicts.append({
                **dict(zip(KEY_FIELDS, key)), "updated_at": updated_at.isoformat(),
                "event_id": event_id, "reason": "same_version_different_state",
            })
        current = latest.get(key)
        version = (updated_at, event_id)
        current_version = (_timestamp(current["updated_at"]), str(current["event_id"])) if current else None
        if current is None or version > current_version:
            latest[key] = event
    return latest, id_conflicts, version_conflicts


def reconcile(events_path: Path, batch_path: Path, *, batch_timezone: str | None = None,
              required_schema_version: int | None = None) -> dict:
    if batch_timezone is not None:
        ZoneInfo(batch_timezone)  # Reject unknown zones before reading inputs.
    events, invalid = read_events(events_path,
                                  required_schema_version=required_schema_version)
    latest, conflicts, version_conflicts = _latest_events(events)
    with batch_path.open(encoding="utf-8-sig", newline="") as stream:
        batch_rows = list(csv.DictReader(stream))
    batch: dict[tuple[str, str, str], dict] = {}
    batch_duplicates, batch_invalid = [], []
    for row_number, row in enumerate(batch_rows, start=2):
        key = tuple(row.get(field) for field in KEY_FIELDS)
        if not _valid_business_key(key) or not row.get("unit") or any(
            _number(row.get(field)) is None for field in ("consumption", "unit_price", "cost")
        ) or _number(row.get("consumption")) < 0 or _number(row.get("unit_price")) <= 0 \
                or _number(row.get("cost")) < 0 or (batch_timezone is not None
                    and _batch_timestamp(row.get("updated_at"), batch_timezone) is None):
            batch_invalid.append({"row": row_number, "reason": "invalid_batch_row"})
            continue
        if key in batch:
            batch_duplicates.append({"row": row_number, "key": dict(zip(KEY_FIELDS, key))})
            continue
        batch[key] = row
    stream_state = {key: event for key, event in latest.items() if event["op"] != "DELETE"}

    only_batch, only_stream, mismatches, version_mismatches = [], [], [], []
    for key in sorted(set(batch) | set(stream_state)):
        if key not in stream_state:
            only_batch.append(dict(zip(KEY_FIELDS, key)))
            continue
        if key not in batch:
            only_stream.append(dict(zip(KEY_FIELDS, key)))
            continue
        differences = {}
        for field in VALUE_FIELDS:
            left, right = batch[key].get(field), stream_state[key].get(field)
            if field in {"consumption", "unit_price", "cost"}:
                equal = _number(left) == _number(right)
            else:
                equal = (left or "") == (right or "")
            if not equal:
                differences[field] = {"batch": left, "stream": right}
        if differences:
            mismatches.append({**dict(zip(KEY_FIELDS, key)), "differences": differences})
        if batch_timezone is not None:
            batch_version = _batch_timestamp(batch[key].get("updated_at"), batch_timezone)
            stream_version = _timestamp(stream_state[key]["updated_at"])
            if batch_version != stream_version:
                version_mismatches.append({
                    **dict(zip(KEY_FIELDS, key)),
                    "batch_updated_at_utc": batch_version.isoformat() if batch_version else None,
                    "stream_updated_at_utc": stream_version.isoformat() if stream_version else None,
                })

    success = bool(events and batch_rows) and not (
        invalid or conflicts or version_conflicts or batch_invalid or batch_duplicates
        or only_batch or only_stream or mismatches or version_mismatches
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "inputs": {
            "events_jsonl": str(events_path), "events_sha256": _fingerprint(events_path),
            "batch_csv": str(batch_path), "batch_sha256": _fingerprint(batch_path),
            "batch_timezone": batch_timezone,
            "required_schema_version": required_schema_version,
        },
        "counts": {
            "event_rows": len(events), "event_invalid_rows": len(invalid),
            "event_id_conflicts": len(conflicts),
            "version_conflicts": len(version_conflicts), "stream_business_keys": len(stream_state),
            "batch_rows": len(batch_rows), "batch_invalid_rows": len(batch_invalid),
            "batch_duplicate_keys": len(batch_duplicates), "only_in_batch": len(only_batch),
            "only_in_stream": len(only_stream), "value_mismatches": len(mismatches),
            "version_mismatches": len(version_mismatches),
            "delete_events_applied": sum(event["op"] == "DELETE" for event in latest.values()),
        },
        "samples": {
            "invalid_events": invalid[:10], "event_id_conflicts": conflicts[:10],
            "version_conflicts": version_conflicts[:10],
            "batch_invalid_rows": batch_invalid[:10], "batch_duplicate_keys": batch_duplicates[:10],
            "only_in_batch": only_batch[:10], "only_in_stream": only_stream[:10],
            "value_mismatches": mismatches[:10],
            "version_mismatches": version_mismatches[:10],
        },
        "scope_note": (
            "Compares an exported event log to a batch snapshot at record_date × workshop_code × "
            "energy_code grain. It does not itself consume Kafka or capture source-database hard deletes."
            + (" Batch updated_at is checked using the explicit batch timezone."
               if batch_timezone else " Batch updated_at is not checked without --batch-timezone.")
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True, help="Kafka JSONL export")
    parser.add_argument("--batch", type=Path, required=True, help="Batch energy CSV snapshot")
    parser.add_argument("--batch-timezone", help="IANA zone for naive batch updated_at; enables source-version comparison")
    parser.add_argument("--required-schema-version", type=int,
                        help="reject events without this exact integer schema_version")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    report = reconcile(args.events, args.batch, batch_timezone=args.batch_timezone,
                       required_schema_version=args.required_schema_version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"STREAM_BATCH_RECONCILIATION {'PASS' if report['success'] else 'FAIL'} "
          f"report={args.output}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
