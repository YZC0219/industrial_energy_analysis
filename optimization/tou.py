"""Continuous hourly production allocation; prices are illustrative, not tariffs."""
from __future__ import annotations

import numpy as np
from scipy.optimize import linprog


def optimize(prices, baseline_units, capacity_units, base_kw, kwh_per_unit,
             site_limit_kw, operating_days=30):
    arrays = [np.asarray(x, dtype=float) for x in
              (prices, baseline_units, capacity_units, base_kw)]
    prices, baseline, capacity, base = arrays
    if any(a.shape != (24,) or not np.isfinite(a).all() or (a < 0).any()
           for a in arrays):
        raise ValueError("Expected 24 finite non-negative hourly values")
    if not np.isfinite([kwh_per_unit, site_limit_kw, operating_days]).all() or kwh_per_unit <= 0 or site_limit_kw <= 0 or operating_days <= 0:
        raise ValueError("Invalid conversion, site limit or operating days")
    upper = np.minimum(capacity, (site_limit_kw - base) / kwh_per_unit)
    if (upper < 0).any() or (baseline > upper + 1e-8).any():
        raise ValueError("Baseline violates hourly production/site constraints")
    solution = linprog(prices * kwh_per_unit, A_eq=np.ones((1, 24)),
                       b_eq=[baseline.sum()], bounds=list(zip(np.zeros(24), upper)),
                       method="highs")
    if not solution.success:
        raise ValueError(f"Infeasible schedule: {solution.message}")
    before = float(prices @ (base + baseline * kwh_per_unit))
    after = float(prices @ (base + solution.x * kwh_per_unit))
    return {"baseline_cost_yuan": before, "optimized_cost_yuan": after,
            "daily_saving_yuan": before - after,
            "scenario_monthly_saving_yuan": (before - after) * operating_days,
            "operating_days": operating_days, "production_units": float(baseline.sum()),
            "optimized_units": solution.x.tolist(),
            "optimized_kw": (base + solution.x * kwh_per_unit).tolist(),
            "assumptions": "连续可分配产量；无启停、爬坡、最小开机时长和需量电费约束"}


def demo():
    prices = [0.35] * 7 + [0.7] * 3 + [1.1] * 4 + [0.7] * 4 + [1.4] * 3 + [0.7] * 3
    result = optimize(prices, [5.] * 24, [15.] * 24, [100.] * 24, 20., 400.)
    return {"data_source": "synthetic", "tariff_source": "illustrative_four_band",
            "prices_yuan_per_kwh": prices, **result}
