"""《基于 Spark 的多城市气象与空气质量数据采集及预处理系统》答辩 PPT。"""

from __future__ import annotations

import json
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output" / "data_collection" / "基于Spark的多城市气象空气质量采集预处理.pptx"
REPORTS = ROOT / "output" / "data_collection" / "reports"

BG = RGBColor(0x12, 0x15, 0x1A)
CARD = RGBColor(0x1A, 0x1F, 0x27)
COPPER = RGBColor(0xD4, 0xA5, 0x74)
TEAL = RGBColor(0x3E, 0xCF, 0xBF)
TEXT = RGBColor(0xEC, 0xE7, 0xDF)
MUTED = RGBColor(0x9A, 0xA3, 0xB0)


def _load_stats() -> dict:
    meta = ROOT / "output" / "data_collection" / "meta" / "latest_dwd_batch.json"
    if not meta.exists():
        return {}
    info = json.loads(meta.read_text(encoding="utf-8"))
    q = Path(info.get("quality_report") or "")
    if q.exists():
        return json.loads(q.read_text(encoding="utf-8"))
    return info


def _bg(slide):
    shape = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Emu(0), Emu(0), Inches(13.333), Inches(7.5))
    shape.line.fill.background()
    shape.fill.solid()
    shape.fill.fore_color.rgb = BG
    spTree = slide.shapes._spTree
    sp = shape._element
    spTree.remove(sp)
    spTree.insert(2, sp)


def _text(slide, left, top, width, height, text, *, size=18, bold=False, color=TEXT, align=PP_ALIGN.LEFT):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = "Microsoft YaHei"


def _bullets(slide, left, top, width, height, lines, *, size=16):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(7)
        run = p.add_run()
        run.text = line
        run.font.size = Pt(size)
        run.font.color.rgb = TEXT
        run.font.name = "Microsoft YaHei"


def _card(slide, left, top, width, height):
    shape = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARD
    shape.line.color.rgb = RGBColor(0x3A, 0x32, 0x28)


