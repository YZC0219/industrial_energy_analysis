"""Summarize independently metered industrial assets from the public Parquet mirror.

This is an evidence report, not a facility total or a savings verification.
Only the ALL-phase 15-minute energy signal is used, to avoid phase double counts.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "real" / "asset_energy"
OUTPUT = ROOT / "output" / "phase5_asset_energy"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    import duckdb

    provenance_path = SOURCE / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    for item in provenance["files"]:
        path = SOURCE / item["file"]
        if digest(path) != item["sha256"]:
            raise ValueError(f"源文件 SHA-256 不匹配：{path}")

    con = duckdb.connect()
    con.execute("SET threads=2")
    paths = {
        "energy": SOURCE / "fact" / "fact_energy_15m.parquet",
        "asset": SOURCE / "dim" / "dim_asset.parquet",
        "obs_window": SOURCE / "dim" / "dim_observation_window.parquet",
        "quality": SOURCE / "fact" / "fact_data_quality_event.parquet",
        "signal": SOURCE / "dim" / "dim_signal_info.parquet",
    }
    for name, path in paths.items():
        escaped_path = path.as_posix().replace("'", "''")
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{escaped_path}')")
    all_signal = con.execute("""
        SELECT signal_id FROM signal
        WHERE signal_name = 'energy_kwh_15m' AND phase = 'ALL' AND unit = 'kWh'
    """).fetchall()
    if all_signal != [(4,)]:
        raise ValueError(f"ALL 相电量信号定义已改变：{all_signal}")

    # Row counts are deliberately retained at each quality gate; a subset is not
    # extrapolated to full-year consumption. Quality event intervals are excluded
    # conservatively even when the original fault describes a specific phase.
    rows = con.execute("""
        WITH all_phase AS (
            SELECT e.asset_id, a.asset_type, a.is_submeter, e.time,
                   e.value_float AS kwh, w.is_reliable_window,
                   EXISTS (
                       SELECT 1 FROM quality q
                       WHERE q.asset_id = e.asset_id
                         AND e.time >= q.issue_start
                         AND e.time < COALESCE(q.issue_end, q.asset_stream_end)
                   ) AS flagged
            FROM energy e
            JOIN asset a USING (asset_id)
            JOIN obs_window w USING (observation_window_id)
            WHERE e.signal_id = 4
        )
        SELECT asset_id, asset_type, is_submeter,
               COUNT(*) AS all_phase_windows,
               COUNT(*) FILTER (WHERE is_reliable_window = 1
                   AND NOT flagged AND kwh >= 0 AND isfinite(kwh)) AS usable_windows,
               COUNT(*) FILTER (WHERE flagged) AS quality_flagged_windows,
               COUNT(*) FILTER (WHERE kwh < 0) AS negative_windows,
               MIN(time) AS first_observation_utc,
               MAX(time) AS last_observation_utc,
               ROUND(SUM(kwh) FILTER (WHERE is_reliable_window = 1
                   AND NOT flagged AND kwh >= 0 AND isfinite(kwh)), 2) AS observed_usable_kwh,
               ROUND(AVG(kwh) FILTER (WHERE is_reliable_window = 1
                   AND NOT flagged AND kwh >= 0 AND isfinite(kwh)) * 4, 2) AS mean_kw_usable
        FROM all_phase
        GROUP BY asset_id, asset_type, is_submeter
        ORDER BY asset_type, asset_id
    """).fetchall()
    columns = [column[0] for column in con.description]
    asset_rows = [dict(zip(columns, row)) for row in rows]

    OUTPUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT / "asset_energy_summary.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(asset_rows)

    compressors = [row for row in asset_rows if row["asset_type"] == "CompressedAir"]
    if len(compressors) != 2:
        raise ValueError(f"预期 2 个压缩空气资产，实际 {len(compressors)} 个")
    total_windows = sum(row["all_phase_windows"] for row in asset_rows)
    usable_windows = sum(row["usable_windows"] for row in asset_rows)
    summary = {
        "source": provenance["source_page"],
        "source_doi": provenance["source_doi"],
        "source_provenance_sha256": digest(provenance_path),
        "asset_count": len(asset_rows),
        "all_phase_windows": total_windows,
        "usable_windows": usable_windows,
        "usable_share": round(usable_windows / total_windows, 4),
        "quality_flagged_windows": sum(row["quality_flagged_windows"] for row in asset_rows),
        "negative_windows": sum(row["negative_windows"] for row in asset_rows),
        "compressed_air_assets": compressors,
        "interpretation": "Independent asset meter summaries; do not sum across meters as facility total. No production, intervention dates, costs, or measured savings are present.",
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# 真实设备级用电数据：独立诊断",
        "",
        f"来源：[公开数据镜像]({provenance['source_page']})；[原始数据 DOI]({provenance['source_doi']})。",
        "读取 ALL 相 15 分钟电量（`signal_id=4`），每个设备只统计自己的计量窗口。",
        "剔除不可靠窗口、已标注质量事件覆盖的时间窗、负值和非有限值；不外推缺测时间。",
        "",
        f"- 资产数：{len(asset_rows)}；ALL 相窗口：{total_windows:,}；可用窗口：{usable_windows:,}（{usable_windows/total_windows:.1%}）。",
        f"- 质量事件覆盖窗口：{summary['quality_flagged_windows']:,}；负值窗口：{summary['negative_windows']:,}（两类可能重叠）。",
        "- 压缩空气设备：",
        "",
        "| 资产 ID | 时间范围（UTC） | 可用窗口 | 可用窗口实测电量 kWh | 可用窗口平均功率 kW |",
        "|---|---|---:|---:|---:|",
    ]
    for row in compressors:
        lines.append(
            f"| {row['asset_id']} | {row['first_observation_utc']} 至 {row['last_observation_utc']} "
            f"| {row['usable_windows']:,} | {row['observed_usable_kwh']:,.2f} | {row['mean_kw_usable']:,.2f} |"
        )
    lines += [
        "",
        "电量仅覆盖各设备自身的有效观测窗口；不同设备的覆盖时间和计量层级可能不同。",
        "配电、总表、光伏与下游设备可能存在上下游关系，**不能将 43 个设备电量相加作为工厂总用电**。",
        "本数据没有产品产量、措施成本、实施日期或实施后对照，不能据此计算单位产品能耗、投资回收期或已实现节能量。",
        "详细设备表见 `asset_energy_summary.csv`；源文件和机器人规则记录见 `data/real/asset_energy/provenance.json`。",
        "",
    ]
    (OUTPUT / "asset_diagnosis.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入 {OUTPUT}；资产 {len(asset_rows)} 个，可用窗口 {usable_windows:,}")


if __name__ == "__main__":
    main()
