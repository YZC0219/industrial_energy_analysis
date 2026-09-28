"""Build a standalone workshop energy baseline and savings scenario assessment.

This tool only reads the existing cleaned CSV artifacts. It does not modify the
warehouse, ETL pipeline, SQL analysis, API, or existing reports.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


ENERGY = {
    "E01": ("电力", "kWh", 0.1229, 0.5703),
    "E02": ("天然气", "m³", 1.3300, 2.1622),
    "E03": ("蒸汽", "t", 95.7000, 260.0000),
    "E04": ("工业水", "m³", 0.2429, 0.3440),
    "E05": ("压缩空气", "m³", 0.0400, 0.0000),
    "E06": ("柴油", "kg", 1.4571, 3.0959),
}
WORKSHOPS = {
    "W01": ("熔炼车间", "吨", False),
    "W02": ("轧制车间", "吨", False),
    "W03": ("热处理车间", "吨", False),
    "W04": ("机加工车间", "件", False),
    "W05": ("表面处理车间", "平方米", False),
    "W06": ("装配车间", "台", False),
    "W07": ("动力站", "吨蒸汽", True),
    "W08": ("包装车间", "件", False),
}
ENERGY_COLUMNS = {
    "record_date", "workshop_code", "energy_code", "consumption", "cost"
}
PRODUCTION_COLUMNS = {"record_date", "workshop_code", "output_qty", "output_unit"}


def build_assessment(energy_path: Path, production_path: Path, output_dir: Path,
                     scenario_rates: tuple[float, ...] = (0.03, 0.05, 0.10)) -> dict:
    energy = pd.read_csv(energy_path, parse_dates=["record_date"])
    production = pd.read_csv(production_path, parse_dates=["record_date"])
    _require_columns(energy, ENERGY_COLUMNS, energy_path)
    _require_columns(production, PRODUCTION_COLUMNS, production_path)
    _validate_codes(energy, production)
    if energy.duplicated(["record_date", "workshop_code", "energy_code"]).any():
        raise ValueError("能源数据存在重复的 日期×车间×能源 业务键")
    if production.duplicated(["record_date", "workshop_code"]).any():
        raise ValueError("产量数据存在重复的 日期×车间 业务键")
    if any(not 0 < rate < 1 for rate in scenario_rates):
        raise ValueError("情景比例必须大于 0 且小于 1")

    # W07 is a utility producer; including its steam production and downstream
    # steam consumption together would double count energy and mix output units.
    energy = energy.loc[energy.workshop_code != "W07"].copy()
    production = production.loc[production.workshop_code != "W07"].copy()
    energy["year"] = energy.record_date.dt.year
    production["year"] = production.record_date.dt.year
    energy["std_coal_tce"] = energy.apply(
        lambda row: row.consumption * ENERGY[row.energy_code][2] / 1000, axis=1
    )
    energy["co2_t"] = energy.apply(
        lambda row: row.consumption * ENERGY[row.energy_code][3] / 1000, axis=1
    )
    energy_annual = energy.groupby(["year", "workshop_code"], as_index=False).agg(
        energy_tce=("std_coal_tce", "sum"), cost_yuan=("cost", "sum"),
        co2_t=("co2_t", "sum"),
    )
    prod_annual = production.groupby(["year", "workshop_code"], as_index=False).agg(
        output_qty=("output_qty", "sum"),
        observed_days=("record_date", "nunique"),
        output_unit=("output_unit", "first"),
    )
    annual = energy_annual.merge(prod_annual, on=["year", "workshop_code"], how="left", validate="one_to_one")
    annual["intensity_kgce_per_unit"] = annual.energy_tce * 1000 / annual.output_qty.where(annual.output_qty > 0)
    annual["workshop_name"] = annual.workshop_code.map(lambda code: WORKSHOPS[code][0])
    annual["output_unit"] = annual.workshop_code.map(lambda code: WORKSHOPS[code][1])
    annual = annual[["year", "workshop_code", "workshop_name", "energy_tce", "cost_yuan",
                     "co2_t", "output_qty", "output_unit", "observed_days", "intensity_kgce_per_unit"]]
    annual = annual.sort_values(["year", "workshop_code"])

    pivot = annual.pivot(index="workshop_code", columns="year", values="intensity_kgce_per_unit")
    baseline = annual.loc[annual.year == 2024].set_index("workshop_code")
    current = annual.loc[annual.year == 2025].set_index("workshop_code")
    compare = current[["workshop_name", "output_unit", "energy_tce", "cost_yuan", "co2_t", "output_qty"]].copy()
    compare = compare.rename(columns={"energy_tce": "2025_energy_tce", "cost_yuan": "2025_cost_yuan",
                                      "co2_t": "2025_co2_t", "output_qty": "2025_output_qty"})
    compare["2024_intensity_kgce_per_unit"] = pivot.get(2024)
    compare["2025_intensity_kgce_per_unit"] = pivot.get(2025)
    compare["intensity_change_pct"] = (
        (compare["2025_intensity_kgce_per_unit"] / compare["2024_intensity_kgce_per_unit"] - 1) * 100
    )
    compare["2024_baseline_energy_at_2025_output_tce"] = (
        compare["2024_intensity_kgce_per_unit"] * compare["2025_output_qty"] / 1000
    )
    compare["baseline_gap_tce"] = (
        compare["2024_baseline_energy_at_2025_output_tce"] - compare["2025_energy_tce"]
    )
    compare = compare.reset_index().sort_values("workshop_code")

    energy["energy_name"] = energy.energy_code.map(lambda code: ENERGY[code][0])
    opportunity = energy.loc[energy.year == 2025].groupby(
        ["workshop_code", "energy_code", "energy_name"], as_index=False
    ).agg(consumption=("consumption", "sum"), cost_yuan=("cost", "sum"),
          energy_tce=("std_coal_tce", "sum"), co2_t=("co2_t", "sum"))
    opportunity["workshop_name"] = opportunity.workshop_code.map(lambda code: WORKSHOPS[code][0])
    opportunity["energy_unit"] = opportunity.energy_code.map(lambda code: ENERGY[code][1])
    scenario_rows = []
    for rate in scenario_rates:
        scenario = opportunity.copy()
        scenario["reduction_rate_pct"] = rate * 100
        scenario["scenario_saving_tce"] = scenario.energy_tce * rate
        scenario["scenario_cost_saving_yuan"] = scenario.cost_yuan * rate
        scenario["scenario_co2_reduction_t"] = scenario.co2_t * rate
        scenario_rows.append(scenario)
    scenarios = pd.concat(scenario_rows, ignore_index=True)
    scenarios = scenarios.sort_values(["reduction_rate_pct", "scenario_cost_saving_yuan"], ascending=[True, False])

    output_dir.mkdir(parents=True, exist_ok=True)
    annual.to_csv(output_dir / "workshop_annual_baseline.csv", index=False, encoding="utf-8-sig")
    compare.to_csv(output_dir / "workshop_intensity_comparison.csv", index=False, encoding="utf-8-sig")
    scenarios.to_csv(output_dir / "energy_savings_scenarios.csv", index=False, encoding="utf-8-sig")
    _write_summary(output_dir / "assessment.md", annual, compare, scenarios, scenario_rates)
    return {"workshops": len(compare), "scenario_rows": len(scenarios), "output_dir": str(output_dir)}


def _require_columns(frame: pd.DataFrame, required: set[str], path: Path) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{path} 缺少必需字段: {', '.join(missing)}")


def _validate_codes(energy: pd.DataFrame, production: pd.DataFrame) -> None:
    unknown_energy = sorted(set(energy.energy_code.dropna()) - set(ENERGY))
    unknown_workshops = sorted(
        (set(energy.workshop_code.dropna()) | set(production.workshop_code.dropna())) - set(WORKSHOPS)
    )
    if unknown_energy or unknown_workshops:
        raise ValueError(f"发现未登记编码: energy={unknown_energy}, workshop={unknown_workshops}")
    if energy[["consumption", "cost"]].isna().any().any() or production.output_qty.isna().any():
        raise ValueError("输入存在缺失的消耗量、费用或产量，停止生成可能误导的基线")


def _write_summary(path: Path, annual: pd.DataFrame, compare: pd.DataFrame,
                   scenarios: pd.DataFrame, rates: tuple[float, ...]) -> None:
    lines = [
        "# 阶段五：能效基线与节能情景评估",
        "",
        "> 本报告由现有清洗 CSV 生成；当前数据为项目模拟数据。节能比例是情景假设，",
        "> 不是已实施措施的实测收益、承诺值或投资回报结论。",
        "",
        "## 口径与边界",
        "",
        "- 基线按车间、年度汇总；单耗为年度折标煤（kgce）除以年度产量。",
        "- 单耗比较仅使用 2024 与 2025 两年均有数据的车间，并按各自产量单位解释。",
        "- 排除 W07 动力站，避免公用工程蒸汽生产与消费重复计入，且不混合不同产量单位。",
        "- “基线差额”是 2024 单耗乘以 2025 产量再减去 2025 实耗；负值表示强度恶化，不代表因果归因。",
        "- 能源品种情景假设 2025 年各车间该能源消耗同比例下降；费用按现有实付费用同比例估算，未计改造成本、负荷影响或价格变化。",
        "- 碳排按项目指标字典中的因子估算；压缩空气按二次能源因子 0 处理，以避免重复计碳。",
        "",
        "## 车间年度单耗对照",
        "",
        "| 车间 | 单位 | 2024 kgce/单位 | 2025 kgce/单位 | 变化 | 2024 基线下 2025 产量能耗 (tce) | 基线差额 (tce) |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in compare.iterrows():
        lines.append(
            f"| {row.workshop_name} | {row.output_unit} | "
            f"{_fmt(row['2024_intensity_kgce_per_unit'])} | "
            f"{_fmt(row['2025_intensity_kgce_per_unit'])} | "
            f"{_fmt(row['intensity_change_pct'], 2)}% | "
            f"{_fmt(row['2024_baseline_energy_at_2025_output_tce'])} | "
            f"{_fmt(row['baseline_gap_tce'])} |"
        )
    lines.extend(["", "## 节能情景汇总", "", "| 假设降幅 | 节能量 (tce) | 费用变化估算 (元) | 碳减排估算 (tCO₂) |", "|---:|---:|---:|---:|"])
    for rate in rates:
        part = scenarios.loc[scenarios.reduction_rate_pct == rate * 100]
        lines.append(f"| {rate:.0%} | {part.scenario_saving_tce.sum():,.2f} | {part.scenario_cost_saving_yuan.sum():,.2f} | {part.scenario_co2_reduction_t.sum():,.2f} |")
    lines.extend(["", "## 可复核产物", "", "- `workshop_annual_baseline.csv`：年度车间基线。",
                  "- `workshop_intensity_comparison.csv`：年度单耗、基线差额及同比。",
                  "- `energy_savings_scenarios.csv`：车间×能源品种×假设比例的情景明细。", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _fmt(value: float, digits: int = 3) -> str:
    return "—" if pd.isna(value) else f"{value:,.{digits}f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="生成独立的车间能效基线与节能情景评估")
    parser.add_argument("--energy", type=Path, default=Path("output/clean_energy.csv"))
    parser.add_argument("--production", type=Path, default=Path("output/clean_production.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("output/phase5_assessment"))
    parser.add_argument("--rates", default="0.03,0.05,0.10", help="情景比例，逗号分隔，例如 0.03,0.05,0.10")
    args = parser.parse_args()
    rates = tuple(float(value.strip()) for value in args.rates.split(",") if value.strip())
    result = build_assessment(args.energy, args.production, args.output_dir, rates)
    print(f"已生成阶段五评估：{result['workshops']} 个车间，{result['scenario_rows']} 条情景明细，目录 {result['output_dir']}")


if __name__ == "__main__":
    main()
