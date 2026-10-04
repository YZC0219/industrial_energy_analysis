from pathlib import Path
import math
import pandas as pd
import matplotlib.pyplot as plt
from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "tests" / "baseline"
OUT = ROOT / "毕业论文最终稿_工业能耗数据仓库与异常检测系统.docx"
ASSET = ROOT / "thesis_assets"
ASSET.mkdir(exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

def csv(prefix):
    return pd.read_csv(next(BASE.glob(prefix + "*.csv")))

q03, q04, q05, q08, q12, q13, q21, q26 = [csv(x) for x in ["Q03_", "Q04_", "Q05_", "Q08_", "Q12_", "Q13_", "Q21_", "Q26_"]]

def chart_energy():
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    d = q03.sort_values("综合能耗_tce")
    ax.barh(d["车间"], d["综合能耗_tce"], color="#345B8C")
    ax.set_xlabel("综合能耗 tce")
    ax.set_title("各车间综合能耗排名")
    ax.grid(axis="x", alpha=.2)
    fig.tight_layout(); p=ASSET/"workshop_energy.png"; fig.savefig(p,dpi=220); plt.close(fig); return p

def chart_structure():
    fig, ax = plt.subplots(figsize=(7.2,4.6))
    colors=["#315A86","#4F81BD","#7EA6CF","#A9C2DC","#C7D8EA","#DDE7F1"]
    ax.pie(q04["折标煤_tce"], labels=q04["能源"], autopct="%1.1f%%", startangle=90, colors=colors,
           wedgeprops={"linewidth":.8,"edgecolor":"white"}, textprops={"fontsize":9})
    ax.set_title("全厂能源结构（折标煤口径）")
    fig.tight_layout(); p=ASSET/"energy_structure.png"; fig.savefig(p,dpi=220); plt.close(fig); return p

def chart_month():
    fig, ax = plt.subplots(figsize=(9,4.3))
    ax.plot(q05["年月"], q05["综合能耗_tce"], marker="o", ms=3, color="#315A86", lw=1.8)
    ax.set_ylabel("综合能耗 tce"); ax.set_title("2024—2025年月度综合能耗趋势")
    ax.tick_params(axis="x", rotation=60, labelsize=7); ax.grid(alpha=.2)
    fig.tight_layout(); p=ASSET/"monthly_trend.png"; fig.savefig(p,dpi=220); plt.close(fig); return p

def chart_saving():
    fig, ax=plt.subplots(figsize=(8.2,4.2)); d=q08.sort_values("单耗同比_pct")
    ax.barh(d["车间"], d["单耗同比_pct"], color="#3B7A57")
    ax.axvline(0,color="black",lw=.7); ax.set_xlabel("2025年单位产品能耗同比 %"); ax.set_title("各车间节能改造效果")
    ax.grid(axis="x",alpha=.2); fig.tight_layout(); p=ASSET/"unit_energy_yoy.png"; fig.savefig(p,dpi=220); plt.close(fig); return p

def chart_detect():
    fig,ax=plt.subplots(figsize=(9,4.5)); x=range(len(q26)); w=.25
    ax.bar([i-w for i in x],q26["固定阈值检出"],w,label="2σ固定阈值",color="#315A86")
    ax.bar(x,q26["产量基线检出"],w,label="产量基线",color="#5B9BD5")
    ax.bar([i+w for i in x],q26["累计和报警天数"],w,label="CUSUM",color="#D18B47")
    ax.set_xticks(list(x),q26["车间"],rotation=25,ha="right"); ax.set_ylabel("检出或报警天数")
    ax.set_title("三种异常检测方法结果对比"); ax.legend(frameon=False,ncol=3); ax.grid(axis="y",alpha=.2)
    fig.tight_layout(); p=ASSET/"detection_compare.png"; fig.savefig(p,dpi=220); plt.close(fig); return p

charts=[chart_energy(),chart_structure(),chart_month(),chart_saving(),chart_detect()]

doc=Document()
sec=doc.sections[0]; sec.top_margin=Cm(2.5); sec.bottom_margin=Cm(2.5); sec.left_margin=Cm(3.0); sec.right_margin=Cm(2.5)

styles=doc.styles
styles["Normal"].font.name="Times New Roman"; styles["Normal"]._element.rPr.rFonts.set(qn("w:eastAsia"),"宋体"); styles["Normal"].font.size=Pt(12)
styles["Normal"].paragraph_format.line_spacing=1.5; styles["Normal"].paragraph_format.first_line_indent=Cm(.85); styles["Normal"].paragraph_format.space_after=Pt(0)
for s,size,font in [("Title",22,"黑体"),("Heading 1",16,"黑体"),("Heading 2",14,"黑体"),("Heading 3",12,"黑体")]:
    styles[s].font.name=font; styles[s]._element.rPr.rFonts.set(qn("w:eastAsia"),font); styles[s].font.size=Pt(size); styles[s].font.color.rgb=RGBColor(0,0,0)
    styles[s].paragraph_format.space_before=Pt(12); styles[s].paragraph_format.space_after=Pt(6); styles[s].paragraph_format.keep_with_next=True
# Remove decorative borders inherited from Word's built-in Title style.
title_ppr = styles["Title"]._element.get_or_add_pPr()
for node in title_ppr.findall(qn("w:pBdr")):
    title_ppr.remove(node)

def set_cell_shading(cell, fill):
    tcPr=cell._tc.get_or_add_tcPr(); shd=OxmlElement('w:shd'); shd.set(qn('w:fill'),fill); tcPr.append(shd)

def add_table(headers, rows, widths=None):
    t=doc.add_table(rows=1, cols=len(headers)); t.alignment=WD_TABLE_ALIGNMENT.CENTER; t.style="Table Grid"
    for i,h in enumerate(headers):
        c=t.rows[0].cells[i]; c.text=str(h); set_cell_shading(c,"345B8C"); c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
        for r in c.paragraphs[0].runs: r.font.color.rgb=RGBColor(255,255,255); r.bold=True; r.font.size=Pt(9); r._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),'宋体')
        c.paragraphs[0].alignment=WD_ALIGN_PARAGRAPH.CENTER
    for ri,row in enumerate(rows):
        cells=t.add_row().cells
        for i,v in enumerate(row):
            cells[i].text="" if pd.isna(v) else str(v); cells[i].vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cells[i].paragraphs[0].alignment=WD_ALIGN_PARAGRAPH.CENTER if len(str(v))<15 else WD_ALIGN_PARAGRAPH.LEFT
            for r in cells[i].paragraphs[0].runs: r.font.size=Pt(8.5); r._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),'宋体')
            if ri%2: set_cell_shading(cells[i],"F2F6FA")
    doc.add_paragraph().paragraph_format.space_after=Pt(0)
    return t

