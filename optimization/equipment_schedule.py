"""Finite-horizon equipment MILP; synthetic tariffs, initially/finally off."""
import json
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix


def optimize_equipment(prices, base_kw, site_limit_kw, equipment, precedence=()):
    prices, base = np.asarray(prices, float), np.asarray(base_kw, float)
    if prices.shape != (24,) or base.shape != (24,) or not np.isfinite([*prices, *base, site_limit_kw]).all() or min(prices) < 0 or min(base) < 0 or site_limit_kw <= 0:
        raise ValueError("Invalid 24-hour tariffs, base load or site limit")
    if not equipment or len({e["id"] for e in equipment}) != len(equipment):
        raise ValueError("Equipment IDs must be nonempty and unique")
    index = {e["id"]: i for i, e in enumerate(equipment)}
    for e in equipment:
        for key in ("rate", "kwh_per_unit", "on_kw", "startup_cost", "demand", "ramp"):
            if not np.isfinite(e[key]) or e[key] < 0:
                raise ValueError("Invalid equipment parameter")
        if e["rate"] <= 0 or e["ramp"] <= 0 or e["demand"] <= 0:
            raise ValueError("Capacity, ramp and demand must be positive")
        if type(e["min_up"]) is not int or not 1 <= e["min_up"] <= 23 or type(e["max_starts"]) is not int or e["max_starts"] < 1:
            raise ValueError("Invalid minimum run duration or start limit")
        if len(e["baseline_units"]) != 24 or len(e["baseline_on"]) != 24:
            raise ValueError("Baseline must have 24 hours")
    n = len(equipment) * 72
    def variable(i, kind, t):
        return i * 72 + kind * 24 + t  # production, on, start
    costs = np.zeros(n)
    upper = np.ones(n)
    integers = np.ones(n)
    baseline = np.zeros(n)
    rows, lower, limits = [], [], []
    def constraint(coefficients, lo=-np.inf, hi=np.inf):
        rows.append(coefficients)
        lower.append(lo)
        limits.append(hi)
    for i, e in enumerate(equipment):
        q, on = np.asarray(e["baseline_units"], float), np.asarray(e["baseline_on"], float)
        if not np.isfinite(q).all() or not np.isfinite(on).all() or (q < 0).any() or not np.isin(on, [0, 1]).all():
            raise ValueError("Invalid baseline values")
        starts = np.maximum(0, on - np.r_[0, on[:-1]])
        for t in range(24):
            a, b, s = [variable(i, kind, t) for kind in range(3)]
            upper[a], integers[a] = e["rate"], 0
            costs[a], costs[b], costs[s] = prices[t] * e["kwh_per_unit"], prices[t] * e["on_kw"], e["startup_cost"]
            baseline[a], baseline[b], baseline[s] = q[t], on[t], starts[t]
            constraint({a: 1, b: -e["rate"]}, hi=0)
            transition = {s: 1, b: -1}
            if t:
                transition[variable(i, 1, t - 1)] = 1
            constraint(transition, lo=0)
            constraint({s: 1, b: -1}, hi=0)
            if t:
                constraint({s: 1, variable(i, 1, t - 1): 1}, hi=1)
            # A run must finish within the horizon, with the last hour off.
            if t + e["min_up"] > 23:
                upper[s] = 0
            else:
                constraint({**{variable(i, 1, k): 1 for k in range(t, t + e["min_up"])}, s: -e["min_up"]}, lo=0)
            ramp = {a: 1}
            if t:
                ramp[variable(i, 0, t - 1)] = -1
            constraint(ramp, lo=-e["ramp"], hi=e["ramp"])
        upper[variable(i, 1, 23)] = 0
        constraint({variable(i, 0, t): 1 for t in range(24)}, e["demand"], e["demand"])
        constraint({variable(i, 2, t): 1 for t in range(24)}, hi=e["max_starts"])
    for t in range(24):
        constraint({variable(i, kind, t): e[key] for i, e in enumerate(equipment)
                    for kind, key in ((0, "kwh_per_unit"), (1, "on_kw"))}, hi=site_limit_kw - base[t])
    for upstream, downstream in precedence:
        if upstream not in index or downstream not in index or upstream == downstream:
            raise ValueError("Invalid process precedence")
        for t in range(24):
            constraint({**{variable(index[downstream], 0, k): 1 for k in range(t + 1)},
                        **{variable(index[upstream], 0, k): -1 for k in range(t)}}, hi=0)
    row_ids, col_ids, values = [], [], []
    for r, coefficients in enumerate(rows):
        for col, value in coefficients.items():
            row_ids.append(r); col_ids.append(col); values.append(value)
    matrix = coo_matrix((values, (row_ids, col_ids)), shape=(len(rows), n)).tocsc()
    lower, limits = np.asarray(lower), np.asarray(limits)
    def validate(vector):
        mapped = matrix @ vector
        if (vector < -1e-6).any() or (vector > upper + 1e-6).any() or (mapped < lower - 1e-6).any() or (mapped > limits + 1e-6).any():
            raise ValueError("Schedule violates production, process or equipment constraints")
        if np.max(np.abs(vector[integers == 1] - np.round(vector[integers == 1]))) > 1e-6:
            raise ValueError("Nonbinary equipment state")
    validate(baseline)
    solution = milp(costs, integrality=integers, bounds=Bounds(np.zeros(n), upper),
                    constraints=LinearConstraint(matrix, lower, limits),
                    options={"time_limit": 30, "mip_rel_gap": 0.000001})
    if not solution.success:
        raise ValueError("No proven optimal equipment schedule within the time limit")
    validate(solution.x)
    fixed = float(prices @ base)
    schedules = []
    for i, e in enumerate(equipment):
        schedules.append({"id": e["id"], "units": solution.x[i*72:i*72+24].tolist(),
                          "on": np.rint(solution.x[i*72+24:i*72+48]).astype(int).tolist(),
                          "starts": np.rint(solution.x[i*72+48:i*72+72]).astype(int).tolist()})
    optimized = float(costs @ solution.x + fixed)
    original = float(costs @ baseline + fixed)
    return {"data_source": "synthetic", "tariff_source": "illustrative_four_band",
            "baseline_cost_yuan": original, "optimized_cost_yuan": optimized,
            "daily_saving_yuan": original - optimized, "schedules": schedules,
            "mip_gap": float(solution.mip_gap), "constraints_reconciled": True,
            "assumptions": "连续小时产量+二元启停；初始无库存、时域开始前停机、末小时停机；工序间至少一小时流转；非实测收益"}


def demo_inputs():
    equipment = []
    for name, start, energy, overhead in (("heat", 8, 20, 40), ("machine", 10, 8, 15)):
        units = [5 if start <= h < start + 8 else 0 for h in range(24)]
        equipment.append({"id": name, "rate": 10, "kwh_per_unit": energy, "on_kw": overhead,
                          "startup_cost": 15, "demand": 40, "ramp": 5, "min_up": 3, "max_starts": 2,
                          "baseline_units": units, "baseline_on": [int(q > 0) for q in units]})
    return {"prices": [0.35]*7+[0.7]*3+[1.1]*4+[0.7]*4+[1.4]*3+[0.7]*3,
            "base_kw": [80]*24, "site_limit_kw": 320, "equipment": equipment,
            "precedence": [("heat", "machine")]}


if __name__ == "__main__":
    print(json.dumps(optimize_equipment(**demo_inputs()), indent=2))
