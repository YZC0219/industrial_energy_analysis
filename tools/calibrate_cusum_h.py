# -*- coding: utf-8 -*-
"""标定 CUSUM 判定限 h —— 生成 `v_unit_energy_cusum` 里那个 9.9σ。

    python tools/calibrate_cusum_h.py

**为什么需要这个脚本**：教科书默认 h = 5σ，但那个值对应 ARL₀ ≈ 300 天，
是为"在线连续监控、每天判一次"标定的。本项目是对 730 天历史做**一次性回顾**
检验，同一个门槛下误报率完全不同 —— 实测 n=730 时 77% 的车间会至少报一次警。
所以 h 必须由零假设仿真按当前样本量重新标定。

**这一步不能省、也不能复用**：换一批数据、换一个时间范围，都要重跑本脚本。
写进仓库是为了让"h=9.9σ"这个结论**可复核**，而不是只留一句注释。

方法：在纯白噪声上算 max(S⁺, S⁻)，取 99 分位作为 h。
白噪声下不存在真实偏移，任何越限都必然是误报，故 99 分位即 1% 误报率对应的门槛。
"""
from __future__ import annotations

import sys

import numpy as np

# Windows 控制台默认 GBK, 打不出 σ/上标等字符 —— 与其他 tools/ 脚本一致
sys.stdout.reconfigure(encoding="utf-8")

# 与 v_unit_energy_cusum 保持一致
K = 0.5          # 松弛量: 只累积超过 0.5σ 的部分
N_SIM = 5000     # 仿真次数
SEED = 20240918  # 与数据生成同一种子, 保证可复现
QUANTILE = 99

LENGTHS = [365, 730, 1461]   # 含本项目实际长度 730


def cusum_peaks(z: np.ndarray) -> tuple[float, float]:
    """返回 (S⁺ 峰值, S⁻ 峰值)。

    闭式解 S⁺ₜ = Cₜ − min_{0≤j≤t} Cⱼ，Cₜ = Σ(zᵢ − k)。
    与视图里的两个窗口函数(前缀和 + 前缀最小值)是同一个式子。
    """
    c_hi = np.cumsum(z - K)
    c_lo = np.cumsum(-z - K)
    # min 要带上 C₀ = 0
    s_hi = c_hi - np.minimum(0.0, np.minimum.accumulate(c_hi))
    s_lo = c_lo - np.minimum(0.0, np.minimum.accumulate(c_lo))
    return float(s_hi.max()), float(s_lo.max())


def main() -> int:
    rng = np.random.default_rng(SEED)

    print(f"CUSUM 判定限标定 —— {N_SIM:,} 次零假设仿真 (纯白噪声, k={K})")
    print(f"统计量取 max(S⁺, S⁻);  h 取 {QUANTILE} 分位 (即 1% 误报率)\n")
    print(f"{'n':>6}  {'中位':>7}  {'99分位':>7}  {'h=5 误报率':>11}")
    print("-" * 40)

    for n in LENGTHS:
        peaks = np.empty(N_SIM)
        for i in range(N_SIM):
            z = rng.standard_normal(n)
            hi, lo = cusum_peaks(z)
            peaks[i] = max(hi, lo)

        median = float(np.median(peaks))
        h99 = float(np.percentile(peaks, QUANTILE))
        false_alarm = float((peaks > 5.0).mean())
        mark = "   <- 本项目" if n == 730 else ""
        print(f"{n:>6}  {median:>7.2f}  {h99:>7.2f}  "
              f"{false_alarm:>10.1%}{mark}")

    h_project = float(np.percentile(
        np.array([max(*cusum_peaks(rng.standard_normal(730))) for _ in range(N_SIM)]),
        QUANTILE))
    print(f"\n本项目采用:  h = {h_project:.1f}σ   (sql/create_table.sql 里硬编码为 9.9)")

    # 仿真带随机性, 且 99 分位对 RNG 流敏感: 实测同一参数下 9.78~10.0 都出现过
    # (numpy 版本不同, 取到的流不同)。视图里写死 9.9 是当时那次的取值 ——
    # 容差放到 ±0.3 覆盖这个波动, 只拦住"量级不对"的情况(比如换数据长度后忘了重标)。
    # 宁可在这里报出来让人确认, 也不静默地让视图与注释不一致。
    if abs(h_project - 9.9) > 0.3:
        print(f"[警告] 本次仿真得 {h_project:.2f}, 与视图里写死的 9.9 相差过大 —— "
              f"请确认是否要更新 sql/create_table.sql。", file=sys.stderr)
        return 1
    print(f"与视图写死的 9.9 在容差内 (本次 {h_project:.2f})。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