def p(text="", boldlead=None, align=None):
    para=doc.add_paragraph()
    if boldlead and text.startswith(boldlead):
        r=para.add_run(boldlead); r.bold=True; para.add_run(text[len(boldlead):])
    else: para.add_run(text)
    if align is not None: para.alignment=align
    return para

def h(text,level=1): doc.add_heading(text,level=level)
def caption(text):
    q=doc.add_paragraph(text); q.alignment=WD_ALIGN_PARAGRAPH.CENTER; q.paragraph_format.first_line_indent=0
    for r in q.runs: r.font.size=Pt(10); r._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),'宋体')
def figure(path, text, width=15.2):
    q=doc.add_paragraph(); q.alignment=WD_ALIGN_PARAGRAPH.CENTER; q.paragraph_format.first_line_indent=0
    q.add_run().add_picture(str(path),width=Cm(width)); caption(text)

# cover
for _ in range(3): p("")
q=doc.add_paragraph(); q.style=styles["Title"]; q.alignment=WD_ALIGN_PARAGRAPH.CENTER; q.paragraph_format.first_line_indent=0; q.add_run("本科毕业论文")
p("")
q=doc.add_paragraph(); q.alignment=WD_ALIGN_PARAGRAPH.CENTER; q.paragraph_format.first_line_indent=0
r=q.add_run("工业能耗数据仓库与异常检测系统的设计与实现"); r.bold=True; r.font.size=Pt(22); r._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),'黑体')
p("")
for line in ["学    校：________________________","学    院：________________________","专    业：________________________","学生姓名：________________________","学    号：________________________","指导教师：________________________"]:
    q=p(line,align=WD_ALIGN_PARAGRAPH.CENTER); q.paragraph_format.first_line_indent=0; q.paragraph_format.space_after=Pt(10)
p("")
p("二〇二六年九月",align=WD_ALIGN_PARAGRAPH.CENTER).paragraph_format.first_line_indent=0
doc.add_page_break()

