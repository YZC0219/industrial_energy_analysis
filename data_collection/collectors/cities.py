"""演示用采集城市清单（可改坐标，不依赖服务器）。"""

from __future__ import annotations

CITIES: list[dict] = [
    {"city_id": "SH", "name": "上海", "lat": 31.2304, "lon": 121.4737},
    {"city_id": "BJ", "name": "北京", "lat": 39.9042, "lon": 116.4074},
    {"city_id": "GZ", "name": "广州", "lat": 23.1291, "lon": 113.2644},
    {"city_id": "CD", "name": "成都", "lat": 30.5728, "lon": 104.0668},
    {"city_id": "WH", "name": "武汉", "lat": 30.5928, "lon": 114.3055},
]
