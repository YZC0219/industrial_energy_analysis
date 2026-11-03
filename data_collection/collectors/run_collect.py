"""真实采集 → ODS 原始 JSON + collect_log（含重试与数据性质）。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cleaning.rules import DATA_NATURE  # noqa: E402
from collectors.air_quality import fetch_air_quality  # noqa: E402
from collectors.cities import CITIES  # noqa: E402
from collectors.weather import fetch_weather  # noqa: E402
from lakehouse_local.paths import batch_dir, ensure_layout  # noqa: E402


def new_batch_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def collect_batch(*, past_days: int, forecast_days: int, cities: list[dict] | None = None) -> dict:
    ensure_layout()
    batch_id = new_batch_id()
    collected_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    ods = batch_dir("ods", batch_id)
    selected = cities or CITIES
    started = time.time()
    records: list[dict] = []
    errors: list[dict] = []
    attempt_log: list[dict] = []

    for city in selected:
        for kind, fetcher in (("weather", fetch_weather), ("air_quality", fetch_air_quality)):
            try:
                doc = fetcher(
                    city,
                    past_days=past_days,
                    forecast_days=forecast_days,
                    attempt_log=attempt_log,
                )
                doc["collected_at_utc"] = collected_at
                doc["batch_id"] = batch_id
                fname = f"{city['city_id']}_{kind}.json"
                path = ods / fname
                path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
                hourly = (doc.get("fetched") or {}).get("hourly") or {}
                n = len(hourly.get("time") or [])
                records.append(
                    {
                        "city_id": city["city_id"],
                        "city_name": city["name"],
                        "kind": kind,
                        "api_endpoint": doc.get("api_endpoint"),
                        "data_nature": doc.get("data_nature"),
                        "file": str(path),
                        "hourly_points": n,
                        "ok": True,
                    }
                )
                print(f"[ods] {city['name']} {kind}: {n} 小时点 → {path.name}")
            except Exception as exc:  # noqa: BLE001
                errors.append({"city_id": city["city_id"], "kind": kind, "error": str(exc)})
                print(f"[ods] FAIL {city['name']} {kind}: {exc}")

    retries = sum(1 for a in attempt_log if a.get("attempt", 1) > 1)
    failed_attempts = sum(1 for a in attempt_log if not a.get("ok"))
    collect_log = {
        "batch_id": batch_id,
        "collected_at_utc": collected_at,
        "data_nature": DATA_NATURE,
        "past_days": past_days,
        "forecast_days": forecast_days,
        "city_count": len(selected),
        "file_count": len(records),
        "error_count": len(errors),
        "http_attempts": attempt_log,
        "http_retry_count": retries,
        "http_failed_attempt_count": failed_attempts,
        "errors": errors,
        "elapsed_sec": round(time.time() - started, 2),
    }
    (ods / "collect_log.json").write_text(
        json.dumps(collect_log, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    manifest = {
        "batch_id": batch_id,
        "layer": "ods",
        "mode": "real-api-collect",
        "title_zh": "基于 Spark 的多城市气象与空气质量数据采集及预处理系统",
        "data_nature": DATA_NATURE,
        "collected_at_utc": collected_at,
        "past_days": past_days,
        "forecast_days": forecast_days,
        "city_count": len(selected),
        "file_count": len(records),
        "error_count": len(errors),
        "http_retry_count": retries,
        "records": records,
        "errors": errors,
        "elapsed_sec": collect_log["elapsed_sec"],
        "ods_dir": str(ods),
        "collect_log": str(ods / "collect_log.json"),
    }
    (ods / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = ensure_layout()["meta"] / "latest_ods_batch.json"
    meta.write_text(
        json.dumps({"batch_id": batch_id, "ods_dir": str(ods), "collected_at_utc": collected_at}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[ods] batch={batch_id} files={len(records)} errors={len(errors)} retries={retries}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="真实气象/空气质量采集到本地 ODS")
    parser.add_argument("--past-days", type=int, default=3)
    parser.add_argument("--forecast-days", type=int, default=1)
    args = parser.parse_args()
    man = collect_batch(past_days=max(1, args.past_days), forecast_days=max(0, args.forecast_days))
    return 0 if man["file_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