h("摘 要",1)
p("在制造业能源成本与碳约束持续强化的背景下，企业需要将分散的计量记录转化为口径统一、可追溯且能够稳定重复运行的分析结果。本文设计并实现一套工业能耗数据仓库与异常检测系统。系统以8个车间、6种能源、2024年1月1日至2025年12月31日共731天的可复现模拟数据为研究对象，构建从数据生成、质量治理、MySQL星型模型、指标分析、异常检测、Airflow调度到可视化报告的完整链路。")
p("针对日期格式混乱、主数据别名、缺失值、重复记录、负数、离群值和费用错误等10类质量问题，本文采用规则化清洗与修正留痕机制。针对公用工程二次能源可能造成的重复计量问题，分别建立全厂、剔除公用工程和连续型车间三类统计口径。异常识别方面，构建2σ固定阈值、考虑产量与季节项的基线模型，以及对白化残差实施的双侧CUSUM方法，并通过仿真将判定限标定为9.9σ。")
p("实验结果表明：全厂综合能耗为63759.63 tce，能源费用为2.1536亿元，碳排放为156783.46 tCO₂；动力站占全厂能耗27.23%，若直接用于单位产品能耗会产生明显重复计量。2025年7个生产车间的单位产品能耗同比下降2.26%至3.88%，按统一口径折算节能量约731 tce、减排约1797 tCO₂、能源毛收益约247万元。产量基线模型将多数车间的残差标准差压低至原固定阈值标准差的24.65%至74.10%，并显著减少节假日低产量造成的误报。系统最终形成29组分析结果、可离线运行的交互式报告及分层回归测试。研究证明，工业能耗分析的核心不仅是图表展示，更在于物理边界、统计口径、数据质量与工程可重复性的协同设计。")
p("关键词：工业能耗；数据仓库；数据清洗；单位产品能耗；CUSUM；异常检测",boldlead="关键词：")
doc.add_page_break()

h("Abstract",1)
p("To support cost control and low-carbon manufacturing, this thesis designs and implements an industrial energy data warehouse and anomaly detection system. The reproducible dataset covers eight workshops, six energy types, and 731 days from 2024 to 2025. The system integrates data generation, quality control, a MySQL star schema, metric analysis, anomaly detection, Airflow orchestration, and a self-contained visualization report.")
p("Ten types of data-quality defects are handled with explicit correction and audit trails. Three scopes are separated to prevent inconsistent interpretation: the whole plant, production workshops excluding utilities, and continuous-process workshops. For anomaly detection, a fixed two-sigma rule, a production-and-seasonality baseline, and a two-sided CUSUM on whitened residuals are compared. The CUSUM decision limit is calibrated by simulation instead of directly copying a conventional threshold.")
p("The plant consumed 63,759.63 tce, incurred CNY 215.36 million in energy cost, and emitted 156,783.46 tCO2. The utility station represented 27.23% of total energy consumption. Unit energy consumption in all seven production workshops decreased by 2.26%–3.88% in 2025. The production baseline substantially reduced holiday-related false alarms and exposed the conditions under which intermittent workshops require cautious interpretation. The resulting system provides 29 analytical datasets, a reproducible interactive report, and layered regression tests.")
p("Key words: industrial energy; data warehouse; data quality; energy intensity; CUSUM; anomaly detection",boldlead="Key words:")
doc.add_page_break()

h("目 录",1)
toc_items = [
    ("第1章 绪论", 5), ("第2章 需求分析与关键技术", 6),
    ("第3章 数据设计与质量治理", 7), ("第4章 数据仓库与管道设计", 9),
    ("第5章 指标体系与业务分析", 11), ("第6章 异常检测方法设计", 14),
    ("第7章 系统实现与测试", 16), ("第8章 结论与展望", 18),
    ("参考文献", 19), ("致谢", 20), ("附录A 主要分析主题", 20),
    ("附录B 系统运行流程", 20),
]
for label, page_no in toc_items:
    q = doc.add_paragraph()
    q.paragraph_format.first_line_indent = 0
    q.paragraph_format.left_indent = Cm(0.4)
    q.paragraph_format.right_indent = Cm(0.4)
    q.paragraph_format.space_after = Pt(5)
    tabs = q.paragraph_format.tab_stops
    tabs.add_tab_stop(Cm(14.2))
    q.add_run(label + "\t" + str(page_no))
doc.add_page_break()

