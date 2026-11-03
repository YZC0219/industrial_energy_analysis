"""轻量备用：pandas 按同一字段规则清洗（Spark 不可用时演示）。

主路径请用 spark_jobs/clean_and_aggregate.py。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cleaning.rules import (  # noqa: E402
    AQ_COLS,
    DATA_NATURE,
    FIELD_RULES,
    SHORT_GAP_HOURS,
    VALUE_COLS,
    WEATHER_COLS,
)
from lakehouse_local.paths import batch_dir, ensure_layout  # noqa: E402


def _latest_ods_batch() -> Path:
    meta = ensure_layout()["meta"] / "latest_ods_batch.json"
    if not meta.exists():
        raise FileNotFoundError("尚未采集：请先运行 collectors/run_collect.py")
    info = json.loads(meta.read_text(encoding="utf-8"))
    path = Path(info["ods_dir"])
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _flatten_source(doc: dict[str, Any]) -> pd.DataFrame:
    hourly = (doc.get("fetched") or {}).get("hourly") or {}
    times = hourly.get("time") or []
    rows = []
    for i, ts in enumerate(times):
        row = {
            "city_id": doc.get("city_id"),
            "city_name": doc.get("city_name"),
            "source": doc.get("source"),
            "data_nature": doc.get("data_nature"),
            "collected_at_utc": doc.get("collected_at_utc"),
            "ts_local": ts,
        }
        for key, series in hourly.items():
            if key == "time":
                continue
            row[key] = series[i] if isinstance(series, list) and i < len(series) else None
        rows.append(row)
    return pd.DataFrame(rows)


def load_ods(ods_dir: Path) -> pd.DataFrame:
    weather_frames, aq_frames = [], []
    for path in sorted(ods_dir.glob("*.json")):
        if path.name in {"manifest.json", "collect_log.json"}:
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        df = _flatten_source(doc)
        if doc.get("source") == "open-meteo-weather":
            weather_frames.append(df)
        elif doc.get("source") == "open-meteo-air-quality":
            aq_frames.append(df)
    weather = pd.concat(weather_frames, ignore_index=True) if weather_frames else pd.DataFrame()
    aq = pd.concat(aq_frames, ignore_index=True) if aq_frames else pd.DataFrame()
    if weather.empty:
        return aq
    if aq.empty:
        return weather
    w_keep = ["city_id", "city_name", "ts_local", "data_nature", "collected_at_utc"] + [
        c for c in WEATHER_COLS if c in weather.columns
    ]
    a_keep = ["city_id", "ts_local"] + [c for c in AQ_COLS if c in aq.columns]
    return weather[w_keep].merge(aq[a_keep], on=["city_id", "ts_local"], how="outer")


def _gap_lengths(is_null: pd.Series) -> pd.Series:
    groups = (~is_null).cumsum()
    return is_null.groupby(groups).transform("sum") * is_null


def preprocess(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    report: dict[str, Any] = {
        "engine": "pandas-fallback",
        "data_nature": DATA_NATURE,
        "field_rules": FIELD_RULES,
        "steps": [],
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    out = df.copy()
    out["ts_local"] = pd.to_datetime(out["ts_local"], errors="coerce")
    for col in VALUE_COLS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    before = len(out)
    out = out.drop_duplicates(subset=["city_id", "ts_local"], keep="last")
    report["steps"].append({"name": "dedupe", "removed": before - len(out), "rows": len(out)})

    nulls_before = {c: int(out[c].isna().sum()) for c in VALUE_COLS if c in out.columns}
    peak_counts: dict[str, int] = {}
    short_filled: dict[str, int] = {}
    long_kept: dict[str, int] = {}

    out = out.sort_values(["city_id", "ts_local"])
    pieces = []
    for city, g in out.groupby("city_id", sort=False):
        g = g.copy()
        for col in VALUE_COLS:
            if col not in g.columns:
                continue
            rule = FIELD_RULES[col]
            lo, hi = rule["physical_min"], rule["physical_max"]
            bad = g[col].notna() & ((g[col] < lo) | (g[col] > hi))
            if rule["anomaly"] == "clip_to_physical_bounds_and_mark":
                g[f"flag_clip_{col}"] = bad.astype(int)
                g[col] = g[col].clip(lo, hi)
            else:
                g[f"flag_phys_{col}"] = bad.astype(int)
                g.loc[bad, col] = np.nan

            gaps = _gap_lengths(g[col].isna())
            short = (gaps > 0) & (gaps <= SHORT_GAP_HOURS)
            longg = gaps > SHORT_GAP_HOURS
            short_filled[col] = short_filled.get(col, 0) + int(short.sum())
            long_kept[col] = long_kept.get(col, 0) + int(longg.sum())
            g[f"flag_long_gap_{col}"] = longg.astype(int)
            g[f"flag_short_gap_filled_{col}"] = short.astype(int)

            if rule["missing"] == "short_gap_fill_zero_long_keep_null":
                g.loc[short, col] = 0.0
            else:
                interpolated = g[col].interpolate(limit=SHORT_GAP_HOURS, limit_area="inside")
                g.loc[short, col] = interpolated.loc[short]

            if rule["anomaly"] == "mark_iqr_peak_keep_value" and g[col].notna().sum() >= 8:
                q1, q3 = g[col].quantile(0.25), g[col].quantile(0.75)
                iqr = q3 - q1
                if iqr > 0:
                    peak = (g[col] < q1 - 1.5 * iqr) | (g[col] > q3 + 1.5 * iqr)
                    g[f"flag_peak_{col}"] = peak.fillna(False).astype(int)
                    peak_counts[col] = peak_counts.get(col, 0) + int(peak.fillna(False).sum())
                else:
                    g[f"flag_peak_{col}"] = 0
            elif rule["anomaly"] == "mark_iqr_peak_keep_value":
                g[f"flag_peak_{col}"] = 0
        pieces.append(g)

    out = pd.concat(pieces, ignore_index=True)
    nulls_after = {c: int(out[c].isna().sum()) for c in VALUE_COLS if c in out.columns}
    report["steps"].extend(
        [
            {"name": "missing_by_field", "short_gap_filled": short_filled, "long_gap_kept_null": long_kept, "nulls_before": nulls_before, "nulls_after": nulls_after},
            {"name": "pollution_peaks_marked", "counts": peak_counts, "policy": "mark only, keep value"},
        ]
    )
    report["summary"] = {
        "rows_in": before,
        "rows_out": len(out),
        "cities": sorted(out["city_id"].dropna().unique().tolist()),
        "duplicates_removed": before - report["steps"][0]["rows"],
        "total_pollution_peaks": int(sum(peak_counts.values())),
        "total_long_gaps_kept": int(sum(long_kept.values())),
        "ts_min": str(out["ts_local"].min()),
        "ts_max": str(out["ts_local"].max()),
    }
    return out, report


def run_preprocess(ods_dir: Path | None = None) -> dict[str, Any]:
    ensure_layout()
    ods = ods_dir or _latest_ods_batch()
    batch_id = ods.name
    raw = load_ods(ods)
    cleaned, report = preprocess(raw)
    dwd = batch_dir("dwd", batch_id)
    raw_csv = dwd / "raw_merged.csv"
    clean_csv = dwd / "cleaned.csv"
    raw.to_csv(raw_csv, index=False, encoding="utf-8-sig")
    cleaned.to_csv(clean_csv, index=False, encoding="utf-8-sig")
    parquet_ok = False
    try:
        cleaned.to_parquet(dwd / "cleaned.parquet", index=False)
        parquet_ok = True
    except Exception as exc:  # noqa: BLE001
        report["parquet_warning"] = str(exc)

    report["batch_id"] = batch_id
    report["note"] = "pandas 轻量备用；正式路径请跑 Spark 作业"
    report["artifacts"] = {
        "raw_merged_csv": str(raw_csv),
        "cleaned_csv": str(clean_csv),
        "cleaned_parquet": str(dwd / "cleaned.parquet") if parquet_ok else None,
    }
    report_path = ensure_layout()["reports"] / f"quality_pandas_{batch_id}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = ensure_layout()["meta"] / "latest_dwd_batch.json"
    meta.write_text(
        json.dumps(
            {
                "batch_id": batch_id,
                "engine": "pandas-fallback",
                "dwd_dir": str(dwd),
                "quality_report": str(report_path),
                "rows": len(cleaned),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[dwd-pandas] rows={len(cleaned)} → {clean_csv}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ods-dir", default="")
    args = parser.parse_args()
    run_preprocess(Path(args.ods_dir) if args.ods_dir else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
