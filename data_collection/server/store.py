"""读取 ODS/DWD/Spark 报告供看板展示。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from cleaning.rules import DATA_NATURE
from lakehouse_local.paths import ensure_layout


class LakeStore:
    def latest_meta(self) -> dict[str, Any]:
        layout = ensure_layout()
        out: dict[str, Any] = {"ods": None, "dwd": None}
        ods_meta = layout["meta"] / "latest_ods_batch.json"
        dwd_meta = layout["meta"] / "latest_dwd_batch.json"
        if ods_meta.exists():
            out["ods"] = json.loads(ods_meta.read_text(encoding="utf-8"))
        if dwd_meta.exists():
            out["dwd"] = json.loads(dwd_meta.read_text(encoding="utf-8"))
        return out

    def overview(self) -> dict[str, Any]:
        meta = self.latest_meta()
        overview: dict[str, Any] = {
            "title_zh": "基于 Spark 的多城市气象与空气质量数据采集及预处理系统",
            "data_nature": DATA_NATURE,
            "has_ods": bool(meta.get("ods")),
            "has_dwd": bool(meta.get("dwd")),
            "batch_id": None,
            "engine": None,
            "rows": 0,
            "reconcile": None,
            "steps": [],
            "architecture_note": "日常：本机 Spark local[1]；验收：两节点 VM Spark/HDFS。pandas 仅备用。",
        }
        if meta.get("ods"):
            overview["batch_id"] = meta["ods"].get("batch_id")
            overview["collected_at_utc"] = meta["ods"].get("collected_at_utc")
        if not meta.get("dwd"):
            return overview

        overview["batch_id"] = meta["dwd"].get("batch_id")
        overview["engine"] = meta["dwd"].get("engine")
        overview["rows"] = meta["dwd"].get("rows") or 0
        overview["hdfs_dwd"] = meta["dwd"].get("hdfs_dwd")
        report_path = Path(meta["dwd"].get("quality_report") or "")
        if report_path.exists():
            quality = json.loads(report_path.read_text(encoding="utf-8"))
            overview["reconcile"] = quality.get("reconcile") or quality.get("summary")
            overview["stats"] = quality.get("stats")
            overview["cleaning_policy"] = quality.get("cleaning_policy")
            overview["architecture"] = quality.get("architecture")
            overview["quality_report"] = str(report_path)
            if quality.get("steps"):
                overview["steps"] = [
                    {"name": s.get("name"), "detail": json.dumps(s, ensure_ascii=False)[:180]}
                    for s in quality["steps"]
                ]
            elif quality.get("reconcile"):
                r = quality["reconcile"]
                overview["steps"] = [
                    {"name": "duplicates_removed", "detail": str(r.get("duplicates_removed"))},
                    {"name": "rows_out", "detail": str(r.get("rows_out"))},
                    {"name": "pollution_peaks", "detail": json.dumps(r.get("pollution_peaks_marked"), ensure_ascii=False)},
                ]
        return overview

    def sample_rows(self, limit: int = 40) -> list[dict[str, Any]]:
        meta = self.latest_meta()
        if not meta.get("dwd"):
            return []
        dwd = Path(meta["dwd"]["dwd_dir"])
        for name in ("cleaned.csv", "cleaned_from_hdfs.csv"):
            path = dwd / name
            if path.exists():
                df = pd.read_csv(path, nrows=max(1, min(limit, 200)))
                cols = [
                    c
                    for c in [
                        "city_id",
                        "city_name",
                        "ts_local",
                        "temperature_2m",
                        "pm2_5",
                        "european_aqi",
                        "flag_peak_pm2_5",
                        "flag_long_gap_pm2_5",
                    ]
                    if c in df.columns
                ]
                return json.loads(df[cols].to_json(orient="records", force_ascii=False))
        return []

    def city_series(self, city_id: str = "SH") -> dict[str, Any]:
        meta = self.latest_meta()
        if not meta.get("dwd"):
            return {"city_id": city_id, "points": []}
        dwd = Path(meta["dwd"]["dwd_dir"])
        path = next((dwd / n for n in ("cleaned.csv", "cleaned_from_hdfs.csv") if (dwd / n).exists()), None)
        if not path:
            return {"city_id": city_id, "points": []}
        usecols = lambda c: c in {"city_id", "ts_local", "temperature_2m", "pm2_5", "european_aqi", "flag_peak_pm2_5"}
        df = pd.read_csv(path, usecols=usecols)
        sub = df[df["city_id"] == city_id].sort_values("ts_local")
        if len(sub) > 96:
            sub = sub.iloc[:: max(1, len(sub) // 96)]
        points = []
        for r in sub.itertuples():
            points.append(
                {
                    "ts": str(r.ts_local),
                    "temp": None if pd.isna(r.temperature_2m) else float(r.temperature_2m),
                    "pm25": None if pd.isna(getattr(r, "pm2_5", None)) else float(r.pm2_5),
                    "aqi": None if pd.isna(getattr(r, "european_aqi", None)) else float(r.european_aqi),
                    "peak": int(getattr(r, "flag_peak_pm2_5", 0) or 0),
                }
            )
        return {"city_id": city_id, "points": points}

    def ods_manifest(self) -> dict[str, Any] | None:
        meta = self.latest_meta()
        if not meta.get("ods"):
            return None
        man = Path(meta["ods"]["ods_dir"]) / "manifest.json"
        log = Path(meta["ods"]["ods_dir"]) / "collect_log.json"
        out = json.loads(man.read_text(encoding="utf-8")) if man.exists() else meta["ods"]
        if log.exists():
            out["collect_log_summary"] = {
                k: json.loads(log.read_text(encoding="utf-8")).get(k)
                for k in (
                    "collected_at_utc",
                    "http_retry_count",
                    "http_failed_attempt_count",
                    "file_count",
                    "error_count",
                    "data_nature",
                )
            }
        return out
