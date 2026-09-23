"""Export pandas and MySQL-result daily baselines in the Spark comparison schema."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

STD_COAL = {"E01": 0.1229, "E02": 1.3300, "E03": 95.7000, "E04": 0.2429, "E05": 0.0400, "E06": 1.4571}
CO2 = {"E01": 0.5703, "E02": 2.1622, "E03": 260.0, "E04": 0.3440, "E05": 0.0, "E06": 3.0959}
CANONICAL = ["record_date", "workshop_code", "tce", "co2_t", "cost_yuan", "unit_energy_kgce"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--energy", default="output/clean_energy.csv")
    parser.add_argument("--production", default="output/clean_production.csv")
    parser.add_argument("--mysql-snapshot", default="output/Q28_各车间日度能耗与产量.csv")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    energy = pd.read_csv(args.energy)
    production = pd.read_csv(args.production)
    energy["std_coal_kgce"] = energy["consumption"] * energy["energy_code"].map(STD_COAL)
    energy["co2_kg"] = energy["consumption"] * energy["energy_code"].map(CO2)
    daily = (energy.groupby(["record_date", "workshop_code"], as_index=False)
             .agg(std_coal_kgce=("std_coal_kgce", "sum"), co2_kg=("co2_kg", "sum"), cost_yuan=("cost", "sum")))
    daily = daily.merge(production[["record_date", "workshop_code", "output_qty"]],
                        on=["record_date", "workshop_code"], how="left", validate="one_to_one")
    daily["tce"] = daily["std_coal_kgce"] / 1000
    daily["co2_t"] = daily["co2_kg"] / 1000
    daily["unit_energy_kgce"] = daily["std_coal_kgce"] / daily["output_qty"]
    daily[CANONICAL].sort_values(CANONICAL[:2]).to_csv(out / "pandas_daily.csv", index=False)

    mysql = pd.read_csv(args.mysql_snapshot).rename(columns={
        "日期": "record_date", "车间编码": "workshop_code", "综合能耗_tce": "tce",
        "碳排放_tCO2": "co2_t", "能源费用_元": "cost_yuan", "单位产品能耗_kgce": "unit_energy_kgce",
    })
    mysql[CANONICAL].sort_values(CANONICAL[:2]).to_csv(out / "mysql_daily.csv", index=False)
    print(f"pandas={len(daily)} mysql={len(mysql)}")


if __name__ == "__main__":
    main()
