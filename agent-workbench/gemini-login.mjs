import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { googleAgentEntry } from './gemini-entry.mjs';
const root = path.dirname(fileURLToPath(import.meta.url));
const entry = googleAgentEntry();
if (!entry) {
  console.error('Google Agent is not installed. Follow README.md Google client setup; prefer tools/agy.exe.'); process.exitCode = 1;
} else {
  const temporary = path.join(root, 'data/tmp'); fs.mkdirSync(temporary, { recursive: true });
  const child = spawn(entry.endsWith('.exe') ? entry : process.execPath, entry.endsWith('.exe') ? [] : [entry], { cwd: root, stdio: 'inherit',
    env: { ...process.env, GEMINI_CLI_HOME: path.join(root, 'data/gemini-home'), TEMP: temporary, TMP: temporary } });
  child.on('error', e => { console.error(e.message); process.exitCode = 1; });
  child.on('exit', code => process.exitCode = code ?? 1);
}
