"""Open-Meteo 空气质量（模型产品，非监测站原始观测）。"""

from __future__ import annotations

from typing import Any

from cleaning.rules import DATA_NATURE
from collectors.http_util import get_json

AQ_URL = DATA_NATURE["api_air_quality"]
HOURLY = (
    "pm2_5",
    "pm10",
    "nitrogen_dioxide",
    "european_aqi",
    "ozone",
    "carbon_monoxide",
)


def fetch_air_quality(
    city: dict[str, Any],
    *,
    past_days: int = 3,
    forecast_days: int = 1,
    attempt_log: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = get_json(
        AQ_URL,
        {
            "latitude": city["lat"],
            "longitude": city["lon"],
            "hourly": ",".join(HOURLY),
            "past_days": past_days,
            "forecast_days": forecast_days,
            "timezone": "Asia/Shanghai",
        },
        attempt_log=attempt_log,
    )
    return {
        "source": "open-meteo-air-quality",
        "api_endpoint": AQ_URL,
        "data_nature": DATA_NATURE["nature"],
        "data_nature_zh": DATA_NATURE["nature_zh"],
        "city_id": city["city_id"],
        "city_name": city["name"],
        "lat": city["lat"],
        "lon": city["lon"],
        "fetched": payload,
    }
