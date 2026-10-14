const $ = selector => document.querySelector(selector);
const names = { gemini: 'Gemini', codex: 'Codex', deepseek: 'DeepSeek' };
const statuses = { queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消', pending: '待执行', interrupted: '已中断' };
const presets = { full: ['requirements', 'architecture', 'implementation', 'testing', 'review', 'documentation'], plan: ['requirements', 'research', 'architecture'], build: ['implementation', 'testing', 'review'], review: ['review', 'documentation'] };
let state, selectedJob = null, selectedProject = '', initialized = false, timer, toastTimer;
function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
function toast(text, bad = false) { const node = $('#toast'); node.textContent = text; node.classList.toggle('error', bad); node.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => node.hidden = true, 6000); }
async function api(url, method = 'GET', value) {
  const response = await fetch(url, { method, headers: value === undefined ? {} : { 'Content-Type': 'application/json' }, body: value === undefined ? undefined : JSON.stringify(value) });
  const result = await response.json(); if (!response.ok) throw new Error(result.error || '请求失败'); return result;
}
function view(name) {
  document.querySelectorAll('.view').forEach(node => node.hidden = node.id !== `${name}-view`);
  document.querySelectorAll('.nav').forEach(node => node.classList.toggle('active', node.dataset.view === name));
  $('#view-title').textContent = { workspace: '协作工作台', history: '任务记录', settings: '模型与连接' }[name];
}
function renderProviders() {
  const info = { gemini: ['✦', '需求 · 研究 · 架构'], codex: ['⌘', '实现 · 测试 · 集成'], deepseek: ['≈', '审查 · 分析 · 文档'] };
  $('#provider-cards').replaceChildren(...Object.keys(info).map(id => {
    const ready = id === 'deepseek' ? state.settings.hasDeepseekKey : Boolean(state.settings.detected[id]);
    const card = element('div', 'provider-card');
    const content = element('div'); content.append(element('div', 'provider-name', names[id]), element('div', 'provider-desc', info[id][1]));
    const status = element('div', `provider-state ${ready ? '' : 'off'}`); status.append(element('span', 'dot'), document.createTextNode(ready ? id === 'deepseek' ? '已配置' : '已检测' : '待连接'));
    card.append(element('div', `provider-icon ${id}`, info[id][0]), content, status); return card;
  }));
}
function renderProjects() {
  const select = $('#project-select'); const old = selectedProject || select.value;
  select.replaceChildren();
  if (!state.projects.length) { const option = element('option', '', '先创建一个项目'); option.value = ''; select.append(option); }
  for (const project of state.projects) { const option = element('option', '', project.name); option.value = project.id; select.append(option); }
  if (state.projects.some(p => p.id === old)) select.value = old;
  selectedProject = select.value;
  $('#project-list').replaceChildren(...state.projects.map(project => {
    const button = element('button', `project-link ${project.id === selectedProject ? 'active' : ''}`, `◇  ${project.name}`);
    button.title = project.directory; button.onclick = () => { selectedProject = project.id; select.value = project.id; renderProjects(); view('workspace'); }; return button;
  }));
  if (!state.projects.length) $('#project-list').append(element('div', 'muted', '还没有项目'));
}
function buildRoles() {
  $('#role-picker').replaceChildren(...state.roles.map((role, i) => {
    const row = element('div', 'role-row'); row.dataset.role = role.id;
    const check = element('input'); check.type = 'checkbox'; check.id = `role-${role.id}`; check.checked = presets.full.includes(role.id);
    const label = element('label', '', role.name); label.htmlFor = check.id;
    const select = element('select'); select.setAttribute('aria-label', `${role.name}使用的模型`);
    for (const provider of ['gemini', 'codex', 'deepseek']) { const option = element('option', '', names[provider]); option.value = provider; select.append(option); } select.value = role.provider;
    check.onchange = () => { $('#preset').value = 'custom'; updateRoleSummary(); }; select.onchange = () => $('#preset').value = 'custom';
    row.append(check, element('span', 'role-index', String(i + 1).padStart(2, '0')), label, select); return row;
  })); updateRoleSummary();
}
function updateRoleSummary() {
  document.querySelectorAll('.role-row').forEach(row => row.classList.toggle('off', !row.querySelector('input').checked));
  const count = document.querySelectorAll('.role-row input:checked').length;
  $('#step-summary').textContent = `${count} 个角色按顺序执行，成果自动交接`;
}
function jobMarkdown(job) { return `# ${job.projectName}\n\n目标：${job.goal}\n\n状态：${statuses[job.status]}\n${job.error ? `\n错误：${job.error}\n` : ''}${job.steps.map(step => `\n## ${state.roles.find(r => r.id === step.roleId)?.name} · ${names[step.provider]}\n\n${step.result || statuses[step.status]}\n`).join('')}`; }
function exportJob(job) {
  const url = URL.createObjectURL(new Blob([jobMarkdown(job)], { type: 'text/markdown;charset=utf-8' })); const anchor = element('a'); anchor.href = url; anchor.download = `协作成果-${job.id.slice(0, 8)}.md`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function renderExecution() {
  const container = $('#execution-content'), job = state.jobs.find(j => j.id === selectedJob) || state.jobs[0];
  if (!job) {
    $('#execution-badge').textContent = '等待任务';
    const empty = element('div', 'empty-state'), orbit = element('div', 'orbit'); orbit.append(element('div', 'orbit-center', '✧'), element('div', 'orbit-dot one', '✦'), element('div', 'orbit-dot two', '⌘'), element('div', 'orbit-dot three', '≈'));
    const tags = element('div', 'empty-tags'); ['明确目标', '分工协作', '记录成果'].forEach(t => tags.append(element('span', '', t)));
    empty.append(orbit, element('h3', '', '你的 Agent 团队，准备就绪。'), element('p', '', '创建项目，描述目标并选择角色。\n每一步的成果都会保存在这里。'), tags); container.replaceChildren(empty); return;
  }
  selectedJob = job.id; $('#execution-badge').textContent = statuses[job.status]; $('#execution-badge').className = `badge ${job.status}`;
  // Preserve open result panels and scroll position while polling.
  const opened = new Set([...container.querySelectorAll('details[open]')].map(d => d.dataset.key));
  const detail = element('div', 'job-detail'); detail.append(element('div', 'muted', job.projectName), element('p', 'job-goal', job.goal));
  const toolbar = element('div', 'job-toolbar'), download = element('button', 'small-button', '↓ 导出成果'); download.onclick = () => exportJob(job);
  const again = element('button', 'small-button', '复制目标'); again.onclick = () => { $('#goal').value = job.goal; selectedProject = job.projectId; renderProjects(); view('workspace'); };
  toolbar.append(download, again);
  if (['queued', 'running'].includes(job.status)) { const cancel = element('button', 'small-button', '取消任务'); cancel.onclick = async () => { try { await api(`/api/jobs/${job.id}/cancel`, 'POST', {}); toast('已请求取消'); await refresh(); } catch(e) { toast(e.message, true); } }; toolbar.append(cancel); }
  detail.append(toolbar);
  job.steps.forEach((step, i) => {
    const block = element('div', `step ${step.status}`), head = element('div', 'step-head');
    head.append(element('span', '', `${String(i + 1).padStart(2, '0')}  ${state.roles.find(r => r.id === step.roleId)?.name || step.roleId}`), element('small', '', `${names[step.provider]} · ${statuses[step.status]}`)); block.append(head);
    if (step.result) { const panel = element('details'); panel.dataset.key = String(i); panel.open = opened.has(String(i)) || (job.status === 'completed' && i === job.steps.length - 1); panel.append(element('summary', '', '查看成果'), element('div', 'result', step.result)); block.append(panel); } detail.append(block);
  });
  if (job.error) detail.append(element('div', 'job-error', job.error));
  const logs = element('details', 'logs'); logs.dataset.key = 'logs'; logs.open = opened.has('logs'); logs.append(element('summary', '', `运行日志 · ${job.logs.length} 条`), element('pre', '', job.logs.map(l => `${new Date(l.at).toLocaleTimeString()} ${l.text}`).join('\n'))); detail.append(logs); container.replaceChildren(detail);
}
function renderHistory() {
  $('#history-count').textContent = state.jobs.length;
  $('#history-list').replaceChildren(...state.jobs.map(job => {
    const card = element('div', 'history-card'), content = element('div', 'history-main'); content.append(element('h3', '', job.goal), element('p', '', `${job.projectName} · ${new Date(job.createdAt).toLocaleString()} · ${job.steps.length} 个角色`));
    const open = element('button', 'small-button', '查看'); open.onclick = () => { selectedJob = job.id; renderExecution(); view('workspace'); }; card.append(content, element('span', `badge ${job.status}`, statuses[job.status]), open); return card;
  })); if (!state.jobs.length) $('#history-list').append(element('div', 'panel empty-state', '还没有任务记录。去工作台开始第一次协作。'));
}
function fillSettings() {
  $('#deepseek-model').value = state.settings.deepseekModel; $('#codex-path').value = state.settings.codexPath; $('#gemini-path').value = state.settings.geminiPath; $('#timeout').value = state.settings.timeoutMinutes;
  $('#key-hint').textContent = state.settings.hasDeepseekKey ? '已配置密钥；服务端不会把密钥返回到页面。' : '尚未配置。填写密钥后才能调用 DeepSeek。';
  $('#codex-hint').textContent = `检测结果：${state.settings.detected.codex || '未检测到'}`; $('#gemini-hint').textContent = `检测结果：${state.settings.detected.gemini || '未检测到；运行安装Gemini.cmd'}`;
}
async function refresh() {
  state = await api('/api/state'); renderProviders(); renderProjects(); renderHistory(); renderExecution();
  if (!initialized) { buildRoles(); fillSettings(); initialized = true; }
}
document.querySelectorAll('.nav').forEach(node => node.onclick = () => { if (node.dataset.view === 'settings') fillSettings(); view(node.dataset.view); });
$('#project-select').onchange = () => { selectedProject = $('#project-select').value; renderProjects(); };
$('#preset').onchange = () => { const preset = presets[$('#preset').value]; if (preset) { document.querySelectorAll('.role-row').forEach(row => row.querySelector('input').checked = preset.includes(row.dataset.role)); updateRoleSummary(); } };
for (const id of ['#new-project', '#new-project-small']) $(id).onclick = () => $('#project-dialog').showModal();
$('#close-dialog').onclick = () => $('#project-dialog').close();
$('#project-form').onsubmit = async event => {
  event.preventDefault(); try { const project = await api('/api/projects', 'POST', { name: $('#project-name').value, directory: $('#project-directory').value, description: $('#project-description').value }); selectedProject = project.id; $('#project-dialog').close(); event.target.reset(); await refresh(); toast('项目已创建'); } catch(e) { toast(e.message, true); }
};
$('#run-job').onclick = async () => {
  const button = $('#run-job'); button.disabled = true;
  try {
    const steps = [...document.querySelectorAll('.role-row')].filter(row => row.querySelector('input').checked).map(row => ({ roleId: row.dataset.role, provider: row.querySelector('select').value }));
    const job = await api('/api/jobs', 'POST', { projectId: $('#project-select').value, goal: $('#goal').value, allowWrite: $('#allow-write').checked, steps }); selectedJob = job.id; await refresh(); toast('任务已提交，开始协作');
  } catch(e) { toast(e.message, true); } finally { button.disabled = false; }
};
$('#settings-form').onsubmit = async event => {
  event.preventDefault(); try { await api('/api/settings', 'PUT', { deepseekKey: $('#deepseek-key').value, clearKey: $('#clear-key').checked, deepseekModel: $('#deepseek-model').value, codexPath: $('#codex-path').value, geminiPath: $('#gemini-path').value, timeoutMinutes: Number($('#timeout').value) }); $('#deepseek-key').value = ''; $('#clear-key').checked = false; await refresh(); fillSettings(); toast('设置已保存'); } catch(e) { toast(e.message, true); }
};
async function poll() { try { await refresh(); } catch(e) { toast(`无法连接本地服务：${e.message}`, true); } finally { timer = setTimeout(poll, document.hidden ? 8000 : 2500); } }
poll(); window.addEventListener('beforeunload', () => clearTimeout(timer));
