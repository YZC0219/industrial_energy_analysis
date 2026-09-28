"""Create a time-split, equipment-level review queue from public meter data.

This ranks deviations from historical operation; it does not identify production
causes or estimate achievable savings. Requires requirements-asset.txt.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "real" / "asset_energy"
OUTPUT = ROOT / "output" / "phase5_asset_review"
CUTOFF = date(2025, 11, 1)
MIN_DAILY_WINDOWS = 80
MIN_BASELINE_DAYS = 20
RELATIVE_THRESHOLD = 1.25
ABSOLUTE_THRESHOLD_KW = 2.0
NETWORK_ASSET_TYPES = {
    "Electrical_Distribution", "Electrical_GridImport", "Electrical_PVGeneration"
}


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    import duckdb

    provenance_path = SOURCE / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    for item in provenance["files"]:
        if digest(SOURCE / item["file"]) != item["sha256"]:
            raise ValueError(f"源文件 SHA-256 不一致：{item['file']}")

    con = duckdb.connect()
    con.execute("SET threads=2")
    source_views = {
        "energy": "fact/fact_energy_15m.parquet",
        "asset": "dim/dim_asset.parquet",
        "obs_window": "dim/dim_observation_window.parquet",
        "quality": "fact/fact_data_quality_event.parquet",
        "signal": "dim/dim_signal_info.parquet",
    }
    for name, relative in source_views.items():
        path = (SOURCE / relative).as_posix().replace("'", "''")
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{path}')")
    signal = con.execute("""
        SELECT signal_id FROM signal
        WHERE signal_name = 'energy_kwh_15m' AND phase = 'ALL' AND unit = 'kWh'
    """).fetchall()
    if signal != [(4,)]:
        raise ValueError(f"ALL 相电量字段不符合预期：{signal}")

    con.execute("""
        CREATE TEMP VIEW asset_day AS
        WITH usable AS (
            SELECT e.asset_id, a.asset_type, e.time, e.value_float AS kwh
            FROM energy e
            JOIN asset a USING (asset_id)
            JOIN obs_window w USING (observation_window_id)
            WHERE e.signal_id = 4
              AND w.is_reliable_window = 1
              AND e.value_float >= 0
              AND isfinite(e.value_float)
              AND NOT EXISTS (
                  SELECT 1 FROM quality q
                  WHERE q.asset_id = e.asset_id
                    AND e.time >= q.issue_start
                    AND e.time < COALESCE(q.issue_end, q.asset_stream_end)
              )
        )
        SELECT asset_id, asset_type, CAST(time AS DATE) AS day,
               CASE WHEN EXTRACT(ISODOW FROM time) >= 6 THEN 'weekend' ELSE 'weekday' END AS day_type,
               COUNT(*) AS windows, ROUND(SUM(kwh), 4) AS observed_kwh,
               ROUND(4 * AVG(kwh), 4) AS mean_kw
        FROM usable
        GROUP BY asset_id, asset_type, CAST(time AS DATE), day_type
    """)
    con.execute(f"""
        CREATE TEMP VIEW baseline AS
        SELECT asset_id, asset_type, day_type, COUNT(*) AS baseline_days,
               ROUND(MEDIAN(mean_kw), 4) AS median_kw,
               ROUND(QUANTILE_CONT(mean_kw, 0.95), 4) AS p95_kw
        FROM asset_day
        WHERE day < DATE '{CUTOFF.isoformat()}' AND windows >= {MIN_DAILY_WINDOWS}
        GROUP BY asset_id, asset_type, day_type
        HAVING COUNT(*) >= {MIN_BASELINE_DAYS}
    """)

    rows = con.execute("""
        SELECT d.asset_id, d.asset_type, d.day, d.day_type,
               d.windows, d.observed_kwh, d.mean_kw,
               b.baseline_days, b.median_kw, b.p95_kw,
               ROUND(GREATEST(b.p95_kw * ?, b.p95_kw + ?), 4) AS review_threshold_kw,
               d.mean_kw > GREATEST(b.p95_kw * ?, b.p95_kw + ?) AS is_review_candidate,
               ROUND(GREATEST(d.mean_kw - b.p95_kw, 0) * d.windows / 4, 2) AS screening_excess_kwh
        FROM asset_day d
        JOIN baseline b USING (asset_id, asset_type, day_type)
        WHERE d.day >= ? AND d.windows >= ?
        ORDER BY d.day, d.asset_id
    """, [RELATIVE_THRESHOLD, ABSOLUTE_THRESHOLD_KW,
          RELATIVE_THRESHOLD, ABSOLUTE_THRESHOLD_KW,
          CUTOFF, MIN_DAILY_WINDOWS]).fetchall()
    columns = [column[0] for column in con.description]
    observations = [dict(zip(columns, row)) for row in rows]
    all_crossings = [row for row in observations if row["is_review_candidate"]]
    candidates = [row for row in all_crossings
                  if row["asset_type"] not in NETWORK_ASSET_TYPES]
    candidates.sort(key=lambda row: row["screening_excess_kwh"], reverse=True)

    baseline_rows = con.execute("""
        SELECT * FROM baseline ORDER BY asset_type, asset_id, day_type
    """).fetchall()
    baseline_columns = [column[0] for column in con.description]
    baseline_dicts = [dict(zip(baseline_columns, row)) for row in baseline_rows]

    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUTPUT / "asset_daily_comparison.csv", columns, observations)
    write_csv(OUTPUT / "review_queue.csv", columns, candidates)
    write_csv(OUTPUT / "baseline_by_asset_daytype.csv", baseline_columns, baseline_dicts)
    result = {
        "source": provenance["source_page"],
        "source_provenance_sha256": digest(provenance_path),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_end_exclusive": CUTOFF.isoformat(),
        "review_start_inclusive": CUTOFF.isoformat(),
        "min_daily_windows": MIN_DAILY_WINDOWS,
        "min_baseline_days_per_asset_daytype": MIN_BASELINE_DAYS,
        "relative_threshold": RELATIVE_THRESHOLD,
        "absolute_threshold_kw": ABSOLUTE_THRESHOLD_KW,
        "eligible_baseline_groups": len(baseline_dicts),
        "eligible_review_asset_days": len(observations),
        "all_threshold_crossings": len(all_crossings),
        "network_meter_crossings_excluded_from_equipment_queue": len(all_crossings) - len(candidates),
        "review_candidates": len(candidates),
        "reviewed_assets": len({row["asset_id"] for row in observations}),
        "candidate_assets": len({row["asset_id"] for row in candidates}),
        "interpretation": "Candidate deviation above historical P95, not measured waste, attainable savings, or confirmed equipment failure.",
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# 真实设备级用电复核队列",
        "",
        f"来源：[公开设备数据]({provenance['source_page']})。",
        f"使用 {CUTOFF} 前的合格日期计算各资产工作日/周末平均功率 P95；只筛查其后的日期。",
        f"一天至少需要 {MIN_DAILY_WINDOWS} 个可靠、非故障、非负的 15 分钟窗口；",
        f"每个资产×日型至少需要 {MIN_BASELINE_DAYS} 个基线日。",
        f"复核阈值为 `max(历史 P95 × {RELATIVE_THRESHOLD}, 历史 P95 + {ABSOLUTE_THRESHOLD_KW} kW)`。",
        "",
        f"- 可用基线分组：{len(baseline_dicts)}；纳入筛查的资产：{result['reviewed_assets']}。",
        f"- 可比资产日：{len(observations):,}；设备复核候选：{len(candidates):,} 个资产日，涉及 {result['candidate_assets']} 个资产。",
        f"- 另有 {result['network_meter_crossings_excluded_from_equipment_queue']} 个进线/配电/光伏表阈值超出日，不混入设备队列。",
        "- 单条记录的 P95 超出量仅用于该资产内部排序，**不是确认浪费量或可实现节能量**；不跨资产加总。",
        "",
        "| 日期（UTC） | 资产 | 类型 | 日型 | 可用窗口 | 平均功率 kW | 历史 P95 kW | 筛查超出量 kWh |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for row in candidates[:10]:
        lines.append(
            f"| {row['day']} | {row['asset_id']} | {row['asset_type']} | {row['day_type']} "
            f"| {row['windows']} | {row['mean_kw']:.2f} | {row['p95_kw']:.2f} "
            f"| {row['screening_excess_kwh']:,.2f} |"
        )
    lines += [
        "",
        "已知计量质量事件覆盖的时间窗被剔除；数据没有设备运行状态、班次、产量和维护记录。",
        "超过历史阈值只说明该资产日需要人工复核，不能据此确认设备故障、工序原因或措施收益。",
        "供电、配电与下游资产可能重复计量，队列中的跨资产超出量不能用作工厂总电量变化。",
        "完整队列、所有可比资产日和分组基线分别见 `review_queue.csv`、",
        "`asset_daily_comparison.csv`、`baseline_by_asset_daytype.csv`。",
        "",
    ]
    (OUTPUT / "review.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"筛查 {len(observations):,} 个资产日；设备复核候选 {len(candidates):,} 个")


if __name__ == "__main__":
    main()
