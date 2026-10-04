"""Bounded, event-time replay reference for a 15-minute sliding mean alarm.

For live ingestion use the existing Kafka/Flink pipeline. This reference sorts
finite inputs; it does not implement streaming watermarks or CDC retractions.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
import math


def replay(points, mean_kw, std_kw, window_minutes=15, min_points=15):
    if not math.isfinite(mean_kw) or not math.isfinite(std_kw) or std_kw <= 0 or window_minutes <= 0 or min_points < 2:
        raise ValueError("Invalid baseline/window")
    parsed, seen = [], set()
    for point in points:
        timestamp = datetime.fromisoformat(point["event_time"])
        value = float(point["power_kw"])
        if timestamp.tzinfo is None or not math.isfinite(value) or value < 0:
            raise ValueError("Expected timezone-aware timestamps and non-negative kW")
        key = (point["workshop_code"], point["event_id"])
        if key in seen:
            raise ValueError("Duplicate sample event_id")
        seen.add(key)
        parsed.append((timestamp, point, value))
    windows, alerts = {}, []
    for timestamp, point, value in sorted(parsed, key=lambda item: item[0]):
        queue = windows.setdefault(point["workshop_code"], deque())
        queue.append((timestamp, value))
        while queue and queue[0][0] <= timestamp - timedelta(minutes=window_minutes):
            queue.popleft()
        average = sum(v for _, v in queue) / len(queue)
        if len(queue) >= min_points and average > mean_kw + 2 * std_kw:
            xs = [(t - queue[0][0]).total_seconds() / 60 for t, _ in queue]
            xmean = sum(xs) / len(xs)
            denominator = sum((x - xmean) ** 2 for x in xs)
            slope = sum((x - xmean) * (v - average) for x, (_, v) in zip(xs, queue)) / denominator if denominator else None
            alerts.append({"event_id": point["event_id"], "workshop_code": point["workshop_code"],
                           "event_time": timestamp.isoformat(), "mean_kw": average,
                           "threshold_kw": mean_kw + 2 * std_kw,
                           "slope_kw_per_minute": slope,
                           "sample_count": len(queue)})
    return alerts