h("第1章 绪论",1)
h("1.1 研究背景",2)
p("能源是制造企业生产经营的重要成本要素，也是碳排放核算的主要活动数据来源。传统能耗管理往往依赖月度抄表和电子表格汇总，能够回答“用了多少”，却难以稳定回答“由哪个车间、哪种能源、哪类生产状态造成”“变化是否超出正常波动”“统计口径是否与上期一致”等问题。随着计量点增多，数据质量、主数据一致性和算法可解释性会直接影响节能结论。")
p("工业场景还存在与一般互联网数据不同的物理边界。例如动力站消耗天然气生产蒸汽，蒸汽又被生产车间消费；若将两端同时计入单位产品能耗，同一份能量会被重复核算。间歇型车间在停产日仍有待机能耗，产量接近零会使单位产品能耗异常放大。由此可见，系统首先要正确表达工艺与计量关系，其次才是统计模型和可视化。")
h("1.2 研究目的与意义",2)
p("本文的目标是构建一条可重复运行、可验证、可解释的工业能耗分析链路。理论意义在于把能源统计口径、数据质量治理与过程异常检测统一到同一分析框架；工程意义在于形成可部署的数据仓库、调度流程和报告产物，使指标变化能够追溯到源数据、清洗规则和查询版本。")
h("1.3 国内外研究与技术基础",2)
p("数据仓库领域通常以主题化、集成化、相对稳定和反映历史变化的数据集合支撑分析决策[1]。维度建模将事实与分析维度分离，适合按时间、车间和能源品种进行切片与聚合[2]。统计过程控制方面，Shewhart控制图善于发现单点大偏移[3]，Page提出的累积和方法则对持续小偏移更敏感[4]。本文不追求复杂模型堆叠，而是把这些成熟方法与工业计量边界、生产节律及可重复数据工程结合。")
h("1.4 研究内容与技术路线",2)
p("研究内容包括：一是构造带业务规律和质量缺陷的可复现数据；二是建立清洗、拒绝和修正台账；三是设计包含3张维表、2张事实表和5个分析视图的星型模型；四是完成29项能耗、费用、碳排、强度和风险分析；五是比较2σ、产量基线和CUSUM；六是通过Airflow、Docker Compose和自动化测试保障管道运行。技术路线为“数据生成—清洗治理—仓库装载—指标计算—异常检测—报告生成—回归验证”。")
h("1.5 论文结构",2)
p("全文共八章。第1章说明背景与研究内容；第2章给出需求与关键技术；第3章介绍数据与质量治理；第4章阐述仓库和管道设计；第5章建立指标体系；第6章研究异常检测方法；第7章展示系统实现与实验结果；第8章总结研究结论、局限与后续方向。")

h("第2章 需求分析与关键技术",1)
h("2.1 业务需求",2)
p("系统面向能源管理人员、车间管理人员和数据维护人员。能源管理人员关注总量、结构、费用、碳排和节能绩效；车间管理人员关注单耗、待机损耗和异常时段；数据维护人员关注源数据质量、装载状态和结果一致性。核心问题包括总量核算、跨车间比较、同比环比、气温影响、停产待机、采购价格波动和异常预警。")
h("2.2 功能与非功能需求",2)
add_table(["类别","需求","验收方式"],[
    ["数据治理","识别并处理10类质量问题，保留修正与拒绝记录","清洗统计与回归测试"],
    ["数据仓库","支持日期、车间、能源多维分析及历史修正","唯一键、upsert与查询验证"],
    ["业务分析","输出Q01至Q29共29组结果","结果文件与快照逐行比对"],
    ["异常检测","比较固定阈值、产量基线和CUSUM","检出数、节假日占比与残差尺度"],
    ["可视化","离线打开、动态筛选、桌面与移动端适配","浏览器交互与聚合恒等式"],
    ["可运维性","失败可重跑、装载幂等、结果可复现","Airflow DAG与端到端测试"],
])
h("2.3 关键技术",2)
p("数据处理层使用Python、pandas和NumPy完成模拟数据构造及清洗；存储分析层使用MySQL 8.0，利用窗口函数、公共表表达式和视图组织重复口径；调度层以Airflow DAG串联五个任务；展示层使用原生HTML、CSS、JavaScript与SVG，将数据内联为单文件报告；质量层以pytest、查询快照和端到端检查实现分层验证。")
h("2.4 系统总体架构",2)
p("系统采用分层结构。源数据层保存模拟的能耗与产量记录；治理层生成标准化数据及清洗台账；仓库层保存维表、事实表和算法视图；分析层输出29份主题结果；应用层生成普通报告与数据大屏；调度和测试作为横向能力覆盖全链路。任务依赖顺序为generate_raw_data、clean_data、load_warehouse、run_analysis和build_report。")

