"""Build a standalone Pages case study from committed phase-five evidence."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from string import Template


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "phase5.html"
REPO = "https://github.com/YZC0219/industrial_energy_analysis/blob/main/"


def read_json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def read_csv(relative: str) -> list[dict]:
    with (ROOT / relative).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def repo_link(relative: str, label: str) -> str:
    return f'<a href="{REPO}{html.escape(relative, quote=True)}">{html.escape(label)}</a>'


def main() -> None:
    itac = read_json("output/phase5_itac/assessment_summary.json")
    asset = read_json("output/phase5_asset_energy/summary.json")
    review = read_json("output/phase5_asset_review/summary.json")
    steel = read_json("output/paper/uci_steel/uci_steel_experiment_summary.json")
    episodes = read_csv("output/phase5_real_data/high_use_review_episodes.csv")
    metrics = {row["model"]: row for row in steel["metrics"]}
    lightgbm = metrics["lightgbm"]
    naive = metrics["seasonal_naive_7d"]
    advantage = (naive["mae"] - lightgbm["mae"]) / naive["mae"] * 100
    compressor = sorted(asset["compressed_air_assets"], key=lambda row: row["asset_id"])
    if len(compressor) != 2 or len(episodes) != 62:
        raise ValueError("阶段五证据结构或口径已变化，请先更新展示页生成逻辑")

    values = {
        "itac_intensity": f"{itac['electricity_kwh_per_production_unit']:,.2f}",
        "production": f"{itac['annual_production']:,.0f}",
        "electricity": f"{itac['annual_electricity_kwh']:,.0f}",
        "implemented": str(itac["implemented_status_count"]),
        "recommendations": str(itac["recommendations"]),
        "estimated_savings": f"{itac['estimated_implemented_annual_savings_usd']:,.0f}",
        "estimated_cost": f"{itac['estimated_implemented_cost_usd']:,.0f}",
        "asset_count": str(asset["asset_count"]),
        "usable_windows": f"{asset['usable_windows']:,}",
        "usable_share": f"{asset['usable_share']:.1%}",
        "reviewed_days": f"{review['eligible_review_asset_days']:,}",
        "candidates": str(review["review_candidates"]),
        "candidate_assets": str(review["candidate_assets"]),
        "compressor_a": f"{compressor[0]['observed_usable_kwh']:,.2f}",
        "compressor_b": f"{compressor[1]['observed_usable_kwh']:,.2f}",
        "steel_rows": f"{steel['integrity']['raw_rows']:,}",
        "test_days": str(steel["protocol"]["test_samples_per_model"]),
        "lgb_mae": f"{lightgbm['mae']:,.2f}",
        "naive_mae": f"{naive['mae']:,.2f}",
        "advantage": f"{advantage:.1f}",
        "events": str(len(episodes)),
        "lgb_bar": f"{lightgbm['mae'] / naive['mae'] * 100:.1f}",
        "itac_evidence": repo_link("output/phase5_itac/assessment_case.md", "查看工厂评估明细"),
        "asset_evidence": repo_link("output/phase5_asset_review/review.md", "查看设备复核队列"),
        "steel_evidence": repo_link("output/paper/uci_steel/uci_steel_experiment_summary.json", "查看预测评估摘要"),
        "itac_provenance": repo_link("data/real/itac_2026/provenance.json", "ITAC 下载记录"),
        "asset_provenance": repo_link("data/real/asset_energy/provenance.json", "设备数据下载记录"),
        "steel_provenance": repo_link("data/real/uci_steel_energy/provenance.json", "UCI 下载记录"),
    }
    page = Template("""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="工业能耗项目的公开真实数据案例：工厂基线、设备用电复核和钢厂负荷预测。">
