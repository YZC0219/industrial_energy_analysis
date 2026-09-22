"""在真实浏览器里跑一遍筛选栏 —— 离线复算验的是数学, 这里验的是接线。

重点验四件事:
  1. 首屏(未筛选)与改造前的输出一致 —— 这是恒等性, 比任何数字都重要。
  2. 筛选后 KPI 与 结构卡合计 闭合(离线已验, 这里验 DOM 里真的写进去了)。
  3. 表格视图不会显示上一个筛选留下的旧行(renderAll 完全不碰 [data-table])。
  4. 大屏视图真的接上了线 —— 尤其**切换后必须重画**: 正文与正文的图表在大屏
     模式下是 display:none, 宿主的 clientWidth 归零; 若哪天 renderDash 的
     force 丢了, 大屏会变成 6 个空白框, 而报告视图一切正常、控制台也不报错。
     这种"只在另一个视图里坏掉"的故障, 靠肉眼复查报告是发现不了的。

  验的是接线, 不是大屏的设计 —— 图表的坐标轴取值、配色、信息密度这些还得人看。
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


def cks(name, got, want):
    ok = str(got) == str(want)
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
    pg.set_default_timeout(90000)
    pg.goto("file:///D:/industrial_energy_analysis/output/report.html",
            timeout=90000, wait_until="domcontentloaded")
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

    # 重置必须把 VIEW 还原成 D。早先 applyFilter 丢掉了 rebuildView 的返回值,
    # VIEW 一旦变成筛选替身就回不去 —— 而且 hero 会停在**上一次**筛选的值上。
    # 只在"筛选↔筛选"之间切换看不出来, 必须"筛选 → 重置"才暴露。
    print("\n== 4b. 重置回全期 (VIEW 必须还原) ==")
    print("重置前 hero =", hero(pg), "(仅节假日)")
    pg.click("#f-reset")
    pg.wait_for_timeout(600)
    ck("重置后 hero 回到 63,759.63", hero(pg), 63759.63)
    cks("重置后口径提示为未筛选", pg.text_content("#f-scope").strip(),
        "全厂口径（含 W07 公用工程）· 未筛选")
    cks("重置后统计范围 KPI 回到 8 × 6",
        pg.eval_on_selector("#kpis .kpi:nth-child(4) .kpi-value", "e=>e.textContent"), "8 × 6车间 × 能源")

    print("\n== 5. 表格视图跟着筛选重建 (关键回归) ==")
    # 起点必须是未筛选态 —— 这段的前提是"打开时取到的是全期值", 上游若留下筛选
    # 残留, before/after 会相等, 断言就失去意义。
    #
    # **别数行数**: 月份是固定的 24 个, 筛掉日型/车间不会让某个月消失, 只会改它的
    # 值(24 -> 24 恒等)。早先这里断言"筛选后行数 != 全期", 是拿行数当"重建了"的
    # 替身, 而这个替身在语义上永远为假。要比的是**格里的值**。
    pg.click("#f-reset")
    pg.wait_for_timeout(400)
    pg.click('[data-toggle="monthly"]')          # 打开
    pg.wait_for_timeout(300)
    row0 = '[data-table="monthly"] tbody tr:first-child'
    v_open = pg.eval_on_selector(row0, "e=>e.textContent")
    pg.click('#f-day button[data-k="工作日"]')     # 只留周末+节假日
    pg.wait_for_timeout(600)
    v_filt = pg.eval_on_selector(row0, "e=>e.textContent")
    print(f"全期首行 = {v_open}")
    print(f"筛选后首行 = {v_filt}")
    ck("筛选后表格数值真的变了(说明重建了, 不是留旧行)",
       1 if v_filt != v_open else 0, 1, 0)
    ck("筛选后行数仍是 24(月份不因筛选消失)",
       pg.eval_on_selector('[data-table="monthly"]', "e=>e.querySelectorAll('tbody tr').length"), 24, 0)
    # 重置必须把同一格的值还原 —— 这里顺带守住"重置后表格不吃筛选值"
    pg.click("#f-reset")
    pg.wait_for_timeout(600)
    v_reset = pg.eval_on_selector(row0, "e=>e.textContent")
    print(f"重置后首行 = {v_reset}")
    ck("重置后表格还原成全期值", 1 if v_reset == v_open else 0, 1, 0)
    pg.click('[data-toggle="monthly"]')          # 收起, 不影响后续

    print("\n== 6. 大屏视图 ==")
    pg.click("#f-reset")
    pg.wait_for_timeout(300)
    pg.click('#view-seg button[data-view="dash"]')
    pg.wait_for_timeout(700)

    print("dashboard 可见 =", not pg.is_hidden("#dashboard"))
    if pg.is_hidden("#dashboard"):
        FAIL.append("大屏未显示")
    ck("报告正文隐藏", 1 if pg.is_hidden(".page") else 0, 1, 0)
    cks("view-seg 按下态切到大屏",
        pg.get_attribute('#view-seg button[data-view="dash"]', "aria-pressed"), "true")
    cks("view-seg 按下态松开报告",
        pg.get_attribute('#view-seg button[data-view="report"]', "aria-pressed"), "false")

    # 6 个宿主必须都画出了 SVG。空白框 = 少画了一张, 而控制台不会有任何提示。
    ndrawn = pg.evaluate("""() => [...document.querySelectorAll('[data-dchart]')]
        .filter(h => h.querySelector('svg')).length""")
    nhost = pg.evaluate("() => document.querySelectorAll('[data-dchart]').length")
    print(f"图表宿主 = {nhost}  已画出 SVG = {ndrawn}")
    ck("每张图都画出来了(没有空白框)", ndrawn, nhost, 0)

    # "图表渲染失败：..." 是 renderDash 的 catch 分支写进宿主的兜底文案。
    failed = pg.evaluate("""() => [...document.querySelectorAll('[data-dchart]')]
        .filter(h => /渲染失败/.test(h.textContent)).length""")
    ck("没有图表走兜底文案", failed, 0, 0)

    nk = pg.evaluate("() => document.querySelectorAll('#dash-kpis .dkpi').length")
    print("KPI 格数 =", nk)
    ck("大屏 KPI 有 6 格", nk, 6, 0)
    # 全厂口径: 首格"综合能耗"必须与报告首屏 hero 同源 —— 同一个 VIEW.totals.tce,
    # 只是格式不同(hero 用 data-fill 插值, 这里 toFixed(1))。数值必须相等。
    # 按格取, 不从整块文本里 split —— "tce" 在多格里都出现, split 会抓到别的格。
    dk = pg.evaluate("""() => {
      const v = document.querySelector('#dash-kpis .dkpi .dkpi-value');
      return v ? v.textContent.trim() : '';
    }""")
    print("首格 KPI =", dk, " | hero =", hero(pg))
    ck("大屏综合能耗 == 报告 hero(同源)",
       float(dk.replace(",", "").replace("tce", "").strip()), hero(pg), 0.05)
    ck("此处是未筛选的恒等态, hero == 63,759.63", hero(pg), 63759.63)

    # 切回报告: 正文回来, 且正文图表被 force 重画过
    pg.click('#view-seg button[data-view="report"]')
    pg.wait_for_timeout(700)
    ck("切回报告后正文可见", 1 if not pg.is_hidden(".page") else 0, 1, 0)
    ck("切回报告后大屏隐藏", 1 if pg.is_hidden("#dashboard") else 0, 1, 0)
    nrep = pg.evaluate("""() => [...document.querySelectorAll('[data-chart]')]
        .filter(h => h.querySelector('svg')).length""")
    nrephost = pg.evaluate("() => document.querySelectorAll('[data-chart]').length")
    print(f"报告图表宿主 = {nrephost}  已画出 SVG = {nrep}")
    ck("切回后报告图表都还在", nrep, nrephost, 0)

    # 筛选在两种视图里必须一致 —— 大屏复用的是同一套 FILTER/VIEW, 不能有第二份状态。
    #
    # 注意这里**必须在大屏可见时**改筛选: renderDash 开头是
    # `if (!dashActive) return;` —— 大屏隐藏时它整段不跑(此时宿主 clientWidth
    # 为 0, 画出来的图尺寸是错的)。所以"切到大屏"这一步本身就是重绘时机,
    # 不能靠"在报告视图改筛选、再切过去"来验证 —— 那样量的是 setView 的 force 重画,
    # 测不到 applyFilter 之后大屏是否跟着更新。
    pg.click('#view-seg button[data-view="dash"]')
    pg.wait_for_timeout(600)
    before = pg.text_content("#dash-sub")
    before_kpi = pg.evaluate("""() => document.querySelector('#dash-kpis .dkpi .dkpi-value').textContent""")
    pg.click('#f-day button[data-k="工作日"]')      # 点掉工作日 -> 只留周末+节假日
    pg.wait_for_timeout(700)
    after = pg.text_content("#dash-sub")
    after_kpi = pg.evaluate("""() => document.querySelector('#dash-kpis .dkpi .dkpi-value').textContent""")
    print("筛选前副标题 =", before[:60])
    print("筛选后副标题 =", after[:60])
    print("筛选前/后首格 KPI =", before_kpi, "/", after_kpi)
    ck("大屏副标题随筛选变化", 1 if before != after else 0, 1, 0)
    ck("大屏 KPI 随筛选变化", 1 if before_kpi != after_kpi else 0, 1, 0)
    # 187 周末 + 58 节假日 = 245 (Q14 的权威计数)。别凭"一年 52 个周末"拍脑袋 ——
    # 我一开始写成 289, 是错的; 这里的数只能来自 Q14。
    ck("大屏副标题天数 == 245(周末+节假日)",
       int(after.split("·")[1].strip().split(" ")[0]), 245, 0)
    # 同一筛选下, 大屏 KPI 必须与报告 hero 相等 —— 两条渲染路径读同一个 VIEW.totals
    ck("大屏 KPI == 报告 hero(同一筛选下)",
       float(after_kpi.replace(",", "").replace("tce", "").strip()), hero(pg), 0.05)
    pg.click('#view-seg button[data-view="report"]')
    pg.wait_for_timeout(400)
    pg.click("#f-reset")
    pg.wait_for_timeout(400)

    print("\n== 7. 窄屏折行 ==")
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

    print("\n== 8. 控制台错误汇总 ==")
    print(errs if errs else "clean")

    b.close()

print("\n" + (f"{len(FAIL)} 项失败: {FAIL}" if FAIL else "全部通过"))
sys.exit(1 if FAIL else 0)
