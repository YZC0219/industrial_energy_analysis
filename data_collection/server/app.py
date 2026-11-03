"""基于 Spark 的多城市气象与空气质量数据采集及预处理 — 作业台。"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.run_collect import collect_batch  # noqa: E402
from server.store import LakeStore  # noqa: E402

WEB_DIR = ROOT / "web"
store = LakeStore()
app = FastAPI(title="Spark气象空气质量采集预处理", version="3.0.0")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "spark-meteo-aq-course", "vm_required_for_demo": False}


@app.get("/api/v1/overview")
def overview() -> dict:
    return store.overview()


@app.get("/api/v1/samples")
def samples(limit: int = Query(40, ge=1, le=200)) -> dict:
    return {"samples": store.sample_rows(limit)}


@app.get("/api/v1/series")
def series(city_id: str = Query("SH")) -> dict:
    return store.city_series(city_id.upper())


@app.get("/api/v1/ods")
def ods() -> dict:
    return {"manifest": store.ods_manifest()}


@app.post("/api/v1/run")
def run_pipeline(
    past_days: int = Query(3, ge=1, le=7),
    forecast_days: int = Query(1, ge=0, le=2),
    engine: str = Query("spark-local", pattern="^(spark-local|spark-cluster|pandas)$"),
) -> dict:
    man = collect_batch(past_days=past_days, forecast_days=forecast_days)
    if man["file_count"] == 0:
        return {"ok": False, "error": "采集失败", "manifest": man}

    result: dict = {"ok": True, "ods": man, "engine": engine}
    if engine == "spark-local":
        try:
            from tools.run_spark_local import run_local

            result["spark"] = run_local()
        except Exception as exc:  # noqa: BLE001
            from preprocess.pipeline import run_preprocess

            result["fallback"] = "pandas"
            result["pandas"] = run_preprocess()
            result["spark_error"] = str(exc)
    elif engine == "spark-cluster":
        from tools.run_spark_cluster import run_cluster

        result["spark"] = run_cluster()
    else:
        from preprocess.pipeline import run_preprocess

        result["pandas"] = run_preprocess()
    result["overview"] = store.overview()
    return result


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
