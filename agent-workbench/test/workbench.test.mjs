import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Readable } from 'node:stream';
import { createApp, codexResult, runProcess } from '../server.mjs';
import { installedGeminiEntry } from '../gemini-entry.mjs';
const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

async function setup(t, invokeOverride) {
  const dataDir = fs.mkdtempSync(path.join(root, 'data', 'test-'));
  const app = createApp({ dataDir, invokeOverride });
  t.after(async () => { await app.close(); fs.rmSync(dataDir, { recursive: true, force: true }); });
  const request = async (route, method = 'GET', body, extra = {}) => {
    // Exercise the same HTTP handler in-process: also works when sandbox denies loopback sockets.
    const req = Readable.from(body ? [Buffer.from(JSON.stringify(body))] : []);
    req.url = route; req.method = method;
    req.headers = { host: '127.0.0.1:3210', ...(body ? { 'content-type': 'application/json' } : {}) };
    for (const [name, value] of Object.entries(extra)) req.headers[name.toLowerCase()] = value;
    return new Promise((resolve, reject) => {
      const headers = {}; let status = 200;
      const res = { setHeader: (name, value) => headers[name] = value,
        writeHead: (code, values) => { status = code; Object.assign(headers, values); },
        end: output => { try { resolve({ status, headers, data: JSON.parse(output) }); } catch { resolve({ status, headers, data: String(output) }); } } };
      Promise.resolve(app.server.listeners('request')[0](req, res)).catch(reject);
    });
  };
  const project = (await request('/api/projects', 'POST', { name: '测试项目', directory: root, description: '用于测试' })).data;
  return { app, request, project, dataDir };
}
async function waitJob(request, id, expected) {
  for (let i = 0; i < 100; i++) {
    const job = (await request('/api/state')).data.jobs.find(j => j.id === id);
    if (expected.includes(job.status)) return job;
    if (['failed', 'cancelled', 'interrupted'].includes(job.status)) throw new Error(`意外任务状态 ${job.status}: ${job.error}`);
    await new Promise(resolve => setTimeout(resolve, 15));
  }
  throw new Error('任务未及时进入预期状态');
}
test('依序调用真实调度层并传递前序成果，保存可恢复记录', async t => {
  const calls = [];
  const { request, project, dataDir } = await setup(t, async input => { calls.push(input); return `成果-${input.provider}`; });
  const created = await request('/api/jobs', 'POST', { projectId: project.id, goal: '制作成果', steps: [{ roleId: 'architecture', provider: 'gemini' }, { roleId: 'implementation', provider: 'codex' }, { roleId: 'review', provider: 'deepseek' }] });
  assert.equal(created.status, 201);
  const job = await waitJob(request, created.data.id, ['completed']);
  assert.deepEqual(calls.map(c => c.provider), ['gemini', 'codex', 'deepseek']);
  assert.match(calls[1].prompt, /成果-gemini/); assert.match(calls[2].prompt, /成果-codex/);
  assert.match(calls[1].prompt, /只读分析/); assert.equal(job.steps.length, 3);
  assert.equal(JSON.parse(fs.readFileSync(path.join(dataDir, 'state.json'))).jobs[0].status, 'completed');
});
test('队列使用 FIFO，取消排队任务不会调用模型', async t => {
  let release; const calls = [];
  const { request, project } = await setup(t, async ({ job }) => { calls.push(job.goal); if (job.goal === 'first') await new Promise(resolve => release = resolve); return job.goal; });
  const submit = async goal => (await request('/api/jobs', 'POST', { projectId: project.id, goal, steps: [{ roleId: 'review', provider: 'deepseek' }] })).data;
  const first = await submit('first'); await waitJob(request, first.id, ['running']);
  const second = await submit('second'), third = await submit('third'), fourth = await submit('fourth');
  await request(`/api/jobs/${third.id}/cancel`, 'POST', {}); release();
  await waitJob(request, fourth.id, ['completed']);
  assert.deepEqual(calls, ['first', 'second', 'fourth']);
  assert.equal((await waitJob(request, third.id, ['cancelled'])).status, 'cancelled');
  assert.equal((await waitJob(request, second.id, ['completed'])).status, 'completed');
});
test('失败停止后续调用并隐藏错误中的密钥', async t => {
  let calls = 0;
  const { request, project } = await setup(t, async () => { calls++; throw new Error('error SECRET_TEST_KEY'); });
  await request('/api/settings', 'PUT', { deepseekKey: 'SECRET_TEST_KEY', timeoutMinutes: 1 });
  const created = (await request('/api/jobs', 'POST', { projectId: project.id, goal: '失败测试', steps: [{ roleId: 'review', provider: 'deepseek' }, { roleId: 'documentation', provider: 'deepseek' }] })).data;
  const job = await waitJob(request, created.id, ['failed']);
  assert.equal(calls, 1); assert.equal(job.steps[1].status, 'pending'); assert.doesNotMatch(JSON.stringify(job), /SECRET_TEST_KEY/);
});
test('运行任务可取消，不执行下一阶段', async t => {
  let calls = 0;
  const { request, project } = await setup(t, async ({ signal }) => {
    calls++; await new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }));
  });
  const job = (await request('/api/jobs', 'POST', { projectId: project.id, goal: 'cancel', steps: [{ roleId: 'review', provider: 'deepseek' }, { roleId: 'documentation', provider: 'deepseek' }] })).data;
  await waitJob(request, job.id, ['running']); await request(`/api/jobs/${job.id}/cancel`, 'POST', {});
  assert.equal((await waitJob(request, job.id, ['cancelled'])).status, 'cancelled'); assert.equal(calls, 1);
});
test('密钥不出现在状态响应，非法设置不部分生效', async t => {
  const { request } = await setup(t, async () => 'ok');
  assert.equal((await request('/api/settings', 'PUT', { deepseekKey: 'PRIVATE_KEY', deepseekModel: 'deepseek-chat', timeoutMinutes: 5 })).status, 200);
  const state = (await request('/api/state')).data;
  assert.equal(state.settings.hasDeepseekKey, true); assert.doesNotMatch(JSON.stringify(state), /PRIVATE_KEY/);
  assert.equal((await request('/api/settings', 'PUT', { deepseekModel: 'bad', timeoutMinutes: -1 })).status, 400);
  assert.equal((await request('/api/state')).data.settings.deepseekModel, 'deepseek-chat');
  await request('/api/settings', 'PUT', { clearKey: true, timeoutMinutes: 5 });
  assert.equal((await request('/api/state')).data.settings.hasDeepseekKey, false);
});
test('拒绝跨站请求、路径穿越和不存在的项目目录', async t => {
  const { request } = await setup(t, async () => 'ok');
  assert.equal((await request('/api/projects', 'POST', { name: 'bad', directory: root }, { Origin: 'https://example.com' })).status, 403);
  assert.equal((await request('/api/projects', 'POST', { name: 'bad', directory: path.join(root, 'does-not-exist') })).status, 400);
  assert.equal((await request('/api/state', 'GET', undefined, { Host: 'evil.example:3210' })).status, 403);
  assert.equal((await request('/data/settings.json')).status, 404);
});
test('Codex JSONL 解析最终消息和显式失败', () => {
  assert.equal(codexResult('{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n'), 'done');
  assert.throws(() => codexResult('{"type":"turn.failed","error":{"message":"failed"}}'), /failed/);
  assert.throws(() => codexResult('invalid output'), /未返回/);
});
test('Gemini 启动路径遵循包的 bin 声明', () => {
  const directory = path.join(root, 'tools/node_modules/@google/gemini-cli');
  if (!fs.existsSync(path.join(directory, 'package.json'))) return;
  const metadata = JSON.parse(fs.readFileSync(path.join(directory, 'package.json'), 'utf8'));
  assert.equal(installedGeminiEntry(), path.resolve(directory, metadata.bin.gemini));
});
test('生产 handler 提供网页资源，拒绝访问设置文件', async t => {
  const { request } = await setup(t, async () => 'ok');
  const html = await request('/');
  assert.equal(html.status, 200); assert.match(html.data, /Agent 工作台/);
  assert.match(html.headers['Content-Type'], /text\/html/);
  assert.match((await request('/app.js')).data, /function renderExecution/);
  assert.match((await request('/style.css')).data, /@media/);
  assert.equal((await request('/server.mjs')).status, 404);
});
test('进程适配器通过 stdin 传递 Unicode 任务，无 shell 展开', async () => {
  const output = await runProcess(process.execPath, ['-e', 'process.stdin.setEncoding("utf8"); let s=""; process.stdin.on("data",d=>s+=d); process.stdin.on("end",()=>process.stdout.write(s));'], { cwd: root, input: '中文任务 $(never-execute) `literal`' });
  assert.equal(output, '中文任务 $(never-execute) `literal`');
});
