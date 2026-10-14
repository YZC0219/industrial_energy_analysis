import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
const root = path.dirname(fileURLToPath(import.meta.url));
const url = 'http://127.0.0.1:3210';
const open = () => {
  const browser = spawn('powershell.exe', ['-NoProfile', '-Command', `Start-Process '${url}'`], { windowsHide: true, stdio: 'ignore', detached: true });
  browser.on('error', () => console.log(`请手动打开 ${url}`)); browser.unref();
};
try {
  const response = await fetch(`${url}/api/state`, { signal: AbortSignal.timeout(1500) });
  const body = await response.json();
  if (!body.roles || !body.settings) throw new Error('3210 端口被其他服务占用');
  open();
} catch {
  fs.mkdirSync(path.join(root, 'data'), { recursive: true });
  const output = fs.openSync(path.join(root, 'data/server.log'), 'a');
  const errors = fs.openSync(path.join(root, 'data/server-error.log'), 'a');
  const child = spawn(process.execPath, [path.join(root, 'server.mjs')], { cwd: root, detached: true, windowsHide: true, stdio: ['ignore', output, errors] });
  child.on('error', e => console.error(`启动失败：${e.message}`)); child.unref(); fs.closeSync(output); fs.closeSync(errors);
  let ready = false;
  for (let attempt = 0; attempt < 40; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 250));
    try {
      const response = await fetch(`${url}/api/state`, { signal: AbortSignal.timeout(1000) });
      const body = await response.json(); if (body.roles && body.settings) { ready = true; break; }
    } catch {}
  }
  if (ready) open();
  else { console.error(`未能确认服务启动，请检查 data/server-error.log 或手动打开 ${url}`); process.exitCode = 1; }
}
