# -*- coding: utf-8 -*-
"""
更新 Q01~Q23 回归基线。

    python tests/update_baseline.py            # 用当前 output/ 覆盖基线
    python tests/update_baseline.py --check    # 只看差异, 不写入

**什么时候该用它**: 只有当你**有意**改变了业务口径, 且已确认新结果是正确的。
比如第二阶段统一公用工程口径后, 单耗类查询的数字本就该变 —— 那时更新基线
是正当的, 但必须在提交信息里写明"为什么变、变了多少"。

**什么时候不该用**: 测试失败但你说不出数字为什么该变。那说明是 bug, 不是
基线过期。先查清楚, 别用刷新基线把问题盖过去。
"""

from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(HERE)
OUT_DIR = os.path.join(BASE_DIR, "output")
BASELINE_DIR = os.path.join(HERE, "baseline")


def main() -> int:
    ap = argparse.ArgumentParser(description="更新 Q01~Q23 回归基线")
    ap.add_argument("--check", action="store_true",
                    help="只报告差异, 不写入基线")
    args = ap.parse_args()

    if not os.path.isdir(BASELINE_DIR):
        os.makedirs(BASELINE_DIR, exist_ok=True)

    sources = sorted(f for f in os.listdir(OUT_DIR)
                     if f.startswith("Q") and f.endswith(".csv"))
    if not sources:
        print(f"[错误] {OUT_DIR} 下没有 Q*.csv, 请先运行 --run-analysis",
              file=sys.stderr)
        return 1

    changed, added, same = [], [], []
    for name in sources:
        src = os.path.join(OUT_DIR, name)
        dst = os.path.join(BASELINE_DIR, name)
        if not os.path.exists(dst):
            added.append(name)
        elif filecmp.cmp(src, dst, shallow=False):
            same.append(name)
        else:
            changed.append(name)

    # 基线里有、output 里没有的 —— 多半是查询被删了, 属于可疑情况, 单独报出来
    stale = sorted(
        f for f in os.listdir(BASELINE_DIR)
        if f.endswith(".csv") and f not in sources
    )

    def show(label, items):
        if items:
            print(f"  {label} ({len(items)}): {', '.join(items)}")

    print(f"基线目录: {BASELINE_DIR}")
    show("未变", same)
    show("有差异", changed)
    show("新增", added)
    show("基线独有(output 里已无)", stale)

    if args.check:
        print("\n--check 模式, 未写入。")
        return 0

    for name in changed + added:
        shutil.copy2(os.path.join(OUT_DIR, name),
                     os.path.join(BASELINE_DIR, name))

    print(f"\n已更新 {len(changed) + len(added)} 个基线文件。")
    print("请确认这些变化是有意的, 并在提交信息里说明原因。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
