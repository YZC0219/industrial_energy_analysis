# -*- coding: utf-8 -*-
"""拆解月度环比里的"月长效应" —— 把 Q05 的环比与日均环比并排算出来。

    python tools/decompose_mom.py

**为什么需要这个脚本**：报告正文说"全厂 11 月环比 2024 年 +17.10%、2025 年
+17.49%，采暖季启动把所有人都推高了"。但 Q05 里 12 月的环比更高（+19.33% /
+18.88%），而 12 月同样在采暖季 —— 于是"采暖"这个解释对 12 月不完整。

原因有两个候选：
  1. **真实抬升**（采暖等业务因素）
  2. **月长效应** —— 相邻两个月天数不同（28~31 天），总能耗是"整月求和"，
     31 天的月天然比 30 天的月多约 3.3%，**与业务无关**。

把环比改算在**日均**上即可分离：日均环比剔除了天数差异，剩下的才是真实抬升。

**这一步不能凭直觉**：本脚本跑出来的结论和"12 月更高所以是月长"完全相反 ——
11 月的月长因素是**负向**的（30 天 < 上月 31 天），所以 11 月的真实抬升比
原始环比显示的**更强**；12 月才有约 3.85pp 来自多一天。两个月的性质不同，
不能合并成一句"采暖季启动"。

写进仓库是为了让这个结论**可复核**，而不是只留在某次对话里。
"""
from __future__ import annotations

import calendar
import csv
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE_DIR, "output")
Q05 = os.path.join(OUT_DIR, "Q05_月度能耗趋势与环比.csv")

# 只看这个幅度以上的月份 —— 报告正文讨论的正是这些"系统性抬升"的月份
NEAR = 10.0


def month_days(ym: str) -> int:
    y, m = map(int, ym.split("-"))
    return calendar.monthrange(y, m)[1]


def read_q05() -> dict[str, float]:
    if not os.path.exists(Q05):
        raise SystemExit(f"[错误] 找不到 {Q05}, 请先执行: python src/import_mysql.py --run-analysis")
    with open(Q05, encoding="utf-8-sig", newline="") as f:
        return {r["年月"]: float(r["综合能耗_tce"]) for r in csv.DictReader(f)}


def main() -> None:
    bym = read_q05()
    ys = sorted(bym)

    # 口径与 Q05 一致: 全厂含 W07。这里只做除法, 不重算能耗, 所以能直接对账。
    print("全厂口径(含 W07), 与 Q05 同源\n")
    print(f"{'月份':9s} {'总能耗环比':>11s} {'天数':>5s} {'日均环比':>11s} {'月长贡献':>10s}")
    print("-" * 52)

    rows = []
    for i in range(1, len(ys)):
        cur, prev = ys[i], ys[i - 1]
        raw = (bym[cur] - bym[prev]) / bym[prev] * 100
        dc, dp = month_days(cur), month_days(prev)
        # 日均环比 = 把两个月都化成"每天多少 tce"再比, 天数影响被约掉
        avg = (bym[cur] / dc - bym[prev] / dp) / (bym[prev] / dp) * 100
        rows.append((cur, raw, dc, avg, raw - avg))

    for cur, raw, dc, avg, gap in rows:
        if raw > NEAR or abs(gap) > 2:
            print(f"{cur:9s} {raw:10.2f}% {dc:5d} {avg:10.2f}% {gap:9.2f}pp")

    print("-" * 52)
    print("月长贡献 = 总能耗环比 − 日均环比。正值表示该月天数多于上月,")
    print("         原始环比里有一部分纯粹是'多了一天'撑起来的。\n")

    print("报告正文点名的两个 11 月:")
    for ym in ("2024-11", "2025-11"):
        r = next((x for x in rows if x[0] == ym), None)
        if r:
            cur, raw, dc, avg, gap = r
            print(f"  {cur}  原始 {raw:6.2f}%  日均 {avg:6.2f}%  月长 {gap:+5.2f}pp"
                  f"  -> 月长是{'负向' if gap < 0 else '正向'}的, 真实抬升比原始值"
                  f"{'更强' if gap < 0 else '更弱'}")

    print("\n对照 12 月(原始环比高于同年的 11 月, 但性质不同):")
    for ym in ("2024-12", "2025-12"):
        r = next((x for x in rows if x[0] == ym), None)
        if r:
            cur, raw, dc, avg, gap = r
            print(f"  {cur}  原始 {raw:6.2f}%  日均 {avg:6.2f}%  月长 {gap:+5.2f}pp"
                  f"  -> 其中 {gap:.2f}pp 来自多一天")

    print("\n结论: 11 月与 12 月的抬升不是一回事 ——")
    print("  · 11 月: 30 天, 少于上月 31 天, 月长压低原始环比; 真实抬升约 +21%。")
    print("  · 12 月: 31 天, 多于上月 30 天, 月长抬高原始环比约 +3.85pp; 真实抬升约 +15.5%。")
    print("  报告正文把两者合并成'采暖季启动把所有人都推高了', 对 11 月成立,")
    print("  对 12 月不完整 —— 12 月原始值反超 11 月, 主要靠那 3.85pp 的月长。")


if __name__ == "__main__":
    main()