def build() -> Path:
    stats_doc = _load_stats()
    rec = stats_doc.get("reconcile") or stats_doc.get("stats") or {}
    engine = stats_doc.get("engine") or "（运行后自动填入）"
    rows = rec.get("rows_out") or stats_doc.get("rows") or "—"
    dups = rec.get("duplicates_removed", "—")
    peaks = rec.get("pollution_peaks_marked") or {}
    peak_total = sum(int(v) for v in peaks.values()) if isinstance(peaks, dict) else "—"

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    s = prs.slides.add_slide(blank)
    _bg(s)
    _text(s, Inches(0.9), Inches(2.0), Inches(11), Inches(0.4), "数据采集与预处理 · 期末作业", size=15, color=COPPER, bold=True)
    _text(s, Inches(0.9), Inches(2.5), Inches(11.5), Inches(1.4), "基于 Spark 的多城市气象与空气质量\n数据采集及预处理系统", size=32, bold=True)
    _text(s, Inches(0.9), Inches(4.5), Inches(11), Inches(1.2), "真实 Open-Meteo API → ODS → Spark 清洗/汇总 → DWD Parquet → 看板与对账\n日常：本机 Spark local[1]　验收：现有两节点 VM（HDFS + Spark）", size=16, color=MUTED)

    s = prs.slides.add_slide(blank)
    _bg(s)
    _text(s, Inches(0.8), Inches(0.45), Inches(11), Inches(0.5), "01  数据性质（答辩必讲）", size=26, bold=True, color=COPPER)
    _bullets(
        s,
        Inches(0.9),
        Inches(1.3),
        Inches(11.5),
        Inches(5.5),
        [
            "调用 Open-Meteo Forecast / Air-Quality API，产品为数值模式预报及归档预报。",
            "不是中国环境监测总站等地面自动站的原始观测报文。",
            "官方说明：https://open-meteo.com/en/docs",
            "作业结论描述为「基于公开模型产品的采集与预处理」，不宣称实测监测替代。",
            "采集清单与 JSON 中写明 data_nature = model_forecast_and_archived_forecast。",
        ],
        size=17,
    )

    s = prs.slides.add_slide(blank)
    _bg(s)
    _text(s, Inches(0.8), Inches(0.45), Inches(11), Inches(0.5), "02  总体链路", size=26, bold=True, color=COPPER)
    stages = [
        ("真实采集", "5 城×气象/空气质量\ncollect_log 重试"),
        ("ODS", "原始 JSON\n来源/时间/性质"),
        ("Spark", "去重·对齐·缺口\n峰值标记"),
        ("DWD", "Parquet/CSV\n质量对账"),
        ("汇总", "Spark SQL\n城市日指标"),
    ]
    for i, (t, b) in enumerate(stages):
        x = 0.7 + i * 2.5
        _card(s, Inches(x), Inches(2.1), Inches(2.3), Inches(3.2))
        _text(s, Inches(x + 0.15), Inches(2.4), Inches(2.0), Inches(0.5), t, size=17, bold=True, color=TEAL)
        _text(s, Inches(x + 0.15), Inches(3.2), Inches(2.0), Inches(1.6), b, size=14, color=MUTED)

    s = prs.slides.add_slide(blank)
    _bg(s)
    _text(s, Inches(0.8), Inches(0.45), Inches(11), Inches(0.5), "03  字段级清洗依据", size=26, bold=True, color=COPPER)
    _bullets(
        s,
        Inches(0.9),
        Inches(1.25),
        Inches(11.5),
        Inches(5.5),
        [
            "短缺口（≤3 小时）：连续型气象插值；降水短缺口填 0。",
            "长缺口：保留空值并 flag_long_gap_*，禁止无限插值或最终一律补零。",
            "湿度/云量：物理界 [0,100]，越界裁剪并标记。",
            "污染类（PM/AQI/气体）：IQR 识别峰值 → 仅标记 flag_peak_*，默认保留原值。",
            "规则集中在 cleaning/rules.py，Spark 作业与 pandas 备用共用同一政策说明。",
        ],
        size=16,
    )

    s = prs.slides.add_slide(blank)
    _bg(s)
    _text(s, Inches(0.8), Inches(0.45), Inches(11), Inches(0.5), "04  双运行模式与架构", size=26, bold=True, color=COPPER)
    _bullets(
        s,
        Inches(0.9),
        Inches(1.25),
        Inches(11.5),
        Inches(5.5),
        [
            "演示：spark-submit --master local[1] --driver-memory 1g（控制宿主内存）。",
            "验收：复用 VM Ubuntu(.131 Master/NN) + Ubuntu2(.132 Worker/DN)，无需新租服务器。",
            "集群资源：executor 1 核 / 768MB，HDFS 副本 1（单 DataNode 实验拓扑）。",
            "pandas 路径仅作 Spark 不可用时的演示备用，报告中标注 engine。",
            f"当前证据引擎：{engine}",
        ],
        size=16,
    )

    s = prs.slides.add_slide(blank)
    _bg(s)
    _text(s, Inches(0.8), Inches(0.45), Inches(11), Inches(0.5), "05  实际数据量与对账（运行后写入）", size=26, bold=True, color=COPPER)
    _bullets(
        s,
        Inches(0.9),
        Inches(1.25),
        Inches(11.5),
        Inches(5.5),
        [
            f"清洗后行数 rows_out：{rows}",
            f"去重删除 duplicates_removed：{dups}",
            f"污染峰值标记合计：{peak_total}",
            f"气象小时行 / 空气质量小时行：{rec.get('weather_hourly_rows', '—')} / {rec.get('aq_hourly_rows', '—')}",
            f"城市日汇总行 city_daily_rows：{rec.get('city_daily_rows', '—')}",
            "完整数字以 output/data_collection/reports/spark_*.json 为准，答辩展示该文件。",
        ],
        size=17,
    )

    s = prs.slides.add_slide(blank)
    _bg(s)
    _text(s, Inches(0.8), Inches(0.45), Inches(11), Inches(0.5), "06  课程证据清单", size=26, bold=True, color=COPPER)
    _bullets(
        s,
        Inches(0.9),
        Inches(1.25),
        Inches(11.5),
        Inches(5.5),
        [
            "来源与性质：ODS JSON 字段 api_endpoint / data_nature_*",
            "采集时间与重试：ods/*/collect_log.json（attempt、elapsed_ms、error）",
            "处理前后统计：spark_job_report / spark_local_*.json / spark_cluster_*.json",
            "Spark 作业记录：reports/spark_*_log_*.txt + HDFS 路径清单",
            "可视化：http://127.0.0.1:8020/ 看板；本 PPT 自动嵌入最近一次对账数字",
        ],
        size=16,
    )

    s = prs.slides.add_slide(blank)
    _bg(s)
    _text(s, Inches(0.8), Inches(0.45), Inches(11), Inches(0.5), "07  复现命令", size=26, bold=True, color=COPPER)
    _bullets(
        s,
        Inches(0.9),
        Inches(1.25),
        Inches(11.5),
        Inches(5.5),
        [
            "演示：python data_collection/tools/run_course.py --engine spark-local --ppt --serve",
            "验收：python data_collection/tools/run_course.py --engine spark-cluster --skip-collect",
            "仅采集：python data_collection/collectors/run_collect.py",
            "备用：python data_collection/tools/run_course.py --engine pandas",
        ],
        size=17,
    )

    s = prs.slides.add_slide(blank)
    _bg(s)
    _text(s, Inches(0.9), Inches(2.7), Inches(11.5), Inches(1.2), "采真数、讲清性质；用 Spark 洗净并对账。", size=28, bold=True, align=PP_ALIGN.CENTER)
    _text(s, Inches(0.9), Inches(4.1), Inches(11.5), Inches(0.8), "峰值先标记再解释；集群按需开机，本机可演示。", size=16, color=MUTED, align=PP_ALIGN.CENTER)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    prs.save(OUT)
    return OUT


if __name__ == "__main__":
    print("PPT written:", build())
