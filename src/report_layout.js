/* Report navigation and progressive disclosure; computations stay in the report engine. */
(() => {
  'use strict';
  const root = document.documentElement;
  const page = document.querySelector('.page');
  if (!page) return;
  const panels = {
    overview: ['能源总览', '先看规模与趋势，再定位能效改进的方向。'],
    structure: ['结构与趋势', '用能结构、月度变化与气候响应，放在同一个分析视角。'],
    efficiency: ['效率与成本', '对照单位产品能耗、节能折算与停产损耗。'],
    anomaly: ['异常诊断', '从月度突增，到日度单耗与产量基线，逐层定位异常。'],
    evidence: ['方法与证据', '查看清洗过程、模型表现与可复现的计算依据。']
  };
  const groups = {结构:'structure',趋势:'structure',成因:'structure',成效:'efficiency',环保绩效:'efficiency',损耗:'efficiency',情景:'efficiency',风险:'anomaly',异常:'anomaly',基线:'anomaly'};
  page.querySelectorAll(':scope > .sec').forEach(section => {
    if (!section.dataset.reportGroup) {
      const tag = section.querySelector('.sec-head > .eyebrow');
      section.dataset.reportGroup = section.classList.contains('overview') ? 'overview' : (groups[tag?.textContent.trim()] || 'evidence');
    }
    if (section.hidden) section.dataset.sourceUnavailable = 'true';
  });
  const masthead = page.querySelector('.masthead');
  masthead.dataset.reportGroup = 'overview';
  const facts = masthead.querySelector('.facts');
  if (facts) {
    const disclosure = document.createElement('details');
    disclosure.className = 'data-notes';
    disclosure.dataset.reportGroup = 'overview';
    disclosure.innerHTML = '<summary>数据来源与统计口径<span>模拟数据 · 可复现</span></summary>';
    disclosure.append(facts);
    page.querySelector('.overview-charts').after(disclosure);
  }
  page.querySelectorAll('.sec-head > .sec-sub').forEach(paragraph => {
    const detail = document.createElement('details');
    detail.className = 'analysis-note';
    detail.innerHTML = '<summary>展开分析解读与口径</summary>';
    paragraph.before(detail);
    detail.append(paragraph);
  });
  page.querySelectorAll('.card-head > .card-sub').forEach(paragraph => {
    if (paragraph.textContent.trim().length < 110) return;
    const detail = document.createElement('details');
    detail.className = 'chart-explainer';
    detail.innerHTML = '<summary>图表说明</summary>';
    paragraph.before(detail);
    detail.append(paragraph);
  });
  const hashGroups = {'energy-overview':'overview','energy-structure':'structure','energy-trend':'structure','energy-efficiency':'efficiency','energy-scenario':'efficiency','energy-anomaly':'anomaly','recent-evidence':'evidence'};
  function selectPanel(panel, scroll = true) {
    if (!panels[panel]) panel = 'overview';
    if (root.classList.contains('dash-mode') || root.classList.contains('ops-mode')) {
      document.querySelector('[data-view="report"]')?.click();
    }
    root.dataset.reportPanel = panel;
    page.querySelectorAll(':scope > [data-report-group]').forEach(section => {
      section.hidden = section.dataset.reportGroup !== panel || section.dataset.sourceUnavailable === 'true';
    });
    document.querySelectorAll('[data-report-panel]').forEach(button => {
      if (button.tagName === 'BUTTON') button.setAttribute('aria-pressed', String(button.dataset.reportPanel === panel));
    });
    document.getElementById('panel-title').textContent = panels[panel][0];
    document.getElementById('panel-description').textContent = panels[panel][1];
    window.dispatchEvent(new Event('resize'));
    if (scroll) window.scrollTo({top:0, behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('button[data-report-panel]');
    if (button) selectPanel(button.dataset.reportPanel);
    const link = event.target.closest('a[href^="#"]');
    if (link) {
      const id = link.getAttribute('href').slice(1);
      if (hashGroups[id]) selectPanel(hashGroups[id], false);
    }
  });
  window.addEventListener('hashchange', () => {
    const id = location.hash.slice(1);
    if (hashGroups[id]) selectPanel(hashGroups[id], false);
  });
  const foot = document.createElement('footer');
  foot.className = 'report-foot';
  foot.textContent = '能迹 EnergyTrace · 模拟数据分析报告 · 筛选图表与指标联动，章节解读采用全期基准。';
  page.append(foot);
  selectPanel(hashGroups[location.hash.slice(1)] || 'overview', false);
})();
