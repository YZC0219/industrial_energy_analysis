# 残差、CUSUM 与节能情景统一

2026-10-05，本窗口继续报告公式工作。已只读核对其他窗口：在线模型问答、CDC 当日批次及调度延迟正在分别处理，本轮没有修改这些任务或重复运行其验收。

分析契约升级为版本 3，共九项定义。新增内容使用现有算法和输入，保留历史 SQL 轨迹及报警标记，不重新训练模型、不重新标定 CUSUM，也不重跑主数仓。

## 本轮变更

| 内容 | 统一规则 | 解释边界 |
| --- | --- | --- |
| 残差控制带 | 残差均值 ± 2σ，均值可为负数，σ 必须非负 | 不采用能耗图的下界归零；σ 为零时控制带退化为均值线 |
| CUSUM 展示与统计 | 阈值读取 Q27；统计已有 SQL 报警标记、方向及相邻记录中由未报警到报警的次数 | 不从舍入后的轨迹重新判断报警；记录数不代表重新标定的样本长度，段数不等于设备故障持续时间 |
| 节能情景 | 各能源的能耗、费用、碳排分别乘假设降幅 | 使用各能源原有费用与碳排，不用全厂平均系数折算；仅为假设估算 |

原报告中的残差图和 CUSUM 图现在使用统一解释器。CUSUM 副标题显示实际日记录数，阈值来自当前导出数据；同一车间导出中若阈值不一致，报告生成会停止。SQL 的白化/未白化轨迹、Z 值、报警日和方向均保留。

报告新增节能情景区域，可切换 3%、5%、10% 降幅，并跟随日期、车间和日型筛选。范围沿用既有阶段五定义：2025 年、剔除 W07 公用工程。只纳入选中的有数据日期；仅选 W07 或选到 2024 年等没有合格记录的范围时，明确显示没有可用记录，不生成假零结论。

现有 `tools/phase5_energy_assessment.py` 的三项情景计算也已改为调用同一 Python 定义；其输入读取、年度筛选、产物结构和原 CLI 比例约束保持原有行为。报告使用本次 Q 导出中的能源日明细，阶段五使用清洗 CSV，两者输入精度不同，因此统一的是计算规则，不承诺不同输入的结果逐位相等。历史阶段五产物未重写。

基线差额和按比例情景都不代表已实施措施的实测效果。情景未计改造成本、负荷变化或价格变化；同厂措施收益核验仍需实施记录和前后计量。

## 验收

- 50 项相关测试通过，包括残差负下界、零 σ、CUSUM 阈值边界、日期及标记一致性、非法情景比例、现有阶段五入口及混合阈值拒绝。
- 七个车间的残差控制带和七个车间的 CUSUM 阈值/统计在实际浏览器中与 Python 一致；导出序列及标记未改变。
- 66 条既有阶段五情景记录逐行核对通过，文件 SHA-256 在核验前后不变。
- 三档降幅 × 五种范围，共 15 组真实筛选结果通过；范围包含全量、W04 部分月、周末、仅 W07、2024 年空年度。
- 原报告命令与现有 Airflow `build_report` 操作器单独执行成功，两份 HTML 在统一换行符后完全一致。
- 29 张分析表、三种原查询范围、异常与摘要接口继续通过兼容核验。

验收仅涉及当前源码和报告节点，没有触发完整 DAG、更新数仓、替换运行中的 CDC 固定源码副本或重复执行全库 bug 回归。

## 产物与复现

- [原入口生成的新报告](../output/extensions/report_entry_789de89e9be8/local/report.html)
- [原命令与 Airflow 报告任务验收](../output/extensions/report_entry_789de89e9be8/verification.md)
- [浏览器验收记录](../output/extensions/analysis_formulas_e66fde8f03e9/verification.md)
- [情景区域截图](../output/extensions/analysis_formulas_e66fde8f03e9/scenarios.png)

新产物均保存到 D 盘独立目录，未覆盖原报告。

```powershell
python -m pytest tests/test_analysis_formulas.py tests/test_browser_semantics.py tests/test_report_evidence.py -q --basetemp D:/industrial_energy_analysis/tmp/remaining_formula_tests
python -m tools.verify_analysis_report
python -m tools.verify_report_entry
```