<title>阶段五 · 真实工业数据案例</title>
<style>
:root{color-scheme:light;--bg:#edf1f4;--card:#fff;--ink:#17212b;--muted:#53616e;--line:#d7e0e6;--blue:#1d5f9c;--soft:#e8f2fa}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.7 system-ui,"Noto Sans SC",sans-serif}
a{color:var(--blue);text-underline-offset:3px}a:focus-visible{outline:3px solid #e38821;outline-offset:3px}
.wrap{width:min(1100px,calc(100% - 36px));margin:auto}.nav{display:flex;justify-content:space-between;gap:20px;align-items:center;padding:20px 0;font-size:.94rem}.nav a{font-weight:650}
header{background:#122b40;color:#f5f9fc}header a{color:#b9dcff}.hero{padding:48px 0 58px}.eyebrow{color:#a6d4f2;font-size:.82rem;font-weight:750;letter-spacing:.14em;text-transform:uppercase}
h1{font-size:clamp(2rem,5vw,3.4rem);line-height:1.2;max-width:820px;margin:12px 0 22px}h2{font-size:1.6rem;line-height:1.3;margin:0 0 16px}h3{font-size:1.13rem;margin:0 0 10px}p{margin:0 0 14px}.hero p{max-width:800px;color:#d7e5ee;font-size:1.08rem}
.chips{display:flex;gap:10px;flex-wrap:wrap;margin-top:24px}.chip{border:1px solid #52718a;border-radius:100px;padding:5px 12px;color:#d8eaf5;font-size:.83rem}
main{padding:34px 0 65px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-bottom:24px}.card,.case,.note{background:var(--card);border:1px solid var(--line);border-radius:15px;box-shadow:0 2px 8px rgba(10,30,45,.035)}
.card{padding:22px}.card .n{font-size:2.1rem;font-weight:750;line-height:1.1;color:#174f80;font-variant-numeric:tabular-nums}.card .unit{font-size:.92rem;color:var(--muted);margin-top:6px}.card p{font-size:.87rem;color:var(--muted);margin:8px 0 0}
.case{padding:28px;margin:22px 0}.tag{display:inline-block;background:var(--soft);color:#195987;border-radius:100px;padding:3px 11px;font-size:.8rem;font-weight:750;margin-bottom:12px}.case .cols{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(230px,1fr);gap:28px}.case aside{background:#f3f7fa;border-radius:11px;padding:18px}.case aside p:last-child{margin-bottom:0}
.metric{font-size:1.65rem;font-weight:750;color:#174f80;font-variant-numeric:tabular-nums}.small{font-size:.88rem;color:var(--muted)}.case .links{margin-top:15px;font-weight:650}
.bars{margin:18px 0}.bar-row{display:grid;grid-template-columns:115px 1fr 115px;align-items:center;gap:8px;margin:9px 0;font-size:.86rem}.track{height:12px;background:#d7e5ee;border-radius:100px;overflow:hidden}.fill{height:100%;background:#2b7daf;border-radius:100px}.fill.base{background:#93aab9}
.note{padding:24px;margin-top:22px;border-left:5px solid #d48633}.note ul{margin:8px 0 0;padding-left:21px}.note li{margin:5px 0}footer{border-top:1px solid var(--line);padding:22px 0 35px;font-size:.86rem;color:var(--muted)}
@media(max-width:760px){.grid{grid-template-columns:1fr}.case .cols{grid-template-columns:1fr}.case{padding:20px}.hero{padding:35px 0 45px}.bar-row{grid-template-columns:90px 1fr 90px;font-size:.78rem}}
</style>
</head>
<body>
<header><div class="wrap"><nav class="nav" aria-label="页面导航"><strong>工业能耗数据项目</strong><div><a href="./index.html">可视化主报告</a>　<a href="https://github.com/YZC0219/industrial_energy_analysis">项目源码</a></div></nav>
<div class="hero"><div class="eyebrow">Phase 05 · Public industrial data</div><h1>从真实用能记录，到可复核的能源管理线索</h1>
<p>三个独立公开数据案例，分别展示工厂年度基线、设备计量筛查和钢厂负荷预测。数据来自不同企业，各项结论只在各自的数据范围内成立。</p>
<div class="chips"><span class="chip">来源与 SHA-256 可追溯</span><span class="chip">下载前核查 robots.txt</span><span class="chip">估算与实测分开标注</span></div></div></div></header>
<main class="wrap">
<div class="grid" aria-label="案例关键数字">
<div class="card"><div class="n">${itac_intensity}</div><div class="unit">kWh / ton</div><p>ITAC 单厂年度电耗强度；ton 沿用源数据单位。</p></div>
<div class="card"><div class="n">${candidates}</div><div class="unit">设备复核候选日</div><p>从 ${reviewed_days} 个可比资产日中筛出，仍需现场确认。</p></div>
<div class="card"><div class="n">${test_days}</div><div class="unit">滚动测试日</div><p>UCI 单一钢厂 2018 年日用电预测对照。</p></div>
</div>
<section class="case" id="itac"><span class="tag">案例 01 · 工厂级</span><h2>把产量、用电与措施成本放在同一张账上</h2><div class="cols"><div>
<p>美国 ITAC 的 DL0238 评估记录显示：年产量 <strong>${production} ton</strong>、年用电 <strong>${electricity} kWh</strong>。据此得到 ${itac_intensity} kWh/ton 的单年电耗强度。</p>
<p>${recommendations} 项建议中有 ${implemented} 项被记录为已实施。这些建议对应年节省<strong>估计</strong> ${estimated_savings} 美元、投资<strong>估计</strong> ${estimated_cost} 美元。逐项可查看压缩机控制、蒸汽疏水阀等建议及其估计回收期。</p>
<p class="links">${itac_evidence} · ${itac_provenance}</p></div><aside><h3>解释边界</h3><p>实施状态来自评估数据库；节省额与成本是工程估计。缺少同厂改造前后连续计量，不能把估计额当作已核验节能。</p><p class="small">来源：<a href="https://itac.university/assessment/DL0238">ITAC 官方评估页</a></p></aside></div></section>
<section class="case" id="assets"><span class="tag">案例 02 · 设备级</span><h2>先识别计量质量，再排出设备复核顺序</h2><div class="cols"><div>
<p>公开设备数据覆盖 <strong>${asset_count} 个资产</strong>。只取 ALL 相 15 分钟电量，并剔除不可靠窗口与已知计量故障时段后，保留 <strong>${usable_windows} 个窗口（${usable_share}）</strong>。</p>
<p>两台压缩空气设备在各自可用窗口内分别记录 ${compressor_a} 与 ${compressor_b} kWh。使用 2025-11-01 前的历史数据建立工作日/周末 P95，只筛查之后的资产日，得到 <strong>${candidates} 个候选日</strong>，涉及 ${candidate_assets} 个设备资产。</p>
<p class="links">${asset_evidence} · ${asset_provenance}</p></div><aside><h3>复核线索，不是故障结论</h3><p>候选日须结合班次、设备状态和订单核查。进线、配电、光伏与下游设备可能重复计量，因此不跨资产加总电量或“超出量”。</p><p class="small">来源：<a href="https://doi.org/10.5281/zenodo.19180972">原始数据 DOI</a>（CC BY 4.0）</p></aside></div></section>
<section class="case" id="steel"><span class="tag">案例 03 · 负荷预测</span><h2>在单厂真实序列上检验预测与筛查方法</h2><div class="cols"><div>
<p>UCI 钢厂数据有 <strong>${steel_rows} 条 15 分钟记录</strong>。按日汇总后，滚动预测共 ${test_days} 个测试日；LightGBM 的 MAE 为 ${lgb_mae} kWh/日，7 日季节朴素基线为 ${naive_mae} kWh/日。本次实验前者低约 ${advantage}%。</p>
<div class="bars" role="img" aria-label="预测平均绝对误差：LightGBM ${lgb_mae} 千瓦时每天，季节朴素基线 ${naive_mae} 千瓦时每天">
<div class="bar-row"><span>LightGBM</span><div class="track"><div class="fill" style="width:${lgb_bar}%"></div></div><strong>${lgb_mae}</strong></div>
<div class="bar-row"><span>7 日基线</span><div class="track"><div class="fill base" style="width:100%"></div></div><strong>${naive_mae}</strong></div></div>
<p>独立的负荷筛查将最后两个月的高用能时段合并为 <strong>${events} 个待复核事件</strong>。模型误差和候选事件都不代表节能效果。</p>
<p class="links">${steel_evidence} · ${steel_provenance}</p></div><aside><h3>单厂验证范围</h3><p>没有产品产量、设备映射、措施日期或能源价格；不能从预测优势推出普遍模型优势，也不能据此计算实际投资回报。</p><p class="small">来源：<a href="https://archive.ics.uci.edu/dataset/851/steel+industry+energy+consumption">UCI Steel Industry Energy Consumption</a>（CC BY 4.0）</p></aside></div></section>
<section class="note"><h2>下一步需要什么数据</h2><p>要核验一项真实措施，需要同一工厂、同一设备或工序的实施日期、前后计量、产量、天气、产品结构和成本记录。目前三组公开数据不能跨厂拼接为一项改造的前后对照。</p>
<ul><li>本页金额、节省估计与设备筛查线索各自保留原始口径。</li><li>模拟数据的完整车间基线与情景方法见 <a href="https://github.com/YZC0219/industrial_energy_analysis/blob/main/docs/阶段五_能效基线与节能情景.md">阶段五技术说明</a>。</li></ul></section>
</main><footer><div class="wrap">工业能耗数据仓库与分析系统 · 独立公开数据案例 · 页面由仓库内已归档证据生成</div></footer>
</body></html>
""").substitute(values)
    OUTPUT.write_text(page, encoding="utf-8")
    print(f"已生成 {OUTPUT}")


if __name__ == "__main__":
    main()
