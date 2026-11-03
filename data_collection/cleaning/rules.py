"""清洗依据：按字段制定，答辩可逐条说明。

数据性质（必须讲清）：
Open-Meteo Forecast API 返回的是「数值天气预报 / 空气质量模型」产品，
含近期归档时段与未来预报时段，不是地面自动站原始观测原值。
官方：https://open-meteo.com/en/docs
"""

from __future__ import annotations

DATA_NATURE = {
    "provider": "Open-Meteo",
    "api_weather": "https://api.open-meteo.com/v1/forecast",
    "api_air_quality": "https://air-quality-api.open-meteo.com/v1/air-quality",
    "nature": "model_forecast_and_archived_forecast",
    "nature_zh": "数值模式预报及归档预报产品（非站点原始观测）",
    "docs": "https://open-meteo.com/en/docs",
    "disclaimer_zh": "作业分析对象为公开模型产品时序，结论不可直接等同于官方监测站实测。",
}

# 短缺口：连续缺失小时数 <= 该阈值时允许插值；更长则保留空值并打标
SHORT_GAP_HOURS = 3

FIELD_RULES: dict[str, dict] = {
    "temperature_2m": {
        "group": "weather_continuous",
        "unit": "°C",
        "physical_min": -50,
        "physical_max": 55,
        "missing": "short_gap_interpolate_long_keep_null",
        "anomaly": "mark_outside_physical_bounds",
        "winsorize": False,
        "reason_zh": "气温连续变化，短缺口插值；超物理范围标记，不做 IQR 盖帽以免抹平寒潮/热浪。",
    },
    "relative_humidity_2m": {
        "group": "weather_bounded",
        "unit": "%",
        "physical_min": 0,
        "physical_max": 100,
        "missing": "short_gap_interpolate_long_keep_null",
        "anomaly": "clip_to_physical_bounds_and_mark",
        "winsorize": False,
        "reason_zh": "湿度有明确 [0,100] 物理界；越界先纠正到边界并标记。",
    },
    "wind_speed_10m": {
        "group": "weather_continuous",
        "unit": "km/h",
        "physical_min": 0,
        "physical_max": 250,
        "missing": "short_gap_interpolate_long_keep_null",
        "anomaly": "mark_outside_physical_bounds",
        "winsorize": False,
        "reason_zh": "风速非负；极端大风保留数值并标记，避免盖帽掩盖天气过程。",
    },
    "shortwave_radiation": {
        "group": "weather_continuous",
        "unit": "W/m²",
        "physical_min": 0,
        "physical_max": 1300,
        "missing": "short_gap_interpolate_long_keep_null",
        "anomaly": "mark_outside_physical_bounds",
        "winsorize": False,
        "reason_zh": "辐射夜间可为 0；负值非法。",
    },
    "precipitation": {
        "group": "weather_sparse",
        "unit": "mm",
        "physical_min": 0,
        "physical_max": 200,
        "missing": "short_gap_fill_zero_long_keep_null",
        "anomaly": "mark_outside_physical_bounds",
        "winsorize": False,
        "reason_zh": "降水大量为零；短缺口用 0，长缺口不填，避免假晴假雨。",
    },
    "cloud_cover": {
        "group": "weather_bounded",
        "unit": "%",
        "physical_min": 0,
        "physical_max": 100,
        "missing": "short_gap_interpolate_long_keep_null",
        "anomaly": "clip_to_physical_bounds_and_mark",
        "winsorize": False,
        "reason_zh": "云量有界；短缺口插值。",
    },
    "pm2_5": {
        "group": "pollution",
        "unit": "µg/m³",
        "physical_min": 0,
        "physical_max": 1000,
        "missing": "short_gap_interpolate_long_keep_null",
        "anomaly": "mark_iqr_peak_keep_value",
        "winsorize": False,
        "reason_zh": "污染峰值先 IQR 标记并解释，默认保留原值，不统一盖帽。",
    },
    "pm10": {
        "group": "pollution",
        "unit": "µg/m³",
        "physical_min": 0,
        "physical_max": 2000,
        "missing": "short_gap_interpolate_long_keep_null",
        "anomaly": "mark_iqr_peak_keep_value",
        "winsorize": False,
        "reason_zh": "同 PM2.5：峰值标记优先于抹平。",
    },
    "nitrogen_dioxide": {
        "group": "pollution",
        "unit": "µg/m³",
        "physical_min": 0,
        "physical_max": 1000,
        "missing": "short_gap_interpolate_long_keep_null",
        "anomaly": "mark_iqr_peak_keep_value",
        "winsorize": False,
        "reason_zh": "气态污染物峰值保留并打标。",
    },
    "ozone": {
        "group": "pollution",
        "unit": "µg/m³",
        "physical_min": 0,
        "physical_max": 800,
        "missing": "short_gap_interpolate_long_keep_null",
        "anomaly": "mark_iqr_peak_keep_value",
        "winsorize": False,
        "reason_zh": "臭氧峰值保留并打标。",
    },
    "carbon_monoxide": {
        "group": "pollution",
        "unit": "µg/m³",
        "physical_min": 0,
        "physical_max": 30000,
        "missing": "short_gap_interpolate_long_keep_null",
        "anomaly": "mark_iqr_peak_keep_value",
        "winsorize": False,
        "reason_zh": "CO 峰值保留并打标。",
    },
    "european_aqi": {
        "group": "pollution_index",
        "unit": "index",
        "physical_min": 0,
        "physical_max": 500,
        "missing": "short_gap_interpolate_long_keep_null",
        "anomaly": "mark_iqr_peak_keep_value",
        "winsorize": False,
        "reason_zh": "AQI 为指数，异常高值标记，不盖帽。",
    },
}

WEATHER_COLS = [
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "shortwave_radiation",
    "precipitation",
    "cloud_cover",
]
AQ_COLS = [
    "pm2_5",
    "pm10",
    "nitrogen_dioxide",
    "european_aqi",
    "ozone",
    "carbon_monoxide",
]
VALUE_COLS = WEATHER_COLS + AQ_COLS