h("第3章 数据设计与质量治理",1)
h("3.1 数据范围与业务假设",2)
p("研究数据覆盖2024年1月1日至2025年12月31日，共731个自然日、8个车间和6种能源。车间包括熔炼、轧制、热处理、机加工、表面处理、装配、动力站和包装；能源包括电力、天然气、蒸汽、工业水、压缩空气和柴油。数据由固定随机种子生成，并嵌入季节、气温、产量、节假日、价格和节能改造等规律。模拟数据不代表具体企业真实经营状况，但其结构约束用于验证系统方法。")
add_table(["维度","规模或内容","说明"],[["时间","731天","2024—2025年"],["车间","8个","7个生产车间与1个公用工程"],["能源","6种","一次能源与二次能源并存"],["车间日","5848条","每个车间每日一条汇总"],["清洗后能耗明细","19006条","日期×车间×能源业务键唯一"],["分析结果","29组","Q01—Q29"]])
h("3.2 数据质量问题与处理策略",2)
p("为了检验清洗流程，源数据主动注入多格式日期、主数据别名、编码缺失、单位不统一、重复记录、离群值、负数、缺失值、价格缺失和费用偏差等问题。处理原则是：能够无歧义修正的记录进入修正台账；无法确定业务含义或业务键冲突的记录进入拒绝台账；所有清洗统计单独落盘。")
add_table(["问题","处理规则","风险控制"],[["日期格式不一","按候选格式逐级解析","失败记录剔除并留痕"],["名称别名与编码缺失","按主数据映射与回填","统一标准编码"],["业务键重复","按更新时间取最新，时间相同时稳定保留首条","保证确定性"],["消耗量离群","按车间×能源的Q3+3IQR识别，单元格置空后插补","避免整行删除"],["负消耗量","取绝对值并记录原因","保留原始与修正信息"],["缺失消耗量","按车间×能源×年月中位数插补","降低跨季节偏差"],["单价缺失或为0","按能源×年月中位数插补","保持价格同质性"],["费用偏差超过5%","按消耗量×单价重算","恢复计算恒等式"]])
h("3.3 离群处理缺陷与修复",2)
p("早期实现将包含离群消耗量的整行删除，使该车间当日永久缺少一种能源。日总能耗因此形成不真实的下降，后续CUSUM将数据清洗缺陷误判为过程异常。修复后仅将异常单元格置空，再进入同组中位数插补。清洗后能耗明细由18972条增至19006条，消耗量插补由295项增至329项，能源品种不足的车间日由34个降至0，残差Z值下限由-11.6改善至-3.27。")
h("3.4 插补的边界",2)
p("插补可维持数据矩阵完整，但会引入估计值并压低方差。若下游无法区分实测与插补，控制限可能变窄，误报反而增加。因此系统保留修正原因和插补标记，分析结论也不把插补结果解释为真实测量。实际接入时还应根据仪表状态、停机记录和相邻计量点进行多源校验。")

h("第4章 数据仓库与管道设计",1)
h("4.1 星型模型",2)
p("仓库以能耗和产量为两个事实主题，使用车间、能源和日历三张维表。fact_energy_consumption的粒度为日期×车间×能源，保存实物消耗量、单价、费用及更新时间；fact_production的粒度为日期×车间，保存产量。维度模型使能源结构、车间排名、日型比较和时间趋势共享同一主数据。")
add_table(["类型","对象","粒度或职责"],[["维表","dim_workshop","车间、工序、生产方式、产量单位"],["维表","dim_energy_type","能源、单位、折标煤系数、排放因子、参考单价"],["维表","dim_calendar","日期、年月、工作日、周末、法定节假日"],["事实表","fact_energy_consumption","日期×车间×能源"],["事实表","fact_production","日期×车间"],["视图","v_energy_enriched","折标煤、费用、碳排统一计算"],["视图","v_daily_workshop","车间日度综合能耗"],["视图","v_monthly_workshop","月度能耗与单位产品能耗"],["视图","v_unit_energy_baseline","期望单耗、残差与标准化"],["视图","v_unit_energy_cusum","双侧累积和与判定限"]])
h("4.2 三类统计口径",2)
p("全厂口径用于能源结构、总量、总费用与总排放；剔除公用工程口径用于单位产品能耗，避免动力站生产蒸汽和生产车间消费蒸汽被重复计算；连续型车间口径用于气温相关性，减少间歇型车间停产日对相关系数的扭曲。全厂综合能耗63759.63 tce，剔除动力站后为46400.52 tce，二者相差17359.11 tce，即全厂的27.23%。")
h("4.3 增量装载与历史修正",2)
p("事实表装载采用水位线增量抽取与基于updated_at的新旧版本判断。相同业务键只有严格更新的记录才能覆盖现有值，空时间戳按最旧版本处理，防止过期数据重放导致历史回退。首次部署使用建表与全量装载组合；日常运行只处理水位线之后的数据。该设计同时满足幂等重跑和历史修正。")
h("4.4 调度与部署",2)
p("Airflow DAG将五个任务按依赖顺序编排，失败任务可独立重跑。Docker Compose提供MySQL、Airflow初始化、Web服务器、调度器与触发器服务。报告构建还可在持续集成环境中读取固定查询基线，不依赖在线数据库，从而保证发布结果可复现。发布前必须先对实时查询结果与基线逐行比对，避免旧基线生成一个形式正确但结论过期的报告。")

