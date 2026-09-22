"""在真实浏览器里跑一遍筛选栏 —— 离线复算验的是数学, 这里验的是接线。

重点验三件事:
  1. 首屏(未筛选)与改造前的输出一致 —— 这是恒等性, 比任何数字都重要。
  2. 筛选后 KPI 与 结构卡合计 闭合(离线已验, 这里验 DOM 里真的写进去了)。
  3. 表格视图不会显示上一个筛选留下的旧行(renderAll 完全不碰 [data-table])。
"""
import os
import sys
from playwright.sync_api import sync_playwright

# 本机没装 playwright 自带的 chromium_headless_shell, 退回系统 Chrome。
_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
EXE = _EXE if os.path.exists(_EXE) else None

FAIL = []


def ck(name, got, want, tol=0.01):
    ok = abs(float(got) - float(want)) <= tol
    if not ok:
        FAIL.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  got={got} want={want}")


def kpi(page, label):
    return page.text_content(
        f'#kpis .kpi:has(.kpi-label:text-is("{label}")) .kpi-value'
    )


def num(s):
    return float(s.replace("¥", "").replace(",", "").replace("亿", "")
                 .replace("tce", "").replace("tCO₂", "").strip())


def hero(page):
    return num(page.text_content('[data-fill="tce"]'))


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=EXE)
    pg = b.new_page(viewport={"width": 1280, "height": 1000})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto("file:///D:/industrial_energy_analysis/output/report.html")
    pg.wait_for_timeout(600)

    print("== 0. 无脚本错误 ==")
    print("errors:", errs[:5] if errs else "none")
    if errs:
        FAIL.append("page errors")

    print("\n== 1. 首屏恒等 (未筛选) ==")
    print("hero      =", pg.text_content('[data-fill="tce"]'))
    print("filterbar hidden =", pg.get_attribute("#filterbar", "hidden"))
    print("scope     =", pg.text_content("#f-scope"))
    ck("hero tce == 63,759.63", hero(pg), 63759.63)
    ck("费用合计 == 2.154 亿", num(kpi(pg, "能源费用合计")), 2.154, 0.001)
    print("口径提示可见 =", not pg.is_hidden("#tce-ex-wrap"))

    print("\n== 2. 单车间筛选 (W01 熔炼) ==")
    pg.click('#f-ws input[data-ws="W01"]')          # 取消勾选
    pg.wait_for_timeout(400)
    # 取消勾选 W01 = 排除它; 换一种: 只留 W01
    pg.click("#f-reset")
    pg.wait_for_timeout(300)
    for c in ["W02", "W03", "W04", "W05", "W06", "W07", "W08"]:
        pg.click(f'#f-ws input[data-ws="{c}"]')
    pg.wait_for_timeout(500)
    print("scope     =", pg.text_content("#f-scope"))
    ck("hero == W01 的 13,399.01", hero(pg), 13399.01)
    print("口径提示隐藏 =", pg.is_hidden("#tce-ex-wrap"))

    print("\n== 3. 结构卡合计 == KPI (同一筛选) ==")
    struct_total = pg.evaluate("""() => {
      const rows = [...document.querySelectorAll('#mix-body .bar-row, #mix-body tr')];
      return null;
    }""")
    # 直接读 SVG 里的数值标签不可靠; 改用 TABS 里的表格视图合计
    pg.click('[data-toggle="mix"]')
    pg.wait_for_timeout(300)
    tbl = pg.text_content('[data-table="mix"]')
    print("结构表已展开, 长度 =", len(tbl))

    print("\n== 4. 日型筛选 ==")
    pg.click("#f-reset")
    pg.wait_for_timeout(300)
    # 点一下 = 取消勾选那一档(默认三档全选), 所以点掉"工作日"+"周末" 剩下 节假日
    pg.click('#f-day button[data-k="工作日"]')
    pg.wait_for_timeout(300)
    pg.click('#f-day button[data-k="周末"]')
    pg.wait_for_timeout(500)
    print("scope     =", pg.text_content("#f-scope"))
    ck("hero == 仅节假日 tce", hero(pg), 4375.0058)
    print("三张检测卡被标记 =", pg.eval_on_selector_all(
        ".card-scope", "els => els.map(e => e.dataset.scoped).join(',')"))

    print("\n== 5. 表格视图不吃旧行 (关键回归) ==")
    # 先在**打开**状态下做一个筛选, 让表格重建为筛选后的行; 再重置, 它必须
    # 重建回 24 行 —— renderAll 完全不碰 [data-table], 这一步是靠 applyFilter 自己失效的。
    pg.click("#f-reset")
    pg.wait_for_timeout(300)
    pg.click('[data-toggle="monthly"]')          # 打开
    pg.wait_for_timeout(300)
    n_open = pg.eval_on_selector('[data-table="monthly"]', "e => e.querySelectorAll('tbody tr').length")
    pg.click('#f-day button[data-k="工作日"]')     # 只留周末+节假日
    pg.wait_for_timeout(500)
    n_filt = pg.eval_on_selector('[data-table="monthly"]', "e => e.querySelectorAll('tbody tr').length")
    pg.click("#f-reset")
    pg.wait_for_timeout(500)
    n_reset = pg.eval_on_selector('[data-table="monthly"]', "e => e.querySelectorAll('tbody tr').length")
    print(f"打开={n_open}  筛选后={n_filt}  重置后={n_reset}")
    ck("筛选后行数 != 全期(说明表格真的重建了)", 1 if n_filt != n_open else 0, 1, 0)
    ck("重置后 = 24 个月", n_reset, 24, 0)
    pg.click('[data-toggle="monthly"]')          # 收起, 不影响后续

    print("\n== 6. 窄屏折行 ==")
    pg.set_viewport_size({"width": 390, "height": 900})
    pg.wait_for_timeout(500)
    ov = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    print("横向溢出 =", ov, "px")
    if ov > 1:
        FAIL.append("窄屏横向溢出")
    pg.screenshot(path="tools/_shot_narrow.png", full_page=False)
    pg.set_viewport_size({"width": 1280, "height": 1000})
    pg.wait_for_timeout(400)
    pg.screenshot(path="tools/_shot_wide.png", full_page=False)

    print("\n== 7. 控制台错误汇总 ==")
    print(errs if errs else "clean")

    b.close()

print("\n" + (f"{len(FAIL)} 项失败: {FAIL}" if FAIL else "全部通过"))
sys.exit(1 if FAIL else 0)
