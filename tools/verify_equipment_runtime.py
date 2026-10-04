"""Solve and independently reconcile equipment schedule and a known optimum."""
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from optimization.equipment_schedule import optimize_equipment, demo_inputs


def main():
    inputs = demo_inputs()
    result = optimize_equipment(**inputs)
    power = np.array(inputs["base_kw"], float)
    startup_cost = 0
    units = {}
    for e, schedule in zip(inputs["equipment"], result["schedules"]):
        q, on, starts = [np.asarray(schedule[key]) for key in ("units", "on", "starts")]
        assert np.isin(on, [0,1]).all() and np.isin(starts, [0,1]).all() and on[-1] == 0
        assert abs(sum(q) - e["demand"]) < 1e-6
        assert (q <= on*e["rate"] + 1e-6).all()
        assert max(abs(np.diff(np.r_[0,q]))) <= e["ramp"] + 1e-6
        assert np.array_equal(starts, np.maximum(0,on-np.r_[0,on[:-1]]))
        assert sum(starts) <= e["max_starts"]
        for t in np.where(starts == 1)[0]:
            assert sum(on[t:t+e["min_up"]]) == e["min_up"]
        units[e["id"]] = q
        power += q * e["kwh_per_unit"] + on * e["on_kw"]
        startup_cost += sum(starts) * e["startup_cost"]
    assert max(power) <= inputs["site_limit_kw"] + 1e-6
    for up, down in inputs["precedence"]:
        assert (np.cumsum(units[down]) <= np.r_[0,np.cumsum(units[up])[:-1]] + 1e-6).all()
    assert abs(float(np.asarray(inputs["prices"]) @ power + startup_cost) - result["optimized_cost_yuan"]) < 1e-6
    e = {"id":"benchmark", "rate":1, "kwh_per_unit":1, "on_kw":0, "startup_cost":0,
         "demand":2, "ramp":1, "min_up":2, "max_starts":1,
         "baseline_units":[0]*20+[1,1,0,0], "baseline_on":[0]*20+[1,1,0,0]}
    benchmark = optimize_equipment([.1,.1]+[1]*22, [0]*24, 1, [e])
    assert abs(benchmark["optimized_cost_yuan"] - .2) < 1e-6
    folder = ROOT / "output/extensions" / ("equipment_" + uuid4().hex[:12])
    folder.mkdir(parents=True)
    (folder / "schedule.json").write_text(json.dumps({"inputs": inputs, "result": result}, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"success": True, "source_sha256": hashlib.sha256((ROOT / "optimization/equipment_schedule.py").read_bytes()).hexdigest(),
              "binary_states_min_up_ramp_site_and_precedence_reconciled": True,
              "known_optimum_yuan": benchmark["optimized_cost_yuan"], "data_source": "synthetic",
              "baseline_cost_yuan": result["baseline_cost_yuan"], "optimized_cost_yuan": result["optimized_cost_yuan"],
              "daily_saving_yuan": result["daily_saving_yuan"], "mip_gap": result["mip_gap"],
              "evidence_directory": str(folder.relative_to(ROOT))}
    (ROOT / "output/extensions/equipment_runtime.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
