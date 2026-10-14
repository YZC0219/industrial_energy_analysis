import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.dirname(fileURLToPath(import.meta.url));
export function googleAgentEntry() {
  const antigravity = path.join(root, 'tools/agy.exe');
  return fs.existsSync(antigravity) ? antigravity : installedGeminiEntry();
}
export function installedGeminiEntry() {
  const directory = path.join(root, 'tools/node_modules/@google/gemini-cli');
  try {
    const metadata = JSON.parse(fs.readFileSync(path.join(directory, 'package.json'), 'utf8'));
    const relative = typeof metadata.bin === 'string' ? metadata.bin : metadata.bin?.gemini;
    if (!relative) return '';
    const entry = path.resolve(directory, relative);
    if (!entry.startsWith(directory + path.sep) || !fs.existsSync(entry)) return '';
    return entry;
  } catch { return ''; }
}