h("第5章 指标体系与业务分析",1)
h("5.1 能源与碳排指标",2)
p("实物消耗按能源折标煤系数换算为综合能耗，费用按消耗量与实际单价计算，碳排放按活动数据与排放因子计算。压缩空气作为二次能源不重复计算碳排。指标字典统一记录名称、单位、粒度、适用口径和数据来源，减少同名指标的语义漂移。")
h("5.2 总量与结构结果",2)
p("731天内全厂综合能耗为63759.63 tce，能源费用为215364002.57元，碳排放为156783.46 tCO₂。天然气折标煤40865.55 tce，占64.09%；电力15418.22 tce，占24.18%；蒸汽6608.16 tce，占10.36%。费用口径中，天然气和电力分别占49.24%和39.61%，是成本管理的重点。")
figure(charts[0],"图5-1 各车间综合能耗排名")
figure(charts[1],"图5-2 全厂能源结构")
p("动力站、熔炼、轧制和热处理四个车间累计费用占比84.30%，符合帕累托式重点管理思路。动力站能耗排名第一，但其职责是向其他车间提供公用介质，因此“占比高”并不等于“低效”，必须进一步结合产出、管网损失与供需边界解释。")
h("5.3 时间趋势与影响因素",2)
figure(charts[2],"图5-3 月度综合能耗趋势")
p("月度总量同时受到季节、生产天数和产量影响。天然气和蒸汽与气温呈显著负相关，相关系数分别为-0.9555和-0.9262；电力相关系数为0.1738，呈弱正相关。分档结果显示，零度以下日均天然气消耗55532 m³，而28℃以上为30953 m³，体现供热负荷的季节性。月度环比还包含28至31天的月长效应，因此报告同时提供按日均能耗拆解，避免把日历差异误判为生产异常。")
h("5.4 单耗与节能绩效",2)
figure(charts[3],"图5-4 2025年各车间单位产品能耗同比变化")
p("7个生产车间2025年单位产品能耗均下降，其中熔炼车间降幅3.88%，热处理车间3.17%，轧制车间2.26%。以2024年单耗乘以2025年实际产量得到“效率未提升时的应耗量”，再减去实际消耗，折算节能量约731 tce；按全期碳强度2.4590 tCO₂/tce折算减排约1797 tCO₂；按单位能耗成本3378元/tce折算能源毛收益约247万元。该收益未扣除改造投资和运维费用，不应表述为投资回报。")
h("5.5 停产待机与风险",2)
p("表面处理、机加工、装配和包装车间各有58个停产日，其待机能耗分别为110.89、34.10、19.82和8.46 tce，对应待机费用约43.72万元、19.60万元、11.30万元和4.77万元。节假日的全厂日均能耗75.431 tce，低于工作日90.419 tce，但并未降至零，说明公用工程和待机负荷构成可进一步治理的基础负荷。")

h("第6章 异常检测方法设计",1)
h("6.1 2σ固定阈值",2)
p("对每个车间的单位产品能耗序列计算历史均值μ和标准差σ，当观测值偏离μ超过2σ时判为异常。该方法透明且计算简单，适合快速发现单日大偏离；但它把产量、季节和日型导致的系统变化都视为随机波动，尤其在间歇型车间的低产量日容易产生高单耗误报。")
h("6.2 产量与季节基线",2)
p("为了先解释可预期变化，本文建立如下期望单耗模型：期望单耗=a+b·ln(产量)+g·sin(2πd/365)+h·cos(2πd/365)，其中d为年内日序。参数通过最小二乘正规方程求解，实际单耗与期望单耗之差作为残差，再按车间残差标准差进行标准化。对数产量用于描述规模效应，正余弦项用于描述平滑年周期。")
p("基线模型并不等价于因果模型。系数只能说明在当前模拟机制和变量集合下的统计关系，不能直接证明产量变化导致能效变化。真实企业应用应加入产品结构、设备状态、班次、环境温湿度和检修记录，并通过时间外样本验证模型稳定性。")
h("6.3 双侧CUSUM",2)
p("CUSUM对标准化残差的持续偏移进行累积。设标准化残差为z_t，参考值为k，则上侧统计量C_t^+=max[0,C_(t-1)^++z_t-k]，下侧统计量C_t^-=max[0,C_(t-1)^--z_t-k]。当任一统计量超过判定限h时产生报警。本文先按车间×日历月对白化残差去除剩余季节性，再计算双侧累积和。")
p("常见经验阈值不能直接迁移到长度约730的多个序列。本文对零均值白噪声执行5000次仿真，以零分布最大累积和的99分位标定h=9.9σ。该阈值只适用于当前序列长度、白化方式和目标误报水平；接入新数据时必须重新估计残差尺度、检查自相关并重新标定。")
h("6.4 方法比较",2)
figure(charts[4],"图6-1 三种异常检测方法结果对比")
p("固定阈值在机加工、表面处理和装配车间分别检出60、56和58天，其中节假日占比95.0%、98.2%和100.0%，说明多数报警由低产量放大单耗造成。产量基线将上述车间检出数降至7、8和15天，节假日占比分别降至42.9%、50.0%和13.3%。各车间残差标准差相当于固定阈值标准差的24.65%至74.10%，说明模型解释了部分产量与季节波动。")
add_table(["车间","2σ检出","基线检出","CUSUM报警天数","噪声压低比"],[[r["车间"],int(r["固定阈值检出"]),int(r["产量基线检出"]),int(r["累计和报警天数"]),f'{r["噪声压低比"]:.4f}'] for _,r in q26.iterrows()])
h("6.5 适用条件与解释边界",2)
p("表面处理和包装属于间歇型车间，停产或极低产量使回归自变量的业务含义减弱，白化残差仍可能保留自相关。因此，这两个车间的CUSUM更适合解释为停机—重启模式或持续状态变化信号，而不宜直接认定为单位产品能耗异常。系统保留报警以维持完整报告，但必须结合停机记录和设备事件复核。")

