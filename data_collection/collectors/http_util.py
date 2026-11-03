"""带超时、重试与尝试日志的 HTTP GET。"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    retries: int = 3,
    timeout: float = 25.0,
    attempt_log: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    full = url if not params else f"{url}?{urlencode(params)}"
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        started = time.time()
        entry: dict[str, Any] = {
            "url": full.split("?")[0],
            "attempt": attempt,
            "ok": False,
            "elapsed_ms": None,
            "error": None,
            "http_status": None,
        }
        try:
            req = Request(full, headers={"User-Agent": "EnergyTrace-CourseCollector/2.0"})
            with urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", 200)
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("响应不是 JSON 对象")
            entry["ok"] = True
            entry["http_status"] = status
            entry["elapsed_ms"] = int((time.time() - started) * 1000)
            if attempt_log is not None:
                attempt_log.append(entry)
            return data
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            last = exc
            entry["error"] = str(exc)
            entry["elapsed_ms"] = int((time.time() - started) * 1000)
            if isinstance(exc, HTTPError):
                entry["http_status"] = exc.code
            if attempt_log is not None:
                attempt_log.append(entry)
            time.sleep(0.8 * attempt)
    raise RuntimeError(f"请求失败: {full} ({last})")
