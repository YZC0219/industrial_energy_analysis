"""Diagnose high-use operating intervals in the public UCI steel dataset.

Read-only, standalone Stage 5 analysis. All findings are screening evidence;
they are not confirmed faults or measured savings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# The input is small; one BLAS thread keeps the standalone run usable when
# Docker and the local VM already occupy most workstation memory.
os.environ["OPENBLAS_NUM_THREADS"] = "1"
import pandas as pd


GROUPS = ["Load_Type", "WeekStatus", "minute_of_day"]
REQUIRED = {
    "date", "Usage_kWh", "Lagging_Current_Reactive.Power_kVarh",
    "Lagging_Current_Power_Factor", "NSM", "WeekStatus", "Load_Type",
}


def run(input_path: Path, output_dir: Path, tariff_yuan_per_kwh: float | None = None,
        min_peer_samples: int = 20) -> dict:
    raw = pd.read_csv(input_path)
    missing = sorted(REQUIRED - set(raw.columns))
    if missing:
        raise ValueError(f"输入缺少字段: {missing}")
    if len(raw) != 35_040:
        raise ValueError(f"UCI 数据预期 35,040 行，实际 {len(raw):,} 行")
    raw["timestamp"] = pd.to_datetime(raw.date, format="%d/%m/%Y %H:%M", errors="raise")
    raw["usage_kwh"] = pd.to_numeric(raw.Usage_kWh, errors="raise")
    raw["lagging_reactive_kvarh"] = pd.to_numeric(
        raw["Lagging_Current_Reactive.Power_kVarh"], errors="raise"
    )
    raw["lagging_power_factor_pct"] = pd.to_numeric(
        raw["Lagging_Current_Power_Factor"], errors="raise"
    )
    raw["minute_of_day"] = raw.timestamp.dt.hour * 60 + raw.timestamp.dt.minute
    raw = raw.sort_values("timestamp").reset_index(drop=True)
    if raw.timestamp.duplicated().any():
        raise ValueError("数据存在重复时间戳")
    gaps = raw.timestamp.diff().dropna().ne(pd.Timedelta(minutes=15)).sum()
    if gaps:
        raise ValueError(f"15 分钟时间序列存在 {int(gaps)} 个间隔缺口")
    if raw[["usage_kwh", "lagging_reactive_kvarh", "lagging_power_factor_pct"]].isna().any().any():
        raise ValueError("目标或诊断字段存在缺失值")
    if tariff_yuan_per_kwh is not None and tariff_yuan_per_kwh < 0:
        raise ValueError("电价不能为负数")

    # Chronological holdout: learn matched operating-regime thresholds from
    # January–October, then apply them only to November–December.
    cutoff = pd.Timestamp("2018-11-01")
    reference = raw.loc[raw.timestamp < cutoff]
    analysis = raw.loc[raw.timestamp >= cutoff].copy()
    if reference.empty or analysis.empty:
        raise ValueError("数据必须同时覆盖 2018-11-01 前后的基线期和评估期")
    grouped = reference.groupby(GROUPS, observed=True)["usage_kwh"]
    baseline = grouped.agg(
        peer_n="size", peer_median_kwh="median",
        peer_p95_kwh=lambda values: values.quantile(.95),
    ).reset_index()
    analysis = analysis.merge(baseline, on=GROUPS, how="left", validate="many_to_one")
    analysis["peer_n"] = analysis.peer_n.fillna(0).astype(int)
    analysis["excess_over_peer_p95_kwh"] = (analysis.usage_kwh - analysis.peer_p95_kwh).clip(lower=0)
    analysis["high_use_candidate"] = (
        (analysis.peer_n >= min_peer_samples)
        & analysis.peer_p95_kwh.notna()
        & (analysis.usage_kwh > analysis.peer_p95_kwh)
    )

    # Aggregate contiguous 15-minute candidates as episodes, retaining matched
    # peer evidence and source rows for follow-up.
    flagged = analysis.loc[analysis.high_use_candidate].copy()
    if len(flagged):
        new_episode = flagged.timestamp.diff().ne(pd.Timedelta(minutes=15))
        flagged["episode_id"] = new_episode.cumsum()
        episodes = flagged.groupby("episode_id", as_index=False).agg(
            start=("timestamp", "min"), end=("timestamp", "max"),
            interval_count=("timestamp", "size"), load_type=("Load_Type", "first"),
            day_type=("WeekStatus", "first"),
            measured_energy_kwh=("usage_kwh", "sum"),
            peer_p95_energy_kwh=("peer_p95_kwh", "sum"),
            excess_over_peer_p95_kwh=("excess_over_peer_p95_kwh", "sum"),
            min_peer_sample_count=("peer_n", "min"),
            mean_lagging_pf_pct=("lagging_power_factor_pct", "mean"),
            measured_reactive_kvarh=("lagging_reactive_kvarh", "sum"),
        )
        episodes["start"] = episodes.start.dt.strftime("%Y-%m-%d %H:%M")
        episodes["end"] = episodes.end.dt.strftime("%Y-%m-%d %H:%M")
        episodes = episodes.sort_values("excess_over_peer_p95_kwh", ascending=False)
    else:
        episodes = pd.DataFrame(columns=["episode_id", "start", "end", "interval_count", "load_type",
            "day_type", "measured_energy_kwh", "peer_p95_energy_kwh", "excess_over_peer_p95_kwh",
            "min_peer_sample_count", "mean_lagging_pf_pct", "measured_reactive_kvarh"])

    profiles = baseline.rename(columns={"peer_n": "sample_count", "peer_median_kwh": "median_kwh",
                                        "peer_p95_kwh": "p95_kwh"})
    profiles["reference_period"] = "2018-01-01 through 2018-10-31"
    # A review queue, not an electrical compliance finding: low lagging PF plus
    # reactive use is a prompt to inspect equipment and the utility tariff.
    pf_review = analysis.loc[
        (analysis.lagging_power_factor_pct < 90)
        & (analysis.lagging_reactive_kvarh > 0)
    ].copy()
    pf_review = pf_review.sort_values(
        ["lagging_power_factor_pct", "lagging_reactive_kvarh"], ascending=[True, False]
    ).head(250)
    pf_review = pf_review[["timestamp", "Load_Type", "WeekStatus", "usage_kwh",
                           "lagging_power_factor_pct", "lagging_reactive_kvarh"]]
    pf_review["timestamp"] = pf_review.timestamp.dt.strftime("%Y-%m-%d %H:%M")

    # Sensitivity is a fraction of excess over the matched peer P95, not a claim
    # that the excess is controllable. Monetization appears only with user input.
    excess_kwh = float(episodes.excess_over_peer_p95_kwh.sum()) if len(episodes) else 0.0
    scenarios = []
    for reduction_pct in (10, 25, 50):
        saving_kwh = excess_kwh * reduction_pct / 100
        item = {"share_of_candidate_excess_reduced_pct": reduction_pct,
                "scenario_saving_kwh": saving_kwh,
                "scenario_cost_yuan": (saving_kwh * tariff_yuan_per_kwh
                                        if tariff_yuan_per_kwh is not None else None),
                "assumed_tariff_yuan_per_kwh": tariff_yuan_per_kwh}
        scenarios.append(item)
    scenario_df = pd.DataFrame(scenarios)

    output_dir.mkdir(parents=True, exist_ok=True)
    profiles.to_csv(output_dir / "load_profiles_by_regime.csv", index=False, encoding="utf-8-sig")
    episodes.head(100).to_csv(output_dir / "high_use_review_episodes.csv", index=False, encoding="utf-8-sig")
    pf_review.to_csv(output_dir / "power_factor_review_queue.csv", index=False, encoding="utf-8-sig")
    scenario_df.to_csv(output_dir / "candidate_savings_scenarios.csv", index=False, encoding="utf-8-sig")
    provenance = {
        "source_file": str(input_path),
        "source_sha256": _sha256(input_path),
        "rows": int(len(raw)),
        "timestamp_start": raw.timestamp.min().isoformat(),
        "timestamp_end": raw.timestamp.max().isoformat(),
        "baseline_period": "2018-01-01 through 2018-10-31",
        "evaluation_period": "2018-11-01 through 2018-12-31",
        "evaluation_rows": int(len(analysis)),
        "timestamp_duplicates": 0,
        "non_15_minute_gaps": int(gaps),
        "minimum_peer_sample_count": min_peer_samples,
        "screening_method": "January-October baseline empirical 95th percentile within Load_Type × WeekStatus × quarter-hour-of-day, applied to November-December holdout",
        "scenario_basis": "share of candidate measured kWh excess over matched peer P95",
        "tariff_yuan_per_kwh": tariff_yuan_per_kwh,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "limitations": [
            "single facility and one calendar year",
            "Load_Type and time matching are observational strata, not causal controls",
            "high-use candidates are review signals, not confirmed faults or avoidable energy",
            "no production quantity, equipment map, intervention records, or tariff supplied",
            "scenario savings are sensitivities; they are not measured savings or ROI",
        ],
    }
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_report(output_dir / "diagnosis.md", raw, analysis, episodes, pf_review, scenario_df,
                  excess_kwh, tariff_yuan_per_kwh, min_peer_samples)
    return {"rows": len(raw), "evaluation_rows": len(analysis),
            "candidate_intervals": int(analysis.high_use_candidate.sum()),
            "review_episodes": len(episodes), "power_factor_review_rows": len(pf_review),
            "candidate_excess_kwh": excess_kwh, "output_dir": str(output_dir)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_report(path: Path, raw: pd.DataFrame, analysis: pd.DataFrame, episodes: pd.DataFrame,
                  pf_review: pd.DataFrame, scenarios: pd.DataFrame,
                  excess_kwh: float, tariff: float | None, min_peer_samples: int) -> None:
    top = episodes.head(10)
    lines = [
        "# 真实钢厂负荷诊断与措施情景",
        "",
        "> 这是 UCI 单一钢厂 2018 年数据的筛查分析。异常候选不是确认故障；节能情景不是已实现或可承诺的节能量。",
        "",
        "## 数据与方法",
        "",
        f"- 时间范围：{raw.timestamp.min():%Y-%m-%d} 至 {raw.timestamp.max():%Y-%m-%d}，共 {len(raw):,} 条 15 分钟记录。",
        "- 基线期：2018 年 1 月至 10 月；评估期：2018 年 11 月至 12 月。只用基线期数据计算参照阈值。",
        f"- 高用能筛查：在相同 Load_Type、WeekStatus 和日内 15 分钟时段内，基线期至少有 {min_peer_samples} 个参照样本，且评估期实测用电高于基线经验 P95。",
        "- 连续相邻的候选时段合并成事件；事件中的超额量相对匹配组 P95 计算。P95 是复核队列阈值，不是设备故障阈值。",
        f"- 低功率因数复核队列：滞后功率因数低于 90% 且无功电量为正的记录，最多列出 250 条；90% 仅是项目筛查参数，不代表法规或电网考核线。",
        "",
        "## 诊断摘要",
        "",
        f"- 评估期记录：{len(analysis):,}；高用能候选间隔：{int(analysis.high_use_candidate.sum()):,}。",
        f"- 合并后的候选事件：{len(episodes):,}。",
        f"- 候选超额用电量合计：{excess_kwh:,.2f} kWh。此量可能包含未观测到的合理工况差异，不可直接当作可节省量。",
        f"- 低功率因数复核队列：最多 {len(pf_review):,} 条。应结合设备、电容补偿状态和实际电费条款排查。",
        "",
        "## 优先复核事件（前 10）",
        "",
        "| 开始 | 结束 | 负荷类型 | 日型 | 间隔数 | 实测电量 (kWh) | 匹配 P95 (kWh) | 超额 (kWh) | 平均滞后功率因数 (%) |",
        "|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in top.itertuples(index=False):
        lines.append(f"| {row.start} | {row.end} | {row.load_type} | {row.day_type} | {row.interval_count} | "
                     f"{row.measured_energy_kwh:.2f} | {row.peer_p95_energy_kwh:.2f} | "
                     f"{row.excess_over_peer_p95_kwh:.2f} | {row.mean_lagging_pf_pct:.1f} |")
    lines.extend([
        "", "## 措施优先级建议", "",
        "1. **先核实尖峰事件：** 按事件时间回看班次、设备启停、订单/产品与检修日志；若有可控的集中启动或待机运行，再评估错峰启动、停机联锁或运行设定优化。当前数据没有设备映射，不能把事件归因到具体设备。",
        "2. **复核无功治理：** 对低滞后功率因数且无功电量偏高的时段，检查电容器组、变频器和主要电机运行状态；是否有经济收益取决于合同功率因数考核和设备改造成本。数据不足以估算回收期。",
        "3. **补齐验证数据：** 采集设备/产线标识、产量、运行状态、工作班次、能源账单和措施实施日期，再确定可比基线和实施后观察期。",
        "", "## 超额量敏感性情景", "",
        "| 假设可降低候选超额量 | 情景电量 (kWh) | 情景费用 (元) |",
        "|---:|---:|---:|",
    ])
    for row in scenarios.itertuples(index=False):
        money = "未估算（未提供电价）" if pd.isna(row.scenario_cost_yuan) else f"{row.scenario_cost_yuan:,.2f}"
        lines.append(f"| {row.share_of_candidate_excess_reduced_pct}% | {row.scenario_saving_kwh:,.2f} | {money} |")
    lines.extend([
        "", "费用仅在显式传入电价后计算；未计需量费用、峰谷价差、措施成本、产量影响和维护成本，因此不是净收益或投资回报。",
        "", "## 结论边界", "",
        "该数据来自一座钢厂的单一年份，不含产量、工序/设备映射、干预事件和电价。时间切分降低了阈值回看评估期的泄漏，但 Load_Type 仍是观察性分组，不构成因果控制。这里识别的是可供人工核查的高用电与低功率因数信号；设备原因、可控性及措施效果都需要补充现场数据确认。", "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 UCI 钢厂负荷诊断、复核队列与情景分析")
    parser.add_argument("--input", type=Path,
                        default=Path("data/real/uci_steel_energy/Steel_industry_data.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("output/phase5_real_data"))
    parser.add_argument("--tariff-yuan-per-kwh", type=float, default=None,
                        help="可选电价；不提供时不估算货币收益")
    parser.add_argument("--min-peer-samples", type=int, default=20)
    args = parser.parse_args()
    if args.min_peer_samples < 2:
        parser.error("--min-peer-samples 必须至少为 2")
    result = run(args.input, args.output_dir, args.tariff_yuan_per_kwh, args.min_peer_samples)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
