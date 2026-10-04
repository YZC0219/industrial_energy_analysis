'use strict';
const $ = id => document.getElementById(id);
const states = {pending:'待核查', confirmed:'已确认异常', false_positive:'误报', resolved:'已处理'};
let offset = 0, total = 0, selected = null, requestVersion = 0, detailVersion = 0;
let submittedFilters = new URLSearchParams();

function node(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
function message(text = '') { $('message').textContent = text; }
function localTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai'}) : '未知';
}
async function api(path, body) {
  const response = await fetch(path, body ? {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
  } : {});
  let data;
  try { data = await response.json(); }
  catch { throw Error('服务返回异常，请稍后重试'); }
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : '输入无效，请检查填写内容');
  return data;
}
function table(rows) {
  const wrap = node('div', undefined, 'scroll');
  if (!rows.length) { wrap.textContent = '当前批次没有相关明细'; return wrap; }
  const keys = [...new Set(rows.flatMap(row => Object.keys(row)))];
  const t = node('table'), head = node('tr');
  keys.forEach(key => head.append(node('th', key))); t.append(head);
  rows.forEach(row => {
    const tr = node('tr');
    keys.forEach(key => tr.append(node('td', row[key] ?? '—'))); t.append(tr);
  });
  wrap.append(t); return wrap;
}
function clearDetail() {
  selected = null; ++detailVersion;
  $('detail').replaceChildren(node('p', '选择一条异常，查看证据并记录处理结果。', 'muted'));
}
function captureFilters() {
  submittedFilters = new URLSearchParams();
  [['workshop_code','workshop'], ['date_from','from'], ['date_to','to'],
   ['status','status'], ['sort','sort']].forEach(([key,id]) => {
    if ($(id).value) submittedFilters.set(key, $(id).value);
  });
  if ($('priority').checked) submittedFilters.set('priority_only', 'true');
}
async function load() {
  const version = ++requestVersion;
  const params = new URLSearchParams(submittedFilters);
  params.set('limit', 50); params.set('offset', offset);
  $('refresh').disabled = true;
  try {
    const data = await api('/api/v1/diagnostics?' + params);
    if (version !== requestVersion) return;
    total = data.total;
    if (offset >= total && offset > 0) {
      offset = Math.max(0, Math.floor((total - 1) / 50) * 50); return await load();
    }
    $('count').textContent = total + ' 条';
    $('updated').textContent = '产物更新时间（北京时间）：' + localTime(data.generated_at) +
      ' · ' + (data.llm_configured ? '模型已配置' : '证据检索模式');
    const choice = $('workshop').value;
    $('workshop').replaceChildren(node('option', '全部车间'));
    $('workshop').firstChild.value = '';
    data.workshops.forEach(w => {
      const option = node('option', w.code + ' ' + w.name); option.value = w.code;
      $('workshop').append(option);
    });
    $('workshop').value = choice;
    $('summary').replaceChildren();
    Object.entries(states).forEach(([key,label]) => {
      const b = node('button', undefined, 'stat');
      b.append(node('span', label), node('strong', data.summary[key]));
      b.onclick = () => { $('status').value = key; $('filters').requestSubmit(); };
      $('summary').append(b);
    });
    const priority = node('div', undefined, 'stat priority-stat');
    priority.append(node('span', '优先核查 · 未处理'), node('strong', data.summary.priority_open));
    $('summary').append(priority);
    $('list').replaceChildren();
    data.items.forEach(item => {
      const b = node('button', undefined, 'incident' + (selected === item.id ? ' active' : ''));
      b.dataset.id = item.id;
      b.append(node('strong', item.workshop + ' · ' + item.metric),
        node('span', item.event_date + ' · ' + (item.period === 'day' ? '日度' : '月度') + ' · ' + states[item.status]),
        node('small', item.severity + ' · ' + item.direction + ' · 实际 ' + item.value + ' ' + item.unit, 'badge'));
      b.onclick = () => show(item.id); $('list').append(b);
    });
    if (!data.items.length) $('list').append(node('p', '当前筛选条件下没有异常。', 'muted'));
    if (selected && !data.items.some(item => item.id === selected)) clearDetail();
    $('prev').disabled = offset === 0; $('next').disabled = offset + 50 >= total;
    $('page').textContent = total ? `${offset+1}–${Math.min(offset+50,total)}` : '0';
  } catch (error) {
    if (version === requestVersion) {
      message(error.message); $('list').replaceChildren(node('p', '异常列表暂时不可用，请刷新重试。'));
      $('summary').replaceChildren(); clearDetail(); $('prev').disabled = $('next').disabled = true;
    }
  } finally { if (version === requestVersion) $('refresh').disabled = false; }
}
function trendChart(rows, incident) {
  const wrap = node('div', undefined, 'trend');
  const valid = rows.map(row => ({date:row['日期'], value:Number(row['综合能耗_tce'])}))
    .filter(row => Number.isFinite(row.value));
  if (!valid.length) { wrap.textContent = '暂无趋势数据'; return wrap; }
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', '0 0 600 190');
  svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', '综合能耗趋势，单位 tce');
  const max = Math.max(...valid.map(row => row.value)), min = Math.min(...valid.map(row => row.value));
  const range = max - min || Math.max(Math.abs(max) * .1, 1);
  const x = index => valid.length === 1 ? 300 : 45 + index * 520 / (valid.length - 1);
  const y = value => 140 - (value - min) * 110 / range;
  const line = document.createElementNS(ns, 'polyline');
  line.setAttribute('points', valid.map((row,index) => `${x(index)},${y(row.value)}`).join(' '));
  line.setAttribute('fill', 'none'); line.setAttribute('stroke', '#246652'); line.setAttribute('stroke-width', '3');
  svg.append(line);
  valid.forEach((row,index) => {
    const dot = document.createElementNS(ns, 'circle');
    dot.setAttribute('cx', x(index)); dot.setAttribute('cy', y(row.value)); dot.setAttribute('r', '4');
    dot.setAttribute('fill', row.date === incident.event_date ? '#b97227' : '#246652');
    const title = document.createElementNS(ns, 'title'); title.textContent = `${row.date}：${row.value} tce`;
    dot.append(title); svg.append(dot);
  });
  [[45,170,valid[0].date],[480,170,valid.at(-1).date], [4,25,max.toFixed(2)], [4,145,min.toFixed(2)]]
    .forEach(([xx,yy,text]) => {
      const label = document.createElementNS(ns, 'text'); label.setAttribute('x', xx); label.setAttribute('y', yy);
      label.setAttribute('font-size', '12'); label.setAttribute('fill', '#63796e'); label.textContent = text;
      svg.append(label);
    });
  wrap.append(svg, node('small', '综合能耗（tce） · ' + valid.length + ' 个日点 · 来源 Q28。能耗趋势与单耗检测指标不同。'));
  return wrap;
}
function labeled(label, input) { const el = node('label', label); el.append(input); return el; }
function renderQuestion(root, id) {
  const form = node('form', undefined, 'form'), q = node('textarea');
  q.placeholder = '例如：本次异常有哪些证据？'; q.required = true; q.maxLength = 1000; q.rows = 2;
  const ask = node('button', '查看证据回答', 'primary'), answer = node('div'), quick = node('div', undefined, 'quick');
  ['本次异常有哪些证据？','同期能源费用是多少？','同期产量是多少？'].forEach(text => {
    const b = node('button', text); b.type = 'button'; b.onclick = () => { q.value = text; form.requestSubmit(); };
    quick.append(b);
  });
  form.append(node('h3', '围绕当前异常提问'), quick, q, ask, answer);
  form.onsubmit = async event => {
    event.preventDefault(); if (ask.disabled) return;
    ask.disabled = true; answer.replaceChildren(node('p', '正在分析…'));
    try {
      const result = await api('/api/v1/diagnostics/ask', {question:q.value, incident_id:id});
      answer.replaceChildren(node('p', result.mode_label, 'muted'), node('p', result.summary));
      result.claims.forEach(claim => {
        const block = node('div', undefined, 'answer');
        claim.citations.forEach(cite => {
          block.append(typeof cite.evidence_value === 'object' ? table([cite.evidence_value]) : node('p', claim.statement));
          block.append(node('small', '来源：' + cite.source_path.split(/[\\/]/).pop() + ' · ' + cite.record_key));
        });
        answer.append(block);
      });
    } catch (error) { answer.replaceChildren(node('p', error.message)); }
    finally { ask.disabled = false; }
  };
  root.append(form);
}
function renderFeedback(root, data, id) {
  const form = node('form', undefined, 'form'), status = node('select'), recent = data.history[0];
  Object.entries(states).forEach(([key,label]) => { const o = node('option', label); o.value = key; status.append(o); });
  status.value = recent?.status || 'pending';
  const operator = node('input'); operator.placeholder = '处理人'; operator.required = true;
  operator.maxLength = 80; operator.value = recent?.operator || '';
  const reason = node('textarea'); reason.placeholder = '确认原因或判断依据（如未确认，请明确写待核实）';
  reason.maxLength = 2000; reason.value = recent?.reason || '';
  const action = node('textarea'); action.placeholder = '处理措施及结果'; action.maxLength = 2000;
  action.value = recent?.action || '';
  const save = node('button', '保存处理反馈', 'primary');
  const validation = () => { reason.required = status.value !== 'pending'; action.required = status.value === 'resolved'; };
  status.onchange = validation; validation();
  form.append(node('h3', '处理反馈'), labeled('处理状态', status), labeled('处理人', operator),
    labeled('确认原因 / 判断依据', reason), labeled('处理措施 / 结果', action), save);
  form.onsubmit = async event => {
    event.preventDefault(); if (save.disabled) return; save.disabled = true;
    try {
      await api('/api/v1/diagnostics/' + id + '/feedback', {status:status.value, operator:operator.value,
        reason:reason.value, action:action.value, expected_revision:recent?.id || 0});
      if (selected === id) { await show(id); await load(); }
      message('反馈已保存');
    } catch (error) { message(error.message); }
    finally { save.disabled = false; }
  };
  root.append(form, node('h3', '处理历史'));
  if (!data.history.length) root.append(node('p', '尚无处理记录', 'muted'));
  data.history.forEach(h => {
    const block = node('div', undefined, 'history');
    block.append(node('strong', states[h.status] + ' · ' + h.operator), node('p', h.reason || '原因未填写'),
      node('p', h.action || '措施未填写'), node('small', localTime(h.created_at))); root.append(block);
  });
}
async function show(id) {
  selected = id; const version = ++detailVersion;
  document.querySelectorAll('.incident').forEach(b => b.classList.toggle('active', b.dataset.id === id));
  $('detail').replaceChildren(node('p', '正在读取证据…'));
  try {
    const data = await api('/api/v1/diagnostics/' + id);
    if (version !== detailVersion) return;
    const item = data.incident, root = $('detail'); root.replaceChildren(node('h2', item.workshop + ' · ' + item.metric));
    root.append(node('p', item.event_date + ' · 来源：' + item.evidence_source, 'muted'));
    const facts = node('div', undefined, 'facts');
    [['实际值',item.value + ' ' + item.unit], [item.period === 'day' ? '车间均值' : '告警阈值',item.expected + ' ' + item.unit],
     ['检测信号',item.z_score === null ? '环比超过 25%' : 'Z = ' + item.z_score]].forEach(([key,value]) => {
      const f = node('div', key, 'fact'); f.append(node('b', value)); facts.append(f);
    });
    root.append(facts);
    if (item.deviation_pct !== null) root.append(node('p', '单耗相对车间均值：' +
      (item.deviation_pct >= 0 ? '+' : '') + item.deviation_pct + '% · ' + item.direction, 'muted'));
    root.append(node('h3', item.period === 'day' ? '异常日前 14 天能耗趋势' : '异常月份能耗趋势'), trendChart(data.trend,item),
      node('h3', '异常原始证据'), table(data.evidence), node('h3', '相关能耗与产量'), table(data.daily_context),
      node('p', data.boundary, 'notice'), node('h3', '建议核查'));
    const checks = node('ul'); data.checklist.forEach(text => checks.append(node('li',text))); root.append(checks);
    renderQuestion(root,id); renderFeedback(root,data,id);
  } catch (error) { if (version === detailVersion) $('detail').replaceChildren(node('p',error.message)); }
}
$('filters').onsubmit = event => {
  event.preventDefault();
  if ($('from').value && $('to').value && $('from').value > $('to').value) { message('起始日期不能晚于结束日期'); return; }
  message(); offset = 0; clearDetail(); captureFilters(); load();
};
$('clear').onclick = () => { $('filters').reset(); $('filters').requestSubmit(); };
$('refresh').onclick = async () => { message(); await load(); if (selected) await show(selected); };
$('prev').onclick = () => { offset = Math.max(0,offset-50); clearDetail(); load(); };
$('next').onclick = () => { offset += 50; clearDetail(); load(); };
captureFilters(); load();
