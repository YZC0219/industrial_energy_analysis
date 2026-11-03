"""Open-Meteo 小时气象（Forecast API：模式预报/归档预报产品）。"""

from __future__ import annotations

from typing import Any

from cleaning.rules import DATA_NATURE
from collectors.http_util import get_json

WEATHER_URL = DATA_NATURE["api_weather"]
HOURLY = (
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "shortwave_radiation",
    "precipitation",
    "cloud_cover",
)


def fetch_weather(
    city: dict[str, Any],
    *,
    past_days: int = 3,
    forecast_days: int = 1,
    attempt_log: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = get_json(
        WEATHER_URL,
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
        "source": "open-meteo-weather",
        "api_endpoint": WEATHER_URL,
        "data_nature": DATA_NATURE["nature"],
        "data_nature_zh": DATA_NATURE["nature_zh"],
        "city_id": city["city_id"],
        "city_name": city["name"],
        "lat": city["lat"],
        "lon": city["lon"],
        "fetched": payload,
    }
