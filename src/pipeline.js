'use strict';
const $ = id => document.getElementById(id);
const labels = {success:'成功',failed:'失败',running:'执行中',queued:'排队中',scheduled:'待执行',up_for_retry:'等待重试',upstream_failed:'上游失败',skipped:'跳过',removed:'已移除',deferred:'等待恢复',unknown:'未知',passed:'通过',available:'文件存在',missing:'未生成'};
const tones = {success:'good',passed:'good',failed:'bad',upstream_failed:'bad',running:'busy',queued:'busy',up_for_retry:'warn'};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = value => { if (!value) return '—'; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? '—' : new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(parsed); };
const shortDate = value => { if(!value) return '无计划'; return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit'}).format(new Date(value)); };
function duration(value) { if(value === null || value === undefined) return '—'; const n = Math.max(0,Math.round(value)); if(n < 60) return `${n} 秒`; if(n < 3600) return `${Math.floor(n/60)} 分 ${n%60} 秒`; if(n < 86400) return `${Math.floor(n/3600)} 小时 ${Math.floor(n%3600/60)} 分`; return `${Math.floor(n/86400)} 天 ${Math.floor(n%86400/3600)} 小时`; }
function delay(value) { return value === null || value === undefined ? '待记录' : value < 0 ? `提前 ${duration(-value)}` : value === 0 ? '准点' : duration(value); }
function pill(value, text, tone) { return `<span class="pill ${esc(tone ?? tones[value] ?? '')}">${esc(text ?? labels[value] ?? '未知')}</span>`; }
function stat(title, value, hint, badge='') { return `<article class="stat"><div class="stat-label">${esc(title)}${badge}</div><strong class="stat-value">${esc(value)}</strong><small>${esc(hint)}</small></article>`; }
function delivery(title, badge, detail) { return `<div class="delivery"><div class="delivery-top"><span>${esc(title)}</span>${badge}</div><p>${esc(detail)}</p></div>`; }
function render(data) {
  const run = data.current, success = data.last_success, fresh = data.snapshot.freshness === 'fresh' && data.snapshot.available === true;
  $('updated').textContent = `页面读取 ${date(data.served_at)}`;
  $('notice').textContent = data.notices.join(' ');
  if (!fresh && !data.notices.length) $('notice').textContent = '当前记录已过期，请重新采集运行状态。';
  const badge = fresh ? pill('passed','采集有效') : pill('unknown',data.snapshot.freshness === 'stale' ? '记录过期' : data.snapshot.freshness === 'fresh' ? '采集不可用' : '历史记录','warn');
  $('summary').innerHTML = stat('最近批次状态',labels[run?.state] ?? '未知',run?.failed_steps.length ? `失败位置：${run.failed_steps.join('、')}` : `采集时间：${date(data.snapshot.checked_at)}`,badge)
    + stat('最近成功完成',success?.finished_at ? date(success.finished_at).slice(5,16) : '暂无记录',success ? `计划开始：${date(success.planned_at)}` : '尚无成功归档或采集记录')
    + stat('最近批次启动延迟',delay(run?.delay_seconds),run?.planned_at ? `计划开始：${date(run.planned_at)}` : '等待计划与实际开始时间',run?.delay_seconds > 0 ? pill('unknown','延迟','warn') : '')
    + stat('下一次调度',data.schedule.next_at ? date(data.schedule.next_at).slice(5,16) : '尚未确认',data.schedule.paused === true ? '调度已暂停' : '每日 02:00 · 北京时间；以调度记录为准');
  $('run-description').textContent = run ? `批次 ${run.run_id}` : '尚无可用的当前批次记录';
  $('run-description').className = 'run-id';
  $('run-state').outerHTML = `<span id="run-state">${pill(run?.state ?? 'unknown',fresh ? undefined : '历史 / 未确认')}</span>`;
  const steps = run?.tasks ?? [{id:'sync_current_dimensions',label:'同步维度',state:'unknown'},{id:'capture_complete_cdc',label:'捕获数据变更',state:'unknown'},{id:'apply_cdc_dwd_dws_ads',label:'更新分层数仓',state:'unknown'}];
  $('flow').innerHTML = steps.map((task,i)=>`<li class="step"><div class="step-top"><span class="step-index">STEP 0${i+1}</span>${pill(task.state)}</div><h3>${esc(task.label)}</h3><p>开始 ${esc(date(task.started_at))}</p><p>耗时 ${esc(duration(task.duration_seconds))} · 尝试记录 ${esc(task.attempt ?? '—')}</p></li>`).join('');
  $('run-facts').innerHTML = [['计划开始',date(run?.planned_at)],['实际开始',date(run?.started_at)],['完成时间',date(run?.finished_at)],['旧写入链路',data.schedule.old_writer_paused === true ? '已暂停' : data.schedule.old_writer_paused === false ? '未暂停 · 需核查竞争写入' : '尚未确认']].map(([label,value])=>`<div class="fact">${esc(label)}<b>${esc(value)}</b></div>`).join('');
  $('deliveries').innerHTML = delivery('批次执行',pill(run?.state ?? 'unknown'),run ? `批次：${run.run_id}。${fresh ? '采集时间有效。' : '当前状态需重新采集确认。'}` : '当前调度状态未知；历史成功记录不代表最近批次完成。')
    + delivery('当前批次逐字段对账',pill(run?.reconciliation ?? 'unknown'),run?.audit_source ? `证据：${run.audit_source}；源记录数 ${run.source_rows ?? '—'}。` : '尚无与当前批次对应的审计证据。执行成功不自动视为对账通过。')
    + delivery('本地分析报告',pill(data.report.state),`文件修改：${date(data.report.modified_at)}。报告与当前 CDC 批次的对应关系尚未确认。`);
  $('health-time').textContent = `采集于 ${date(data.health.checked_at)}`;
  $('health-freshness').innerHTML = data.health.freshness === 'fresh' ? '15 分钟内' : '历史 / 未确认';
  $('health-freshness').className = `pill ${data.health.freshness === 'fresh' ? 'good' : 'warn'}`;
  $('components').innerHTML = data.health.components.map(component=>`<div class="component"><span><i class="dot ${data.health.freshness === 'fresh' ? esc(tones[component.state] ?? '') : ''}"></i>${esc(component.label)}</span>${pill(component.state,data.health.freshness === 'fresh' ? undefined : `当时${labels[component.state] ?? '未知'}`,data.health.freshness === 'fresh' ? undefined : '')}</div>`).join('');
  $('history').innerHTML = data.history.length ? data.history.map(row=>`<tr><td title="${esc(row.run_id)}">${esc(date(row.planned_at))}</td><td>${esc(date(row.started_at))}</td><td>${esc(row.planned_at ? delay(row.delay_seconds) : '不适用')}</td><td>${esc(duration(row.duration_seconds))}</td><td>${pill(row.state)}</td><td>${pill(row.reconciliation)}</td></tr>`).join('') : '<tr><td colspan="6" class="empty">暂无批次记录。采集服务可用后将显示运行历史。</td></tr>';
  const bars = data.history.slice(0,7).reverse(), max = Math.max(1,...bars.map(row=>Math.max(0,row.delay_seconds ?? 0)));
  $('delay-chart').innerHTML = bars.length ? bars.map(row=>`<div class="bar-item" title="${esc(date(row.planned_at))} · ${esc(row.planned_at ? delay(row.delay_seconds) : '不适用')}"><span class="bar-value">${esc(!row.planned_at ? '不适用' : row.delay_seconds === null ? '未开始' : row.delay_seconds < 3600 ? `${Math.round(Math.max(0,row.delay_seconds)/60)} 分` : `${(row.delay_seconds/3600).toFixed(1)} 时`)}</span><div class="bar-box"><div class="bar" style="height:${Math.max(3,Math.max(0,row.delay_seconds ?? 0)/max*100)}px"></div></div><small>${esc(shortDate(row.planned_at))}</small></div>`).join('') : '<p class="empty">暂无延迟记录</p>';
  $('sources').textContent = `批次来源：${data.snapshot.source}。环境检查：${data.health.source ?? '暂无'}。`;
}
let loading = false;
async function refresh() {
  if(loading) return; loading = true; $('refresh').disabled = true;
  try {
    const response = await fetch('/api/v1/pipeline',{cache:'no-store',signal:AbortSignal.timeout(15000)});
    if(!response.ok) throw new Error('无法读取运行记录');
    const data = await response.json(); render(data); $('error').hidden = true;
  } catch(error) {
    $('error').textContent = '刷新失败；下方如有内容，是上一次读取的记录。请检查本地服务。';
    $('error').hidden = false;
    $('updated').textContent = '本次刷新未成功';
  } finally { loading = false; $('refresh').disabled = false; }
}
$('refresh').addEventListener('click',refresh);
refresh();
setInterval(()=>{ if(!document.hidden) refresh(); },30000);