h("第7章 系统实现与测试",1)
h("7.1 数据处理与分析实现",2)
p("数据生成脚本以固定随机种子保证跨运行一致；清洗脚本分别输出标准数据、拒绝台账、修正台账和质量统计；装载脚本支持建表、全量装载、增量装载和独立执行分析；SQL文件集中定义仓库对象与Q01至Q29查询。重复使用的业务口径下沉至视图，避免在多个查询中复制公式。")
h("7.2 报告与交互实现",2)
p("报告使用原生HTML、CSS、JavaScript与SVG生成，不依赖外部图表服务，所有分析数据与样式均内联，可离线打开。页面提供总览、结构、趋势、成因、成效、损耗和风险等主题，支持日期、车间和日型筛选、深浅主题、悬停提示及等价表格。数据大屏复用Q28和Q29形成的前端立方与同一筛选引擎，避免两套视图出现数值分叉。")
h("7.3 测试策略",2)
p("测试分为五层：清洗规则测试验证业务键、缺失值和产物一致性；指标字典测试验证系数、参数与口径；数据库测试验证幂等装载、中断续跑、历史修正和水位线推进；分析快照测试将29份查询结果与基线逐行比较；端到端测试核对产物完整性、时效性及跨结果恒等关系。前端还检查筛选聚合、控制台错误与移动端横向溢出。")
add_table(["测试对象","重点风险","主要断言"],[["清洗","错误修正或遗漏","唯一键、缺失值、台账计数"],["指标字典","公式与文档漂移","系数、阈值、单位一致"],["数据库装载","重复、回退、漏装","幂等、upsert、水位线"],["查询快照","口径静默变化","29份结果逐行一致"],["端到端","链路局部正确但整体错误","时效性、总分恒等、产物完整"],["前端","筛选结果与后端不一致","聚合恒等、无报错、无溢出"]])
h("7.4 实验结果讨论",2)
p("实验说明，单纯增加算法复杂度不能替代数据质量控制。若离群处理删除整行，后续任何检测方法都会面对被人为制造的结构突变。统计口径也会显著改变结论：是否纳入动力站会使单位产品能耗分母与分子失去一致物理边界；是否剔除间歇型停产日会改变气温相关性。系统价值主要来自“数据—口径—算法—验证”的闭环。")
p("另一方面，模拟数据能验证流程与方法，却不能证明系统在真实企业中的节能效果。真实部署还需要接入计量器具台账、校准状态、产品BOM、设备运行日志和生产计划；排放因子与折标煤系数应按企业所在地区、核算边界和最新适用标准维护。")

h("第8章 结论与展望",1)
h("8.1 研究结论",2)
p("本文完成了一套工业能耗数据仓库与异常检测系统，实现从可复现数据、质量治理、星型模型、增量装载、29项分析、三种异常检测到交互报告和分层测试的完整闭环。研究得到四点结论：第一，能源分析必须先明确物理边界和统计口径；第二，质量缺陷会被异常检测放大，清洗留痕和回归验证不可缺少；第三，加入产量与季节项的基线能减少节假日低产量误报；第四，CUSUM阈值应按序列长度、残差结构和目标误报率标定。")
h("8.2 创新与实践特点",2)
p("本文的实践特点不是提出全新算法，而是把工业计量语义、三类统计口径、可追溯清洗、仿真标定CUSUM、幂等增量装载和结果快照组合为可运行系统。尤其是将清洗缺陷造成的假异常纳入工程复盘，并用测试守住修复结果，使异常检测不再脱离数据生产过程。")
h("8.3 局限性",2)
p("研究存在三方面局限。其一，数据为模拟数据，尚未覆盖仪表漂移、通信中断和产品结构切换等真实复杂性；其二，基线模型变量较少，且采用同一时段拟合与评价，泛化能力仍需滚动验证；其三，节能收益为毛收益，没有纳入改造投资、维护成本和能源价格情景。")
h("8.4 后续工作",2)
p("后续可从四方面扩展：接入真实计量与设备事件数据，建立数据质量评分；引入分层模型或广义加性模型描述非线性负荷；对CUSUM采用滚动训练、概念漂移监测和车间分类型阈值；建设指标版本与数据血缘界面，使每个报告数字可追溯到源记录、清洗规则、SQL版本和模型版本。")

