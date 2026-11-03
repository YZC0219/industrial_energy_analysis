"""工业电表采集报文约定（演示口径）。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
import hashlib
import json

VALID_STATES = frozenset({"running", "idle", "standby", "offline", "spike"})
ENERGY_CODES = ("E01",)  # 演示仅电力；可扩展 E02 蒸汽等


@dataclass(frozen=True)
class MeterSample:
    event_id: str
    device_id: str
    workshop_id: str
    energy_code: str
    ts_utc: str
    power_kw: float
    state: str
    source: str = "edge-agent"
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_event_id(device_id: str, ts_utc: str, power_kw: float) -> str:
    raw = f"{device_id}|{ts_utc}|{power_kw:.6f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def validate_sample(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    required = ("event_id", "device_id", "workshop_id", "energy_code", "ts_utc", "power_kw", "state")
    missing = [k for k in required if k not in payload or payload[k] in (None, "")]
    if missing:
        return None, f"缺少字段: {', '.join(missing)}"

    try:
        power = float(payload["power_kw"])
    except (TypeError, ValueError):
        return None, "power_kw 必须是数值"

    if power < 0 or power > 5000:
        return None, "power_kw 超出合理范围 [0, 5000]"

    state = str(payload["state"])
    if state not in VALID_STATES:
        return None, f"state 非法: {state}"

    energy = str(payload["energy_code"])
    if energy not in ENERGY_CODES:
        return None, f"energy_code 暂不支持: {energy}"

    cleaned = {
        "event_id": str(payload["event_id"])[:64],
        "device_id": str(payload["device_id"])[:32],
        "workshop_id": str(payload["workshop_id"])[:16],
        "energy_code": energy,
        "ts_utc": str(payload["ts_utc"]),
        "power_kw": round(power, 4),
        "state": state,
        "source": str(payload.get("source") or "edge-agent")[:64],
        "schema_version": str(payload.get("schema_version") or "1.0")[:16],
    }
    return cleaned, None


def dumps_line(sample: MeterSample) -> str:
    return json.dumps(sample.to_dict(), ensure_ascii=False)
