"""Build a standalone Chinese journal-style manuscript from verified experiment outputs."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "output" / "paper"
DOCX = ROOT / "工业车间日能耗预测_可复现方法学论文稿.docx"
ASSET = OUTDIR / "figures"
ASSET.mkdir(parents=True, exist_ok=True)
REAL_OUT = OUTDIR / "uci_steel"
REAL_ASSET = ASSET / "uci_steel"
REAL_ASSET.mkdir(parents=True, exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

report = json.loads((OUTDIR / "paper_experiment_summary.json").read_text(encoding="utf-8"))
metrics = json.loads((ROOT / "output" / "ml_model_metrics.json").read_text(encoding="utf-8"))
deep = json.loads((ROOT / "output" / "ml_deep_metrics.json").read_text(encoding="utf-8"))
ablation = pd.DataFrame(report["ablation_summary"])
preds = pd.read_csv(OUTDIR / "paper_lightgbm_fold1_predictions.csv")
shap_data = pd.read_csv(OUTDIR / "paper_shap_fold1.csv")

# Figure 1: fold errors across the existing benchmark models.
base_preds = pd.read_csv(ROOT / "output" / "ml_model_predictions.csv")
deep_preds = pd.read_csv(ROOT / "output" / "ml_deep_predictions.csv")
all_preds = pd.concat([base_preds, deep_preds], ignore_index=True)
all_preds["abs_error"] = (all_preds.actual - all_preds.prediction).abs()
fold_error = all_preds.groupby(["model", "fold"], as_index=False).agg(mae=("abs_error", "mean"))
fig, ax = plt.subplots(figsize=(8.8, 4.4))
for model, grp in fold_error.groupby("model"):
    ax.plot(grp.fold, grp.mae, marker="o", linewidth=1.5, label=model)
ax.set(xlabel="滚动测试折", ylabel="MAE（tce）", title="各模型逐折预测误差")
ax.grid(alpha=.25); ax.legend(frameon=False, ncol=2); fig.tight_layout()
fig.savefig(ASSET / "fold_mae.png", dpi=220); plt.close(fig)

# Figure 2: fold-level ablation MAE.
fold_ab = pd.read_csv(OUTDIR / "paper_ablation_by_fold.csv")
fig, ax = plt.subplots(figsize=(8.8, 4.4))
for name, grp in fold_ab.groupby("ablation", sort=False):
    ax.plot(grp.fold, grp.mae, marker="o", linewidth=1.4, label=name)
ax.set(xlabel="滚动测试折", ylabel="MAE（tce）", title="LightGBM 特征消融的逐折 MAE")
ax.grid(alpha=.25); ax.legend(frameon=False, ncol=2); fig.tight_layout()
fig.savefig(ASSET / "ablation_mae.png", dpi=220); plt.close(fig)

# Figure 3: TreeSHAP global importance.
cols = [x for x in shap_data.columns if x.startswith("shap::")]
importance = pd.DataFrame({"feature": [x.removeprefix("shap::") for x in cols],
                           "mean_abs": [shap_data[x].abs().mean() for x in cols]})
importance = importance.nlargest(12, "mean_abs").sort_values("mean_abs")
fig, ax = plt.subplots(figsize=(8.8, 4.8))
ax.barh(importance.feature, importance.mean_abs, color="#3f78a8")
ax.set(xlabel="平均绝对 SHAP 值（tce）", title="首个测试折 LightGBM 的 SHAP 全局重要性")
ax.grid(axis="x", alpha=.2); fig.tight_layout()
fig.savefig(ASSET / "shap_importance.png", dpi=220); plt.close(fig)

doc = Document()
sec = doc.sections[0]
sec.top_margin = Cm(2.2); sec.bottom_margin = Cm(2.2); sec.left_margin = Cm(2.5); sec.right_margin = Cm(2.2)
styles = doc.styles
styles["Normal"].font.name = "Times New Roman"
styles["Normal"]._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
styles["Normal"].font.size = Pt(10.5)
styles["Normal"].paragraph_format.line_spacing = 1.35
for name, size in [("Title", 18), ("Heading 1", 14), ("Heading 2", 12)]:
    styles[name].font.name = "黑体"; styles[name]._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")
    styles[name].font.size = Pt(size); styles[name].font.color.rgb = RGBColor(0, 0, 0)
title_ppr = styles["Title"]._element.get_or_add_pPr()
for border in title_ppr.findall(qn("w:pBdr")):
    title_ppr.remove(border)

def p(text="", bold=False, align=None):
    para = doc.add_paragraph()
    if align is not None: para.alignment = align
    run = para.add_run(text); run.bold = bold
    return para

def heading(text, level=1): doc.add_heading(text, level=level)

def table(headers, rows):
    t = doc.add_table(rows=1, cols=len(headers)); t.style = "Table Grid"
    for c, value in zip(t.rows[0].cells, headers): c.text = str(value)
    for row in rows:
        cells = t.add_row().cells
        for c, value in zip(cells, row): c.text = str(value)
    for row in t.rows:
        for cell in row.cells:
            for para in cell.paragraphs:
                para.paragraph_format.space_after = Pt(0)
                for run in para.runs:
                    run.font.size = Pt(8.5); run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    return t

def figure(path, caption, width=15.5):
    para = doc.add_paragraph(); para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para.add_run().add_picture(str(path), width=Cm(width))
    cp = doc.add_paragraph(caption); cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cp.paragraph_format.first_line_indent = Cm(0)

title = doc.add_paragraph(style="Title"); title.alignment = WD_ALIGN_PARAGRAPH.CENTER
title.add_run("工业能耗预测的滚动验证与可解释分析")
title_ppr = title._p.get_or_add_pPr()
for border in title_ppr.findall(qn("w:pBdr")):
    title_ppr.remove(border)
p("合成多车间案例及真实钢铁厂数据复现", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
p("作者：待填写　单位：待填写", align=WD_ALIGN_PARAGRAPH.CENTER)
p("稿件状态：方法学案例初稿；数据为固定种子模拟数据，不能作为真实工厂实证结论。", align=WD_ALIGN_PARAGRAPH.CENTER)

heading("摘要", 1)
p("工业能耗预测需要在时间顺序约束下评价模型，并区分历史负荷与外生特征的预测贡献。本文先在固定种子生成的多车间模拟数据上比较季节性朴素基线、LightGBM、LSTM 与 Transformer，再对 LightGBM 执行特征组消融、折级配对检验和 TreeSHAP 解释；另以 UCI 韩国钢铁厂公开实测数据进行单厂外部复现。模拟数据覆盖 8 个车间、6 种能源及 731 日，采用 28 日预热、365 日扩展训练窗、30 日测试窗和 30 日步长，共 11 折、2,640 个车间日测试样本。季节基线、LightGBM、LSTM 与 Transformer 的 MAE/RMSE 分别为 0.7550/1.3300、0.4674/0.7750、0.5213/0.8538 和 0.5448/0.8415 tce。去除天气/日历组或目标历史组后，模拟数据折均 MAE 分别增加 0.0569 和 0.0508 tce；Holm 校正后的配对检验 p 值分别为 0.0078 和 0.0146。公开钢厂数据包含 35,040 条 15 分钟真实测量记录，聚合为 365 个日样本；在 7 个滚动折上，LightGBM 的 MAE/RMSE 为 769.9/1,039.2 kWh/day，优于 7 日滞后基线的 869.6/1,167.5 kWh/day。该数据上的消融表明近期能耗历史有统计增益，而日历组增益不显著。研究提供了从模拟控制实验到公开真实测量复现的可追溯流程，但单厂单年记录不能证明跨工厂泛化；模拟数据结果也不构成现场节能或故障预警证据[29]。")
p("关键词：工业能耗预测；LightGBM；时间序列滚动验证；特征消融；配对检验；SHAP")

heading("1 引言", 1)
p("制造业能耗预测涉及从设备、工序到车间和工厂等不同系统边界，生产状态、产品类型、环境条件及历史负荷都会影响预测关系。制造业能耗建模综述指出，研究在预测尺度、时间范围、输入变量及系统边界上差异显著，因此模型比较必须说明具体任务和可用信息[1]。针对工业过程的综述进一步指出，数据质量、关键参数不可测与跨场景泛化是预测模型落地的重要限制[2]。已有制造案例覆盖了工厂能耗、生产排程、设备状态和过程用能等多类问题[3–8]，但结果通常依赖具体工艺与数据采样频率。")
p("树模型适合表格特征，递归网络与注意力网络则提供序列建模对照[18–21]。然而，仅比较总体 MAE 容易掩盖时间阶段差异，也无法回答特征组是否带来可重复增益。预测解释还需把模型贡献限定为预测函数的归因，而非物理因果作用[22]。因此本文聚焦三个问题：在相同时间折上，树模型与序列模型的误差如何变化？生产、天气/日历、能源价格/状态及目标历史组对 LightGBM 的预测误差有何增量贡献？TreeSHAP 对模型输出的解释是否与消融结果一致？")
p("本文的贡献是提供一套可复核的合成工业能耗预测实验：统一时间切分与样本资格、按特征组进行消融、以时间折为配对单位进行推断，并展示树模型 SHAP 解释。本文不以合成样本替代工业现场观测，也不据此声称模型能够识别真实设备故障。")

heading("2 相关研究", 1)
p("制造业能耗预测研究从单机和工序能耗模型延伸至生产单元、工厂级预测与生产计划协同[1,3,6–8]。Walther 与 Weigold 对制造业电能预测文献按系统边界、输入、建模方法、预测时域和建模目的进行分类，指出多尺度、生产过程信息与预测用途决定方法选择[1]。工业过程能耗及碳排放预测综述也强调，不同产业参数可获得性和测量不确定性限制模型通用性[2]。与本研究任务较接近的研究包括基于制造工厂数据的机器学习预测[4,5]、制造设施模拟数据上的深度预测[3]、流程工业短期负荷预测[12]以及工厂尺度生产能耗的多层建模[7,8]。这些研究提供了方法背景，但数据边界、目标变量和预测频率与本文的车间日综合能耗不同。")
p("多变量能源预测通常比较树模型、统计方法和神经网络，并使用按时间顺序的样本外评估。滚动起点验证能够考察多个预测阶段，且评估协议应明确窗口扩展、重估频率和测试期[23,24]。Diebold–Mariano 方法用于比较预测损失序列[25]；本研究的主要推断则采用折级配对检验，以避免把同一时间折中的车间日预测当作独立复制。SHAP 基于 Shapley 值提供局部加性贡献和全局汇总，但解释仍针对模型函数而非因果机制[22,26–28]。")

heading("3 数据与研究方法", 1)
heading("3.1 数据范围与预测目标", 2)
p("数据来自项目中的固定随机种子模拟生成流程，日期范围为 2024 年 1 月 1 日至 2025 年 12 月 31 日，含 8 个车间、6 种能源。能耗明细经规则清洗后聚合到车间—日层级，以折标煤吨（tce）作为预测目标。模拟器编码了季节、生产、气温、节假日、价格和年度能效趋势；因此数据分布部分由生成规则预设，不代表实际仪表测量或真实企业运行。")
p("任务定义为滚动单步预测：对日期 t 的每个车间，在仅使用 t 日开始前已知信息时预测当日总能耗。生产量、实测温度、能源价格及事后记录的运行状态占比仅使用 t−1 值；日历特征使用 t 日已知信息。目标滞后和滚动统计先移位再计算，以避免目标日信息泄漏。")
heading("3.2 特征与模型", 2)
p("输入特征包括前一日生产量、温度、低负荷/停产状态占比、能源价格指数，星期和周末指示、年周期正余弦项，以及 t−1、t−7、t−14 能耗滞后和 7/28 日移动均值；车间身份以独热变量编码。所有模型使用相同滚动折及共同目标行资格。比较模型为 7 日季节朴素预测、LightGBM、LSTM 和 Transformer。LightGBM 参数固定为 180 棵树、学习率 0.04、31 个叶节点、最大深度 7，并设定固定随机种子；深度模型沿用项目记录的固定网络配置与训练轮数。")
heading("3.3 时间验证协议与指标", 2)
p("数据包含 731 个连续自然日。排除共同 28 日特征预热期后，采用最初 365 日训练窗、完整 30 日测试窗、每次前移 30 日并逐折扩展训练区间的滚动起点评估。最终得到 11 个测试折；每折 30 日×8 车间=240 条目标记录，总计 2,640 条测试记录。指标为微平均 MAE 与 RMSE，逐折误差另行报告。")
p("对特征消融，分别移除生产量、天气/日历、价格/状态、目标历史四组变量，并保持目标样本、折定义和模型超参数不变。将每个消融模型与全特征模型在同一测试折的 MAE 作差，以 11 个时间折作为配对单位。报告折级平均差与 20,000 次折级 bootstrap 百分位区间，并使用双侧 Wilcoxon 符号秩检验；四个预先规定的消融比较以 Holm 方法校正。折数较少，因此置信区间和 p 值视为探索性不确定性度量。")
heading("3.4 SHAP", 2)
p("使用首个测试折拟合的 LightGBM 模型计算 TreeSHAP。为控制计算并保证可复现，从该折测试样本中以固定种子抽取 60 个车间日。全局重要性定义为样本内平均绝对 SHAP 值，并同时保存每个样本的输入取值、预测值、基准值和单变量贡献。由于同类滞后变量相关，SHAP 贡献可能在相关特征之间分摊；结果不作因果解释。")

heading("4 结果", 1)
heading("4.1 模型比较", 2)
model_rows = []
for item in metrics["models"]:
    model_rows.append([item["model"], f"{item['mae']:.4f}", f"{item['rmse']:.4f}", str(item["samples"])])
for item in deep["models"]:
    model_rows.append([item["model"], f"{item['mae']:.4f}", f"{item['rmse']:.4f}", str(item["samples"])])
table(["模型", "MAE（tce）", "RMSE（tce）", "测试样本数"], model_rows)
p("已有统一验证产物显示，LightGBM 的总体 MAE 最低（0.4674 tce），较 7 日季节基线低约 38.1%；LSTM 与 Transformer 的总体 MAE 分别为 0.5213 和 0.5448 tce。逐折性能存在阶段变化，见图1。模型排名是本数据生成机制、特征集合和指定预测协议下的比较，不代表跨工厂或跨工艺的普遍优劣。")
figure(ASSET / "fold_mae.png", "图1　季节基线、LightGBM、LSTM 与 Transformer 的逐折 MAE")
heading("4.2 特征消融及统计比较", 2)
full = ablation[ablation.ablation == "full"].iloc[0]
table_rows=[]
for _, row in ablation.iterrows():
    table_rows.append([row.ablation, f"{row.mae_mean:.4f}±{row.mae_sd:.4f}", f"{row.rmse_mean:.4f}±{row.rmse_sd:.4f}"])
table(["特征配置", "折均 MAE（均值±标准差）", "折均 RMSE（均值±标准差）"], table_rows)
test_rows=[]
for item in report["paired_tests"]:
    test_rows.append([item["comparison"], f"{item['mean_mae_delta_vs_full']:.4f}",
                      f"[{item['bootstrap_95ci_low']:.4f}, {item['bootstrap_95ci_high']:.4f}]",
                      f"{item['wilcoxon_p_holm']:.4f}"])
table(["消融对比（相对全特征）", "MAE 差", "折级 bootstrap 95% 区间", "Holm 校正 p"], test_rows)
p("全特征 LightGBM 的逐折平均 MAE 为 0.4674 tce。去除天气/日历组和目标历史组，MAE 分别增加 0.0569 与 0.0508 tce，校正后 p 值分别为 0.0078 与 0.0146，折级 bootstrap 区间均未跨越 0。去除生产量组和价格/状态组的 MAE 差较小，其区间跨越 0，且校正后检验不显著。逐折分布见图2。小样本下，不显著结果不能解释为这些变量完全无效。")
figure(ASSET / "ablation_mae.png", "图2　全特征与四组消融模型的逐折 MAE")
heading("4.3 SHAP 全局解释", 2)
top = report["shap"]["global_importance"][:5]
p("首折 60 个测试样本的全局解释中，tce_lag_1 平均绝对 SHAP 值最高，其后为 tce_rolling_mean_7、tce_lag_14、tce_rolling_mean_28 和 tce_lag_7。该排序与移除目标历史组后误差增加的消融结果方向一致。天气/日历组的消融影响明显，但单个日历和温度特征的 SHAP 重要性分散，说明分组消融与单变量归因回答的问题不同。SHAP 数值单位为模型输出 tce 的贡献量，不能解读为某因素引发了相应能耗变化。")
figure(ASSET / "shap_importance.png", "图3　LightGBM 首折测试样本的平均绝对 TreeSHAP 值")

# Independent real-data replication, kept separate from the synthetic multi-workshop benchmark.
real = json.loads((REAL_OUT / "uci_steel_experiment_summary.json").read_text(encoding="utf-8"))
real_fold = pd.DataFrame(real["fold_metrics"])
fig, ax = plt.subplots(figsize=(8.8, 4.4))
for model, grp in real_fold.groupby("model"):
    ax.plot(grp.fold, grp.mae, marker="o", linewidth=1.5, label=model)
ax.set(xlabel="滚动测试折", ylabel="MAE（kWh/day）", title="UCI 单钢厂真实数据的逐折预测误差")
ax.grid(alpha=.25); ax.legend(frameon=False); fig.tight_layout()
fig.savefig(REAL_ASSET / "uci_fold_mae.png", dpi=220); plt.close(fig)
real_shap = pd.read_csv(REAL_OUT / "uci_steel_shap.csv")
real_shap_cols=[c for c in real_shap if c.startswith("shap::")]
real_imp=pd.DataFrame({"feature":[c.removeprefix("shap::") for c in real_shap_cols],
                       "mean_abs":[real_shap[c].abs().mean() for c in real_shap_cols]}).nlargest(10,"mean_abs").sort_values("mean_abs")
fig, ax=plt.subplots(figsize=(8.4,4.3)); ax.barh(real_imp.feature,real_imp.mean_abs,color="#4d8b66")
ax.set(xlabel="平均绝对 SHAP 值（kWh/day）",title="UCI 钢厂数据 LightGBM 的 TreeSHAP 全局重要性")
ax.grid(axis="x",alpha=.2); fig.tight_layout(); fig.savefig(REAL_ASSET/"uci_shap_importance.png",dpi=220); plt.close(fig)
heading("5 讨论", 1)
p("本案例最清晰的经验结果是，在给定的合成数据和次日预测协议下，历史能耗与季节/日历信息对树模型预测较重要；生产量以及能源价格/运行状态组的边际改善较弱。生成过程明确包含季节项和序列相关结构，这可能使相关特征的价值被放大。因此，实验更适合检验滚动评估、消融和解释工作流是否可复现，而非估计真实工厂变量的因果效应。")
p("对于实际部署，预测时可用变量的时间戳必须严格定义。例如生产计划和天气预报可以在 t 日前获得，而本项目只有实测事后数据，所以统一滞后到 t−1。若未来替换为计划数据，应重新定义特征可用性并重新运行滚动验证。对于跨车间复用的结论，还需要在独立企业、不同工艺与更长观测期上做外部验证，并补充经维护工单或操作员确认的真实异常标签。")
heading("6 局限与结论", 1)
p("研究有四项主要限制。第一，数据完全合成，固定种子且生成机制显式植入趋势与季节，不能支持外部有效性或现场节能收益结论。第二，只有 11 个时间测试折，折级推断功效有限，测试折的相邻时间段也可能相关。第三，SHAP 仅基于首个测试折的 60 个样本与一个折模型，重要性可能随季节、车间和模型重训而变化。第四，现有任务是单步能耗预测，不含真实异常事件标签，不能计算预警提前量、检出率或故障诊断效果。")
p("在上述边界内，本文完成了季节基线、树模型与深度序列模型的统一滚动比较，并通过折级消融检验发现天气/日历与目标历史特征组在该合成案例中提供稳定误差改善；TreeSHAP 显示近期能耗历史是首折 LightGBM 的主要预测贡献。后续工作应以授权的真实车间数据复现协议，扩展独立工厂验证，检验 SHAP 跨时间稳定性，并使用经确认的事件标签评价异常告警。")

heading("附录A 单钢厂真实数据外部复现", 1)
p("为检查方法在实际测量数据上的可运行性，另对 UCI Machine Learning Repository 的 Steel Industry Energy Consumption 数据集进行独立分析[29]。该公开数据来自韩国 DAEWOO Steel Co. Ltd.，采用 CC BY 4.0 许可。数据含 2018 年 35,040 条 15 分钟记录，只有一个钢厂，未提供实测产量或多个车间标识。原始时间戳排序存在日边界倒序，但规范排序后记录无重复、无缺失，15 分钟间隔完整；每天 96 条记录，聚合为 365 个日总用电量样本。")
p("由于真实数据只有一年，不能沿用模拟研究中更长的 365 日初始训练窗及 7 日以上日序列输入设置。外部复现采用 28 日预热、120 日初始训练、30 日测试和 30 日步长，得到 7 折、210 个样本；比较固定参数 LightGBM 与 7 日滞后朴素基线。特征仅包括能耗历史和可预知日历变量，未使用同期用电功率因数、负荷类别或 CO₂ 实测量作为输入，以避免它们在聚合日预测时作为目标日事后信息造成泄漏。")
real_metric_rows=[]
for x in real["metrics"]: real_metric_rows.append([x["model"],f"{x['mae']:.1f}",f"{x['rmse']:.1f}",str(x["samples"])])
table(["模型", "MAE（kWh/day）", "RMSE（kWh/day）", "测试样本数"], real_metric_rows)
p("LightGBM 微平均 MAE/RMSE 为 769.9/1039.2 kWh/day，7 日朴素基线为 869.6/1167.5 kWh/day。逐折表现并非稳定领先所有测试时期。折级消融中，移除近期历史后 MAE 平均增加 152.0 kWh/day，Holm 校正 p=0.0469；移除较长历史组后增加 49.5 kWh/day，但未显著（p=0.2188）；移除日历组后 MAE 平均下降 34.2 kWh/day，差异不显著（p=0.5781）。只有 7 个配对时间折，检验结果应视为探索性。")
figure(REAL_ASSET/"uci_fold_mae.png","图A1　单钢厂真实数据上的逐折 MAE")
figure(REAL_ASSET/"uci_shap_importance.png","图A2　单钢厂真实数据首个测试折 TreeSHAP 重要性")
p("首折 30 个样本的 SHAP 排序显示星期周期、前一日能耗与 7 日均值贡献较大。此结果与模拟多车间数据不同：真实数据中日历信息对某些阶段有解释作用，但将日历组整体加入并未降低平均误差。该外部复现增强了方法流程的真实性检查，但因只有单厂单年、数据缺少产量且无工艺/仪表上下文，不能据此推断跨工厂泛化或归因机制。数据来源、许可与 SHA-256 详见 data/real/uci_steel_energy/provenance.json。")

heading("参考文献", 1)
refs = (ROOT / "docs" / "论文参考文献.md").read_text(encoding="utf-8").splitlines()
for line in refs:
    if line[:1].isdigit() and ". " in line:
        p(line.replace("*", ""))

heading("复现信息", 1)
p("项目代码入口：python -m ml.paper_experiments；论文构建入口：python tools/build_forecasting_paper.py。分析产物保存在 output/paper/，包括逐折消融 CSV、首折 SHAP 样本解释、实验汇总 JSON 与图表。模型总体对照使用项目当前滚动评估产物。运行依赖包括 pandas、numpy、LightGBM、SciPy、SHAP、matplotlib 和 python-docx。")

doc.save(DOCX)
print(DOCX)