h("参考文献",1)
refs=[
"[1] Inmon W H. Building the Data Warehouse. 4th ed. Wiley, 2005.",
"[2] Kimball R, Ross M. The Data Warehouse Toolkit. 3rd ed. Wiley, 2013.",
"[3] Shewhart W A. Economic Control of Quality of Manufactured Product. D. Van Nostrand, 1931.",
"[4] Page E S. Continuous inspection schemes. Biometrika, 1954, 41(1/2): 100-115.",
"[5] Montgomery D C. Introduction to Statistical Quality Control. 8th ed. Wiley, 2019.",
"[6] ISO 50001:2018. Energy management systems—Requirements with guidance for use.",
"[7] 国家市场监督管理总局, 国家标准化管理委员会. GB/T 2589—2020 综合能耗计算通则.",
"[8] 国家发展和改革委员会. 工业企业温室气体排放核算和报告通则及相关行业指南.",
"[9] Apache Software Foundation. Apache Airflow Documentation.",
"[10] Oracle Corporation. MySQL 8.0 Reference Manual.",
"[11] McKinney W. Data Structures for Statistical Computing in Python. Proceedings of the 9th Python in Science Conference, 2010: 56-61.",
"[12] Harris C R, Millman K J, van der Walt S J, et al. Array programming with NumPy. Nature, 2020, 585: 357-362.",
"[13] Hunter J D. Matplotlib: A 2D graphics environment. Computing in Science & Engineering, 2007, 9(3): 90-95.",
"[14] W3C. Scalable Vector Graphics SVG 2. W3C Candidate Recommendation.",
"[15] Fowler M. Patterns of Enterprise Application Architecture. Addison-Wesley, 2002.",
"[16] Kleppmann M. Designing Data-Intensive Applications. O’Reilly Media, 2017.",
]
for x in refs:
    q=p(x); q.paragraph_format.first_line_indent=0; q.paragraph_format.hanging_indent=Cm(.74); q.paragraph_format.space_after=Pt(4)

h("致 谢",1)
p("本论文的完成离不开指导教师在选题、系统设计和论文写作方面的指导，也感谢课程学习与项目实践中给予帮助的老师和同学。项目从最初的能耗图表逐步发展为包含数据治理、数据仓库、异常检测、调度部署和自动化测试的完整系统，这一过程使我更加理解：可靠的分析结论不仅来自模型，也来自对数据边界、工程细节和验证方法的持续追问。谨向所有给予帮助和支持的人表示感谢。")

h("附录A 主要分析主题",1)
add_table(["编号范围","主题"],[["Q01—Q04","能源总量、车间排名、能源结构与费用结构"],["Q05—Q08","月度趋势、环比、同比与单位产品能耗"],["Q09—Q15","费用帕累托、车间结构、气温、停产日与节假日"],["Q16—Q23","异常、连续上涨、移动平均、价格、碳排与突增预警"],["Q24—Q27","2σ、产量基线、方法比较与CUSUM序列"],["Q28—Q29","动态筛选使用的车间日度汇总与能源结构明细"]])
h("附录B 系统运行流程",1)
p("本地流程依次执行数据生成、数据清洗、仓库初始化与全量装载、分析查询和报告生成。首次部署必须组合执行初始化与全量装载，日常运行采用水位线增量模式。数据库或口径发生有意变化后，应先重新生成查询结果，再与基线比较；只有差异能够被业务解释时才更新基线。")

# footer page number field
for section in doc.sections:
    fp=section.footer.paragraphs[0]; fp.alignment=WD_ALIGN_PARAGRAPH.CENTER; fp.paragraph_format.first_line_indent=0
    fld=OxmlElement('w:fldSimple'); fld.set(qn('w:instr'),'PAGE'); fp._p.append(fld)

# metadata and core properties
doc.core_properties.title="工业能耗数据仓库与异常检测系统的设计与实现"
doc.core_properties.subject="本科毕业论文"
doc.core_properties.keywords="工业能耗, 数据仓库, 异常检测, CUSUM"
doc.save(OUT)
print(OUT)
